from datetime import timedelta
import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.workspaces.models import Membership, Invitation, Workspace


@pytest.fixture
def owner(desk, settings):
    settings.PUBLIC_BASE_URL="http://127.0.0.1:5173"
    settings.DEBUG=True
    m=desk["admin"];m.is_owner=True;m.save()
    return m


def invite(client, desk, email="invited@example.test", role="operator"):
    result=client.post("/api/v1/invitations",{"email":email,"role":role,"team_id":str(desk["team"].pk)},format="json")
    assert result.status_code==201,result.json()
    data=result.json();return data,data["link"].split("#invite=")[1]


@pytest.mark.django_db
@pytest.mark.parametrize("origin", ["http://127.0.0.1:5173", "https://team.owndesk.work"])
def test_invite_accept_login_and_disable(desk, owner, client_for, settings, origin):
    settings.PUBLIC_BASE_URL = origin
    settings.DEBUG = origin.startswith("http://")
    admin=client_for(owner);data,token=invite(admin,desk)
    assert admin.get("/api/v1/invitations").json()["organization_url"] == origin
    assert data["link"] == f"{origin}/#invite={token}"
    before=Workspace.objects.count();client=APIClient()
    payload={"token":token,"name":"Employee","password":"Employee-testing-password-2026"}
    assert client.post("/api/v1/auth/invitations/accept",payload,format="json").status_code==200
    user=User.objects.get(email="invited@example.test");m=Membership.objects.get(user=user)
    assert m.workspace_id==owner.workspace_id and m.role=="operator" and not m.is_owner
    assert not user.email_verified and Workspace.objects.count()==before
    assert list(m.teammember_set.values_list("team_id",flat=True))==[desk["team"].pk]
    assert client.post("/api/v1/auth/invitations/accept",payload,format="json").status_code==400
    client.logout()
    assert client.post("/api/v1/auth/login",{"username":user.email,"password":payload["password"]},format="json").status_code==200
    assert admin.patch(f"/api/v1/employees/{m.pk}",{"active":False},format="json").status_code==200
    assert client.get("/api/v1/auth/me").status_code==403


@pytest.mark.django_db
def test_revoke_replace_expiry_and_permissions(desk, owner, client_for):
    admin=client_for(owner);data,token=invite(admin,desk)
    _,replacement=invite(admin,desk)
    guest=APIClient()
    assert guest.post("/api/v1/auth/invitations/inspect",{"token":token},format="json").status_code==400
    current=Invitation.objects.get(revoked_at=None)
    assert admin.post(f"/api/v1/invitations/{current.pk}/revoke",{},format="json").status_code==200
    assert guest.post("/api/v1/auth/invitations/inspect",{"token":replacement},format="json").status_code==400
    data,token=invite(admin,desk)
    Invitation.objects.filter(pk=data["id"]).update(expires_at=timezone.now()-timedelta(seconds=1))
    assert guest.post("/api/v1/auth/invitations/inspect",{"token":token},format="json").status_code==400
    assert client_for(desk["operator"]).post("/api/v1/invitations",{},format="json").status_code==403
    assert admin.patch(f"/api/v1/employees/{owner.pk}",{"active":False},format="json").status_code==403
    desk["manager"].role="admin";desk["manager"].save()
    assert client_for(desk["manager"]).post("/api/v1/invitations",{"email":"admin2@example.test","role":"admin","team_id":str(desk["team"].pk)},format="json").status_code==403


@pytest.mark.django_db
def test_existing_account_cannot_be_taken_over(desk, owner, client_for):
    existing=User.objects.create_user(username="existing-invite",email="existing@example.test",password="Existing-password-2026")
    _,token=invite(client_for(owner),desk,existing.email)
    client=APIClient()
    assert client.post("/api/v1/auth/invitations/accept",{"token":token,"password":"Attacker-password-2026","name":"Attacker"},format="json").status_code==403
    client.force_login(existing)
    assert client.post("/api/v1/auth/invitations/accept",{"token":token},format="json").status_code==200
    existing.refresh_from_db();assert existing.check_password("Existing-password-2026")


@pytest.mark.django_db
def test_other_workspace_cannot_cancel(desk, owner, client_for):
    from apps.workspaces.models import Workspace
    admin=client_for(owner);data,_=invite(admin,desk)
    foreign=Workspace.objects.create(name="Other")
    m=Membership.objects.create(workspace=foreign,user=desk["second"].user,role="admin")
    assert client_for(m).post(f'/api/v1/invitations/{data["id"]}/revoke',{},format="json").status_code==404
