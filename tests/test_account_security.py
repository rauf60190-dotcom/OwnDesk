import re
import pyotp
import pytest
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from apps.accounts.models import SecondFactor, PasswordRecovery
from apps.accounts.tasks import send_registration_mail


@pytest.fixture
def owner(desk,settings):
    settings.DEBUG=True
    settings.PUBLIC_BASE_URL="http://127.0.0.1:5173"
    settings.EMAIL_HOST="smtp.test";settings.DEFAULT_FROM_EMAIL="desk@example.test"
    settings.EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
    user=desk["admin"].user;user.email="security@example.test";user.save()
    return user


@pytest.mark.django_db
def test_mfa_requires_code_denies_replay_and_recovers(desk,owner,client_for):
    c=client_for(desk["admin"]);other=client_for(desk["admin"])
    data={"action":"setup","password":"Testing-password-123"}
    secret=c.post("/api/v1/auth/security",data,format="json").json()["secret"]
    enabled=c.post("/api/v1/auth/security",{"action":"enable","password":data["password"],"code":pyotp.TOTP(secret).now()},format="json")
    assert enabled.status_code==200
    codes=enabled.json()["recovery_codes"];assert len(codes)==8
    factor=SecondFactor.objects.get(user=owner);assert secret not in factor.encrypted_secret and codes[0] not in str(factor.recovery_hashes)
    assert other.get("/api/v1/auth/me").status_code==403
    fresh=APIClient();login=fresh.post("/api/v1/auth/login",{"username":owner.username,"password":data["password"]},format="json")
    assert login.json()["mfa_required"]
    assert fresh.get("/api/v1/auth/me").status_code==403
    assert fresh.post("/api/v1/auth/mfa",{"code":pyotp.TOTP(secret).now()},format="json").status_code==400
    assert fresh.post("/api/v1/auth/mfa",{"code":codes[0]},format="json").status_code==200
    fresh.logout()
    fresh.post("/api/v1/auth/login",{"username":owner.username,"password":data["password"]},format="json")
    assert fresh.post("/api/v1/auth/mfa",{"code":codes[0]},format="json").status_code==400
    assert fresh.post("/api/v1/auth/mfa",{"code":codes[1]},format="json").status_code==200
    assert fresh.post("/api/v1/auth/security",{"action":"disable","password":data["password"],"code":codes[2]},format="json").status_code==200
    assert not SecondFactor.objects.filter(user=owner).exists()


@pytest.mark.django_db
@pytest.mark.parametrize('deployment_mode', ['organization', 'portal'])
def test_session_list_revoke_and_cross_user_protection(desk,owner,client_for,settings,deployment_mode):
    settings.DEPLOYMENT_MODE=deployment_mode
    a=client_for(desk["admin"]);b=client_for(desk["admin"]);stranger=client_for(desk["operator"])
    sessions=a.get("/api/v1/auth/security").json()["sessions"]
    assert len(sessions)==2
    target=next(s for s in sessions if not s["current"])
    assert target["id"]!=b.session.session_key
    stranger.post("/api/v1/auth/security",{"action":"revoke_session","session_id":target["id"]},format="json")
    assert b.get("/api/v1/auth/me").status_code==200
    stranger_sessions=stranger.get("/api/v1/auth/security").json()["sessions"]
    assert len(stranger_sessions)==1 and stranger_sessions[0]["current"]
    assert target["id"] not in [s["id"] for s in stranger_sessions]
    stranger.post("/api/v1/auth/security",{"action":"revoke_others"},format="json")
    assert a.get("/api/v1/auth/me").status_code==200
    assert b.get("/api/v1/auth/me").status_code==200
    a.post("/api/v1/auth/security",{"action":"revoke_session","session_id":target["id"]},format="json")
    assert b.get("/api/v1/auth/me").status_code==403
    assert a.get("/api/v1/auth/me").status_code==200


@pytest.mark.django_db
@pytest.mark.parametrize('deployment_mode', ['organization', 'portal'])
def test_anonymous_cannot_list_or_revoke_sessions(desk,owner,client_for,settings,deployment_mode):
    settings.DEPLOYMENT_MODE=deployment_mode
    authenticated=client_for(desk["admin"])
    target=authenticated.get("/api/v1/auth/security").json()["sessions"][0]["id"]
    anonymous=APIClient()
    assert anonymous.get("/api/v1/auth/security").status_code==403
    assert anonymous.post("/api/v1/auth/security",{"action":"revoke_session","session_id":target},format="json").status_code==403
    assert anonymous.post("/api/v1/auth/security",{"action":"revoke_others"},format="json").status_code==403
    assert authenticated.get("/api/v1/auth/me").status_code==200


@pytest.mark.django_db
def test_recovery_single_use_revokes_sessions_and_keeps_mfa(desk,owner,client_for):
    c=client_for(desk["admin"]);guest=APIClient()
    SecondFactor.objects.create(user=owner,encrypted_secret="test",enabled=True)
    response=guest.post("/api/v1/auth/recovery",{"email":owner.email},format="json")
    assert response.status_code==202 and "preview_link" not in response.json()
    token=re.search(r"#reset=([^\s]+)",PasswordRecovery.objects.get().mail_body)[1]
    send_registration_mail()
    from django.core import mail
    assert len(mail.outbox)==1 and token in mail.outbox[0].body
    payload={"token":token,"password":"Replacement-password-2026"}
    assert guest.post("/api/v1/auth/reset",payload,format="json").status_code==200
    assert c.get("/api/v1/auth/me").status_code==403
    assert SecondFactor.objects.get(user=owner).enabled
    assert guest.post("/api/v1/auth/reset",payload,format="json").status_code==400
    owner.refresh_from_db();assert owner.check_password(payload["password"])


@pytest.mark.django_db
def test_recovery_privacy_expiry_and_local_preview(desk,owner,settings):
    c=APIClient();missing=c.post("/api/v1/auth/recovery",{"email":"missing@example.test"},format="json")
    real=c.post("/api/v1/auth/recovery",{"email":owner.email},format="json")
    assert missing.json()==real.json()
    token=re.search(r"#reset=([^\s]+)",PasswordRecovery.objects.get().mail_body)[1]
    PasswordRecovery.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
    assert c.post("/api/v1/auth/reset",{"token":token,"password":"Replacement-password-2026"},format="json").status_code==400
    settings.LOCAL_EMAIL_PREVIEW=True;settings.DEBUG=True
    # Local preview must not reveal recovery tokens for real users.
    assert "preview_link" not in c.post("/api/v1/auth/recovery",{"email":owner.email},format="json").json()
    owner.is_local_account=True;owner.save()
    assert "preview_link" in c.post("/api/v1/auth/recovery",{"email":owner.email},format="json").json()


@pytest.mark.django_db
def test_security_requires_csrf_and_reauthentication(desk,owner,client_for):
    c=client_for(desk["admin"])
    assert c.post("/api/v1/auth/security",{"action":"setup","password":"wrong"},format="json").status_code==403
    assert not SecondFactor.objects.exists()
    c=client_for(desk["admin"],csrf=True)
    assert c.post("/api/v1/auth/security",{"action":"revoke_others"},format="json").status_code==403
    assert APIClient(enforce_csrf_checks=True).post("/api/v1/auth/reset",{}).status_code==403
