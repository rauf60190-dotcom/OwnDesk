from django.db import models
from apps.core.models import Entity, Scoped


class Workspace(Entity):
    name = models.CharField(max_length=200)
    timezone = models.CharField(max_length=100, default="Asia/Samarkand")
    next_ticket_number = models.PositiveBigIntegerField(default=1)
    settings = models.JSONField(default=dict)


class Membership(Scoped):
    ROLES = [(r, r) for r in ["admin", "manager", "operator", "observer"]]
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    role = models.CharField(max_length=16, choices=ROLES, default="operator")
    active = models.BooleanField(default=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    is_owner = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(is_owner=False) | models.Q(role="admin", active=True), name="owner_is_active_admin"),
            models.UniqueConstraint(
                fields=["workspace", "user"], name="unique_workspace_member"
            )
        ]


class Invitation(Scoped):
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=Membership.ROLES, default="operator")
    team = models.ForeignKey("teams.Team", on_delete=models.PROTECT)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    mail_body = models.TextField(blank=True, default="")
    mail_state = models.CharField(max_length=16, default="manual")
    mail_started_at = models.DateTimeField(null=True)


class InstallationSetup(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    token_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)


class ConnectedInstallation(Entity):
    owner = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="installations")
    name = models.CharField(max_length=200)
    code_hash = models.CharField(max_length=64, unique=True)
    code_expires_at = models.DateTimeField()
    paired_at = models.DateTimeField(null=True)
    connector_hash = models.CharField(max_length=64, blank=True)
    version = models.CharField(max_length=32, blank=True)
    last_seen_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)


class InstallationRoute(models.Model):
    installation = models.OneToOneField(ConnectedInstallation, on_delete=models.PROTECT, related_name="route")
    hostname = models.CharField(max_length=253, unique=True)
    tunnel_id = models.UUIDField(null=True)
    dns_record_id = models.CharField(max_length=64, blank=True)
    state = models.CharField(max_length=24, default="pending")
    updated_at = models.DateTimeField(auto_now=True)
    verified_at = models.DateTimeField(null=True)
