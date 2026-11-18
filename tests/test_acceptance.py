import io
import uuid
from unittest.mock import patch
import pytest
from django.db import connection, transaction, IntegrityError
from django.core.management import call_command
from django.core.management.base import CommandError
from apps.integrations.models import InboundEvent, OutboundJob
from apps.integrations.telegram.inbound import process_event
from apps.integrations.tasks import deliver
from apps.integrations.views import untriaged_for
from apps.conversations.models import Conversation, Message
from apps.tickets.models import Ticket
from apps.workspaces.models import Workspace
from apps.teams.models import Team
from apps.attachments.models import Attachment

pytestmark = pytest.mark.django_db


def test_group_thread_from_webhook_to_closed_ticket(desk, client_for):
    connection = desk["connection"]
    chat = Conversation.objects.create(
        workspace=desk["ws"],
        connection=connection,
        kind="supergroup",
        external_id="-1001234567890123",
        approved=True,
        mode="shared",
        title="Команда клиента",
    )
    update = {
        "update_id": 781,
        "message": {
            "message_id": 55,
            "message_thread_id": 33,
            "date": 1780000000,
            "from": {"id": 789, "first_name": "Али"},
            "chat": {"id": int(chat.external_id), "type": "supergroup"},
            "text": "/new Не работает вход",
        },
    }
    c = client_for(desk["operator"])
    webhook = f"/api/v1/webhooks/telegram/{connection.id}"
    for _ in range(2):
        assert (
            c.post(
                webhook,
                update,
                format="json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="test-secret",
            ).status_code
            == 200
        )
    event = InboundEvent.objects.get(update_id="781")
    process_event(event.id)
    process_event(event.id)
    ticket = Ticket.objects.get(conversation=chat)
    assert str(ticket.id) in [
        t["id"] for t in c.get("/api/v1/tickets?queue=free").json()["results"]
    ]
    path = f"/api/v1/tickets/{ticket.id}"
    claimed = c.post(
        path + "/claim", {"expected_version": ticket.version}, format="json"
    ).json()
    sent = c.post(
        path + "/messages",
        {
            "expected_version": claimed["version"],
            "operation_key": str(uuid.uuid4()),
            "reply_id": "55",
            "text": "Вход восстановлен",
        },
        format="json",
    )
    assert sent.status_code == 201
    message = Message.objects.get(id=sent.json()["id"])
    from django.test import override_settings
    from cryptography.fernet import Fernet

    mock_key = Fernet.generate_key()
    connection.encrypted_token = Fernet(mock_key).encrypt(b"test-credential").decode()
    connection.save()
    with (
        override_settings(TOKEN_ENCRYPTION_KEY=mock_key.decode()),
        patch("apps.integrations.telegram.adapter.httpx.post") as http,
    ):
        http.return_value.json.return_value = {"ok": True, "result": {"message_id": 56}}
        deliver(message.job.id)
        payload = http.call_args.kwargs["json"]
        assert payload["chat_id"] == "-1001234567890123"
        assert payload["message_thread_id"] == 33
        assert payload["reply_parameters"]["message_id"] == 55
    ticket.refresh_from_db()
    assert ticket.last_agent_message_at
    resolved = c.post(
        path + "/transition",
        {
            "expected_version": ticket.version,
            "status": "resolved",
            "resolution": "Доступ проверен",
        },
        format="json",
    )
    closed = c.post(
        path + "/transition",
        {"expected_version": resolved.json()["version"], "status": "closed"},
        format="json",
    )
    assert closed.json()["status"] == "closed"
    assert c.get(path + "/history").json()["count"] >= 5


def test_visible_untriaged_and_explicit_assignment(desk, client_for):
    conv = desk["conversation"]
    m = Message.objects.create(
        workspace=desk["ws"],
        conversation=conv,
        direction="inbound",
        text="Новая проблема",
        metadata={"sender_id": "456"},
    )
    assert untriaged_for(desk["manager"]).filter(pk=m.id).exists()
    result = client_for(desk["manager"]).post(
        f"/api/v1/untriaged/{m.id}/assign",
        {"subject": "Отдельное обращение"},
        format="json",
    )
    assert result.status_code == 200
    m.refresh_from_db()
    assert str(m.ticket_id) == result.json()["ticket_id"]
    assert not untriaged_for(desk["manager"]).filter(pk=m.id).exists()


@pytest.mark.postgres
def test_database_rejects_cross_workspace_and_invalid_assignment(desk):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL required")
    other = Workspace.objects.create(name="Чужое пространство")
    team = Team.objects.create(workspace=other, name="Чужая команда")
    with pytest.raises(IntegrityError), transaction.atomic():
        Ticket.objects.filter(pk=desk["ticket"].pk).update(team=team)
    invalid = Team.objects.create(workspace=desk["ws"], name="Ограниченная команда")
    with pytest.raises(IntegrityError), transaction.atomic():
        Ticket.objects.filter(pk=desk["ticket"].pk).update(
            team=invalid, assignee=desk["operator"]
        )


def test_attachment_restore_and_corruption_guard(desk, tmp_path):
    body = b"%PDF-1.7\nservice-desk-test"
    a = Attachment.objects.create(
        workspace=desk["ws"],
        filename="test.pdf",
        size=len(body),
        mime_type="application/pdf",
        object_key=f"{desk['ws'].id}/test",
        state="ready",
    )
    with patch(
        "apps.attachments.management.commands.snapshot_files.storage"
    ) as storage:
        client = storage.return_value
        client.get_object.return_value = {"Body": io.BytesIO(body)}
        restored = {}
        client.put_object.side_effect = lambda **kw: restored.update(
            {kw["Key"]: kw["Body"].read()}
        )
        call_command("snapshot_files", "export", tmp_path)
        call_command("snapshot_files", "restore", tmp_path)
        assert restored[a.object_key] == body
        (tmp_path / str(a.id)).write_bytes(b"x" * len(body))
        with pytest.raises(CommandError):
            call_command("snapshot_files", "restore", tmp_path)
        assert client.put_object.call_count == 1


def test_foreign_attachment_is_hidden(desk, client_for):
    other = Workspace.objects.create(name="Другая организация")
    a = Attachment.objects.create(
        workspace=other, filename="private.pdf", object_key="secret", state="ready"
    )
    c = client_for(desk["admin"])
    with patch("apps.attachments.views.storage") as storage:
        assert c.get(f"/api/v1/attachments/{a.id}/download").status_code == 404
        storage.assert_not_called()
