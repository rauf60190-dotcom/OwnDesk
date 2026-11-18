import getpass
import json
import os
import secrets
from pathlib import Path
from time import sleep
from uuid import UUID

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.workspaces.models import Workspace


class Command(BaseCommand):
    help = "Pair this organization with the OwnDesk portal and send connector heartbeats."

    def add_arguments(self, parser):
        parser.add_argument("--state", required=True, help="Private connector state file outside the application archive.")
        parser.add_argument("--once", action="store_true", help="Pair or check the connection and exit.")
        parser.add_argument("--prepare-route", action="store_true", help="Ask the portal to prepare this installation's public address.")

    def handle(self, *args, **options):
        if settings.DEPLOYMENT_MODE != "organization" or not Workspace.objects.exists():
            raise CommandError("First configure the organization server.")
        target = Path(options["state"]).resolve()
        portal = "https://owndesk.work/api/v1/portal/installations"
        try:
            if target.exists():
                state = json.loads(target.read_text(encoding="utf-8"))
            else:
                code = getpass.getpass("Код подключения из кабинета OwnDesk: ").strip()
                if not 32 <= len(code) <= 128:
                    raise CommandError("Invalid pairing code.")
                state = {"code": code, "connector_key": secrets.token_urlsafe(48)}
                self.save_state(target, state, exclusive=True)
            key = state["connector_key"]
            if not isinstance(key, str) or not 43 <= len(key) <= 128:
                raise CommandError("Invalid connector state.")
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                if not state.get("id"):
                    version = (settings.BASE_DIR.parent / "VERSION").read_text().strip()
                    response = client.post(portal + "/pair", json={
                        "code": state["code"], "connector_key": key, "version": version,
                    })
                    response.raise_for_status()
                    state = {"id": response.json()["id"], "connector_key": key}
                    self.save_state(target, state)
                self.stdout.write("Установка привязана к кабинету.")
                while True:
                    try:
                        response = client.post(f"{portal}/{state['id']}/heartbeat",
                                               headers={"Authorization": f"Bearer {key}"})
                        if response.status_code in (401, 403):
                            raise CommandError("Connection revoked. Create a new connection in the portal.")
                        response.raise_for_status()
                        if options["prepare_route"]:
                            config = client.post(f"{portal}/{state['id']}/configuration",
                                                 headers={"Authorization": f"Bearer {key}"})
                            config.raise_for_status()
                            hostname = config.json()["hostname"]
                            expected = "org-" + UUID(str(state["id"])).hex + ".owndesk.work"
                            if hostname != expected:
                                raise CommandError("Portal returned an invalid public hostname.")
                            state["public_hostname"] = hostname
                            self.save_state(target, state)
                            self.stdout.write("Адрес подготовлен. Рабочий сервер должен отдельно подтвердить готовность через HTTPS.")
                    except httpx.HTTPError:
                        if options["once"]:
                            raise CommandError("Portal unavailable. Connector state retained for retry.") from None
                        self.stderr.write("Портал недоступен. Повторная проверка через 30 секунд.")
                    if options["once"]:
                        return
                    sleep(30)
        except KeyboardInterrupt:
            self.stdout.write("Коннектор остановлен.")
        except (httpx.HTTPError, ValueError, KeyError, OSError):
            raise CommandError("Connection failed. Check portal availability and the private state file.") from None

    @staticmethod
    def save_state(target, state, exclusive=False):
        target.parent.mkdir(parents=True, exist_ok=True)
        destination = target if exclusive else target.with_name(target.name + "." + secrets.token_hex(8) + ".tmp")
        try:
            with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            if not exclusive:
                os.replace(destination, target)
        finally:
            if not exclusive:
                destination.unlink(missing_ok=True)
