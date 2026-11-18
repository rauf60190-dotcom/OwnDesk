import base64
import hashlib
import secrets
import time
from datetime import timedelta
import pyotp
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied, Throttled
from drf_spectacular.utils import extend_schema
from apps.workspaces.models import Membership
from .models import User, PasswordRecovery, SecondFactor, LoginAttempt, SecurityEvent
from .registration import digest, public_origin, local_preview_enabled, request_source, start_session


class SecurityInput(serializers.Serializer):
    action=serializers.CharField(required=False)
    email=serializers.EmailField(required=False)
    token=serializers.CharField(max_length=200,required=False,write_only=True)
    password=serializers.CharField(max_length=256,required=False,write_only=True)
    code=serializers.CharField(max_length=64,required=False,write_only=True)
    session_id=serializers.CharField(max_length=64,required=False)


def values(request):
    s=SecurityInput(data=request.data);s.is_valid(raise_exception=True);return s.validated_data


def charge(key,limit=10,minutes=15):
    # Its transaction commits before validation; failed attempts cannot roll back counters.
    with transaction.atomic():
        row,_=LoginAttempt.objects.select_for_update().get_or_create(key=digest(key),defaults={"window":timezone.now()})
        if row.window<=timezone.now()-timedelta(minutes=minutes):row.window,row.failures=timezone.now(),0
        if row.failures>=limit:raise Throttled(wait=minutes*60,detail="Слишком много попыток. Попробуйте позже.")
        row.failures+=1;row.save()


def secret_cipher():
    key=settings.ACCOUNT_ENCRYPTION_KEY or base64.urlsafe_b64encode(hashlib.sha256(("account-mfa:"+settings.SECRET_KEY).encode()).digest()).decode()
    return Fernet(key.encode())


def session_rows(user):
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        data=session.get_decoded()
        if data.get("_auth_user_id")==str(user.pk):yield session,data


def revoke_sessions(user,keep=None):
    for session,_ in session_rows(user):
        if session.pk!=keep:session.delete()


def session_id(key):
    return salted_hmac("account-session",key).hexdigest()


@transaction.atomic
def begin_login(request,user,membership):
    current=User.objects.select_for_update().get(pk=user.pk)
    if not current.is_active or current.password!=user.password:raise PermissionDenied("Повторите вход.")
    if SecondFactor.objects.filter(user=user,enabled=True).exists():
        logout(request)
        request.session["mfa_pending"]={"user":str(user.pk),"membership":str(membership.pk) if membership else None,
            "stamp":user.get_session_auth_hash(),"expires":time.time()+300}
        return False
    start_session(request,user,membership)
    return True


def consume_code(factor,code):
    code=code.strip().replace(" ","")
    hashed=digest(code)
    for saved in factor.recovery_hashes:
        if secrets.compare_digest(hashed,saved):
            factor.recovery_hashes=[x for x in factor.recovery_hashes if x!=saved]
            factor.save(update_fields=["recovery_hashes"]);return True
    if len(code)!=6 or not code.isdigit():return False
    secret=secret_cipher().decrypt(factor.encrypted_secret.encode()).decode()
    totp=pyotp.TOTP(secret);counter=int(time.time())//30
    for candidate in (counter,counter-1,counter+1):
        if candidate>factor.last_counter and secrets.compare_digest(totp.at(candidate*30),code):
            factor.last_counter=candidate;factor.save(update_fields=["last_counter"]);return True
    return False


def reauthenticate(request,data):
    if request.user.has_usable_password():
        if not request.user.check_password(data.get("password","")):raise PermissionDenied("Введите текущий пароль.")
    elif time.time()-request.session.get("signed_in_at",0)>300:
        raise PermissionDenied("Сначала повторно войдите через Google.")


