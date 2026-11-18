"""Back up and restore the data created by the packaged local launchers."""
import argparse
from contextlib import closing, contextmanager
from hashlib import file_digest
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory


PROFILES = {
    "organization": ("demo.sqlite3", "telegram.key", "account-security.key"),
    "portal": ("portal.sqlite3", "portal-session.key", "portal-security.key"),
}


class SnapshotError(Exception):
    pass


@contextmanager
def local_runtime_lock(database):
    lock_path = database.with_suffix(".worker.lock")
    check_path(lock_path)
    with lock_path.open("a+b") as lock:
        if not lock.seek(0, os.SEEK_END):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SnapshotError("Stop the local OwnDesk server and retry.") from exc
        yield


def check_path(path):
    if path.is_symlink() or path.is_junction():
        raise SnapshotError("Symbolic links and junctions are unsupported in backup data.")


def data_files(root, profile):
    check_path(root)
    for name in PROFILES[profile]:
        path = root / name
        check_path(path)
        if not path.is_file():
            raise SnapshotError(f"Missing required file: {name}")
        yield path
    connector = root / "portal-connection.json"
    check_path(connector)
    if profile == "organization" and connector.exists():
        if not connector.is_file():
            raise SnapshotError("Invalid connector state file.")
        yield connector
    attachments = root / "private-attachments"
    check_path(attachments)
    if profile == "organization" and attachments.exists():
        if not attachments.is_dir():
            raise SnapshotError("Invalid attachment directory.")
        for directory, dirs, files in os.walk(attachments, onerror=raise_error):
            for name in dirs + files:
                check_path(Path(directory) / name)
            for name in files:
                path = Path(directory) / name
                if not path.is_file():
                    raise SnapshotError("Invalid attachment file.")
                yield path


def raise_error(error):
    raise error


def checksums(root, profile):
    result = {}
    for path in data_files(root, profile):
        with path.open("rb") as stream:
            result[path.relative_to(root).as_posix()] = file_digest(stream, "sha256").hexdigest()
    return result


def check_database(connection, profile, root):
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise SnapshotError("SQLite integrity check failed.")
    has_workspace = connection.execute("SELECT EXISTS(SELECT 1 FROM workspaces_workspace)").fetchone()[0]
    if profile == "portal" and has_workspace:
        raise SnapshotError("The portal backup contains organization data.")
    attachments = root / "private-attachments"
    rows = connection.execute("SELECT object_key, size FROM attachments_attachment WHERE state = 'ready'")
    for key, size in rows:
        path = (attachments / key).resolve()
        if profile != "organization" or not path.is_relative_to(attachments) or not path.is_file() or path.stat().st_size != size:
            raise SnapshotError("A ready attachment is missing or has the wrong size.")


def backup(installation, destination, profile):
    runtime = installation / ".runtime"
    database = runtime / PROFILES[profile][0]
    if destination.is_relative_to(installation):
        raise SnapshotError("Choose a backup location outside the installation directory.")
    if os.path.lexists(destination):
        raise SnapshotError("Choose a new backup directory.")
    check_path(runtime)
    check_path(database)
    if not database.is_file():
        raise SnapshotError(f"Missing required file: {database.name}")
    with local_runtime_lock(database), TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "snapshot"
        staged.mkdir(mode=0o700)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(staged / database.name)) as copy:
                source.backup(copy)
        for path in data_files(runtime, profile):
            if path != database:
                target = staged / path.relative_to(runtime)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        with closing(sqlite3.connect(staged / database.name)) as copy:
            check_database(copy, profile, staged)
        manifest = {"version": 1, "profile": profile, "files": checksums(staged, profile)}
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        publish(staged, destination)


def publish(staged, destination):
    if os.path.lexists(destination):
        raise SnapshotError("Destination already exists.")
    staged.rename(destination)


