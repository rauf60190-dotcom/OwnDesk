from django.db import models
from apps.core.models import Scoped


WEB_SUPPORT = "web_support"


class Conversation(Scoped):
    connection = models.ForeignKey(
        "integrations.ChannelConnection",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    external_id = models.CharField(max_length=40, null=True, blank=True)
    kind = models.CharField(max_length=20, default="private")
    title = models.CharField(max_length=200, blank=True)
    approved = models.BooleanField(default=False)
    mode = models.CharField(max_length=20, default="single_customer")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "external_id"], name="unique_external_chat"
            )
        ]


class ConversationParticipant(Scoped):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="participants"
    )
    external_id = models.CharField(max_length=40)
    customer = models.ForeignKey(
        "customers.Customer", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "external_id"],
                name="unique_conversation_participant",
            )
        ]


class Message(Scoped):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    ticket = models.ForeignKey(
        "tickets.Ticket",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="messages",
    )
    author = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    customer = models.ForeignKey(
        "customers.Customer", null=True, blank=True, on_delete=models.SET_NULL
    )
    author_name = models.CharField(max_length=200, blank=True)
    direction = models.CharField(max_length=20, default="inbound")
    kind = models.CharField(max_length=20, default="text")
    visibility = models.CharField(max_length=20, default="public")
    text = models.TextField(blank=True)
    external_id = models.CharField(max_length=40, null=True, blank=True)
    reply_id = models.CharField(max_length=40, null=True, blank=True)
    thread_id = models.CharField(max_length=40, null=True, blank=True)
    operation_key = models.UUIDField(null=True, blank=True)
    ticket_sequence = models.PositiveBigIntegerField(default=0)
    edited_at = models.DateTimeField(null=True, blank=True)
    external_date = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "external_id"], name="unique_external_message"
            ),
            models.UniqueConstraint(
                fields=["workspace", "operation_key"], name="unique_send_operation"
            ),
        ]
        indexes = [
            models.Index(fields=["ticket", "created_at"]),
            models.Index(fields=["conversation", "thread_id"]),
        ]
