import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import PermissionDenied, ValidationError, Throttled
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from datetime import timedelta
from apps.accounts.models import LoginAttempt
from apps.accounts.registration import digest, create_account, start_session
from .models import InstallationSetup, Workspace


class SetupInput(serializers.Serializer):
    code = serializers.CharField(max_length=200, write_only=True)
    organization = serializers.CharField(max_length=200)
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=256, write_only=True)
    timezone = serializers.CharField(max_length=100, default="Asia/Tashkent")


@method_decorator([csrf_protect, sensitive_post_parameters("code", "password")], name="dispatch")
class CompleteSetup(APIView):
    permission_classes=[AllowAny]
    authentication_classes=[]

    @extend_schema(request=SetupInput, responses={200:{"type":"object"}})
    def post(self, request):
        if settings.DEPLOYMENT_MODE != "organization" or Workspace.objects.exists():
            raise PermissionDenied("Первоначальная настройка недоступна.")
        # Commit unsuccessful-attempt counters independently of the setup transaction.
        with transaction.atomic():
            attempt,_=LoginAttempt.objects.select_for_update().get_or_create(
                key=digest("installation-setup"),defaults={"window":timezone.now()})
            if attempt.window < timezone.now()-timedelta(minutes=15):
                attempt.window,attempt.failures=timezone.now(),0
            if attempt.failures>=10: raise Throttled(wait=900)
            attempt.failures+=1;attempt.save()
        s=SetupInput(data=request.data);s.is_valid(raise_exception=True);d=s.validated_data
        validate_password(d["password"])
        try: ZoneInfo(d["timezone"])
        except (ZoneInfoNotFoundError, ValueError): raise ValidationError("Неизвестный часовой пояс.")
        with transaction.atomic():
            grant=InstallationSetup.objects.select_for_update().filter(pk=1).first()
            if (not grant or grant.completed_at or grant.expires_at<=timezone.now()
                or not secrets.compare_digest(grant.token_hash,digest(d["code"]))):
                raise ValidationError("Неверный или просроченный код установки.")
            if Workspace.objects.exists(): raise PermissionDenied("Установка уже настроена.")
            user,membership=create_account(d["email"].lower(),d["name"],password=d["password"])
            # A setup code proves local control, not ownership of the email address.
            user.email_verified=False;user.save(update_fields=["email_verified"])
            workspace=membership.workspace;workspace.name=d["organization"];workspace.timezone=d["timezone"];workspace.save(update_fields=["name","timezone"])
            grant.completed_at=timezone.now();grant.token_hash="";grant.save(update_fields=["completed_at","token_hash"])
        start_session(request,user,membership)
        return Response({"ok":True})
