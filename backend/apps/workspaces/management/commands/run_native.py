from pathlib import Path
import threading

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from local_snapshot import local_runtime_lock, SnapshotError


class Command(BaseCommand):
    help = "Serve one production organization with its persisted job worker in a single ASGI process."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8000)

    def handle(self, *args, **options):
        if settings.DEBUG or settings.DEPLOYMENT_MODE != "organization" or settings.OWNDESK_RUNTIME != "native":
            raise CommandError("Requires DEBUG=0, organization mode and OWNDESK_RUNTIME=native.")
        media = Path(settings.PRIVATE_MEDIA_ROOT)
        if not settings.PRIVATE_MEDIA_ROOT or not media.is_absolute():
            raise CommandError("Set an absolute PRIVATE_MEDIA_ROOT outside the frontend directory.")
        frontend = (settings.BASE_DIR.parent / "frontend").resolve()
        if media.resolve().is_relative_to(frontend):
            raise CommandError("Private attachments must not be inside the public frontend directory.")
        if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
            raise CommandError("The native production runtime requires PostgreSQL.")
        media.mkdir(parents=True, exist_ok=True)
        try:
            with local_runtime_lock(media / "native-server"):
                self.serve(options["port"])
        except SnapshotError:
            raise CommandError("This native installation is already running.") from None

    def serve(self, port):
        from apps.integrations.native_worker import run
        from apps.accounts.tasks import run_local_mail
        from config.asgi import application
        from daphne.server import Server
        stop = threading.Event()
        threads = [threading.Thread(target=fn, args=(stop,), daemon=True) for fn in (run, run_local_mail)]
        for thread in threads:
            thread.start()
        try:
            Server(application=application, endpoints=[f"tcp:port={port}:interface=127.0.0.1"]).run()
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=20)
