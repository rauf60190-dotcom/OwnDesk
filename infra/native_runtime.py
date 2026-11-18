"""Portable supervisor for one organization, on Windows or inside a Linux container."""
import argparse
import base64
from contextlib import ExitStack
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlencode
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def save_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def profile_for(data):
    profile = json.loads((data / "runtime-profile.json").read_text(encoding="utf-8"))
    if profile.get("format") != 1:
        raise ValueError("Unsupported runtime profile")
    state = json.loads((data / "portal-connection.json").read_text(encoding="utf-8"))
    hostname = "org-" + UUID(str(state["id"])).hex + ".owndesk.work"
    if profile.get("public_hostname") != hostname:
        raise ValueError("Profile hostname does not match the paired installation")
    api_port = profile.get("api_port", 8000)
    if not isinstance(api_port, int) or not 1024 <= api_port <= 65535 or api_port == 5173:
        raise ValueError("Invalid API port")
    if profile.get("protocol", "auto") not in {"auto", "http2", "quic"}:
        raise ValueError("Invalid tunnel protocol")
    return profile


def get_secrets(data, managed=False):
    path = data / "secrets.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if managed and not all(value.get(key) for key in ("POSTGRES_ADMIN_PASSWORD", "POSTGRES_APP_PASSWORD")):
            raise RuntimeError("Managed database credentials are missing; restore the original secrets file")
        return value
    value = {"SECRET_KEY": secrets.token_urlsafe(64),
             "TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
             "ACCOUNT_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode()}
    if managed:
        value.update(POSTGRES_ADMIN_PASSWORD=secrets.token_urlsafe(32), POSTGRES_APP_PASSWORD=secrets.token_urlsafe(32))
    save_private(path, value)
    return value


def environment(data, profile, secret):
    hostname = profile["public_hostname"]
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "DEPLOYMENT_MODE": "organization", "OWNDESK_RUNTIME": "native", "DEBUG": "0",
           "PUBLIC_BASE_URL": "https://" + hostname, "ALLOWED_HOSTS": hostname,
           "CSRF_TRUSTED_ORIGINS": "https://" + hostname, "PRIVATE_MEDIA_ROOT": str(data / "private-attachments"),
           "PORTAL_CONNECTOR_STATE_FILE": str(data / "portal-connection.json"),
           "SECRET_KEY": secret["SECRET_KEY"], "TOKEN_ENCRYPTION_KEY": secret["TOKEN_ENCRYPTION_KEY"],
           "ACCOUNT_ENCRYPTION_KEY": secret["ACCOUNT_ENCRYPTION_KEY"],
           "PUBLIC_REGISTRATION_ENABLED": "0", "LOCAL_EMAIL_PREVIEW": "0", "LOCAL_TELEGRAM_POLLING": "0",
           "TRUST_LOOPBACK_CLIENT_IP": "1", "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
           "TUNNEL_TRANSPORT_PROTOCOL": profile.get("protocol", "auto"), "OWNDESK_DIST_DIR": (ROOT / "frontend/dist").as_posix()}
    env["XDG_DATA_HOME"] = str(data / "cache")
    env["XDG_CONFIG_HOME"] = str(data / "config")
    mail_file = data / "mail-settings.json"
    if mail_file.exists():
        mail = json.loads(mail_file.read_text(encoding="utf-8"))
        allowed = {"EMAIL_HOST", "EMAIL_PORT", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD",
                   "EMAIL_USE_TLS", "EMAIL_USE_SSL", "DEFAULT_FROM_EMAIL"}
        if not isinstance(mail, dict) or mail.keys() - allowed or any(not isinstance(v, str) for v in mail.values()):
            raise ValueError("Invalid private mail settings")
        if mail.get("EMAIL_HOST") and not (mail.get("EMAIL_USE_TLS", "1") == "1" or mail.get("EMAIL_USE_SSL") == "1"):
            raise ValueError("SMTP requires TLS or SSL")
        env.update(mail)
    for key in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ZONE_ID"):
        env.pop(key, None)
    pg = profile.get("postgres")
    if pg:
        for field in ("database", "role", "admin_role"):
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", pg[field]):
                raise ValueError("Invalid PostgreSQL identifier")
        port = pg["port"]
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError("Invalid PostgreSQL port")
        env["DATABASE_URL"] = (f"postgresql://{pg['role']}:{quote(secret['POSTGRES_APP_PASSWORD'], safe='')}"
                               f"@127.0.0.1:{port}/{pg['database']}")
        env["PATH"] = str((ROOT / pg["bin"]).resolve()) + os.pathsep + env.get("PATH", "")
        env.setdefault("PSYCOPG_IMPL", "python")
    elif not env.get("DATABASE_URL", "").startswith(("postgresql://", "postgres://")):
        raise ValueError("Set DATABASE_URL for the external PostgreSQL server")
    return env