@method_decorator([csrf_protect,sensitive_post_parameters("code")],name="dispatch")
class VerifySecondFactor(APIView):
    permission_classes=[AllowAny]
    authentication_classes=[]

    @extend_schema(request=SecurityInput,responses={200:{"type":"object"}})
    def post(self,request):
        data=values(request);pending=request.session.get("mfa_pending")
        if not pending or pending["expires"]<time.time():raise ValidationError("Время подтверждения истекло. Введите логин и пароль заново.")
        charge("mfa-login:"+pending["user"])
        with transaction.atomic():
            user=User.objects.select_for_update().filter(pk=pending["user"],is_active=True).first()
            if not user or not secrets.compare_digest(user.get_session_auth_hash(),pending["stamp"]):raise PermissionDenied("Повторите вход.")
            if user.is_local_account and not local_preview_enabled(request):raise PermissionDenied("Локальный аккаунт недоступен.")
            m=Membership.objects.filter(pk=pending["membership"],user=user,active=True).first() if pending["membership"] else None
            if settings.DEPLOYMENT_MODE!="portal" and not m:raise PermissionDenied("Нет доступа к организации.")
            factor=SecondFactor.objects.select_for_update().filter(user=user,enabled=True).first()
            if not factor or not consume_code(factor,data.get("code","")):raise ValidationError("Неверный или уже использованный код.")
        request.session.pop("mfa_pending",None)
        start_session(request,user,m)
        return Response({"ok":True})


class AccountSecurity(APIView):
    @extend_schema(responses={200:{"type":"object"}})
    def get(self,request):
        factor=SecondFactor.objects.filter(user=request.user,enabled=True).first()
        return Response({"mfa_enabled":bool(factor),"recovery_codes_left":len(factor.recovery_hashes) if factor else 0,
            "events":list(SecurityEvent.objects.filter(user=request.user).order_by("-created_at","-pk").values("kind","created_at")[:20]),
            "has_password":request.user.has_usable_password(),"sessions":[{"id":session_id(s.pk),"current":s.pk==request.session.session_key,
            "device":d.get("user_agent","Существующая сессия"),"signed_in_at":d.get("signed_in_at"),"expires_at":s.expire_date} for s,d in session_rows(request.user)]})

    @extend_schema(request=SecurityInput,responses={200:{"type":"object"}})
    def post(self,request):
        data=values(request);action=data.get("action")
        if action in ("revoke_session","revoke_others"):
            for session,_ in session_rows(request.user):
                if (action=="revoke_others" and session.pk!=request.session.session_key) or (action=="revoke_session" and secrets.compare_digest(session_id(session.pk),data.get("session_id",""))):
                    session.delete()
            SecurityEvent.objects.create(user=request.user,kind="sessions_revoked")
            return Response({"ok":True})
        charge("mfa-settings:"+str(request.user.pk),limit=15)
        reauthenticate(request,data)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            factor,_=SecondFactor.objects.select_for_update().get_or_create(user=request.user,defaults={"encrypted_secret":""})
            if action=="setup":
                if factor.enabled:raise ValidationError("Двухфакторная защита уже включена.")
                secret=pyotp.random_base32()
                factor.encrypted_secret=secret_cipher().encrypt(secret.encode()).decode();factor.last_counter=-1;factor.save()
                request.session["mfa_setup_until"]=time.time()+600
                return Response({"secret":secret,"account":request.user.email or request.user.username})
            if action=="enable":
                if factor.enabled or request.session.get("mfa_setup_until",0)<time.time() or not factor.encrypted_secret:raise ValidationError("Начните настройку заново.")
                if not consume_code(factor,data.get("code","")):raise ValidationError("Неверный код приложения.")
                codes=[secrets.token_hex(12) for _ in range(8)]
                factor.enabled=True;factor.recovery_hashes=[digest(x) for x in codes];factor.save()
                request.session.pop("mfa_setup_until",None)
                revoke_sessions(request.user,keep=request.session.session_key)
                SecurityEvent.objects.create(user=request.user,kind="mfa_enabled")
                return Response({"ok":True,"recovery_codes":codes})
            if action=="disable":
                if not factor.enabled or not consume_code(factor,data.get("code","")):raise ValidationError("Введите код приложения или резервный код.")
                factor.delete();revoke_sessions(request.user,keep=request.session.session_key)
                SecurityEvent.objects.create(user=request.user,kind="mfa_disabled")
                return Response({"ok":True})
            raise ValidationError("Неизвестное действие.")


