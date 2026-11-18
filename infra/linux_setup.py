"""Guided Compose deployment on a Linux host; keeps all generated secrets private."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import re
from contextlib import contextmanager
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler


def atomic_private(path, content):
    temp = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def operation_lock(root, create=False):
    """Serialize installer and backup mutations on the actual Linux host."""
    if os.name != 'posix':
        # The Ubuntu shell entrypoint refuses other OSes; pure logic tests run on Windows.
        yield
        return
    import fcntl
    import stat
    state = root / '.runtime/linux'
    for path in [state, *state.absolute().parents]:
        if path.is_symlink():
            raise ValueError('Installation paths must not contain links')
    if not state.exists():
        if not create:
            raise RuntimeError('Run install first; no installation state exists')
        state.mkdir(parents=True, mode=0o700)
    descriptor = os.open(state / 'maintenance.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Maintenance lock must be a private regular file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another OwnDesk maintenance operation is running') from None
        yield
    finally:
        os.close(descriptor)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def public_ready(link):
    """Check the actual public API; never send the private setup fragment to a server."""
    value = urlsplit(link)
    if (value.scheme != 'https' or not re.fullmatch(r'org-[a-f0-9]{32}\.owndesk\.work', value.netloc)
            or value.path != '/' or value.query):
        raise ValueError('Unexpected organization setup address')
    if value.fragment:
        fragment = parse_qs(value.fragment, strict_parsing=True)
        if (set(fragment) != {'setup'} or len(fragment['setup']) != 1
                or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', fragment['setup'][0])):
            raise ValueError('Unexpected setup fragment')
    origin = 'https://' + value.netloc
    endpoint = origin + '/api/v1/health/live'
    request = Request(endpoint, headers={'User-Agent': 'OwnDesk/' + (ROOT / 'VERSION').read_text().strip() + ' Installer',
                                        'Accept': 'application/json'})
    with build_opener(NoRedirect()).open(request, timeout=5) as response:
        data = response.read(4097)
        if (response.status != 200 or response.geturl() != endpoint or len(data) > 4096
                or json.loads(data).get('status') != 'alive'):
            raise OSError('Public organization API is not ready')
    return link


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "start", "stop", "status", "setup-link"), nargs="?", default="install")
    parser.add_argument("--protocol", choices=("auto", "http2", "quic"), default="auto")
    args = parser.parse_args()
    with operation_lock(ROOT, create=args.action == 'install'):
        if (ROOT / '.runtime/linux/backup-recovery.json').exists():
            raise RuntimeError('Interrupted backup detected; run sudo bash OwnDesk-Backup.sh resume first')
        perform(args)


def perform(args):
    state = ROOT / ".runtime/linux"
    if args.action != 'install' and not (state / 'installation.json').is_file():
        raise RuntimeError('Run install first; management commands do not create an installation')
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    settings = state / "installation.json"
    env_file = state / "compose.env"
    if not settings.exists():
        if args.action != "install":
            raise RuntimeError("Run install first")
        if env_file.exists():
            raise RuntimeError("Existing credentials have no installation metadata; restore metadata before continuing")
        atomic_private(settings, json.dumps({"project": "owndesk-" + secrets.token_hex(6),
            "db_admin_password": secrets.token_urlsafe(32), "db_app_password": secrets.token_urlsafe(32)}))
    config = json.loads(settings.read_text())
    project = config["project"]
    if not re.fullmatch(r"owndesk-[0-9a-f]{12}", project):
        raise RuntimeError("Invalid project name")
    for field in ("db_admin_password", "db_app_password"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", config[field]):
            raise RuntimeError("Invalid private database credentials")
    atomic_private(env_file, "POSTGRES_PASSWORD=" + config["db_admin_password"] + "\nPOSTGRES_APP_PASSWORD="
                   + config["db_app_password"] + "\nOWNDESK_VERSION=" + (ROOT / "VERSION").read_text().strip() + "\n")
    base = ["docker", "compose", "--project-name", project, "--project-directory", str(ROOT),
            "--env-file", str(env_file), "-f", str(ROOT / "compose.native.yaml")]

    def compose(*parts, capture=False, stdin=None):
        return subprocess.run([*base, *parts], cwd=ROOT, check=True, input=stdin, text=True,
                              stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None)

    compose("config", "--quiet")
    if args.action == "status":
        compose("ps", "--all")
        return
    if args.action == "stop":
        compose("stop")
        return
    if args.action == "start":
        compose("up", "-d")
    if args.action == "install":
        compose("build", "app")
        compose("up", "-d", "--wait", "--wait-timeout", "120", "db")
        compose("run", "--rm", "--no-deps", "init-data")
        code = getpass.getpass("Код подключения из кабинета owndesk.work (Enter при повторном запуске): ").strip()
        compose("run", "--rm", "--no-deps", "-T", "app", "python", "infra/native_runtime.py", "prepare",
                "--data", "/data", "--pair-code-stdin", "--protocol", args.protocol, stdin=code + "\n")
        compose("up", "-d")
    print("Ожидаем запуска сервера и подготовки кода мастера…", flush=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            result = compose("exec", "-T", "app", "python", "infra/native_runtime.py", "setup-link", "--data", "/data", "--require-ready", capture=True)
            if result.stdout.strip().startswith("https://org-"):
                public_ready(result.stdout.strip())
                label = ("Откройте эту персональную ссылку в браузере (не пересылайте её сотрудникам):"
                         if "/#setup=" in result.stdout else "Адрес установленной организации:")
                print(label + "\n" + result.stdout.strip())
                print("Первое соединение и сертификат могут потребовать дополнительного времени. Статус: sudo bash Setup-OwnDesk.sh status")
                return
        except (subprocess.CalledProcessError, OSError, ValueError, TypeError, AttributeError):
            pass
        time.sleep(1)
    raise RuntimeError("Setup did not become ready. Inspect Compose logs; credentials and volumes have been retained.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print("OwnDesk: " + str(error), file=sys.stderr)
        raise SystemExit(1)
