from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.loader import MigrationLoader


class Command(BaseCommand):
    help = "Check migration compatibility before upgrading a staged local SQLite copy."

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Local upgrade requires a staged SQLite database.")
        loader = MigrationLoader(connection)
        known = set(loader.disk_migrations)
        for migration in loader.replacements.values():
            known.update(migration.replaces)
        if set(loader.applied_migrations) - known:
            raise CommandError("The backup contains migrations missing from this release.")
        call_command("migrate", interactive=False)
        call_command("check")
