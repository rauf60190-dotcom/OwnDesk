from datetime import datetime, timezone

import pytest

from apps.audit.models import AuditEvent
from apps.teams.models import Team
from apps.tickets.models import Ticket
from apps.tickets.services import create_ticket, transition


@pytest.mark.django_db
def test_closures_attribute_latest_event_and_keep_legacy_in_workspace_timezone(desk, client_for, monkeypatch):
    ticket = desk["ticket"]
    legacy = create_ticket(desk["ws"], desk["conversation"], desk["team"], "Старый тикет")
    other_team = Team.objects.create(workspace=desk["ws"], name="Другая команда")
    hidden = create_ticket(desk["ws"], desk["conversation"], other_team, "Чужая команда")

    # 19:00 UTC is midnight in the workspace.
    first_close = datetime(2026, 11, 9, 19, 10, tzinfo=timezone.utc)
    second_close = datetime(2026, 11, 9, 20, 15, tzinfo=timezone.utc)
    ticket.status = "resolved"
    ticket.assignee = desk["operator"]
    ticket.save(update_fields=["status", "assignee"])
    monkeypatch.setattr("django.utils.timezone.now", lambda: second_close)
    transition(desk["admin"], ticket.id, ticket.version, "closed")
    earlier = AuditEvent.objects.create(
        workspace=desk["ws"], ticket=ticket, actor=desk["operator"].user,
        action="ticket.status", changes={"from": "resolved", "to": "closed"},
    )
    AuditEvent.objects.filter(pk=earlier.pk).update(created_at=first_close)
    Ticket.objects.filter(pk__in=[legacy.pk, hidden.pk]).update(status="closed", closed_at=first_close)

    endpoint = "/api/v1/reports/closures"
    manager = client_for(desk["manager"])
    report = manager.get(endpoint, {"period": "today"}).json()
    assert (report["from"], report["to"], report["total"], report["hourly"]) == ("2026-11-10", "2026-11-10", 2, True)
    assert [(item["id"], item["count"]) for item in report["employees"]] == [
        (str(desk["admin"].user_id), 1), (None, 1),
    ]
    assert {item["key"]: item["count"] for item in report["buckets"] if item["count"]} == {"00": 1, "01": 1}
    assert [item["id"] for item in report["details"]["results"]] == [str(ticket.id), str(legacy.id)]
    details = manager.get(endpoint, {"period": "today", "employee": "unknown", "bucket": "00"}).json()
    assert details["total"] == details["details"]["count"] == 1
    assert details["details"]["results"][0]["id"] == str(legacy.id)
    employee = manager.get(endpoint, {"period": "today", "employee": desk["admin"].user_id}).json()
    assert employee["total"] == 1
    assert employee["details"]["results"][0]["id"] == str(ticket.id)
    assert manager.get(endpoint, {"period": "yesterday"}).json()["total"] == 0
    week = manager.get(endpoint, {"period": "week"}).json()
    assert (week["from"], week["to"], week["hourly"]) == ("2026-11-09", "2026-11-10", False)
    assert manager.get("/api/v1/reports/summary", {"period": "month"}).json()["from"] == "2026-11-01"
    assert client_for(desk["operator"]).get(endpoint).status_code == 403
    assert client_for(desk["admin"]).get(endpoint, {"period": "today"}).json()["total"] == 3