@method_decorator(csrf_protect,name="dispatch")
class RequestPasswordRecovery(APIView):
    permission_classes=[AllowAny]
    authentication_classes=[]

    @extend_schema(request=SecurityInput,responses={202:{"type":"object"}})
    def post(self,request):
        data=values(request);email=data.get("email","").lower()
        if not email:raise ValidationError("Укажите email.")
        charge("recovery-ip:"+request_source(request),limit=30)
        charge("recovery-email:"+email,limit=3)
        response={"message":"Если аккаунт доступен для восстановления и почта настроена, придёт письмо со ссылкой. Для входа через Google используйте кнопку Google."}
        user=User.objects.filter(email__iexact=email,is_active=True).first()
        if not user or not user.has_usable_password() or not public_origin():return Response(response,status=202)
        if settings.DEPLOYMENT_MODE!="portal" and not Membership.objects.filter(user=user,active=True).exists():return Response(response,status=202)
        preview=local_preview_enabled(request) and user.is_local_account
        if not preview and not(settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL):return Response(response,status=202)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=user.pk)
            PasswordRecovery.objects.filter(user=user,used_at=None).update(used_at=timezone.now(),mail_body="")
            token=secrets.token_urlsafe(32)
            reset=PasswordRecovery.objects.create(user=user,token_hash=digest(token),password_stamp=digest(user.password),
                expires_at=timezone.now()+timedelta(minutes=30),local_preview=preview)
            link=f"{public_origin()}/#reset={token}"
            if preview:
                reset.mail_state="preview";response.update(message="Тестовое письмо для локального аккаунта. Настоящее письмо не отправлено.",preview_link=link)
            else:reset.mail_body=f"Для смены пароля OwnDesk откройте ссылку:\n{link}\n\nОна действует 30 минут. Если вы не запрашивали смену пароля, проигнорируйте письмо."
            reset.save(update_fields=["mail_state","mail_body"])
        return Response(response,status=202)


@method_decorator([csrf_protect,sensitive_post_parameters("token","password")],name="dispatch")
class ResetPassword(APIView):
    permission_classes=[AllowAny]
    authentication_classes=[]

    @extend_schema(request=SecurityInput,responses={200:{"type":"object"}})
    def post(self,request):
        data=values(request);token=data.get("token","");password=data.get("password","")
        charge("reset-ip:"+request_source(request),limit=30)
        with transaction.atomic():
            hint=PasswordRecovery.objects.filter(token_hash=digest(token)).first()
            if not hint:raise ValidationError("Ссылка недействительна или истекла.")
            user=User.objects.select_for_update().get(pk=hint.user_id)
            reset=PasswordRecovery.objects.select_for_update().filter(token_hash=digest(token)).first()
            if not reset or reset.used_at or reset.expires_at<=timezone.now():raise ValidationError("Ссылка недействительна или истекла.")
            if reset.local_preview and not local_preview_enabled(request):raise PermissionDenied("Тестовая ссылка недоступна.")
            if not user.is_active or not secrets.compare_digest(digest(user.password),reset.password_stamp):raise ValidationError("Ссылка недействительна.")
            validate_password(password,user)
            user.set_password(password)
            if not reset.local_preview:user.email_verified=True
            user.save(update_fields=["password","email_verified"])
            PasswordRecovery.objects.filter(user=user,used_at=None).update(used_at=timezone.now(),mail_body="")
            revoke_sessions(user)
            SecurityEvent.objects.create(user=user,kind="password_reset")
        logout(request)
        return Response({"ok":True,"message":"Пароль изменён. Войдите с новым паролем. Двухфакторная защита сохранена."})
