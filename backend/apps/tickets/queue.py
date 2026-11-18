"""Project queue rows directly to the API shape after permission-aware pagination."""

from django.db.models import BooleanField, Case, F, OuterRef, Subquery, Value, When
from django.db.models.functions import JSONObject
from django.utils import timezone
from apps.conversations.models import Message, WEB_SUPPORT
from apps.sla.models import SLAInstance

ROW_FIELDS = (
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
    "last_message",
    "unread",
)


def queue_rows(visible, page_ids, unread_expression):
    latest = (
        Message.objects.filter(ticket_id=OuterRef("pk"))
        .order_by("-created_at", "-id")
        .annotate(
            preview=JSONObject(
                text="text",
                author=Case(
                    When(author_name="", direction="inbound", then=Value("Клиент")),
                    When(author_name="", then=Value("Бот")),
                    default=F("author_name"),
                ),
                visibility="visibility",
            )
        )
        .values("preview")[:1]
    )
    rows = (
        visible.filter(pk__in=page_ids)
        .annotate(
            team_name=F("team__name"),
            customer_name=F("customer__name"),
            company_name=F("customer__company__name"),
            assignee_name=F("assignee__user__display_name"),
            conversation_title=F("conversation__title"),
            telegram_connected=Case(
                When(
                    conversation__approved=True,
                    conversation__connection__state="connected",
                    then=Value(True),
                ),
                default=Value(False),
                output_field=BooleanField(),
            ),
            web_support=Case(When(conversation__kind=WEB_SUPPORT, then=Value(True)), default=Value(False)),
            unread=unread_expression,
            last_message=Subquery(latest),
        )
        .values(*ROW_FIELDS)
    )
    by_id = {row["id"]: {**row, "deadline": None} for row in rows}
    now = timezone.now()
    timers = (
        SLAInstance.objects.filter(
            ticket_id__in=by_id, completed_at=None, cancelled_at=None
        )
        .order_by("deadline")
        .values("ticket_id", "deadline", "kind", "paused_at", "breached")
    )
    for timer in timers:
        row = by_id[timer["ticket_id"]]
        current = row["deadline"]
        if current is not None and (not current["paused"] or timer["paused_at"]):
            continue
        row["deadline"] = {
            "at": timer["deadline"],
            "kind": timer["kind"],
            "paused": bool(timer["paused_at"]),
            "overdue": not timer["paused_at"] and timer["deadline"] < now,
            "breached": timer["breached"],
        }
    return [by_id[pk] for pk in page_ids if pk in by_id]
