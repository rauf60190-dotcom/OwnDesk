import uuid
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.attachments.storage import LocalMedia, storage
from apps.integrations.models import InboundEvent
from apps.integrations.native_worker import cycle
from apps.tickets.models import Ticket


@pytest.mark.django_db
def test_native_worker_processes_persisted_webhook_without_broker(desk):
    update = {"update_id": 9231, "message": {"message_id": 18, "text": "Нужна помощь",
        "date": 1780000000, "chat": {"id": 777, "type": "private"},
        "from": {"id": 777, "first_name": "Клиент"}}}
    event = InboundEvent.objects.create(connection=desk["connection"], update_id="9231", payload=update)
    with patch("apps.integrations.tasks.handle_event.delay", side_effect=AssertionError("Redis must not be used")):
        cycle({}, {})
        event.refresh_from_db()
        assert event.state == "done"
        count = Ticket.objects.count()
        cycle({}, {})
        assert Ticket.objects.count() == count


@pytest.mark.django_db
def test_native_worker_delivers_queued_message_once(desk, client_for):
    ticket = desk["ticket"]
    client = client_for(desk["operator"])
    path = f"/api/v1/tickets/{ticket.pk}"
    claimed = client.post(path + "/claim", {"expected_version": ticket.version}, format="json")
    response = client.post(path + "/messages", {"expected_version": claimed.json()["version"],
        "text": "Проверка очереди", "operation_key": str(uuid.uuid4())}, format="json")
    assert response.status_code == 201
    job = ticket.messages.get(direction="outbound").job
    with patch("apps.integrations.tasks.handle_delivery.delay", side_effect=AssertionError("Redis must not be used")), patch(
        "apps.integrations.tasks.TelegramAdapter",
    ) as adapter:
        adapter.return_value.send.return_value = {"message_id": 42}
        cycle({}, {})
        cycle({}, {})
    job.refresh_from_db()
    assert job.state == "sent"
    adapter.return_value.send.assert_called_once()


def test_native_private_media_works_with_debug_disabled(settings, tmp_path):
    settings.OWNDESK_RUNTIME = "native"
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path / "private")
    settings.DEBUG = False
    media = storage()
    assert isinstance(media, LocalMedia)
    media.put_object(Bucket="private", Key="files/a.txt", Body=b"private", ContentType="text/plain")
    with media.get_object(Bucket="private", Key="files/a.txt")["Body"] as stream:
        assert stream.read() == b"private"
    with pytest.raises(ValueError):
        media.get_object(Bucket="private", Key="../outside.txt")


def test_native_command_rejects_debug_and_public_media(settings):
    settings.DEBUG = True
    with pytest.raises(CommandError, match="DEBUG=0"):
        call_command("run_native")
    settings.DEBUG = False
    settings.DEPLOYMENT_MODE = "organization"
    settings.OWNDESK_RUNTIME = "native"
    settings.PRIVATE_MEDIA_ROOT = str(settings.BASE_DIR.parent / "frontend/dist/private")
    with pytest.raises(CommandError, match="public frontend"):
        call_command("run_native")


def test_native_worker_notifications_cross_thread_to_asgi_loop():
    import asyncio
    from channels.layers import InMemoryChannelLayer
    from apps.core.native_events import bind_loop, send_group

    async def check():
        bind_loop()
        layer = InMemoryChannelLayer()
        channel = await layer.new_channel()
        await layer.group_add("test_group", channel)
        receiving = asyncio.create_task(layer.receive(channel))
        assert await asyncio.to_thread(send_group, layer, "test_group", {"type": "desk.event", "value": 42})
        assert (await asyncio.wait_for(receiving, 2))["value"] == 42

    asyncio.run(check())
