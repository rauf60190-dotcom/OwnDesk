import getpass
import os
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from apps.accounts.models import User
from apps.workspaces.models import Workspace, Membership
from apps.teams.models import Team, TeamMember
from apps.sla.models import SLAPolicy
from apps.sla.services import DEFAULTS


class Command(BaseCommand):
    help = "Создать первое пространство и администратора. Пароль читается из ADMIN_PASSWORD или скрытого ввода."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument("--workspace", default="Служба поддержки")

    @transaction.atomic
    def handle(self, *args, **options):
        if Workspace.objects.exists():
            raise CommandError(
                "Пространство уже создано. Используйте управление сотрудниками."
            )
        password = os.getenv("ADMIN_PASSWORD") or getpass.getpass(
            "Пароль администратора: "
        )
        validate_password(password)
        user = User.objects.create_user(
            username=options["username"],
            password=password,
            display_name="Администратор",
        )
        ws = Workspace.objects.create(name=options["workspace"])
        m = Membership.objects.create(workspace=ws, user=user, role="admin", is_owner=True)
        team = Team.objects.create(workspace=ws, name="Поддержка")
        TeamMember.objects.create(team=team, membership=m)
        for priority, (a, b, c) in DEFAULTS.items():
            SLAPolicy.objects.create(
                workspace=ws,
                priority=priority,
                first_response_minutes=a,
                next_response_minutes=b,
                resolution_minutes=c,
            )
        self.stdout.write(self.style.SUCCESS("Пространство и администратор созданы."))
