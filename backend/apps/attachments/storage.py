import boto3
from django.conf import settings
import os
import tempfile
from pathlib import Path


class LocalMedia:
    """Private local storage for the loopback installation; served only via the authenticated API."""
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, key):
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Invalid attachment key")
        return path

    def put_object(self, *, Bucket, Key, Body, ContentType):
        path = self.path(Key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                tmp = Path(handle.name)
                if hasattr(Body, 'read'):
                    for chunk in iter(lambda: Body.read(1024 * 1024), b''):
                        handle.write(chunk)
                else:
                    handle.write(Body)
            os.replace(tmp, path)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def get_object(self, *, Bucket, Key):
        return {'Body': self.path(Key).open('rb')}

    def delete_object(self, *, Bucket, Key):
        self.path(Key).unlink(missing_ok=True)


def storage():
    if settings.OWNDESK_RUNTIME == "native":
        if not settings.PRIVATE_MEDIA_ROOT or not Path(settings.PRIVATE_MEDIA_ROOT).is_absolute():
            raise RuntimeError("Native runtime requires an absolute PRIVATE_MEDIA_ROOT")
        return LocalMedia(settings.PRIVATE_MEDIA_ROOT)
    if settings.LOCAL_TELEGRAM_POLLING and not settings.S3_ACCESS_KEY and not settings.S3_SECRET_KEY:
        return LocalMedia(Path(settings.DATABASES['default']['NAME']).parent / 'private-attachments')
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name="us-east-1",
    )


def detect_type(blob):
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if blob.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if blob.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if blob.startswith(b"RIFF") and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob.startswith(b"%PDF-"):
        return "application/pdf"
    if blob.startswith(b"OggS"):
        return "audio/ogg"
    if blob.startswith(b"ID3"):
        return "audio/mpeg"
    if blob[4:8] == b"ftyp":
        return "video/mp4"
    if blob.startswith(b"PK\x03\x04"):
        return "application/zip"
    try:
        blob.decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return "application/octet-stream"
