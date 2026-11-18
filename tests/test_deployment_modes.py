import re
import pytest
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from apps.accounts.models import Registration
from apps.accounts.registration import digest
from apps.workspaces.models import Workspace, Membership, InstallationSetup
from apps.teams.models import Team


@pytest.mark.django_db
def test_portal_registration_never_creates_working_data(settings):
    settings.DEPLOYMENT_MODE="portal";settings.DEBUG=True
    settings.LOCAL_EMAIL_PREVIEW=True;settings.PUBLIC_REGISTRATION_ENABLED=True
    settings.PUBLIC_BASE_URL="http://127.0.0.1:5175"
    c=APIClient();r=c.post("/api/v1/auth/register",{"email":"portal@example.test","name":"Portal owner"},format="json")
    assert r.status_code==202
    token=r.json()["preview_link"].split("#verify=")[1]
    assert c.post("/api/v1/auth/verify",{"token":token,"password":"Portal-password-2026"},format="json").status_code==200
    assert not Workspace.objects.exists() and not Membership.objects.exists() and not Team.objects.exists()
    assert c.get("/api/v1/auth/me").json()["deployment_mode"]=="portal"
    for path in ["tickets","employees","invitations","attachments","integrations/telegram","reports/summary","reports/closures"]:
        assert c.get("/api/v1/"+path).status_code==404
    c.logout()
    assert c.post("/api/v1/auth/login",{"username":"portal@example.test","password":"Portal-password-2026"},format="json").status_code==200


@pytest.mark.django_db
def test_organization_setup_is_private_one_use_and_registration_closed(settings,tmp_path):
    settings.DEPLOYMENT_MODE="organization";settings.PUBLIC_REGISTRATION_ENABLED=True
    settings.PUBLIC_BASE_URL="http://127.0.0.1:5173"
    c=APIClient();options=c.get("/api/v1/auth/options").json()
    assert options["setup_required"] and not options["email_registration"] and not options["google_login"]
    assert c.post("/api/v1/auth/register",{},format="json").status_code==403
    assert c.post("/api/v1/auth/verify",{},format="json").status_code==403
    file=tmp_path/"private-code.txt";call_command("prepare_installation",output=str(file))
    code=file.read_text(encoding="utf-8")
    payload={"code":"wrong","organization":"My organization","email":"owner@example.test","name":"Owner","password":"Owner-testing-password-2026","timezone":"Asia/Tashkent"}
    assert c.post("/api/v1/setup/complete",payload,format="json").status_code==400
    assert not Workspace.objects.exists()
    payload["code"]=code
    assert c.post("/api/v1/setup/complete",payload,format="json").status_code==200
    assert Membership.objects.get().is_owner
    assert Workspace.objects.get().name=="My organization"
    assert not c.get("/api/v1/auth/options").json()["setup_required"]
    assert c.post("/api/v1/setup/complete",payload,format="json").status_code==403
    assert InstallationSetup.objects.get().token_hash==""


@pytest.mark.django_db
def test_existing_organization_is_never_reinitialized(desk,settings):
    settings.DEPLOYMENT_MODE="organization"
    c=APIClient()
    assert not c.get("/api/v1/auth/options").json()["setup_required"]
    assert c.post("/api/v1/setup/complete",{},format="json").status_code==403
    assert Workspace.objects.count()==1


@pytest.mark.django_db
def test_setup_csrf_and_expiry(settings):
    settings.DEPLOYMENT_MODE="organization"
    assert APIClient(enforce_csrf_checks=True).post("/api/v1/setup/complete",{}).status_code==403
    InstallationSetup.objects.create(token_hash=digest("expired"),expires_at=timezone.now()-timedelta(seconds=1))
    c=APIClient()
    assert c.post("/api/v1/setup/complete",{"code":"expired","organization":"Company","email":"owner@example.test","name":"Owner","password":"Owner-testing-password-2026"},format="json").status_code==400
    assert not Workspace.objects.exists()
