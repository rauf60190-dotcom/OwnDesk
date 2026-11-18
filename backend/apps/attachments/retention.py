from datetime import timedelta
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from apps.workspaces.models import Workspace
from apps.integrations.models import InboundEvent
from apps.audit.models import AuditEvent
from .models import Attachment
from .storage import storage


@shared_task
def enforce_retention():
    for ws in Workspace.objects.all().iterator():
        events = ws.settings.get("event_retention_days", 0)
        if events:
            # Keep the unique update ID as a tombstone for redelivery deduplication.
            InboundEvent.objects.filter(
                connection__workspace=ws,
                state__in=["done", "ignored"],
                created_at__lt=timezone.now() - timedelta(days=events),
            ).exclude(payload={}).update(payload={})
        days = ws.settings.get("file_retention_days", 0)
        if not days:
            continue
        eligible = Attachment.objects.filter(
            workspace=ws,
            state="ready",
            created_at__lt=timezone.now() - timedelta(days=days),
        ).filter(Q(message__job__isnull=True) | Q(message__job__state="sent"))
        for pk in eligible.values_list("id", flat=True)[:500]:
            with transaction.atomic():
                item = eligible.select_for_update(of=("self",)).filter(pk=pk).first()
                if item is None:
                    continue
                storage().delete_object(Bucket=settings.S3_BUCKET, Key=item.object_key)
                item.state, item.error = "expired", "Срок хранения файла истёк."
                item.save(update_fields=["state", "error"])
                AuditEvent.objects.create(
                    workspace=ws, action="attachment.expired", changes={"id": str(pk)}
                )
