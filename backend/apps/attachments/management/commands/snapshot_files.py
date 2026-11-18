"""Copy the private files referenced by a consistent database snapshot."""

import hashlib
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.attachments.models import Attachment
from apps.attachments.storage import storage


class Command(BaseCommand):
    help = "Export, verify or restore private attachments using a SHA-256 manifest."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["export", "verify", "restore"])
        parser.add_argument("directory", type=Path)

    def handle(self, *args, **options):
        directory = options["directory"].resolve()
        action = options["action"]
        manifest_path = directory / "manifest.json"
        files = Attachment.objects.filter(state="ready").order_by("id")
        if action == "export":
            directory.mkdir(parents=True, exist_ok=True)
            if any(directory.iterdir()):
                raise CommandError("Для экспорта нужен пустой каталог.")
            manifest = []
            client = storage()
            for item in files.iterator():
                body = client.get_object(
                    Bucket=settings.S3_BUCKET, Key=item.object_key
                )["Body"]
                digest = hashlib.sha256()
                size = 0
                with (directory / str(item.id)).open("xb") as dest:
                    for chunk in iter(lambda: body.read(1024 * 1024), b""):
                        dest.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                body.close()
                if item.size != size:
                    raise CommandError(
                        f"Размер объекта {item.id} отличается от записи в базе."
                    )
                manifest.append(
                    {
                        "id": str(item.id),
                        "key": item.object_key,
                        "workspace": str(item.workspace_id),
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            self.stdout.write(f"Экспортировано объектов: {len(manifest)}")
            return

        if not manifest_path.is_file():
            raise CommandError("Отсутствует завершённый манифест копии.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = {str(item.id): item for item in files.iterator()}
        if len(manifest) != len(rows) or {x["id"] for x in manifest} != set(rows):
            raise CommandError("Перечень файлов не соответствует восстановленной базе.")
        # Validate the entire snapshot before writing to storage.
        for entry in manifest:
            row = rows[entry["id"]]
            if (entry["key"], entry["workspace"], entry["size"]) != (
                row.object_key,
                str(row.workspace_id),
                row.size,
            ):
                raise CommandError(f"Метаданные объекта {row.id} расходятся с базой.")
            path = directory / str(row.id)
            if not path.is_file() or path.stat().st_size != row.size:
                raise CommandError(f"Отсутствует файл {row.id} ожидаемого размера.")
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != entry["sha256"]:
                    raise CommandError(f"Не совпадает контрольная сумма {row.id}.")
        if action == "restore":
            client = storage()
            for entry in manifest:
                row = rows[entry["id"]]
                with (directory / str(row.id)).open("rb") as source:
                    client.put_object(
                        Bucket=settings.S3_BUCKET,
                        Key=row.object_key,
                        Body=source,
                        ContentType=row.mime_type,
                    )
        self.stdout.write(f"Проверено объектов: {len(manifest)}; режим: {action}")