def executable(value):
    path = Path(value)
    if not path.is_absolute() and len(path.parts) > 1:
        value = str(ROOT / path)
    resolved = shutil.which(value)
    if not resolved:
        raise RuntimeError("Required executable is not available: " + value)
    return str(Path(resolved).resolve())


def command(args, env, cwd=ROOT):
    subprocess.run([str(arg) for arg in args], env=env, cwd=cwd, check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def stop_process(process):
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def launch(stack, data, name, args, env, *, stop=True):
    log = stack.enter_context((data / (name + ".log")).open("ab"))
    process = subprocess.Popen([str(arg) for arg in args], cwd=ROOT / "backend", env=env, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=os.name != "nt",
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if stop:
        stack.callback(stop_process, process)
    return process


def managed_database(stack, data, pg, secret, env):
    suffix = ".exe" if os.name == "nt" else ""
    binaries = (ROOT / pg["bin"]).resolve()
    cluster = data / "pgdata"
    if not (cluster / "PG_VERSION").exists():
        password_file = data / (".initdb-" + secrets.token_hex(8))
        try:
            with os.fdopen(os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
                out.write(secret["POSTGRES_ADMIN_PASSWORD"] + "\n")
            command([binaries / ("initdb" + suffix), "-D", cluster, "-U", pg["admin_role"],
                     "--auth=scram-sha-256", "--encoding=UTF8", "--no-locale", "--pwfile", password_file], env)
        finally:
            password_file.unlink(missing_ok=True)
    process = launch(stack, data, "postgres", [binaries / ("postgres" + suffix), "-D", cluster,
                     "-h", "127.0.0.1", "-p", str(pg["port"])], env, stop=False)

    def stop_database():
        if process.poll() is None:
            command([binaries / ("pg_ctl" + suffix), "-D", cluster, "-m", "fast", "-w", "stop"], env)
            process.wait(timeout=20)

    stack.callback(stop_database)
    os.environ["PATH"] = env["PATH"]
    os.environ.setdefault("PSYCOPG_IMPL", "python")
    import psycopg
    from psycopg import sql
    deadline = time.monotonic() + 30
    while True:
        if process.poll() is not None:
            raise RuntimeError("PostgreSQL stopped; see postgres.log")
        try:
            connection = psycopg.connect(host="127.0.0.1", port=pg["port"], dbname="postgres", user=pg["admin_role"],
                                          password=secret["POSTGRES_ADMIN_PASSWORD"], autocommit=True, connect_timeout=2)
            break
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise RuntimeError("PostgreSQL did not become ready") from None
            time.sleep(.2)
    with connection:
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (pg["role"],)).fetchone():
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(pg["role"]), sql.Literal(secret["POSTGRES_APP_PASSWORD"])))
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (pg["database"],)).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(pg["database"]), sql.Identifier(pg["role"])))
    return process


def prepare(data, code, caddy, cloudflared, protocol, postgres=None):
    import httpx
    target = data / "portal-connection.json"
    state = json.loads(target.read_text()) if target.exists() else None
    if state and not state.get("id") and code and state.get("code") != code:
        state = None
    if not state:
        if not 32 <= len(code) <= 128:
            raise ValueError("A valid pairing code from the owner portal is required")
        state = {"code": code, "connector_key": secrets.token_urlsafe(48)}
        save_private(target, state)
    root = "https://owndesk.work/api/v1/portal/installations"
    with httpx.Client(timeout=90, follow_redirects=False, trust_env=False) as client:
        if not state.get("id"):
            response = client.post(root + "/pair", json={"code": state["code"], "connector_key": state["connector_key"],
                "version": (ROOT / "VERSION").read_text().strip()})
            response.raise_for_status()
            state = {"id": response.json()["id"], "connector_key": state["connector_key"]}
            save_private(target, state)
        response = client.post(root + "/" + str(state["id"]) + "/configuration",
                               headers={"Authorization": "Bearer " + state["connector_key"]})
        response.raise_for_status()
        hostname = response.json()["hostname"]
        if hostname != "org-" + UUID(str(state["id"])).hex + ".owndesk.work":
            raise ValueError("Unexpected portal hostname")
    state["public_hostname"] = hostname
    save_private(target, state)
    profile_path = data / "runtime-profile.json"
    if profile_path.exists() and not (data / "secrets.json").exists():
        raise RuntimeError("Existing profile has no private secrets; restore the original secrets file")
    profile = json.loads(profile_path.read_text()) if profile_path.exists() else {"format": 1, "api_port": 8000}
    if postgres is not None:
        if profile_path.exists() and profile.get("postgres") != postgres:
            raise ValueError("Refusing to change an existing database profile; migrate it explicitly")
        profile["postgres"] = postgres
    if profile.get("public_hostname", hostname) != hostname:
        raise ValueError("Refusing to rebind an existing profile to another installation")
    profile.update(public_hostname=hostname, caddy=caddy, cloudflared=cloudflared, protocol=protocol)
    get_secrets(data, managed=bool(profile.get("postgres")))
    save_private(profile_path, profile)
    print("Address prepared: https://" + hostname + ". The protected setup wizard starts with the runtime.")


