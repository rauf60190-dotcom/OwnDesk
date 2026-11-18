from django.utils import timezone
from rest_framework import serializers
from apps.conversations.models import Message, WEB_SUPPORT
from apps.attachments.models import Attachment
from .models import Ticket, PRIORITIES


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = ["id", "filename", "size", "mime_type", "state", "error"]


class MessageSerializer(serializers.ModelSerializer):
    attachments = AttachmentSerializer(many=True, read_only=True)
    delivery = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "ticket_id",
            "conversation_id",
            "author_name",
            "direction",
            "visibility",
            "kind",
            "text",
            "external_id",
            "reply_id",
            "thread_id",
            "created_at",
            "edited_at",
            "sent_at",
            "delivery",
            "attachments",
        ]

    def get_delivery(self, obj) -> dict | None:
        if hasattr(obj, "job"):
            return {
                "state": obj.job.state,
                "error": obj.job.error,
                "attempts": obj.job.attempts,
            }
        return None


class TicketSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(
        source="customer.name", read_only=True, default=None
    )
    company_name = serializers.CharField(
        source="customer.company.name", read_only=True, default=None
    )
    team_name = serializers.CharField(source="team.name", read_only=True)
    assignee_name = serializers.CharField(
        source="assignee.user.display_name", read_only=True, default=None
    )
    conversation_title = serializers.CharField(
        source="conversation.title", read_only=True
    )
    telegram_connected = serializers.SerializerMethodField()
    web_support = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    deadline = serializers.SerializerMethodField()
    unread = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Ticket
        fields = [
            "id",
            "number",
            "subject",
            "description",
            "escalation_level",
            "status",
            "priority",
            "team_id",
            "team_name",
            "assignee_id",
            "assignee_name",
            "customer_id",
            "customer_name",
            "company_name",
            "conversation_id",
            "conversation_title",
            "telegram_connected",
            "web_support",
            "version",
            "created_at",
            "updated_at",
            "last_customer_message_at",
            "last_agent_message_at",
            "resolved_at",
            "closed_at",
            "resolution",
            "deadline",
            "last_message",
            "unread",
        ]

    def get_web_support(self, obj) -> bool:
        return obj.conversation.kind == WEB_SUPPORT

    def get_telegram_connected(self, obj) -> bool:
        return bool(
            obj.conversation.connection_id
            and obj.conversation.approved
            and obj.conversation.connection.state == "connected"
        )

    def get_last_message(self, obj) -> dict | None:
        items = getattr(obj, "latest_messages", [])
        if not items:
            return None
        m = items[0]
        return {
            "text": m.text,
            "author": m.author_name
            or ("Клиент" if m.direction == "inbound" else "Бот"),
            "visibility": m.visibility,
        }

    def get_deadline(self, obj) -> dict | None:
        instances = [
            s for s in obj.deadlines.all() if not s.completed_at and not s.cancelled_at
        ]
        active = [s for s in instances if not s.paused_at]
        if not instances:
            return None
        s = min(active or instances, key=lambda x: x.deadline)
        return {
            "at": s.deadline,
            "kind": s.kind,
            "paused": bool(s.paused_at),
            "overdue": bool(not s.paused_at and s.deadline < timezone.now()),
            "breached": s.breached,
        }


class VersionInput(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)


class TicketEditInput(VersionInput):
    description = serializers.CharField(max_length=20000, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=240, required=False)
    priority = serializers.ChoiceField(choices=PRIORITIES, required=False)


class SendInput(VersionInput):
    text = serializers.CharField(allow_blank=True, default="")
    operation_key = serializers.UUIDField()
    reply_id = serializers.CharField(required=False, allow_null=True)
    attachments = serializers.ListField(child=serializers.UUIDField(), default=list)


class TransitionInput(VersionInput):
    status = serializers.CharField()
    resolution = serializers.CharField(required=False, default="", allow_blank=True)


class AssignInput(VersionInput):
    team_id = serializers.UUIDField()
    assignee_id = serializers.UUIDField(allow_null=True)


class EscalateInput(VersionInput):
    reason = serializers.CharField(max_length=2000)


class CreateInput(serializers.Serializer):
    description = serializers.CharField(max_length=20000, required=False, allow_blank=True, default="")
    attachments = serializers.ListField(child=serializers.UUIDField(), max_length=1, required=False, default=list)
    subject = serializers.CharField(max_length=240)
    team_id = serializers.UUIDField()
    customer_id = serializers.UUIDField(required=False, allow_null=True)
    conversation_id = serializers.UUIDField(required=False, allow_null=True)
