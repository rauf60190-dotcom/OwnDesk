from django.core.management.base import BaseCommand, CommandError
from uuid import UUID

from apps.workspaces.models import ConnectedInstallation
from apps.workspaces.tunnels import prepare_route, TunnelError


class Command(BaseCommand):
    help = "Prepare an installation tunnel and DNS. Traffic stays blocked until secure activation."

    def add_arguments(self, parser):
        parser.add_argument("installation_id", type=UUID)

    def handle(self, *args, **options):
        try:
            route = prepare_route(options["installation_id"])
        except (TunnelError, ConnectedInstallation.DoesNotExist, ValueError) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"Адрес подготовлен: https://{route.hostname}. Доступ пока закрыт; требуется активация защищённого коннектора.")
