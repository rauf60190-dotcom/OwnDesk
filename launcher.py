"""Prepare and run the Windows distribution without a PowerShell script."""

import argparse
from contextlib import ExitStack
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys
import traceback
import socket


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"


def choose_port(runtime):
    preferred = []
    try:
        preferred.append(int((runtime / "local-port").read_text().strip()))
    except (OSError, ValueError):
        pass
    for port in dict.fromkeys([*preferred, 5173, *range(5273, 5294)]):
        if not 1024 <= port <= 65535:
            continue
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError("No free local port found for the setup wizard.")


def command(args, env, log, cwd=ROOT):
    process = subprocess.Popen(
        [str(arg) for arg in args], cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        result = process.wait()
        if result:
            raise RuntimeError(f"Process exited with code {result}. See .runtime/startup.log.")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=30)
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("organization", "demo"), default="organization")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        print("OwnDesk requires Python 3.12.", flush=True)
        return 1
    RUNTIME.mkdir(exist_ok=True)
    sys.path.insert(0, str(ROOT / "backend"))
    from local_snapshot import local_runtime_lock, SnapshotError

    with (RUNTIME / "startup.log").open("a", encoding="utf-8") as log:
        try:
            with ExitStack() as stack:
                stack.enter_context(local_runtime_lock(RUNTIME / "launcher"))
                # Refuse to migrate a database currently used by an older launcher.
                with local_runtime_lock(RUNTIME / "demo.sqlite3"):
                    pass
                port = choose_port(RUNTIME)
                (RUNTIME / "local-port").write_text(str(port), encoding="ascii")
                origin = f"http://127.0.0.1:{port}"
                env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
                python = RUNTIME / "venv/Scripts/python.exe"
                if not python.exists():
                    print("OwnDesk: creating Python environment...", flush=True)
                    command([sys.executable, "-m", "venv", RUNTIME / "venv"], env, log)
                requirements = ROOT / "backend/requirements.txt"
                signature = sha256(requirements.read_bytes()).hexdigest()
                stamp = RUNTIME / "dependencies.sha256"
                if not stamp.exists() or stamp.read_text().strip() != signature:
                    print("OwnDesk: installing dependencies. Internet is required on first launch.", flush=True)
                    command([python, "-m", "pip", "install", "-r", requirements], env, log)
                    stamp.write_text(signature, encoding="ascii")
                env.update(
                    DEBUG="1", DEPLOYMENT_MODE=args.mode, DATABASE_URL="",
                    LOCAL_DB_PATH=str(RUNTIME / "demo.sqlite3"), LOCAL_CHANNELS="1",
                    LOCAL_TELEGRAM_POLLING="1", PUBLIC_BASE_URL=origin,
                    CSRF_TRUSTED_ORIGINS=f"{origin},http://localhost:{port}",
                    PORTAL_CONNECTOR_STATE_FILE=str(RUNTIME / "portal-connection.json"),
                )
                env.setdefault("PUBLIC_REGISTRATION_ENABLED", "1")
                env.setdefault("LOCAL_EMAIL_PREVIEW", "1")
                for filename, variable in (
                    ("telegram.key", "TOKEN_ENCRYPTION_KEY"),
                    ("account-security.key", "ACCOUNT_ENCRYPTION_KEY"),
                ):
                    path = RUNTIME / filename
                    if not path.exists():
                        command([python, "-c",
                                 "from cryptography.fernet import Fernet; import sys; "
                                 "open(sys.argv[1], 'xb').write(Fernet.generate_key())", path], env, log)
                    env[variable] = path.read_text(encoding="utf-8")
                manage = [python, "manage.py"]
                backend = ROOT / "backend"
                print("OwnDesk: preparing database...", flush=True)
                command([*manage, "migrate", "--noinput"], env, log, backend)
                if args.mode == "organization":
                    command([*manage, "prepare_installation", "--output", RUNTIME / "installation-code.txt"], env, log, backend)
                else:
                    env.setdefault("ADMIN_PASSWORD", "Local-review-only-2026")
                    command([*manage, "shell", "-c",
                             "from apps.workspaces.models import Workspace; "
                             "from django.core.management import call_command; "
                             "call_command('seed_demo') if not Workspace.objects.exists() else None"], env, log, backend)
                print("OWNDESK_URL=" + origin, flush=True)
                print(f"OwnDesk: {origin} — keep this window open. Ctrl+C stops the server.", flush=True)
                command([*manage, "run_local", "--port", str(port), *(["--open-browser"] if args.open_browser else [])], env, log, backend)
        except KeyboardInterrupt:
            print("\nOwnDesk stopped.", flush=True)
        except SnapshotError:
            print("OwnDesk is already running from this installation. Check .runtime/local-port for its address.", flush=True)
            return 1
        except (OSError, RuntimeError):
            traceback.print_exc(file=log)
            print("OwnDesk could not start. Details are saved in " + str(RUNTIME / "startup.log"), flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
