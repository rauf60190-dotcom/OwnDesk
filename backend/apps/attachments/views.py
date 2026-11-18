from apps.core.contracts import documented
import uuid
from pathlib import PurePath
from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from apps.core.access import member, tickets_for
from apps.tickets.serializers import AttachmentSerializer
from .models import Attachment
from .storage import storage, detect_type


@documented
class UploadView(APIView):
    def post(self, request):
        m = member(request)
        if m.role == "observer":
            raise PermissionDenied()
        f = request.FILES.get("file")
        if not f or f.size > settings.MAX_UPLOAD_BYTES:
            raise ValidationError("Выберите файл не более 20 МБ.")
        blob = f.read()
        key = f"{m.workspace_id}/{uuid.uuid4()}"
        mime = detect_type(blob)
        try:
            storage().put_object(
                Bucket=settings.S3_BUCKET, Key=key, Body=blob, ContentType=mime
            )
        except Exception:
            raise ValidationError("Хранилище недоступно. Файл не загружен.") from None
        a = Attachment.objects.create(
            workspace=m.workspace,
            uploaded_by=m.user,
            filename=PurePath(f.name).name[:240],
            size=len(blob),
            mime_type=mime,
            object_key=key,
            state="ready",
        )
        return Response(AttachmentSerializer(a).data, status=201)


@documented
class DownloadView(APIView):
    def get(self, request, pk):
        m = member(request)
        qs = Attachment.objects.filter(workspace=m.workspace, state="ready")
        from django.db.models import Q

        qs = qs.filter(
            Q(message__ticket_id__in=tickets_for(m).values("id"))
            | Q(message=None, uploaded_by=m.user)
        )
        a = get_object_or_404(qs, pk=pk)
        return attachment_response(a, request.query_params.get("preview") == "1")


def attachment_response(a, preview):
    try:
        obj = storage().get_object(Bucket=settings.S3_BUCKET, Key=a.object_key)
    except Exception:
        raise ValidationError("Файл временно недоступен.") from None
    response = FileResponse(
        obj["Body"],
        as_attachment=not (preview and a.mime_type in {
            "image/jpeg", "image/png", "image/gif", "image/webp", "audio/ogg", "audio/mpeg", "video/mp4"
        }),
        filename=a.filename,
        content_type=a.mime_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
