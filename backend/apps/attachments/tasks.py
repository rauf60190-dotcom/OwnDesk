from celery import shared_task
from django.conf import settings
from django.db import transaction
from .models import Attachment
from .storage import storage, detect_type
from apps.integrations.telegram.adapter import TelegramAdapter, TelegramError
from .retention import enforce_retention  # Registered by Celery autodiscovery.


@shared_task
def fetch_attachment(pk):
    with transaction.atomic():
        a = (
            Attachment.objects.select_for_update(of=("self",))
            .select_related("message__conversation__connection")
            .get(pk=pk)
        )
        if a.state != "pending":
            return
        try:
            blob = TelegramAdapter(
                connection=a.message.conversation.connection
            ).download(a.telegram_file_id)
            a.mime_type = detect_type(blob)
            a.object_key = f"{a.workspace_id}/{a.id}"
            storage().put_object(
                Bucket=settings.S3_BUCKET,
                Key=a.object_key,
                Body=blob,
                ContentType=a.mime_type,
            )
            a.state, a.size = "ready", len(blob)
        except TelegramError as e:
            a.state, a.error = "failed", str(e)
        except Exception:
            a.state, a.error = "failed", "Не удалось сохранить вложение."
        a.save()
