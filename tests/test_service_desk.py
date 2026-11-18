import uuid
from datetime import timedelta
from unittest.mock import patch
import pytest
from django.db import connection, connections, close_old_connections
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.tickets import services
from apps.tickets.models import Ticket
from apps.workspaces.models import Workspace, Membership
from apps.teams.models import Team
from apps.conversations.models import Conversation, Message
from apps.integrations.models import InboundEvent, OutboundJob
from apps.integrations.telegram.inbound import process_event
from apps.integrations.telegram.adapter import TelegramError
from apps.integrations.tasks import deliver, sweep
from apps.sla import services as sla
from apps.core.errors import Conflict

pytestmark = pytest.mark.django_db


def event(desk, number=1, text="Помогите, пожалуйста", **extra):
    message = {
        "message_id": number,
        "date": int(timezone.now().timestamp()),
        "from": {"id": 456, "first_name": "Клиент"},
        "chat": {"id": 456, "type": "private"},
        "text": text,
        **extra,
    }
    return InboundEvent.objects.create(
        connection=desk["connection"],
        update_id=str(number),
        payload={"update_id": number, "message": message},
    )


def test_claim_and_version_conflict(desk):
    t = services.claim(desk["operator"], desk["ticket"].id, 1)
    assert t.assignee == desk["operator"] and t.status == "in_progress"
    with pytest.raises(Conflict):
        services.claim(desk["second"], t.id, 1)
    with pytest.raises(Conflict):
        services.edit(desk["operator"], t.id, 1, {"subject": "Старые данные"})


def test_observer_and_other_operator_cannot_change(desk):
    t = services.claim(desk["operator"], desk["ticket"].id, 1)
    with pytest.raises(PermissionDenied):
        services.edit(desk["second"], t.id, t.version, {"subject": "Запрещено"})
    with pytest.raises(PermissionDenied):
        services.send(
            desk["observer"], t.id, t.version, "Заметка", uuid.uuid4(), note=True
        )


def test_notes_are_never_queued_for_telegram(desk):
    msg = services.send(
        desk["second"],
        desk["ticket"].id,
        1,
        "Только для команды",
        uuid.uuid4(),
        note=True,
    )
    assert msg.visibility == "internal"
    assert OutboundJob.objects.count() == 0


def test_send_idempotency(desk):
    t = services.claim(desk["operator"], desk["ticket"].id, 1)
    key = uuid.uuid4()
    first = services.send(desk["operator"], t.id, t.version, "Здравствуйте", key)
    again = services.send(desk["operator"], t.id, t.version, "Здравствуйте", key)
    assert first.id == again.id and OutboundJob.objects.count() == 1


def test_status_requires_resolution_and_explicit_close(desk):
    t = services.claim(desk["operator"], desk["ticket"].id, 1)
    with pytest.raises(ValidationError):
        services.transition(desk["operator"], t.id, t.version, "resolved")
    t = services.transition(
        desk["operator"], t.id, t.version, "resolved", "Настройки исправлены"
    )
    assert t.closed_at is None
    t = services.transition(desk["operator"], t.id, t.version, "closed")
    assert t.closed_at is not None


def test_start_does_not_create_ticket(desk):
    before = Ticket.objects.count()
    e = event(desk, text="/start")
    process_event(e.id)
    process_event(e.id)
    assert Ticket.objects.count() == before
    assert Message.objects.filter(direction="automatic").count() == 1


def test_repeat_event_and_external_message_are_deduplicated(desk):
    e = event(desk)
    process_event(e.id)
    process_event(e.id)
    raw = e.payload.copy()
    raw["update_id"] = 2
    e2 = InboundEvent.objects.create(
        connection=desk["connection"], update_id="2", payload=raw
    )
    process_event(e2.id)
    assert Message.objects.filter(direction="inbound").count() == 1


def test_edit_updates_record_and_ignores_older_edit(desk):
    e = event(desk)
    process_event(e.id)
    for number, timestamp, text in [(2, 200, "Новый текст"), (3, 100, "Старая правка")]:
        raw = e.payload["message"].copy()
        raw["edit_date"] = timestamp
        raw["text"] = text
        update = InboundEvent.objects.create(
            connection=desk["connection"],
            update_id=str(number),
            payload={"edited_message": raw},
        )
        process_event(update.id)
    assert Message.objects.get(direction="inbound").text == "Новый текст"


def test_repeated_messages_do_not_move_response_deadline(desk):
    process_event(event(desk).id)
    deadline = desk["ticket"].deadlines.get(kind="first_response").deadline
    process_event(event(desk, 2, "Дополнение").id)
    assert desk["ticket"].deadlines.get(kind="first_response").deadline == deadline


def test_unknown_delivery_not_retried(desk):
    t = services.claim(desk["operator"], desk["ticket"].id, 1)
    message = services.send(desk["operator"], t.id, t.version, "Ответ", uuid.uuid4())
    with patch("apps.integrations.tasks.TelegramAdapter") as Adapter:
        Adapter.return_value.send.side_effect = TelegramError(
            "Подтверждение потеряно", unknown=True
        )
        deliver(message.job.id)
        deliver(message.job.id)
        assert Adapter.return_value.send.call_count == 1
    message.job.refresh_from_db()
    assert message.job.state == "unknown"