def restore(installation, source, prepare=None):
    runtime = installation / ".runtime"
    if os.path.lexists(runtime):
        raise SnapshotError("Restore into a freshly unpacked installation without .runtime.")
    check_path(source)
    manifest_path = source / "manifest.json"
    check_path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or manifest.get("profile") not in PROFILES:
        raise SnapshotError("Unsupported backup format.")
    profile = manifest["profile"]
    with TemporaryDirectory(dir=installation) as temporary:
        staged = Path(temporary) / "runtime"
        staged.mkdir(mode=0o700)
        for path in data_files(source, profile):
            target = staged / path.relative_to(source)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        if manifest.get("files") != checksums(staged, profile):
            raise SnapshotError("Backup integrity check failed.")
        with closing(sqlite3.connect(staged / PROFILES[profile][0])) as database, database:
            check_database(database, profile, staged)
            database.execute("DELETE FROM django_session")
        if prepare is not None:
            prepare(staged, profile)
        publish(staged, runtime)
    return profile


def verify_upgrade(installation, staged, profile):
    backend = installation / "backend"
    venv = staged.parent / "verification-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "-q", "-r",
                    str(backend / "requirements.txt")], check=True)
    database = staged / PROFILES[profile][0]
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DEBUG": "1",
        "DEPLOYMENT_MODE": profile,
        "DATABASE_URL": "",
        "LOCAL_DB_PATH": str(database),
        "LOCAL_CHANNELS": "1",
        "LOCAL_TELEGRAM_POLLING": "0",
        "EMAIL_HOST": "",
        "ACCOUNT_ENCRYPTION_KEY": (staged / PROFILES[profile][2]).read_text(encoding="utf-8"),
    }
    if profile == "organization":
        env["TOKEN_ENCRYPTION_KEY"] = (staged / "telegram.key").read_text(encoding="utf-8")
    else:
        env["SECRET_KEY"] = (staged / "portal-session.key").read_text(encoding="utf-8")
    subprocess.run([str(python), str(backend / "manage.py"), "migrate_local_upgrade"],
                   env=env, cwd=backend, check=True)
    with closing(sqlite3.connect(database)) as connection:
        check_database(connection, profile, staged)


def upgrade(installation, previous, destination, profile):
    if installation.is_relative_to(previous) or previous.is_relative_to(installation):
        raise SnapshotError("Choose two separate installation directories.")
    if os.path.lexists(installation / ".runtime"):
        raise SnapshotError("Upgrade into a freshly unpacked installation without .runtime.")
    if destination.is_relative_to(installation):
        raise SnapshotError("Choose a backup location outside the new installation directory.")
    if not (installation / "frontend/dist/index.html").is_file():
        raise SnapshotError("Unpack the full release, including the built frontend.")
    backup(previous, destination, profile)
    print("Rollback backup saved. Checking the new installation...", flush=True)
    return restore(installation, destination,
                   prepare=lambda staged, mode: verify_upgrade(installation, staged, mode))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore", "upgrade"])
    parser.add_argument("path", type=Path, help="New backup directory, or existing backup to restore")
    parser.add_argument("--profile", choices=PROFILES, default="organization", help="Profile to back up")
    parser.add_argument("--from-installation", type=Path, help="Previous installation for upgrade")
    args = parser.parse_args()
    if (args.action == "upgrade") != (args.from_installation is not None):
        parser.error("--from-installation is required for upgrade and only applies to upgrade.")
    installation = Path(__file__).resolve().parent.parent
    # Preserve the final component so an existing symlink is never used as a destination.
    path = args.path.parent.resolve() / args.path.name
    try:
        if args.action == "backup":
            backup(installation, path, args.profile)
            print("Backup complete.")
        else:
            if args.action == "upgrade":
                profile = upgrade(installation, args.from_installation.resolve(), path, args.profile)
            else:
                profile = restore(installation, path)
            launcher = "Start-Portal.ps1" if profile == "portal" else "Start-OwnDesk.ps1"
            print(f"{args.action.capitalize()} complete. Start with {launcher}.")
    except subprocess.CalledProcessError:
        parser.exit(1, "Upgrade verification failed. The previous installation and backup are available.\n")
    except (SnapshotError, OSError, sqlite3.Error, ValueError, TypeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
