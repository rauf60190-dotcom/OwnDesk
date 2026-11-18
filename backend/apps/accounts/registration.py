"""Public registration: verified identities create isolated workspaces."""
import hashlib
import secrets
from datetime import timedelta
from ipaddress import ip_address
from urllib.parse import urlparse
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.password_validation import validate_password
from django.core.validators import validate_email
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, Throttled, PermissionDenied
from apps.core.contracts import documented
from apps.workspaces.models import Workspace, Membership
from apps.teams.models import Team, TeamMember
from apps.sla.models import SLAPolicy
from apps.sla.services import DEFAULTS
from .models import User, Registration, LoginAttempt


def public_origin():
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme == "https" and parsed.netloc and not parsed.query and not parsed.fragment and parsed.path in ("", "/"):
        return base
    if settings.DEBUG and parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return base
    return ""


def email_enabled():
    return bool(settings.DEPLOYMENT_MODE != "organization" and settings.PUBLIC_REGISTRATION_ENABLED and public_origin() and (
        local_preview_enabled() or (settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL)))


def local_preview_enabled(request=None):
    return bool(settings.DEBUG and settings.LOCAL_EMAIL_PREVIEW and (
        request is None or request.META.get("REMOTE_ADDR") in ("127.0.0.1", "::1")))


