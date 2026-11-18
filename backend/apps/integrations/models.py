from django.db import models
from apps.core.models import Scoped, Entity


class ChannelConnection(Scoped):
    transport = models.CharField(max_length=12, default="webhook")
    bot_id = models.CharField(max_length=40)
    username = models.CharField(max_length=100)
    encrypted_token = models.TextField()
    webhook_secret_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=30, default="webhook_error")
    default_team = models.ForeignKey("teams.Team", on_delete=models.PROTECT)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["bot_id"], name="unique_bot_connection")
        ]


class InboundEvent(Entity):
    connection = models.ForeignKey(ChannelConnection, on_delete=models.CASCADE)
    update_id = models.CharField(max_length=40)
    payload = models.JSONField()
    state = models.CharField(max_length=20, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "update_id"], name="unique_inbound_update"
            )
        ]


class OutboundJob(Entity):
    message = models.OneToOneField(
        "conversations.Message", on_delete=models.CASCADE, related_name="job"
    )
    state = models.CharField(max_length=20, default="queued", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    covered_version = models.PositiveBigIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)


class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=50, primary_key=True)
    seen_at = models.DateTimeField()
