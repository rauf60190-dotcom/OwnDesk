import secrets
from django.conf import settings
from datetime import timedelta
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from apps.accounts.models import User
from apps.accounts.registration import digest, public_origin, start_session
from apps.core.access import member, require_admin
from apps.teams.models import Team, TeamMember
from apps.audit.models import AuditEvent
from .models import Invitation, Membership, Workspace


class InviteInput(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    role = serializers.ChoiceField(choices=Membership.ROLES)
    team_id = serializers.UUIDField()
    send_email = serializers.BooleanField(default=False)


class TokenInput(serializers.Serializer):
    token = serializers.CharField(max_length=200)
    name = serializers.CharField(max_length=150, required=False)
    password = serializers.CharField(max_length=256, required=False, write_only=True)


def valid_invitation(token):
    obj = Invitation.objects.select_for_update().select_related("workspace", "team").filter(token_hash=digest(token)).first()
    if not obj or obj.revoked_at or obj.accepted_at or obj.expires_at <= timezone.now():
        raise ValidationError("Приглашение недействительно или истекло. Запросите новую ссылку.")
    return obj


def describe(obj):
    return {"id": obj.pk, "email": obj.email, "role": obj.role, "team_id": obj.team_id,
            "expires_at": obj.expires_at, "accepted_at": obj.accepted_at, "revoked_at": obj.revoked_at, "mail_state": obj.mail_state}


class InvitationsView(APIView):
    @extend_schema(responses={200: {"type": "object"}})
    def get(self, request):
        m = member(request); require_admin(m)
        return Response({
            "organization_url": public_origin(),
            "email_available": bool(settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL),
            "results": [describe(x) for x in Invitation.objects.filter(workspace=m.workspace).order_by("-created_at")[:100]],
        })

    @extend_schema(request=InviteInput, responses={201: {"type": "object"}})
    @transaction.atomic
    def post(self, request):
        m = member(request); require_admin(m)
        s = InviteInput(data=request.data); s.is_valid(raise_exception=True); data = s.validated_data
        Workspace.objects.select_for_update().get(pk=m.workspace_id)
        m = member(request)
        require_admin(m)
        if data["role"] == "admin" and not m.is_owner:
            raise PermissionDenied("Приглашать администраторов может только владелец.")
        if data["send_email"] and not (settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL):
            raise ValidationError("Для отправки приглашений настройте SMTP на сервере организации.")
        origin = public_origin()
        if not origin:
            raise ValidationError("Сначала настройте адрес сервера организации.")
        team = Team.objects.filter(pk=data["team_id"], workspace=m.workspace).first()
        if not team:
            raise ValidationError("Команда недоступна.")
        email = data["email"].lower()
        if Membership.objects.filter(workspace=m.workspace, user__email__iexact=email).exists():
            raise ValidationError("Сотрудник уже добавлен. Управляйте его доступом в списке сотрудников.")
        # Issuing a replacement revokes all earlier unused links for this address.
        Invitation.objects.filter(workspace=m.workspace, email=email, accepted_at=None, revoked_at=None).update(revoked_at=timezone.now(), mail_body="", mail_state="cancelled")
        token = secrets.token_urlsafe(32)
        obj = Invitation.objects.create(workspace=m.workspace, email=email, role=data["role"], team=team,
            created_by=m.user, token_hash=digest(token), expires_at=timezone.now()+timedelta(hours=72))
        link = f"{origin}/#invite={token}"
        if data["send_email"]:
            obj.mail_body = f"Вас приглашают в организацию {m.workspace.name}.\nОткройте ссылку и создайте пароль: {link}\nСсылка действует 72 часа. Если вы не ожидали приглашение, проигнорируйте письмо."
            obj.mail_state = "queued"
            obj.save(update_fields=["mail_body", "mail_state"])
        AuditEvent.objects.create(workspace=m.workspace, actor=m.user, action="invitation.created", changes={"id":str(obj.pk), "role":obj.role})
        return Response({**describe(obj), "link": link}, status=201)


class RevokeInvitation(APIView):
    @extend_schema(request=None, responses={200: {"type": "object"}})
    @transaction.atomic
    def post(self, request, pk):
        m = member(request); require_admin(m)
        Workspace.objects.select_for_update().get(pk=m.workspace_id)
        m = member(request); require_admin(m)
        obj = Invitation.objects.select_for_update().filter(pk=pk, workspace=m.workspace).first()
        if not obj: raise NotFound()
        if obj.role == "admin" and not m.is_owner: raise PermissionDenied("Действие доступно владельцу.")
        if obj.accepted_at: raise ValidationError("Приглашение уже принято. Отключите сотрудника в списке.")
        obj.revoked_at = timezone.now(); obj.mail_body = ""; obj.mail_state = "cancelled"; obj.save(update_fields=["revoked_at", "mail_body", "mail_state"])
        AuditEvent.objects.create(workspace=m.workspace, actor=m.user, action="invitation.revoked", changes={"id":str(obj.pk)})
        return Response({"ok":True})


@method_decorator(csrf_protect, name="dispatch")
class InspectInvitation(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=TokenInput, responses={200: {"type": "object"}})
    @transaction.atomic
    def post(self, request):
        s=TokenInput(data=request.data);s.is_valid(raise_exception=True)
        obj=valid_invitation(s.validated_data["token"])
        return Response({"organization":obj.workspace.name, "email":obj.email, "role":obj.role,
            "already_authenticated":request.user.is_authenticated and request.user.email.lower()==obj.email,
            "existing_account":User.objects.filter(email__iexact=obj.email).exists()})


@method_decorator(csrf_protect, name="dispatch")
class AcceptInvitation(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=TokenInput, responses={200: {"type": "object"}})
    def post(self, request):
        s=TokenInput(data=request.data);s.is_valid(raise_exception=True);data=s.validated_data
        with transaction.atomic():
            # Use the same workspace lock order as creation before locking the invitation.
            hint=Invitation.objects.filter(token_hash=digest(data["token"])).first()
            if not hint: raise ValidationError("Приглашение недействительно.")
            Workspace.objects.select_for_update().get(pk=hint.workspace_id)
            obj=valid_invitation(data["token"])
            existing=list(User.objects.filter(email__iexact=obj.email)[:2])
            if existing:
                if len(existing)!=1 or not request.user.is_authenticated or request.user.pk!=existing[0].pk:
                    raise PermissionDenied("Сначала войдите в существующий аккаунт с указанной почтой.")
                user=existing[0]
                if not user.is_active: raise PermissionDenied("Аккаунт отключён.")
            else:
                password=data.get("password", "");name=data.get("name", "").strip()
                if not name:raise ValidationError("Укажите имя.")
                validate_password(password)
                try:
                    with transaction.atomic():
                        user=User.objects.create_user(username="user_"+secrets.token_hex(16),email=obj.email,
                            display_name=name,password=password,email_verified=False)
                except IntegrityError:
                    raise ValidationError("Аккаунт уже создан. Обновите страницу и войдите.") from None
            if Membership.objects.filter(workspace=obj.workspace,user=user).exists():
                raise ValidationError("Вы уже добавлены в организацию. Обратитесь к администратору.")
            membership=Membership.objects.create(workspace=obj.workspace,user=user,role=obj.role)
            TeamMember.objects.create(team=obj.team,membership=membership)
            obj.accepted_at=timezone.now();obj.mail_body="";obj.save(update_fields=["accepted_at","mail_body"])
            AuditEvent.objects.create(workspace=obj.workspace,actor=user,action="invitation.accepted",changes={"id":str(obj.pk),"membership":str(membership.pk)})
        start_session(request,user,membership)
        return Response({"ok":True})
