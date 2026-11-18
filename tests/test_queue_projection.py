import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from apps.conversations.models import Message
from apps.tickets.services import create_ticket
from apps.sla.services import incoming


@pytest.mark.django_db
def test_queue_matches_detail_and_query_count_does_not_grow_per_row(desk, client_for):
    ticket = desk["ticket"]
    msg = Message.objects.create(
        workspace=desk["ws"],
        conversation=desk["conversation"],
        ticket=ticket,
        author_name="Клиент",
        text="Сбой в работе",
    )
    incoming(ticket, msg.created_at)
    client = client_for(desk["operator"])
    with CaptureQueriesContext(connection) as first:
        row = client.get("/api/v1/tickets?queue=free").json()["results"][0]
    assert row == client.get(f"/api/v1/tickets/{ticket.id}").json()
    for i in range(25):
        create_ticket(desk["ws"], desk["conversation"], desk["team"], f"Обращение {i}")
    with CaptureQueriesContext(connection) as full_page:
        data = client.get("/api/v1/tickets?queue=free").json()
    assert data["count"] == 26 and len(data["results"]) == 25
    assert len(full_page) <= len(first) + 1