def run(data):
    from local_snapshot import local_runtime_lock
    with local_runtime_lock(data / "runtime-supervisor"):
        profile = profile_for(data)
        if not (data / "secrets.json").exists():
            raise RuntimeError("Private runtime secrets are missing; restore the original file instead of generating replacement keys")
        secret = get_secrets(data, managed=bool(profile.get("postgres")))
        env = environment(data, profile, secret)
        caddy, cloudflared = executable(profile.get("caddy", "caddy")), executable(profile.get("cloudflared", "cloudflared"))
        ports = [profile.get("api_port", 8000), 5173]
        if profile.get("postgres"):
            ports.append(profile["postgres"]["port"])
        for port in ports:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        stop = threading.Event()
        stop_file = data / 'stop-request'
        stop_file.unlink(missing_ok=True)
        old_term = signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            with ExitStack() as stack:
                processes = []
                if profile.get("postgres"):
                    processes.append(managed_database(stack, data, profile["postgres"], secret, env))
                    from native_restore import initial_restore
                    initial_restore(data, profile, secret, env)
                manage = [sys.executable, ROOT / "backend/manage.py"]
                command([*manage, "migrate", "--noinput"], env)
                command([*manage, "check", "--deploy", "--fail-level", "WARNING"], env)
                command([*manage, "prepare_installation", "--output", data / "installation-code.txt"], env)
                config = (ROOT / "infra/Caddyfile.connector").read_text()
                config = config.replace("{\n", "{\n    admin off\n    persist_config off\n", 1)
                config = config.replace("127.0.0.1:8000", f"127.0.0.1:{ports[0]}")
                proxy_file = data / "Caddyfile"
                proxy_file.write_text(config)
                processes.append(launch(stack, data, "api", [*manage, "run_native", "--port", str(ports[0])], env))
                processes.append(launch(stack, data, "caddy", [caddy, "run", "--config", proxy_file, "--adapter", "caddyfile"], env))
                processes.append(launch(stack, data, "connector", [*manage, "run_connector", "--state", data / "portal-connection.json",
                    "--cloudflared", cloudflared], env))
                print("OwnDesk runtime started. Public address: " + env["PUBLIC_BASE_URL"], flush=True)
                while not stop.wait(1):
                    if stop_file.exists():
                        stop_file.unlink(missing_ok=True)
                        break
                    if any(process.poll() is not None for process in processes):
                        raise RuntimeError("A runtime component stopped; see the private logs")
        finally:
            signal.signal(signal.SIGTERM, old_term)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "stop", "setup-link"))
    parser.add_argument("--data", default=os.getenv("OWNDESK_DATA_DIR", str(ROOT / ".runtime/native")))
    parser.add_argument("--caddy", default="caddy")
    parser.add_argument("--cloudflared", default="cloudflared")
    parser.add_argument("--protocol", choices=("auto", "http2", "quic"), default="auto")
    parser.add_argument("--pair-code-stdin", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--postgres-bin")
    parser.add_argument("--postgres-port", type=int, default=55486)
    args = parser.parse_args()
    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    if args.action == "prepare":
        state_path = data / "portal-connection.json"
        existing = json.loads(state_path.read_text()) if state_path.exists() else {}
        code = "" if existing.get('id') else (
            sys.stdin.readline().strip() if args.pair_code_stdin else getpass.getpass("OwnDesk pairing code: ").strip())
        pg = {"bin": args.postgres_bin, "port": args.postgres_port, "database": "owndesk_org",
              "role": "owndesk_org_app", "admin_role": "owndesk_org_dba"} if args.postgres_bin else None
        prepare(data, code, args.caddy, args.cloudflared, args.protocol, pg)
    elif args.action == "stop":
        (data / 'stop-request').touch()
    elif args.action == "setup-link":
        profile = profile_for(data)
        if args.require_ready:
            status = json.loads((data / "portal-connection.json.status.json").read_text())
            if status.get("state") != "active" or not 0 <= time.time() - float(status.get("checked_at", 0)) < 90:
                raise RuntimeError("Public setup address is not ready yet")
        origin = "https://" + profile["public_hostname"]
        code = data / "installation-code.txt"
        print(origin + ("/#" + urlencode({"setup": code.read_text().strip()}) if code.exists() else "/"))
    else:
        run(data)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print("OwnDesk: " + str(error), file=sys.stderr)
        raise SystemExit(1)
