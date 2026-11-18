"""Supervise one dedicated cloudflared process; keep credentials out of argv/logs."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import UUID

import httpx
from django.core.management.base import CommandError

from .connector_access import public_ready, signature
from .management.commands.connect_portal import Command as PairCommand


class Revoked(Exception):
    pass


class ConnectorRuntime:
    def __init__(self, state_path, executable):
        self.path = Path(state_path).resolve()
        self.status_path = self.path.with_name(self.path.name + ".status.json")
        self.executable = str(executable)
        self.process = None
        self.identity = None
        self.token = None
        self.activated = False

    def stop_tunnel(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        self.identity = self.token = None
        self.activated = False

    def status(self, state, public_url=""):
        PairCommand.save_state(self.status_path, {
            "state": state, "public_url": public_url, "checked_at": time.time(),
        })

    @staticmethod
    def accepted(response):
        if response.status_code in (401, 403):
            raise Revoked
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise CommandError("Unexpected connector response.")
        return data

    def cycle(self, client):
        state = json.loads(self.path.read_text(encoding="utf-8"))
        installation_id = str(UUID(state["id"]))
        key = state["connector_key"]
        if not isinstance(key, str) or not 43 <= len(key) <= 128:
            raise CommandError("Invalid connector state.")
        root = f"https://owndesk.work/api/v1/portal/installations/{installation_id}"
        headers = {"Authorization": "Bearer " + key}
        self.accepted(client.post(root + "/heartbeat", headers=headers))
        configuration = self.accepted(client.post(root + "/configuration", headers=headers))
        hostname, token = configuration["hostname"], configuration["token"]
        expected = "org-" + UUID(installation_id).hex + ".owndesk.work"
        if hostname != expected or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9+/=_-]{32,16384}", token):
            raise CommandError("Portal returned an invalid tunnel configuration.")
        if not public_ready(hostname):
            raise CommandError("Public runtime is not configured. Disable DEBUG, set PostgreSQL, HTTPS origin, secure cookies and trusted hostname.")
        # Confirm that the process behind the fixed loopback port is this organization,
        # not another development server. Do not send the connector key to that port.
        import secrets
        challenge = secrets.token_urlsafe(32)
        local = client.get("http://127.0.0.1:5173/api/v1/connector/proof",
                           params={"challenge": challenge},
                           headers={"Host": hostname, "X-Forwarded-Proto": "https"})
        local.raise_for_status()
        local_data = local.json()
        proof = local_data.get("proof", "") if isinstance(local_data, dict) else ""
        if not isinstance(proof, str) or not secrets.compare_digest(
            proof, signature(key, installation_id, hostname, challenge, True),
        ):
            raise CommandError("The local server has not confirmed secure readiness.")
        if self.identity != installation_id or self.token != token or self.process is None or self.process.poll() is not None:
            self.stop_tunnel()
            environment = {**os.environ, "TUNNEL_TOKEN": token}
            self.process = subprocess.Popen(
                [self.executable, "tunnel", "--no-autoupdate", "run"], env=environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.identity, self.token = installation_id, token
        response = client.post(root + "/activate", headers=headers)
        if response.status_code in (401, 403):
            raise Revoked
        if response.status_code == 400:
            # DNS / first tunnel connection may need another cycle. The portal
            # still permits only the proof endpoint until activation succeeds.
            if self.activated:
                self.stop_tunnel()
                self.status("error")
            else:
                self.status("checking")
            return
        result = self.accepted(response)
        origin = "https://" + hostname
        if result.get("public_access") is not True or result.get("public_url") != origin:
            raise CommandError("Portal has not confirmed the public address.")
        if self.process.poll() is not None:
            raise CommandError("Tunnel process stopped before activation completed.")
        self.activated = True
        self.status("active", origin)

    def run(self, stop):
        try:
            with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
                while not stop.is_set():
                    try:
                        self.cycle(client)
                    except Revoked:
                        self.stop_tunnel()
                        self.status("revoked")
                        return
                    except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError, CommandError):
                        self.stop_tunnel()
                        self.status("error")
                    if stop.wait(30):
                        break
        finally:
            self.stop_tunnel()
            if stop.is_set():
                self.status("stopped")
