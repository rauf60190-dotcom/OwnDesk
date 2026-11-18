"""Measure authenticated Django + PostgreSQL requests on a dedicated test database.

Run with the backend virtual environment. DATABASE_URL must name a fresh database
beginning with desk_bench_. This tool does not send traffic to Telegram.
"""

import json
import math
import os
import platform
import statistics
import sys
import time
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
import django

django.setup()
from django.conf import settings
from django.db import connection, close_old_connections
from django.core.management import call_command
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.workspaces.models import Workspace, Membership
from apps.teams.models import Team, TeamMember
from apps.conversations.models import Conversation, Message
from apps.integrations.models import ChannelConnection
from apps.integrations.telegram.adapter import secret_hash
from apps.tickets.models import Ticket


def summary(samples):
    values = sorted(samples)
    return {
        "requests": len(values),
        "p50_ms": round(statistics.median(values), 1),
        "p95_ms": round(values[math.ceil(0.95 * len(values)) - 1], 1),
        "max_ms": round(max(values), 1),
    }


def main():
    database = settings.DATABASES["default"]["NAME"]
    if connection.vendor != "postgresql" or not database.startswith("desk_bench_"):
        raise SystemExit("Use a dedicated PostgreSQL database named desk_bench_*.")
    call_command("migrate", verbosity=0)
    if Workspace.objects.exists():
        raise SystemExit("Benchmark requires an empty destination database.")
    ws = Workspace.objects.create(name="Benchmark", next_ticket_number=10001)
    team = Team.objects.create(workspace=ws, name="Испытания")
    connection_record = ChannelConnection.objects.create(
        workspace=ws,
        bot_id="111",
        username="benchmark",
        default_team=team,
        encrypted_token="offline-test",
        webhook_secret_hash=secret_hash("benchmark-secret"),
        state="connected",
    )
    chat = Conversation.objects.create(
        workspace=ws,
        kind="private",
        connection=connection_record,
        external_id="222",
        approved=True,
    )
    users = []
    for i in range(20):
        user = User.objects.create_user(
            username=f"load-{i}", display_name=f"Оператор {i}"
        )
        member = Membership.objects.create(workspace=ws, user=user, role="operator")
        TeamMember.objects.create(team=team, membership=member)
        users.append(user)
    tickets = Ticket.objects.bulk_create(
        [
            Ticket(
                workspace=ws,
                team=team,
                conversation=chat,
                number=i + 1,
                subject=f"Вопрос №{i + 1}",
            )
            for i in range(10000)
        ],
        batch_size=500,
    )
    for start in range(0, 10000, 250):
        Message.objects.bulk_create(
            [
                Message(
                    workspace=ws,
                    conversation=chat,
                    ticket=ticket,
                    author_name="Клиент",
                    text=f"Сообщение {j + 1} по вопросу {ticket.number}",
                )
                for ticket in tickets[start : start + 250]
                for j in range(10)
            ],
            batch_size=500,
        )
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE tickets_ticket")
        cursor.execute("ANALYZE conversations_message")
        cursor.execute("SELECT version()")
        db_version = cursor.fetchone()[0]

    startup = time.perf_counter()
    if APIClient().get("/api/v1/health/ready").status_code != 200:
        raise RuntimeError("Server readiness check failed")
    startup_ms = (time.perf_counter() - startup) * 1000
    start_requests = Barrier(20, timeout=60)

    def measure(index):
        close_old_connections()
        client = APIClient()
        client.force_login(users[index])
        session = client.session
        session["workspace_id"] = str(ws.id)
        session.save()
        start_requests.wait()
        samples = {"list": [], "detail": []}
        try:
            for i in range(6):
                calls = [
                    ("list", f"/api/v1/tickets?queue=free&page={1 + i}"),
                    ("detail", f"/api/v1/tickets/{tickets[index * 50 + i].id}"),
                ]
                for key, path in calls:
                    start = time.perf_counter()
                    response = client.get(path)
                    if response.status_code != 200:
                        raise RuntimeError(f"{key}: {response.status_code}")
                    samples[key].append((time.perf_counter() - start) * 1000)
            return samples
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=20) as pool:
        runs = list(pool.map(measure, range(20)))
    result = {
        key: summary([x for run in runs for x in run[key]])
        for key in ["list", "detail"]
    }

    def webhook(index):
        close_old_connections()
        payload = {
            "update_id": index,
            "message": {
                "message_id": index,
                "chat": {"id": 222, "type": "private"},
                "from": {"id": 333},
                "date": int(time.time()),
                "text": "Испытание",
            },
        }
        try:
            start = time.perf_counter()
            response = APIClient().post(
                f"/api/v1/webhooks/telegram/{connection_record.id}",
                payload,
                format="json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="benchmark-secret",
            )
            if response.status_code != 200:
                raise RuntimeError(str(response.status_code))
            return (time.perf_counter() - start) * 1000
        finally:
            close_old_connections()

    for rate in [5, 20]:
        calls = []
        with ThreadPoolExecutor(max_workers=20) as pool:
            beginning = time.monotonic()
            for i in range(rate * 3):
                calls.append(pool.submit(webhook, rate * 1000 + i))
                time.sleep(max(0, beginning + (i + 1) / rate - time.monotonic()))
        result[f"webhook_{rate}_per_second"] = summary([f.result() for f in calls])
    result.update(
        date=datetime.now(timezone.utc).isoformat(),
        employees=20,
        tickets=10000,
        messages=100000,
        database=db_version,
        platform=platform.platform(),
        logical_cpu=os.cpu_count(),
        initial_readiness_ms=round(startup_ms, 1),
        method="One readiness probe before timing. All 20 employee sessions are established before measured requests start.",
        scope="Django test client + PostgreSQL. Excludes HTTP, worker, Redis, browser and Telegram delivery.",
    )
    path = root / "docs" / "benchmark.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
