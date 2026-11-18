import threading
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlencode
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from apps.integrations.local_worker import run
from local_snapshot import local_runtime_lock, SnapshotError


class Command(BaseCommand):
    help = "Локальный интерфейс и обработчик текстовых сообщений Telegram"

    def add_arguments(self, parser):
        parser.add_argument("--port",type=int,default=5173)
        parser.add_argument("--open-browser", action="store_true")

    def handle(self, *args, **kwargs):
        if settings.DEPLOYMENT_MODE == "portal":
            from apps.workspaces.models import Workspace
            if Workspace.objects.exists():
                raise CommandError("Portal requires a separate database without organization workspaces.")
        if not settings.LOCAL_TELEGRAM_POLLING:
            raise CommandError("Локальный режим Telegram отключён.")
        try:
            with local_runtime_lock(Path(settings.DATABASES["default"]["NAME"])):
                self.serve(kwargs["port"], kwargs["open_browser"])
        except SnapshotError as exc:
            raise CommandError(str(exc)) from exc

    def serve(self, port, open_browser=False):
        stop = threading.Event()
        thread = threading.Thread(target=run, args=(stop,), daemon=True)
        from apps.accounts.tasks import run_local_mail
        mail_thread = threading.Thread(target=run_local_mail, args=(stop,), daemon=True)
        from apps.workspaces.connection import monitor_connection
        connector_thread = threading.Thread(target=monitor_connection, args=(stop,), daemon=True)
        mail_thread.start()
        connector_thread.start()
        thread.start()
        browser_thread = None
        if open_browser:
            browser_thread = threading.Thread(target=self.open_when_ready, args=(port, stop), daemon=True)
            browser_thread.start()
        try:
            call_command("runserver", f"127.0.0.1:{port}", use_reloader=False)
        finally:
            stop.set()
            thread.join(timeout=30)
            mail_thread.join(timeout=20)
            connector_thread.join(timeout=20)
            if browser_thread:
                browser_thread.join(timeout=2)

    @staticmethod
    def open_when_ready(port, stop):
        origin = f"http://127.0.0.1:{port}"
        for _ in range(60):
            if stop.is_set():
                return
            try:
                with urlopen(origin + "/api/v1/health/live", timeout=1):
                    target = origin
                from apps.workspaces.models import Workspace
                code_file = Path(settings.DATABASES["default"]["NAME"]).parent / "installation-code.txt"
                if settings.DEPLOYMENT_MODE == "organization" and not Workspace.objects.exists() and code_file.is_file():
                    target += "/#" + urlencode({"setup": code_file.read_text(encoding="utf-8").strip()})
                webbrowser.open(target)
                return
            except (URLError, OSError):
                if stop.wait(.5):
                    return