def test_confirmed_send_and_message_during_send(desk):
    process_event(event(desk).id)
    t = Ticket.objects.get(id=desk["ticket"].id)
    t = services.claim(desk["operator"], t.id, t.version)
    message = services.send(desk["operator"], t.id, t.version, "Ответ", uuid.uuid4())

    def telegram_send(_):
        process_event(event(desk, 2, "Ещё один вопрос").id)
        return {"message_id": 900}

    with patch("apps.integrations.tasks.TelegramAdapter") as Adapter:
        Adapter.return_value.send.side_effect = telegram_send
        deliver(message.job.id)
    t.refresh_from_db()
    assert t.deadlines.get(kind="first_response").completed_at
    assert t.deadlines.filter(kind="next_response", completed_at=None).exists()
    assert t.status == "in_progress"


def test_auto_response_does_not_complete_sla(desk):
    process_event(event(desk).id)
    process_event(event(desk, 2, "/start").id)
    job = OutboundJob.objects.get()
    with patch("apps.integrations.tasks.TelegramAdapter") as Adapter:
        Adapter.return_value.send.return_value = {"message_id": 900}
        deliver(job.id)
    assert desk["ticket"].deadlines.get(kind="first_response").completed_at is None


def test_webhook_secret_and_duplicate(desk, client_for):
    from rest_framework.test import APIClient

    c = APIClient()
    url = f"/api/v1/webhooks/telegram/{desk['connection'].id}"
    assert c.post(url, {"update_id": 1}, format="json").status_code == 404
    for _ in range(2):
        assert (
            c.post(
                url,
                {"update_id": 1},
                format="json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="test-secret",
            ).status_code
            == 200
        )
    assert InboundEvent.objects.count() == 1


def test_workspace_isolation_api(desk, client_for):
    ws = Workspace.objects.create(name="Чужая компания")
    team = Team.objects.create(workspace=ws, name="Секретная команда")
    conv = Conversation.objects.create(workspace=ws, kind="manual")
    foreign = services.create_ticket(ws, conv, team, "Секретный текст")
    c = client_for(desk["admin"])
    assert c.get(f"/api/v1/tickets/{foreign.id}").status_code == 404
    assert c.get("/api/v1/tickets?q=Секретный").json()["count"] == 0
    assert (
        c.post(
            f"/api/v1/tickets/{foreign.id}/claim",
            {"expected_version": 1},
            format="json",
        ).status_code
        == 404
    )


def test_api_queues_and_csrf(desk, client_for):
    c = client_for(desk["operator"])
    assert c.get("/api/v1/queues/counts").json()["free"] == 1
    result = c.get("/api/v1/tickets?queue=free")
    assert result.status_code == 200, result.content
    assert result.json()["count"] == 1
    strict = client_for(desk["operator"], csrf=True)
    assert (
        strict.post(
            f"/api/v1/tickets/{desk['ticket'].id}/claim",
            {"expected_version": 1},
            format="json",
        ).status_code
        == 403
    )


def test_last_admin_and_deactivation(desk, client_for):
    c = client_for(desk["admin"])
    assert (
        c.patch(
            f"/api/v1/employees/{desk['admin'].id}", {"active": False}, format="json"
        ).status_code
        == 400
    )
    t = services.claim(desk["operator"], desk["ticket"].id, 1)
    logged = client_for(desk["operator"])
    assert (
        c.patch(
            f"/api/v1/employees/{desk['operator'].id}", {"active": False}, format="json"
        ).status_code
        == 200
    )
    t.refresh_from_db()
    assert t.assignee_id is None
    assert logged.get("/api/v1/auth/me").status_code == 403


def test_unapproved_group_is_not_visible_to_operator(desk, client_for):
    e = event(
        desk,
        chat={"id": -1009876543210, "type": "supergroup", "title": "Новая группа"},
        text="/new Помощь",
    )
    process_event(e.id)
    assert Ticket.objects.count() == 1
    assert client_for(desk["operator"]).get("/api/v1/untriaged").json()["count"] == 0
    assert not Conversation.objects.get(external_id="-1009876543210").approved


def test_ambiguous_private_message_is_untriaged(desk):
    services.create_ticket(
        desk["ws"], desk["conversation"], desk["team"], "Вторая проблема"
    )
    process_event(event(desk).id)
    assert Message.objects.get(direction="inbound").ticket_id is None
    assert Message.objects.filter(direction="automatic").exists()


def test_wait_customer_pauses_only_resolution(desk):
    process_event(event(desk).id)
    t = Ticket.objects.get(pk=desk["ticket"].pk)
    t = services.claim(desk["operator"], t.id, t.version)
    t = services.transition(desk["operator"], t.id, t.version, "waiting_customer")
    assert t.deadlines.get(kind="resolution").paused_at
    assert t.deadlines.get(kind="first_response").paused_at is None


def test_working_calendar_weekend():
    from datetime import datetime, timezone as dtzone

    start = datetime(2026, 11, 6, 17, 0, tzinfo=dtzone.utc)
    schedule = {"week": {str(x): [["09:00", "18:00"]] for x in range(5)}}
    result = sla.add_work(start, 120 * 60, schedule, "UTC")
    assert result.isoformat() == "2026-11-09T10:00:00+00:00"


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_two_concurrent_claims_have_one_winner(desk):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL required")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def attempt(member_id):
        close_old_connections()
        m = Membership.objects.get(id=member_id)
        barrier.wait()
        try:
            services.claim(m, desk["ticket"].id, 1)
            return "won"
        except Conflict:
            return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [desk["operator"].id, desk["second"].id]))
    assert sorted(results) == ["conflict", "won"]
