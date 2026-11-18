"""One local receiver and sender for bots explicitly connected in polling mode."""
import logging
import time
from datetime import timedelta
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from apps.integrations.models import ChannelConnection, InboundEvent, OutboundJob, WorkerHeartbeat
from apps.integrations.tasks import deliver, handle_event
from apps.integrations.telegram.adapter import TelegramAdapter, TelegramError
from apps.attachments.models import Attachment
from apps.attachments.tasks import fetch_attachment


def poll(bot, offset=None):
    request = {"timeout": 0, "limit": 100, "allowed_updates": ["message", "edited_message", "callback_query", "my_chat_member"]}
    if offset is not None:
        request["offset"] = offset
    updates = TelegramAdapter(connection=bot).call("getUpdates", request)
    # Acknowledge a batch only after its entire payload is durable.
    with transaction.atomic():
        for update in updates:
            InboundEvent.objects.get_or_create(connection=bot, update_id=str(update["update_id"]), defaults={"payload": update})
    return updates[-1]["update_id"] + 1 if updates else None


def run(stop):
    from django.conf import settings
    if settings.DEPLOYMENT_MODE == "portal":
        return
    offsets, retry_at = {}, {}
    while not stop.is_set():
        try:
            close_old_connections()
            now = timezone.now()
            WorkerHeartbeat.objects.update_or_create(name="worker", defaults={"seen_at": now})
            bots = ChannelConnection.objects.filter(transport="polling", state="connected")
            jobs = OutboundJob.objects.filter(message__conversation__connection__transport="polling")
            jobs.filter(state="sending", started_at__lt=now-timedelta(minutes=2)).update(state="unknown", error="Отправка была прервана. Проверьте исходный чат.")
            for pk in jobs.filter(state="queued").filter(Q(next_attempt_at=None) | Q(next_attempt_at__lte=now)).order_by("created_at").values_list("pk", flat=True)[:100]:
                deliver(pk)
            for bot in bots:
                if retry_at.get(bot.pk, 0) > time.monotonic():
                    continue
                try:
                    offsets[bot.pk] = poll(bot, offsets.get(bot.pk))
                    if bot.last_error:
                        bot.last_error = ""
                        bot.save(update_fields=["last_error"])
                except TelegramError as error:
                    bot.last_error = str(error)
                    bot.save(update_fields=["last_error"])
                    retry_at[bot.pk] = time.monotonic() + (error.retry_after or 15)
            pending = InboundEvent.objects.filter(connection__in=bots, state="pending", attempts__lt=10).order_by("created_at")
            for pk in pending.values_list("pk", flat=True)[:100]:
                handle_event(pk)
            downloads = Attachment.objects.filter(state="pending", telegram_file_id__gt="",
                message__conversation__connection__in=bots).order_by("created_at")
            for pk in list(downloads.values_list("pk", flat=True)[:5]):
                fetch_attachment(pk)
        except Exception as error:
            logging.getLogger(__name__).warning("Local worker will retry (%s)", type(error).__name__)
        finally:
            close_old_connections()
        stop.wait(1)