def google_enabled():
    return bool(settings.DEPLOYMENT_MODE != "organization" and public_origin() and settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def request_source(request):
    peer = request.META.get("REMOTE_ADDR") or "unknown"
    if settings.TRUST_LOOPBACK_CLIENT_IP and peer in ("127.0.0.1", "::1"):
        forwarded = request.META.get("HTTP_X_OWNDESK_CLIENT_IP", "")
        if "%" not in forwarded:
            try:
                return str(ip_address(forwarded))
            except ValueError:
                pass
    return peer


def limit_registration(request, email):
    now = timezone.now()
    # Persist counters across API processes.
    for scope, value, maximum, minutes in [
        ("signup-ip", request_source(request), 30, 60),
        ("signup-email", email, 1, 1),
    ]:
        attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(
            key=digest(scope + ":" + value), defaults={"window": now})
        if attempt.window <= now - timedelta(minutes=minutes):
            attempt.failures, attempt.window = 0, now
        if attempt.failures >= maximum:
            raise Throttled(wait=minutes * 60, detail="Слишком много запросов. Попробуйте позже.")
        attempt.failures += 1
        attempt.save()


def create_account(email, name, password=None, google_sub=None, local_preview=False):
    # Caller owns the transaction. Never attach a public signup to an existing tenant.
    try:
        with transaction.atomic():
            user = User.objects.create_user(username="user_" + secrets.token_hex(16), email=email,
                display_name=name, password=password, google_sub=google_sub,
                email_verified=not local_preview, is_local_account=local_preview)
    except IntegrityError:
        raise ValidationError("Аккаунт уже создан. Используйте вход.") from None
    if settings.DEPLOYMENT_MODE == "portal":
        return user, None
    workspace = Workspace.objects.create(name=(name + " — OwnDesk")[:200])
    membership = Membership.objects.create(user=user, workspace=workspace, role="admin", is_owner=True)
    team = Team.objects.create(workspace=workspace, name="Поддержка")
    TeamMember.objects.create(team=team, membership=membership)
    for priority, (first, next_, resolution) in DEFAULTS.items():
        SLAPolicy.objects.create(workspace=workspace, priority=priority,
            first_response_minutes=first, next_response_minutes=next_, resolution_minutes=resolution)
    return user, membership


def start_session(request, user, membership):
    login(request, user, backend="apps.accounts.backends.PublicAccountBackend")
    request.session.pop("workspace_id", None)
    if membership:
        request.session["workspace_id"] = str(membership.workspace_id)
    import time
    request.session["signed_in_at"] = time.time()
    request.session["user_agent"] = request.META.get("HTTP_USER_AGENT", "")[:300]
    request.session.save()
    from .models import SecurityEvent
    SecurityEvent.objects.create(user=user,kind="login")


@documented
class RegistrationOptions(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from apps.workspaces.models import Workspace
        return Response({"email_registration": email_enabled(), "google_login": google_enabled(),
                         "deployment_mode": settings.DEPLOYMENT_MODE,
                         "setup_required": settings.DEPLOYMENT_MODE == "organization" and not Workspace.objects.exists(),
                         "public_registration": settings.DEPLOYMENT_MODE != "organization" and settings.PUBLIC_REGISTRATION_ENABLED,
                         "local_email_preview": local_preview_enabled(request)})


@method_decorator(csrf_protect, name="dispatch")
@documented
class Register(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        if not email_enabled():
            raise PermissionDenied("Регистрация по почте ещё не настроена.")
        preview = local_preview_enabled(request)
        if local_preview_enabled() and not preview:
            raise PermissionDenied("Локальная регистрация доступна только на этом компьютере.")
        email = str(request.data.get("email", "")).strip().lower()
        name = str(request.data.get("name", "")).strip()
        validate_email(email)
        if len(email) > 254 or not name or len(name) > 150:
            raise ValidationError("Укажите имя до 150 символов и корректный email.")
        preview_link = None
        with transaction.atomic():
            limit_registration(request, email)
            if not User.objects.filter(email__iexact=email).exists():
                token = secrets.token_urlsafe(32)
                registration, _ = Registration.objects.update_or_create(email=email, defaults={
                    "display_name": name, "token_hash": digest(token), "expires_at": timezone.now() + timedelta(minutes=30),
                    "used_at": None, "mail_state": "queued", "mail_started_at": None, "mail_body": "",
                    "local_preview": preview,
                })
                link = f"{public_origin()}/#verify={registration.pk}.{token}"
                registration.mail_body = (
                    "Вы запросили регистрацию в OwnDesk.\n\n"
                    f"Подтвердите адрес и задайте пароль по ссылке:\n{link}\n\n"
                    "Ссылка действует 30 минут. Если вы не запрашивали регистрацию, проигнорируйте письмо."
                )
                if preview:
                    preview_link = link
                    registration.mail_body, registration.mail_state = "", "preview"
                registration.save(update_fields=["mail_body", "mail_state"])
        result = {"message": "Если адрес доступен для регистрации, мы отправим ссылку. Проверьте входящие и папку «Спам». Если аккаунт уже есть, используйте вход."}
        if preview:
            result = {"message": "Локальный тест: настоящее письмо не отправляется. Если аккаунт уже существует, используйте вход.", "preview_link": preview_link}
        return Response(result, status=202)


@method_decorator([csrf_protect, sensitive_post_parameters("password", "token")], name="dispatch")
@documented
class VerifyRegistration(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        if settings.DEPLOYMENT_MODE == "organization" or not settings.PUBLIC_REGISTRATION_ENABLED:
            raise PermissionDenied("Регистрация отключена.")
        token = str(request.data.get("token", ""))
        try:
            from uuid import UUID
            pk, secret = token.split(".", 1)
            pk = UUID(pk)
        except (ValueError, AttributeError):
            raise ValidationError("Ссылка недействительна. Запросите новое письмо.")
        password = str(request.data.get("password", ""))
        with transaction.atomic():
            reg = Registration.objects.select_for_update().filter(pk=pk).first()
            if not reg or reg.used_at or reg.expires_at <= timezone.now() or not secrets.compare_digest(reg.token_hash, digest(secret)):
                raise ValidationError("Ссылка недействительна или истекла. Запросите новое письмо.")
            if reg.local_preview and not local_preview_enabled(request):
                raise PermissionDenied("Тестовая ссылка работает только в локальном режиме.")
            validate_password(password, User(email=reg.email, display_name=reg.display_name))
            if User.objects.filter(email__iexact=reg.email).exists():
                raise ValidationError("Аккаунт уже существует. Используйте вход.")
            user, membership = create_account(reg.email, reg.display_name, password=password, local_preview=reg.local_preview)
            reg.used_at, reg.mail_body = timezone.now(), ""
            reg.save(update_fields=["used_at", "mail_body"])
        start_session(request, user, membership)
        return Response({"ok": True})
