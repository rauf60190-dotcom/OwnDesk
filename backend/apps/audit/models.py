from django.db import models
from apps.core.models import Scoped


class AuditEvent(Scoped):
    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    ticket = models.ForeignKey(
        "tickets.Ticket",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="history",
    )
    action = models.CharField(max_length=80)
    changes = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]
