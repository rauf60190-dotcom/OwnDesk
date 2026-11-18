from uuid import uuid4

import pytest
from apps.attachments.models import Attachment
from apps.audit.models import AuditEvent
from apps.teams.models import Team, TeamMember
from apps.workspaces.models import Workspace

ROUTE = "/api/v1/workspace/escalation-route"


@pytest.mark.django_db
def test_escalation_moves_to_scoped_team_without_changing_status(desk, client_for):
    admin = client_for(desk["admin"])
    operator = client_for(desk["operator"])
    teams = {line: Team.objects.create(workspace=desk["ws"], name=line) for line in ("l2", "l3", "development")}
    TeamMember.objects.create(team=teams["l2"], membership=desk["second"])
    desk["second"].teammember_set.filter(team=desk["team"]).delete()
    receiver = client_for(desk["second"])
    payload = {line: str(team.pk) for line, team in teams.items()}
    assert operator.patch(ROUTE, payload, format="json").status_code == 403
    foreign = Team.objects.create(workspace=Workspace.objects.create(name="Foreign"), name="Foreign")
    assert admin.patch(ROUTE, {**payload, "l2": str(foreign.pk)}, format="json").status_code == 400
    assert admin.patch(ROUTE, payload, format="json").status_code == 200

    ticket = desk["ticket"]
    ticket.status, ticket.assignee = "waiting_specialist", desk["operator"]
    ticket.save()
    url = f"/api/v1/tickets/{ticket.pk}"
    data = {"expected_version": ticket.version, "reason": "Нужна диагностика"}
    assert operator.post(url + "/escalate", {**data, "reason": " "}, format="json").status_code == 400
    assert operator.post(url + "/escalate", data, format="json").status_code == 200
    ticket.refresh_from_db()
    assert ticket.escalation_level == "l2" and ticket.team_id == teams["l2"].pk
    assert ticket.status == "waiting_specialist" and ticket.assignee_id is None
    assert operator.get(url).status_code == 404
    assert receiver.get(url).status_code == 200
    assert admin.post(url + "/escalate", data, format="json").status_code == 409
    event = AuditEvent.objects.get(ticket=ticket, action="ticket.escalated")
    assert event.actor_id == desk["operator"].user_id and event.changes["reason"] == data["reason"]
    assert event.changes["from"] == "l1" and event.changes["to"] == "l2"
    assert admin.get("/api/v1/tickets?queue=all&line=l2").data["count"] == 1
    assert admin.get("/api/v1/tickets?queue=all&line=l1").data["count"] == 0
    for next_level in ("l3", "development"):
        response = admin.post(url + "/escalate", {**data, "expected_version": ticket.version}, format="json")
        assert response.status_code == 200, response.data
        ticket.refresh_from_db()
        assert ticket.escalation_level == next_level and ticket.team_id == teams[next_level].pk
    assert admin.post(url + "/escalate", {**data, "expected_version": ticket.version}, format="json").status_code == 400


@pytest.mark.django_db
def test_board_ticket_description_and_initial_attachment(desk, client_for):
    staff = client_for(desk["admin"])
    file = Attachment.objects.create(workspace=desk["ws"], uploaded_by=desk["admin"].user,
        filename="photo.png", state="ready", mime_type="image/png")
    payload = {"subject":"Board question","description":"Описание при создании",
               "team_id":str(desk["team"].pk),"attachments":[str(file.pk)]}
    assert staff.post("/api/v1/tickets", {**payload, "attachments":[str(uuid4())]}, format="json").status_code == 400
    response = staff.post("/api/v1/tickets", payload, format="json")
    assert response.status_code == 201, response.data
    data = response.data
    url = f"/api/v1/tickets/{data['id']}"
    assert data["description"] == payload["description"] and data["escalation_level"] == "l1"
    file.refresh_from_db()
    assert str(file.message.ticket_id) == str(data["id"]) and file.message.visibility == "internal"
    update = staff.patch(url, {"expected_version":data["version"],"description":"Дополненное описание"}, format="json")
    assert update.status_code == 200, update.data
    assert update.data["description"] == "Дополненное описание"
    assert staff.get("/api/v1/tickets?queue=all&q=Дополненное").data["count"] == 1
