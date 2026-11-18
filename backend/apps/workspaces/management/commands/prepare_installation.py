import secrets
from pathlib import Path
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from apps.accounts.registration import digest
from apps.workspaces.models import Workspace, InstallationSetup


class Command(BaseCommand):
    help="Create a private one-use installation code, valid for 24 hours."

    def add_arguments(self,parser):
        parser.add_argument("--output",required=True)
        parser.add_argument("--rotate",action="store_true")

    def handle(self,*args,**options):
        if settings.DEPLOYMENT_MODE != "organization":raise CommandError("Use organization mode.")
        target=Path(options["output"]).resolve()
        with transaction.atomic():
            if Workspace.objects.exists():
                self.stdout.write("Organization already configured; setup remains closed.");return
            grant,_=InstallationSetup.objects.select_for_update().get_or_create(pk=1,defaults={"token_hash":"","expires_at":timezone.now()})
            if not options["rotate"] and grant.expires_at>timezone.now() and target.exists():
                if secrets.compare_digest(grant.token_hash,digest(target.read_text(encoding="utf-8").strip())):
                    self.stdout.write("Existing installation code retained.");return
            code=secrets.token_urlsafe(32)
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(code,encoding="utf-8");target.chmod(0o600)
            grant.token_hash=digest(code);grant.expires_at=timezone.now()+timedelta(hours=24);grant.completed_at=None;grant.save()
        self.stdout.write("Installation code written to the private output file. Do not publish it.")
