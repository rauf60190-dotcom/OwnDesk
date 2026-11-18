import os
import sys
from pathlib import Path

os.environ.setdefault("DEBUG", "1")
os.environ.setdefault("LOCAL_CHANNELS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.workspaces.models import Workspace, Membership
from apps.teams.models import Team, TeamMember
from apps.integrations.models import ChannelConnection
from apps.integrations.telegram.adapter import secret_hash
from apps.conversations.models import Conversation
from apps.tickets.services import create_ticket


@pytest.fixture
def desk(db):
    ws = Workspace.objects.create(name="Испытания")
    team = Team.objects.create(workspace=ws, name="Поддержка")
    members = {}
    for name, role in [
        ("admin", "admin"),
        ("operator", "operator"),
        ("second", "operator"),
        ("observer", "observer"),
        ("manager", "manager"),
    ]:
        user = User.objects.create_user(
            username=name, password="Testing-password-123", display_name=name
        )
        m = Membership.objects.create(workspace=ws, user=user, role=role)
        TeamMember.objects.create(team=team, membership=m)
        members[name] = m
    conn = ChannelConnection.objects.create(
        workspace=ws,
        bot_id="123",
        username="desk_test_bot",
        encrypted_token="test-only",
        webhook_secret_hash=secret_hash("test-secret"),
        default_team=team,
        state="connected",
    )
    conv = Conversation.objects.create(
        workspace=ws,
        connection=conn,
        external_id="456",
        kind="private",
        approved=True,
        title="Тестовый клиент",
    )
    ticket = create_ticket(ws, conv, team, "Тестовая проблема")
    return {
        "ws": ws,
        "team": team,
        "connection": conn,
        "conversation": conv,
        "ticket": ticket,
        **members,
    }


@pytest.fixture
def client_for():
    def make(m, csrf=False):
        c = APIClient(enforce_csrf_checks=csrf)
        c.force_login(m.user)
        session = c.session
        session["workspace_id"] = str(m.workspace_id)
        session.save()
        return c

    return make
