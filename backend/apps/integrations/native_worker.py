"""Single process worker backed by PostgreSQL's persisted inbox and outbox."""
import logging
import time

from django.db import close_old_connections

from .local_worker import poll
from .models import ChannelConnection
from .tasks import sweep_once
from .telegram.adapter import TelegramError


def cycle(offsets, retry_at):
    for bot in ChannelConnection.objects.filter(transport="polling", state="connected"):
        if retry_at.get(bot.pk, 0) > time.monotonic():
            continue
        try:
            offset = poll(bot, offsets.get(bot.pk))
            if offset is not None:
                offsets[bot.pk] = offset
            if bot.last_error:
                bot.last_error = ""
                bot.save(update_fields=["last_error"])
        except TelegramError as error:
            bot.last_error = str(error)
            bot.save(update_fields=["last_error"])
            retry_at[bot.pk] = time.monotonic() + (error.retry_after or 15)
    sweep_once(dispatch=lambda task, pk: task(pk))


def run(stop):
    offsets, retry_at = {}, {}
    last_retention = 0
    while not stop.is_set():
        try:
            close_old_connections()
            cycle(offsets, retry_at)
            if time.monotonic() - last_retention >= 3600:
                from apps.attachments.retention import enforce_retention
                enforce_retention()
                last_retention = time.monotonic()
        except Exception:
            logging.getLogger(__name__).exception("Native worker will retry persisted jobs")
        finally:
            close_old_connections()
        stop.wait(1)
