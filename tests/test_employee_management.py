import uuid
import pytest
from apps.accounts.models import User
from apps.workspaces.models import Membership, Workspace
from apps.teams.models import Team
from apps.tickets import services
from apps.conversations.models import Message

pytestmark = pytest.mark.django_db


def test_edit_role_revokes_writing_for_existing_session(desk, client_for):
    admin = client_for(desk["admin"])
    operator = desk["operator"]
    logged = client_for(operator)
    ticket = services.claim(operator, desk["ticket"].pk, 1)
    payload = {"expected_version": ticket.version, "text":"Historical response", "operation_key":str(uuid.uuid4())}
    response = logged.post(f"/api/v1/tickets/{ticket.pk}/messages", payload, format="json")
    assert response.status_code == 201
    message = Message.objects.get(pk=response.json()["id"])
    original_author = message.author_name
    result = admin.patch(f"/api/v1/employees/{operator.pk}", {"name":"New name", "role":"observer", "teams":[str(desk["team"].pk)]}, format="json")
    assert result.status_code == 200
    ticket.refresh_from_db(); assert ticket.assignee_id is None
    payload.update(expected_version=ticket.version, operation_key=str(uuid.uuid4()))
    assert logged.post(f"/api/v1/tickets/{ticket.pk}/messages", payload, format="json").status_code == 403
    assert logged.post(f"/api/v1/tickets/{ticket.pk}/notes", payload, format="json").status_code == 403
    message.refresh_from_db(); assert message.author_name == original_author
    assert logged.get("/api/v1/auth/me").json()["role"] == "observer"


def test_remove_restore_retains_history_and_requires_explicit_enable(desk, client_for):
    admin = client_for(desk["admin"]); target = desk["operator"]
    logged = client_for(target)
    ticket = services.claim(target, desk["ticket"].pk, 1)
    user_id = target.user_id
    path = f"/api/v1/employees/{target.pk}"
    assert admin.post(path+"/remove", {}, format="json").status_code == 200
    assert admin.post(path+"/remove", {}, format="json").status_code == 200
    target.refresh_from_db(); ticket.refresh_from_db()
    assert target.removed_at and not target.active and ticket.assignee_id is None
    assert User.objects.filter(pk=user_id).exists()
    assert logged.get("/api/v1/auth/me").status_code == 403
    assert admin.patch(path,{"active":True},format="json").status_code == 400
    assert admin.post(path+"/restore",{},format="json").status_code == 200
    target.refresh_from_db(); assert target.removed_at is None and not target.active
    assert admin.patch(path,{"active":True},format="json").status_code == 200


def test_employee_management_boundaries(desk, client_for):
    owner = desk["admin"]; owner.is_owner=True; owner.save()
    admin = client_for(owner); target = desk["operator"]
    path = f"/api/v1/employees/{target.pk}"
    assert client_for(target).post(path+"/remove",{},format="json").status_code == 403
    assert admin.post(f"/api/v1/employees/{owner.pk}/remove",{},format="json").status_code == 403
    foreign = Workspace.objects.create(name="Other")
    employee = Membership.objects.create(workspace=foreign,user=desk["second"].user)
    assert admin.post(f"/api/v1/employees/{employee.pk}/remove",{},format="json").status_code == 404
    team = Team.objects.create(workspace=foreign,name="Private")
    assert admin.patch(path,{"name":"Should roll back","teams":[str(team.pk)]},format="json").status_code == 400
    target.user.refresh_from_db(); assert target.user.display_name == "operator"
    deputy = desk["manager"]; deputy.role="admin"; deputy.save()
    assert client_for(deputy).post(f"/api/v1/employees/{owner.pk}/remove",{},format="json").status_code == 403
    assert client_for(deputy).patch(path,{"role":"admin"},format="json").status_code == 403
