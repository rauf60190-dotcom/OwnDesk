from unittest.mock import patch
import uuid
import pytest
from cryptography.fernet import Fernet
from django.test import override_settings
from apps.integrations.local_worker import poll
from apps.integrations.models import ChannelConnection, InboundEvent
from apps.integrations.telegram.inbound import process_event
from apps.integrations.tasks import deliver
from apps.tickets.models import Ticket


@pytest.mark.django_db
@override_settings(LOCAL_TELEGRAM_POLLING=True, TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), PUBLIC_BASE_URL="")
def test_local_bot_connect_receive_claim_and_reply(desk, client_for):
    admin = client_for(desk["admin"])
    operator = client_for(desk["operator"])
    update = {"update_id": 91, "message": {"message_id": 8, "text": "Помогите войти",
        "date": 1780000000, "chat": {"id": 777, "type": "private"}, "from": {"id": 777, "first_name": "Клиент"}}}
    with patch("apps.integrations.telegram.adapter.TelegramAdapter.call") as call:
        call.side_effect = [{"id": 444, "username": "local_test_bot"}, {"url": ""}, [update], [update], [], {"message_id": 9}]
        connected = admin.post("/api/v1/integrations/telegram", {"token": "local-test", "team_id": desk["team"].id}, format="json")
        assert connected.status_code == 201
        bot = ChannelConnection.objects.get(pk=connected.json()["id"])
        assert bot.transport == "polling" and bot.encrypted_token != "local-test"
        assert poll(bot) == 92
        assert poll(bot) == 92
        assert poll(bot, 92) is None
        assert call.call_args.args[1]["offset"] == 92
        event = InboundEvent.objects.get(connection=bot)
        process_event(event.pk)
        ticket = Ticket.objects.get(conversation__connection=bot)
        path = f"/api/v1/tickets/{ticket.id}"
        claimed = operator.post(path + "/claim", {"expected_version": ticket.version}, format="json")
        response = operator.post(path + "/messages", {"expected_version": claimed.json()["version"],
            "text": "Проверьте вход", "operation_key": str(uuid.uuid4())}, format="json")
        assert response.status_code == 201
        job = ticket.messages.get(direction="outbound").job
        deliver(job.pk)
        job.refresh_from_db()
        assert job.state == "sent"
        call.assert_called_with("sendMessage", {"chat_id": "777", "text": "operator · Поддержка\n\nПроверьте вход"})


@pytest.mark.django_db
@override_settings(LOCAL_TELEGRAM_POLLING=True, TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
def test_existing_webhook_is_preserved(desk, client_for):
    with patch("apps.integrations.telegram.adapter.TelegramAdapter.call") as call:
        call.side_effect = [{"id": 444, "username": "busy"}, {"url": "https://existing.example/webhook"}]
        result = client_for(desk["admin"]).post("/api/v1/integrations/telegram", {"token": "test", "team_id":desk["team"].id}, format="json")
        assert result.status_code == 400
        assert not ChannelConnection.objects.filter(bot_id="444").exists()
        assert [x.args[0] for x in call.call_args_list] == ["getMe", "getWebhookInfo"]
