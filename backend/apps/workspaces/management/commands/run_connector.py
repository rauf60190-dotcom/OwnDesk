from pathlib import Path
import shutil
import signal
import threading

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.workspaces.connector_runtime import ConnectorRuntime
from local_snapshot import local_runtime_lock, SnapshotError


class Command(BaseCommand):
    help = "Keep the dedicated OwnDesk tunnel running for an already configured production server."

    def add_arguments(self, parser):
        parser.add_argument("--state", required=True)
        parser.add_argument("--cloudflared", default="cloudflared")

    def handle(self, *args, **options):
        if settings.DEBUG or settings.DEPLOYMENT_MODE != "organization":
            raise CommandError("The public connector requires an organization server with DEBUG=0.")
        target = Path(options["state"]).resolve()
        if not settings.PORTAL_CONNECTOR_STATE_FILE or Path(settings.PORTAL_CONNECTOR_STATE_FILE).resolve() != target:
            raise CommandError("PORTAL_CONNECTOR_STATE_FILE must match --state on the server and connector.")
        if not target.is_file():
            raise CommandError("Pair this installation with the portal first.")
        executable = shutil.which(options["cloudflared"])
        if not executable:
            raise CommandError("Install cloudflared and set --cloudflared to its executable path.")
        stop = threading.Event()
        previous = signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            with local_runtime_lock(target.with_name(target.name + ".supervisor")):
                self.stdout.write("Коннектор запущен. Состояние сохраняется рядом с закрытым файлом привязки.")
                runtime = ConnectorRuntime(target, executable)
                try:
                    runtime.run(stop)
                except KeyboardInterrupt:
                    stop.set()
                    runtime.stop_tunnel()
                    runtime.status("stopped")
        except SnapshotError:
            raise CommandError("The connector is already running for this installation.") from None
        finally:
            signal.signal(signal.SIGTERM, previous)
