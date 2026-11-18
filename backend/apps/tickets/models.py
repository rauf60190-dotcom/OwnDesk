from django.db import models
from apps.core.models import Scoped, Entity

ACTIVE = ["new", "in_progress", "waiting_customer", "waiting_specialist"]
STATUSES = [(s, s) for s in ACTIVE + ["resolved", "closed"]]
ESCALATION_LEVELS = ("l1", "l2", "l3", "development")
PRIORITIES = [(p, p) for p in ["low", "normal", "high", "critical"]]


class Ticket(Scoped):
    number = models.PositiveBigIntegerField()
    conversation = models.ForeignKey(
        "conversations.Conversation", on_delete=models.PROTECT
    )
    customer = models.ForeignKey(
        "customers.Customer", null=True, blank=True, on_delete=models.SET_NULL
    )
    subject = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    escalation_level = models.CharField(
        max_length=16, choices=[(level, level) for level in ESCALATION_LEVELS], default="l1"
    )
    status = models.CharField(max_length=24, choices=STATUSES, default="new")
    priority = models.CharField(max_length=12, choices=PRIORITIES, default="normal")
    team = models.ForeignKey("teams.Team", on_delete=models.PROTECT)
    assignee = models.ForeignKey(
        "workspaces.Membership", null=True, blank=True, on_delete=models.PROTECT
    )
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)
    last_customer_message_at = models.DateTimeField(null=True, blank=True)
    last_agent_message_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)
    previous_ticket = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL
    )
    thread_id = models.CharField(max_length=40, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "number"], name="unique_ticket_number"
            )
        ]
        indexes = [
            models.Index(
                fields=["workspace", "-updated_at", "id"], name="ticket_queue_order"
            ),
            models.Index(fields=["workspace", "status", "-updated_at"]),
            models.Index(fields=["assignee", "status"]),
            models.Index(fields=["team", "status"]),
        ]


class TicketReadState(Entity):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE)
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    last_read_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ticket", "user"], name="unique_read_state")
        ]


class SavedFilter(Scoped):
    owner = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    conditions = models.JSONField(default=dict)


class ReplyTemplate(Scoped):
    name = models.CharField(max_length=120)
    language = models.CharField(max_length=10, default="ru")
    text = models.TextField()
