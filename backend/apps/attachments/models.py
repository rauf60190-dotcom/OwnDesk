from django.db import models
from apps.core.models import Scoped


class Attachment(Scoped):
    message = models.ForeignKey(
        "conversations.Message",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    filename = models.CharField(max_length=240)
    size = models.PositiveBigIntegerField(default=0)
    mime_type = models.CharField(max_length=100, blank=True)
    object_key = models.CharField(max_length=300, blank=True)
    telegram_file_id = models.CharField(max_length=300, blank=True)
    state = models.CharField(max_length=20, default="pending")
    error = models.CharField(max_length=200, blank=True)
