from apps.core.contracts import documented
import hashlib
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import AuthenticationFailed, Throttled, ValidationError
from apps.core.access import member
from apps.workspaces.models import Membership
from apps.teams.models import Team
from .models import LoginAttempt


@method_decorator([ensure_csrf_cookie, csrf_protect], name="dispatch")
@documented
class AuthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        username = str(request.data.get("username", "")).strip()[:254]
        # Hash keys so raw credentials and addresses are not written to technical logs.
        key = hashlib.sha256(username.casefold().encode()).hexdigest()
        now = timezone.now()
        with transaction.atomic():
            attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(
                key=key, defaults={"window": now}
            )
            if attempt.window < now - timedelta(minutes=15):
                attempt.failures, attempt.window = 0, now
            if attempt.failures >= 10:
                raise Throttled(
                    wait=900, detail="Слишком много попыток входа. Повторите позже."
                )
            user = authenticate(
                request, username=self.login_username(username), password=request.data.get("password", "")
            )
            from .registration import local_preview_enabled
            if user and user.is_local_account and not local_preview_enabled(request):
                user = None
            membership = (
                Membership.objects.filter(user=user, active=True).first()
                if user
                else None
            )
            permitted = bool(user and (membership or settings.DEPLOYMENT_MODE == "portal"))
            if not permitted:
                attempt.failures += 1
                attempt.save()
            else:
                attempt.failures = 0
                attempt.save()
        if not permitted:
            raise AuthenticationFailed("Неверный логин или пароль.")
        from .security import begin_login
        if not begin_login(request,user,membership if settings.DEPLOYMENT_MODE!="portal" else None):
            return Response({"mfa_required":True})
        return Response({"ok": True})

    @staticmethod
    def login_username(value):
        from .models import User
        from django.db.models import Q
        from .registration import local_preview_enabled
        # Public accounts sign in by verified email. Legacy staff logins still work.
        matches = list(User.objects.filter(email__iexact=value)[:2]) if "@" in value else []
        user = matches[0] if len(matches) == 1 else None
        return user.username if user else value


@method_decorator(ensure_csrf_cookie, name="dispatch")
@documented
class CSRFView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"csrf": "ready"})


@documented
class MeView(APIView):
    def get(self, request):
        if settings.DEPLOYMENT_MODE == "portal":
            return Response({"id":request.user.pk,"name":request.user.display_name,
                "email":request.user.email,"deployment_mode":"portal","preferences":request.user.preferences})
        m = member(request)
        return Response(
            {
                "id": request.user.id,
                "deployment_mode": settings.DEPLOYMENT_MODE,
                "name": request.user.display_name or request.user.username,
                "username": request.user.username,
                "membership_id": m.id,
                "role": m.role,
                "is_owner": m.is_owner,
                "workspace": {
                    "id": m.workspace_id,
                    "name": m.workspace.name,
                    "timezone": m.workspace.timezone,
                },
                "preferences": request.user.preferences,
                "development": settings.DEBUG,
                "local_telegram": settings.LOCAL_TELEGRAM_POLLING,
            }
        )

    def patch(self, request):
        member(request)
        allowed = {
            k: v
            for k, v in request.data.items()
            if k in ["tickets_expanded", "theme", "columns"]
        }
        if "theme" in allowed and allowed["theme"] not in ["light", "dark"]:
            raise ValidationError("Неизвестная тема.")
        if "tickets_expanded" in allowed and not isinstance(
            allowed["tickets_expanded"], bool
        ):
            raise ValidationError("Ожидается логическое значение для состояния меню.")
        if "columns" in allowed and (
            not isinstance(allowed["columns"], list)
            or any(
                c not in ["team", "created_at", "updated_at"]
                for c in allowed["columns"]
            )
        ):
            raise ValidationError("Некорректный список колонок.")
        request.user.preferences = {**request.user.preferences, **allowed}
        request.user.save(update_fields=["preferences"])
        return self.get(request)


@documented
class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response({"ok": True})
