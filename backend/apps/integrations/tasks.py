from datetime import timedelta
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.db.models import Q, F
from apps.integrations.models import InboundEvent, OutboundJob, WorkerHeartbeat
from apps.integrations.telegram.adapter import TelegramAdapter, TelegramError
from apps.integrations.telegram.inbound import process_event
from apps.sla import services as sla
from apps.tickets.models import Ticket
from apps.notifications.events import publish, notify


def deliver(job_id):
    with transaction.atomic():
        job = (
            OutboundJob.objects.select_for_update(of=("self",))
            .select_related("message__conversation__connection")
            .get(pk=job_id)
        )
        if job.state != "queued" or (
            job.next_attempt_at and job.next_attempt_at > timezone.now()
        ):
            return
        if job.message.conversation.connection.state == "disabled":
            return
        job.state, job.started_at = "sending", timezone.now()
        if job.message.ticket_id:
            job.covered_version = (
                Ticket.objects.select_for_update().get(pk=job.message.ticket_id).version
            )
        job.attempts += 1
        job.save()
        attempt = job.attempts
        message = job.message
        if message.ticket_id:
            publish(message.ticket, "delivery.changed")
    try:
        result = TelegramAdapter(connection=message.conversation.connection).send(
            message
        )
    except TelegramError as error:
        with transaction.atomic():
            job = OutboundJob.objects.select_for_update().get(pk=job_id)
            if job.state != "sending" or job.attempts != attempt:
                return
            job.error = str(error)
            if error.retry_after and job.attempts < 8:
                job.state = "queued"
                job.next_attempt_at = timezone.now() + timedelta(
                    seconds=max(1, error.retry_after)
                )
            else:
                job.state = "unknown" if error.unknown else "failed"
            job.save()
            if message.ticket_id:
                notify(message.ticket, "delivery_error", job.error)
                publish(message.ticket, "delivery.changed")
        return
    except Exception:
        # A crash after attempting the request is ambiguous; never auto-resend.
        OutboundJob.objects.filter(pk=job_id, state="sending", attempts=attempt).update(
            state="unknown", error="Не удалось подтвердить результат отправки."
        )
        return
    with transaction.atomic():
        job = OutboundJob.objects.select_for_update().get(pk=job_id)
        if job.state not in ["sending", "unknown"] or job.attempts != attempt:
            return
        message.external_id = str(result["message_id"])
        message.sent_at = timezone.now()
        message.save(update_fields=["external_id", "sent_at"])
        job.state, job.error = "sent", ""
        job.save(update_fields=["state", "error"])
        if message.ticket_id:
            ticket = Ticket.objects.select_for_update().get(id=message.ticket_id)
            if message.direction == "outbound" and message.visibility == "public":
                ticket.last_agent_message_at = message.sent_at
                sla.confirmed_response(ticket, job.covered_version, message.sent_at)
            ticket.version += 1
            ticket.save()
            publish(ticket, "delivery.changed")


@shared_task
def handle_event(event_id):
    try:
        process_event(event_id)
    except Exception:
        InboundEvent.objects.filter(pk=event_id).update(
            attempts=F("attempts") + 1,
            error="Ошибка обработки; событие сохранено для повторной попытки.",
        )


@shared_task
def handle_delivery(job_id):
    deliver(job_id)


@shared_task
def sweep():
    return sweep_once()


def sweep_once(dispatch=None):
    if dispatch is None:
        dispatch = lambda task, pk: task.delay(str(pk))
    from django.conf import settings
    if settings.DEPLOYMENT_MODE == "portal":
        return
    WorkerHeartbeat.objects.update_or_create(
        name="worker", defaults={"seen_at": timezone.now()}
    )
    OutboundJob.objects.filter(
        state="sending", started_at__lt=timezone.now() - timedelta(minutes=2)
    ).update(
        state="unknown",
        error="Работник прерван во время отправки. Проверьте Telegram перед повтором.",
    )
    for event_id in InboundEvent.objects.filter(
        state="pending", attempts__lt=10
    ).values_list("id", flat=True)[:100]:
        dispatch(handle_event, str(event_id))
    for job_id in (
        OutboundJob.objects.filter(state="queued")
        .filter(Q(next_attempt_at=None) | Q(next_attempt_at__lte=timezone.now()))
        .values_list("id", flat=True)[:100]
    ):
        dispatch(handle_delivery, str(job_id))
    from apps.attachments.models import Attachment
    from apps.attachments.tasks import fetch_attachment

    for attachment_id in Attachment.objects.filter(
        state="pending", telegram_file_id__gt=""
    ).values_list("id", flat=True)[:30]:
        dispatch(fetch_attachment, str(attachment_id))
    from apps.sla.models import SLAInstance

    now = timezone.now()
    upcoming = SLAInstance.objects.filter(
        completed_at=None,
        cancelled_at=None,
        paused_at=None,
        warning_sent=False,
        deadline__gt=now,
        deadline__lte=now + timedelta(minutes=5),
    ).select_related("ticket__assignee")
    for instance in upcoming:
        threshold = min(300, max(15, instance.budget_minutes * 6))
        if (
            instance.ticket.assignee_id
            and (instance.deadline - now).total_seconds() <= threshold
        ):
            if SLAInstance.objects.filter(id=instance.id, warning_sent=False).update(
                warning_sent=True
            ):
                notify(
                    instance.ticket,
                    "sla_warning",
                    f"Приближается срок обращения №{instance.ticket.number}",
                )
                publish(instance.ticket, "notification.created")
    for s in SLAInstance.objects.filter(
        completed_at=None,
        cancelled_at=None,
        paused_at=None,
        breached=False,
        deadline__lt=timezone.now(),
    ).select_related("ticket__assignee"):
        if SLAInstance.objects.filter(id=s.id, breached=False).update(breached=True):
            notify(
                s.ticket, "sla_breached", f"Нарушен срок обращения №{s.ticket.number}"
            )
            publish(s.ticket, "deadline.changed")
