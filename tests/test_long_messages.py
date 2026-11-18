import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.integrations.telegram.adapter import TelegramAdapter, TelegramError, split_text
from apps.integrations.tasks import deliver
from apps.tickets.services import send


@pytest.mark.parametrize("text,limit", [
    ("Длинное предложение с пробелами. " * 600, 4096),
    ("🙂" * 10000, 1024),
    ("я" * 20000, 4096),
    ("", 1024),
])
def test_split_preserves_text_and_limits(text, limit):
    parts = split_text(text, limit)
    assert "".join(parts) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= (limit if i == 0 else 4096)
               for i, part in enumerate(parts))


@pytest.mark.django_db
def test_long_reply_resumes_confirmed_parts_and_is_idempotent(desk):
    ticket = desk["ticket"]
    text = "Длинный ответ. " * 700
    key = uuid.uuid4()
    message = send(desk["admin"], ticket.pk, ticket.version, text, key)
    assert send(desk["admin"], ticket.pk, ticket.version, text, key).pk == message.pk
    adapter = TelegramAdapter(token="test-only")
    with patch("apps.integrations.tasks.TelegramAdapter", return_value=adapter), patch.object(adapter, "call") as call:
        call.side_effect = [{"message_id": 101}, TelegramError("rate limit", retry_after=1)]
        deliver(message.job.pk)
        message.refresh_from_db()
        assert message.metadata["telegram_parts"] == ["101"]
        job = message.job
        job.refresh_from_db()
        assert job.state == "queued"
        job.next_attempt_at = None
        job.save()
        call.reset_mock()
        call.side_effect = [{"message_id": 102}, {"message_id": 103}]
        deliver(job.pk)
        job.refresh_from_db()
        message.refresh_from_db()
        assert job.state == "sent"
        assert message.external_id == "101"
        assert [c.args[1]["text"] for c in call.call_args_list] == message.metadata["telegram_delivery_parts"][1:]


def test_long_caption_uploads_file_once():
    attachment = SimpleNamespace(mime_type="image/png", size=10, filename="test.png", object_key="test")
    message = SimpleNamespace(text="Описание фото. " * 500, metadata={}, direction="outbound", visibility="public", author_name="Анна",
        conversation=SimpleNamespace(external_id="test"), thread_id=None, reply_id=None,
        attachments=Mock(), save=Mock())
    message.attachments.first.return_value = attachment
    adapter = TelegramAdapter(token="test-only")
    with patch("apps.attachments.storage.storage") as storage, patch.object(adapter, "call") as call:
        storage.return_value.get_object.return_value = {"Body": BytesIO(b"image")}
        call.side_effect = [{"message_id": 1}, {"message_id": 2}, {"message_id": 3}]
        adapter.send(message)
        calls = call.call_args_list
        assert calls[0].args[0] == "sendPhoto"
        assert all(c.args[0] == "sendMessage" for c in calls[1:])
        delivered=[calls[0].args[1]["caption"],*[c.args[1]["text"] for c in calls[1:]]]
        assert all(part.startswith("Анна · Поддержка\n\n") for part in delivered)
        assert "".join(part.removeprefix("Анна · Поддержка\n\n") for part in delivered)==message.text


@pytest.mark.django_db
def test_unknown_part_stops_automatic_retry(desk):
    message = send(desk["admin"], desk["ticket"].pk, desk["ticket"].version,
                   "А" * 6000, uuid.uuid4())
    adapter = TelegramAdapter(token="test-only")
    with patch("apps.integrations.tasks.TelegramAdapter", return_value=adapter), patch.object(adapter, "call") as call:
        call.side_effect = [{"message_id": 1}, TelegramError("unknown", unknown=True)]
        deliver(message.job.pk)
        deliver(message.job.pk)
        assert call.call_count == 2
        message.job.refresh_from_db()
        assert message.job.state == "unknown"
