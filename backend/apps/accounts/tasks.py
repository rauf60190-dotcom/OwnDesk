from datetime import timedelta
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from .models import Registration, PasswordRecovery


def run_local_mail(stop):
    from django.db import close_old_connections
    import logging
    while not stop.is_set():
        try:
            close_old_connections()
            send_registration_mail()
        except Exception:
            logging.getLogger(__name__).warning("Registration mail worker will retry")
        finally:
            close_old_connections()
        stop.wait(5)


@shared_task
def send_registration_mail():
    if not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL:
        return
    send_password_recovery_mail()
    send_invitation_mail()
    now = timezone.now()
    Registration.objects.filter(mail_state="sending", mail_started_at__lt=now-timedelta(minutes=2)).update(mail_state="unknown", mail_body="")
    Registration.objects.filter(expires_at__lte=now).update(mail_body="")
    for pk in list(Registration.objects.filter(mail_state="queued", used_at=None, expires_at__gt=now).values_list("pk", flat=True)[:20]):
        with transaction.atomic():
            reg = Registration.objects.select_for_update().get(pk=pk)
            if reg.mail_state != "queued" or not reg.mail_body or reg.used_at:
                continue
            reg.mail_state, reg.mail_started_at = "sending", timezone.now()
            reg.save(update_fields=["mail_state", "mail_started_at"])
            version = reg.token_hash
        try:
            sent = send_mail("Подтвердите регистрацию в OwnDesk", reg.mail_body,
                settings.DEFAULT_FROM_EMAIL, [reg.email], fail_silently=False)
            state = "sent" if sent else "failed"
        except Exception:
            # SMTP can accept mail before losing the connection. No blind resend.
            state = "unknown"
        Registration.objects.filter(pk=pk, token_hash=version, mail_state="sending").update(mail_state=state, mail_body="")


def send_password_recovery_mail():
    now=timezone.now()
    PasswordRecovery.objects.filter(mail_state="sending",mail_started_at__lt=now-timedelta(minutes=2)).update(mail_state="unknown",mail_body="")
    PasswordRecovery.objects.filter(expires_at__lte=now).update(mail_body="")
    for pk in list(PasswordRecovery.objects.filter(mail_state="queued",used_at=None,expires_at__gt=now).values_list("pk",flat=True)[:20]):
        with transaction.atomic():
            reset=PasswordRecovery.objects.select_for_update().select_related("user").get(pk=pk)
            if reset.mail_state!="queued" or reset.used_at or not reset.mail_body:continue
            reset.mail_state,reset.mail_started_at="sending",timezone.now();reset.save(update_fields=["mail_state","mail_started_at"])
        try:
            sent=send_mail("Восстановление доступа в OwnDesk",reset.mail_body,settings.DEFAULT_FROM_EMAIL,[reset.user.email],fail_silently=False)
            state="sent" if sent else "failed"
        except Exception:state="unknown"
        PasswordRecovery.objects.filter(pk=pk,mail_state="sending").update(mail_state=state,mail_body="")


def send_invitation_mail():
    from apps.workspaces.models import Invitation
    now = timezone.now()
    Invitation.objects.filter(mail_state="sending", mail_started_at__lt=now-timedelta(minutes=2)).update(mail_state="unknown", mail_body="")
    Invitation.objects.filter(expires_at__lte=now).exclude(mail_body="").update(mail_body="", mail_state="expired")
    for pk in list(Invitation.objects.filter(mail_state="queued", accepted_at=None, revoked_at=None, expires_at__gt=now).values_list("pk", flat=True)[:20]):
        with transaction.atomic():
            invitation = Invitation.objects.select_for_update().get(pk=pk)
            if invitation.mail_state != "queued" or not invitation.mail_body or invitation.accepted_at or invitation.revoked_at or invitation.expires_at <= timezone.now():
                continue
            invitation.mail_state, invitation.mail_started_at = "sending", timezone.now()
            invitation.save(update_fields=["mail_state", "mail_started_at"])
        try:
            sent = send_mail("Приглашение в OwnDesk", invitation.mail_body,
                settings.DEFAULT_FROM_EMAIL, [invitation.email], fail_silently=False)
            state = "sent" if sent else "failed"
        except Exception:
            state = "unknown"
        Invitation.objects.filter(pk=pk, mail_state="sending").update(mail_state=state, mail_body="")
