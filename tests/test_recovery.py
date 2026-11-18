import uuid
from datetime import timedelta
from unittest.mock import patch
import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.utils import timezone
from apps.tickets import services
from apps.integrations.models import InboundEvent, OutboundJob
from apps.integrations.tasks import sweep
from apps.workspaces.models import Workspace, Membership
from apps.teams.models import Team
from apps.conversations.models import Conversation

pytestmark = pytest.mark.django_db


def test_worker_recovers_persisted_jobs_after_loss_of_broker_queue(desk):
    ticket = services.claim(desk["operator"], desk["ticket"].id, 1)
    reply = services.send(
        desk["operator"], ticket.id, ticket.version, "Ответ", uuid.uuid4()
    )
    event = InboundEvent.objects.create(
        connection=desk["connection"], update_id="1000", payload={"update_id": 1000}
    )
    with (
        patch("apps.integrations.tasks.handle_delivery.delay") as submit_job,
        patch("apps.integrations.tasks.handle_event.delay") as submit_event,
    ):
        sweep()
        submit_job.assert_called_once_with(str(reply.job.id))
        submit_event.assert_called_once_with(str(event.id))
        OutboundJob.objects.filter(id=reply.job.id).update(
            state="sending", started_at=timezone.now() - timedelta(minutes=10)
        )
        submit_job.reset_mock()
        sweep()
        submit_job.assert_not_called()
    reply.job.refresh_from_db()
    assert reply.job.state == "unknown"


def test_unknown_retry_requires_explicit_acknowledgment(desk, client_for):
    ticket = services.claim(desk["operator"], desk["ticket"].id, 1)
    msg = services.send(
        desk["operator"], ticket.id, ticket.version, "Ответ", uuid.uuid4()
    )
    OutboundJob.objects.filter(id=msg.job.id).update(state="unknown")
    ticket.refresh_from_db()
    client = client_for(desk["operator"])
    data = {"expected_version": ticket.version, "message_id": str(msg.id)}
    path = f"/api/v1/tickets/{ticket.id}/retry-delivery"
    assert client.post(path, data, format="json").status_code == 400
    assert (
        client.post(
            path, {**data, "confirm_duplicate": True}, format="json"
        ).status_code
        == 200
    )
    msg.job.refresh_from_db()
    assert msg.job.state == "queued"


@pytest.mark.django_db(transaction=True)
def test_websocket_filters_foreign_ticket_and_revokes_access(desk, client_for):
    from config.asgi import application

    other = Workspace.objects.create(name="Другое пространство")
    other_team = Team.objects.create(workspace=other, name="Другая команда")
    other_chat = Conversation.objects.create(workspace=other)
    foreign = services.create_ticket(other, other_chat, other_team, "Чужая заявка")
    client = client_for(desk["operator"])
    session_cookie = client.cookies["sessionid"].value
    member_id = desk["operator"].id
    group = f"user_{desk['operator'].user_id.hex}"

    async def exercise():
        socket = WebsocketCommunicator(
            application,
            "/ws/events",
            headers=[
                (b"origin", b"http://testserver"),
                (b"cookie", f"sessionid={session_cookie}".encode()),
            ],
        )
        assert (await socket.connect())[0]

        async def emit(ticket_id):
            await get_channel_layer().group_send(
                group,
                {
                    "type": "desk.event",
                    "data": {
                        "id": str(uuid.uuid4()),
                        "type": "ticket.changed",
                        "time": timezone.now().isoformat(),
                        "object_id": str(ticket_id),
                        "version": 1,
                    },
                },
            )

        await emit(foreign.id)
        assert await socket.receive_nothing(timeout=0.1)
        await emit(desk["ticket"].id)
        assert (await socket.receive_json_from())["object_id"] == str(desk["ticket"].id)
        await database_sync_to_async(Membership.objects.filter(id=member_id).update)(
            active=False
        )
        await emit(desk["ticket"].id)
        event = await socket.receive_output()
        assert event["type"] == "websocket.close" and event["code"] == 4403
        await socket.disconnect()

    async_to_sync(exercise)()
