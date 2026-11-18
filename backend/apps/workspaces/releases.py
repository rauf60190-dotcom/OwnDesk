import hashlib
import json
import re
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


ARCHIVE_NAME = "OwnDesk.zip"


class ReleaseUnavailable(APIException):
    status_code = 503
    default_detail = "Дистрибутив пока недоступен. Попробуйте позже."


class ReleaseMetadata(serializers.Serializer):
    version = serializers.CharField()
    sha256 = serializers.CharField()
    size = serializers.IntegerField()


def open_release():
    if settings.DEPLOYMENT_MODE != "portal":
        raise Http404
    if not settings.PORTAL_RELEASE_DIR:
        raise ReleaseUnavailable
    directory = Path(settings.PORTAL_RELEASE_DIR)
    try:
        data = json.loads((directory / "release.json").read_text(encoding="utf-8"))
        metadata = {key: data[key] for key in ("version", "sha256", "size")}
        if (not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", metadata["version"])
            or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
            or type(metadata["size"]) is not int or metadata["size"] <= 0):
            raise ValueError
        archive = (directory / ARCHIVE_NAME).open("rb")
        try:
            if hashlib.file_digest(archive, "sha256").hexdigest() != metadata["sha256"] or archive.seek(0, 2) != metadata["size"]:
                raise ValueError
            archive.seek(0)
        except Exception:
            archive.close()
            raise
    except (OSError, ValueError, TypeError, KeyError):
        raise ReleaseUnavailable from None
    return metadata, archive


class PortalRelease(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=ReleaseMetadata)
    def get(self, request):
        metadata, archive = open_release()
        archive.close()
        return Response(metadata, headers={"Cache-Control": "private, no-store"})


class PortalReleaseDownload(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={(200, "application/zip"): {"type": "string", "format": "binary"}})
    def get(self, request):
        _, archive = open_release()
        response = FileResponse(archive, as_attachment=True, filename=ARCHIVE_NAME, content_type="application/zip")
        response["Cache-Control"] = "private, no-store"
        return response
