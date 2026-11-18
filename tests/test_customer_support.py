from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from apps.attachments.storage import LocalMedia
from apps.customers.models import Customer
from apps.integrations.models import OutboundJob
from apps.tickets.models import Ticket

PREFIX = "/api/v1"


def issue(client, customer, team):
    response = client.post(f"{PREFIX}/customers/{customer.pk}/support-link", {"team_id": str(team.pk)}, format="json")
    assert response.status_code == 201, response.data
    return urlsplit(response.data["link"]).fragment.removeprefix("access=")


def enter(token):
    client = APIClient(enforce_csrf_checks=True)
    assert client.post(f"{PREFIX}/support/session", {"token": token}, format="json").status_code == 403
    client.get(f"{PREFIX}/auth/csrf")
    csrf = client.cookies["csrftoken"].value
    response = client.post(f"{PREFIX}/support/session", {"token": token}, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert response.status_code == 200, response.data
    return client, {"HTTP_X_CSRFTOKEN": csrf}


@pytest.mark.django_db
def test_support_ticket_replies_attachments_and_isolation(desk, client_for, settings, tmp_path):
    settings.PUBLIC_BASE_URL = "http://127.0.0.1:5173"
    settings.DEBUG = True
    first = Customer.objects.create(workspace=desk["ws"], name="First")
    second = Customer.objects.create(workspace=desk["ws"], name="Second")
    Ticket.objects.filter(pk=desk["ticket"].pk).update(customer=first)
    admin = client_for(desk["admin"])
    other_staff = client_for(desk["operator"])
    assert other_staff.post(f"{PREFIX}/customers/{first.pk}/support-link", {}, format="json").status_code == 403
    token = issue(admin, first, desk["team"])
    first_client, first_csrf = enter(token)
    second_client, _ = enter(issue(admin, second, desk["team"]))
    assert first_client.get(f"{PREFIX}/support/tickets").data["count"] == 0
    assert first_client.get(f"{PREFIX}/support/tickets/{desk['ticket'].pk}/messages").status_code == 404
    body = {"subject": "Support question", "text": "Opening description", "operation_key": str(uuid4())}
    url = f"{PREFIX}/support/tickets"
    response = first_client.post(url, body, format="json", **first_csrf)
    assert response.status_code == 201, response.data
    ticket_id = response.data["id"]
    assert first_client.post(url, body, format="json", **first_csrf).data["id"] == ticket_id
    assert first_client.get(url).data["count"] == 1
    ticket = Ticket.objects.get(pk=ticket_id)
    assert ticket.team_id == desk["team"].pk
    thread = f"{PREFIX}/support/tickets/{ticket_id}/messages"
    assert second_client.get(thread).status_code == 404
    assert first_client.get(f"{PREFIX}/tickets/{ticket_id}").status_code == 403
    message_url = f"{PREFIX}/tickets/{ticket_id}"
    response = admin.post(f"{message_url}/notes", {"text":"Internal only","expected_version":ticket.version,"operation_key":str(uuid4())}, format="json")
    assert response.status_code == 201, response.data
    ticket.refresh_from_db()
    response = admin.post(f"{message_url}/messages", {"text":"Public reply","expected_version":ticket.version,"operation_key":str(uuid4())}, format="json")
    assert response.status_code == 201, response.data
    assert not OutboundJob.objects.filter(message__ticket=ticket).exists()

    media = LocalMedia(tmp_path)
    raw = b"\x89PNG\r\n\x1a\nfixture"
    with patch("apps.customers.support.storage", return_value=media), patch("apps.attachments.views.storage", return_value=media):
        response = first_client.post(thread, {"text":"Follow-up", "operation_key":str(uuid4()),
            "file":SimpleUploadedFile("fixture.png", raw)}, format="multipart", **first_csrf)
        assert response.status_code == 201, response.data
        attachment_id = response.data["attachments"][0]["id"]
        download = f"{PREFIX}/support/attachments/{attachment_id}?preview=1"
        assert second_client.get(download).status_code == 404
        response = first_client.get(download)
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == raw
        assert response["Content-Type"] == "image/png"

    text = [row["text"] for row in first_client.get(thread).data["results"]]
    assert text == ["Follow-up", "Public reply", "Opening description"]
    replacement = issue(admin, first, desk["team"])
    assert replacement != token
    assert first_client.get(thread).status_code == 403
    new_client, _ = enter(replacement)
    assert new_client.get(thread).status_code == 200
    assert admin.delete(f"{PREFIX}/customers/{first.pk}/support-link").status_code == 204
    assert new_client.get(thread).status_code == 403
