from datetime import timedelta
from unittest.mock import patch
import pytest
from django.utils import timezone
from apps.accounts.models import SecondFactor
from apps.accounts.registration import digest
from apps.accounts.tasks import send_registration_mail
from apps.workspaces.models import Invitation, Membership, Workspace


@pytest.fixture
def owner(desk, settings):
    settings.PUBLIC_BASE_URL = "http://127.0.0.1:5173"
    settings.DEBUG = True
    m = desk["admin"]
    m.is_owner = True
    m.save()
    return m


@pytest.mark.django_db
def test_transfer_requires_owner_password_and_mfa(desk, owner, client_for):
    url = "/api/v1/workspace/transfer-ownership"
    client = client_for(owner)
    target = desk["operator"]
    target_client = client_for(target)
    data = {"membership_id": str(target.pk), "password": "Testing-password-123"}
    assert target_client.post(url, data, format="json").status_code == 403
    assert client.post(url, {**data, "password": "wrong"}, format="json").status_code == 403
    factor = SecondFactor.objects.create(user=owner.user, enabled=True, recovery_hashes=[digest("backup-test-code")])
    assert client.post(url, data, format="json").status_code == 403
    owner.refresh_from_db(); assert owner.is_owner
    assert client.post(url, {**data, "code": "backup-test-code"}, format="json").status_code == 200
    owner.refresh_from_db(); target.refresh_from_db(); factor.refresh_from_db()
    assert not owner.is_owner and owner.role == "admin"
    assert target.is_owner and target.role == "admin" and not factor.recovery_hashes
    assert Membership.objects.filter(workspace=owner.workspace, is_owner=True).count() == 1
    assert target_client.get("/api/v1/auth/me").status_code == 403
    assert client.post(url, data, format="json").status_code == 403


@pytest.mark.django_db
def test_transfer_rejects_self_foreign_inactive_and_csrf(desk, owner, client_for):
    url = "/api/v1/workspace/transfer-ownership"
    client = client_for(owner)
    target = desk["operator"]; target.active = False; target.save()
    foreign = Membership.objects.create(workspace=Workspace.objects.create(name="Other"), user=desk["second"].user)
    for m in [owner, target, foreign]:
        assert client.post(url, {"membership_id": str(m.pk), "password": "Testing-password-123"}, format="json").status_code == 400
    assert client_for(owner, csrf=True).post(url, {"membership_id":str(desk["manager"].pk), "password":"Testing-password-123"}, format="json").status_code == 403


@pytest.mark.django_db
def test_invitation_mail_delivery_and_cancellation(desk, owner, client_for, settings):
    client = client_for(owner)
    data = {"email":"employee@example.test", "role":"operator", "team_id":str(desk["team"].pk), "send_email":True}
    settings.EMAIL_HOST = ""
    assert client.post("/api/v1/invitations", data, format="json").status_code == 400
    settings.EMAIL_HOST = "smtp.example.test"; settings.DEFAULT_FROM_EMAIL = "desk@example.test"
    def create():
        result = client.post("/api/v1/invitations", data, format="json")
        assert result.status_code == 201
        return Invitation.objects.get(pk=result.json()["id"]), result.json()["link"]
    old, _ = create(); current, link = create()
    old.refresh_from_db(); assert not old.mail_body and old.revoked_at
    with patch("apps.accounts.tasks.send_mail", return_value=1) as mail:
        send_registration_mail(); send_registration_mail()
        assert mail.call_count == 1
        assert link in mail.call_args.args[1]
        assert mail.call_args.args[3] == [data["email"]]
    current.refresh_from_db(); assert current.mail_state == "sent" and not current.mail_body
    listed = client.get("/api/v1/invitations").json()
    assert listed["email_available"] and "mail_body" not in listed["results"][0]
    current, _ = create()
    client.post(f"/api/v1/invitations/{current.pk}/revoke", {}, format="json")
    with patch("apps.accounts.tasks.send_mail") as mail:
        send_registration_mail(); mail.assert_not_called()


@pytest.mark.django_db
def test_invitation_mail_does_not_retry_uncertain_delivery(desk, owner, client_for, settings):
    settings.EMAIL_HOST = "smtp.example.test"; settings.DEFAULT_FROM_EMAIL = "desk@example.test"
    result = client_for(owner).post("/api/v1/invitations", {"email":"employee@example.test", "role":"operator", "team_id":str(desk["team"].pk), "send_email":True}, format="json")
    invitation = Invitation.objects.get(pk=result.json()["id"])
    with patch("apps.accounts.tasks.send_mail", side_effect=TimeoutError) as mail:
        send_registration_mail(); send_registration_mail()
        assert mail.call_count == 1
    invitation.refresh_from_db(); assert invitation.mail_state == "unknown" and not invitation.mail_body
    Invitation.objects.filter(pk=invitation.pk).update(mail_state="sending", mail_body="secret", mail_started_at=timezone.now()-timedelta(minutes=3))
    with patch("apps.accounts.tasks.send_mail") as mail:
        send_registration_mail(); mail.assert_not_called()
    invitation.refresh_from_db(); assert invitation.mail_state == "unknown" and not invitation.mail_body
