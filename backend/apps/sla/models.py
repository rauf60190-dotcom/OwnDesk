from django.db import models
from apps.core.models import Scoped


class SLAPolicy(Scoped):
    priority = models.CharField(max_length=12)
    first_response_minutes = models.PositiveIntegerField(default=15)
    next_response_minutes = models.PositiveIntegerField(default=30)
    resolution_minutes = models.PositiveIntegerField(default=480)
    schedule = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "priority"], name="unique_sla_priority"
            )
        ]


class SLAInstance(Scoped):
    ticket = models.ForeignKey(
        "tickets.Ticket", on_delete=models.CASCADE, related_name="deadlines"
    )
    kind = models.CharField(max_length=20)
    started_at = models.DateTimeField()
    deadline = models.DateTimeField(db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_seconds = models.PositiveIntegerField(default=0)
    budget_minutes = models.PositiveIntegerField()
    opened_version = models.PositiveBigIntegerField(default=0)
    accounted_at = models.DateTimeField(null=True, blank=True)
    spent_seconds = models.PositiveBigIntegerField(default=0)
    warning_sent = models.BooleanField(default=False)
    schedule = models.JSONField(default=dict)
    breached = models.BooleanField(default=False)
