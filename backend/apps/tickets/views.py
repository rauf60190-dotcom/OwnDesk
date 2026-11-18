from uuid import uuid4

from django.db import transaction
from django.db.models import (
    Q,
    F,
    Count,
    Prefetch,
    Subquery,
    OuterRef,
    IntegerField,
    Value,
    Case,
    When,
    Min,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from drf_spectacular.utils import extend_schema
from apps.core.access import member, tickets_for, require_write, team_ids, scoped
from apps.teams.models import Team
from apps.customers.models import Customer
from apps.conversations.models import Conversation, Message
from apps.sla.models import SLAInstance
from apps.sla import services as sla
from .models import Ticket, TicketReadState, ACTIVE
from .serializers import (
    TicketSerializer,
    MessageSerializer,
    VersionInput,
    TicketEditInput,
    SendInput,
    TransitionInput,
    AssignInput,
    CreateInput,
    EscalateInput,
)
from . import services


def unread_count(user):
    last_read = TicketReadState.objects.filter(
        ticket_id=OuterRef("ticket_id"), user=user
    ).values("last_read_at")[:1]
    unread = (
        Message.objects.filter(ticket_id=OuterRef("pk"), direction="inbound")
        .annotate(read_at=Subquery(last_read))
        .filter(Q(read_at=None) | Q(created_at__gt=F("read_at")))
        .values("ticket_id")
        .annotate(n=Count("id"))
        .values("n")[:1]
    )
    return Coalesce(Subquery(unread, output_field=IntegerField()), Value(0))


def hydrated(qs, user):
    return (
        qs.select_related(
            "customer__company", "team", "assignee__user", "conversation__connection"
        )
        .prefetch_related(
            "deadlines",
            Prefetch(
                "messages",
                queryset=Message.objects.order_by("-created_at", "-id")[:1],
                to_attr="latest_messages",
            ),
        )
        .annotate(unread=unread_count(user))
    )


def in_queue(qs, queue, m):
    if queue in ["active", "mine", "free", "overdue"]:
        qs = qs.filter(status__in=ACTIVE)
    if queue == "mine":
        qs = qs.filter(assignee=m)
    elif queue == "free":
        qs = qs.filter(assignee=None)
    elif queue == "overdue":
        overdue = SLAInstance.objects.filter(
            completed_at=None,
            cancelled_at=None,
            paused_at=None,
            deadline__lt=timezone.now(),
        ).values("ticket_id")
        qs = qs.filter(id__in=overdue)
    return qs


class TicketViewSet(viewsets.GenericViewSet):
    serializer_class = TicketSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Ticket.objects.none()
        return hydrated(tickets_for(member(self.request)), self.request.user)

    def list(self, request):
        m = member(request)
        qs = in_queue(tickets_for(m), request.query_params.get("queue", "active"), m)
        for name, field in [
            ("status", "status"),
            ("priority", "priority"),
            ("line", "escalation_level"),
            ("assignee", "assignee_id"),
            ("team", "team_id"),
            ("conversation", "conversation_id"),
            ("customer", "customer_id"),
            ("company", "customer__company_id"),
            ("connection", "conversation__connection_id"),
        ]:
            value = request.query_params.get(name)
            if value:
                if name in ["status", "priority", "line"]:
                    qs = qs.filter(**{field: value})
                else:
                    from uuid import UUID

                    try:
                        UUID(value)
                    except ValueError:
                        raise ValidationError({name: "Неверный идентификатор."})
                    qs = qs.filter(**{field: value})
        for name, lookup in [
            ("from", "created_at__date__gte"),
            ("to", "created_at__date__lte"),
        ]:
            if request.query_params.get(name):
                date = parse_date(request.query_params[name])
                if not date:
                    raise ValidationError({name: "Дата в формате YYYY-MM-DD."})
                qs = qs.filter(**{lookup: date})
        search = request.query_params.get("q", "").strip()
        if search:
            matches = Message.objects.filter(
                ticket_id__in=tickets_for(m).values("id"), text__icontains=search
            ).values("ticket_id")
            condition = (
                Q(subject__icontains=search)
                | Q(description__icontains=search)
                | Q(customer__name__icontains=search)
                | Q(conversation__title__icontains=search)
                | Q(id__in=matches)
            )
            if search.lstrip("#").isdigit():
                condition |= Q(number=int(search.lstrip("#")))
            qs = qs.filter(condition)
        if request.query_params.get("unread") == "1":
            qs = qs.annotate(unread=unread_count(request.user)).filter(unread__gt=0)
        order = request.query_params.get("sort", "-updated_at")
        if order == "priority":
            qs = qs.annotate(
                rank=Case(
                    *[
                        When(priority=p, then=Value(i))
                        for i, p in enumerate(["critical", "high", "normal", "low"])
                    ],
                    output_field=IntegerField(),
                )
            ).order_by("rank", "-updated_at")
        elif order == "deadline":
            qs = qs.annotate(
                soonest=Min(
                    "deadlines__deadline",
                    filter=Q(
                        deadlines__completed_at=None,
                        deadlines__cancelled_at=None,
                        deadlines__paused_at=None,
                    ),
                )
            ).order_by(F("soonest").asc(nulls_last=True), "id")
        else:
            qs = qs.order_by(
                order
                if order in ["-updated_at", "created_at", "-created_at"]
                else "-updated_at",
                "id",
            )
        # Resolve the page first, so joins for the table only touch visible page rows.
        ids = self.paginate_queryset(qs.values_list("pk", flat=True))
        from .queue import queue_rows

        return self.get_paginated_response(
            queue_rows(tickets_for(m), ids, unread_count(request.user))
        )

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(request=CreateInput)
    @transaction.atomic
    def create(self, request):
        m = member(request)
        if m.role == "observer":
            raise PermissionDenied()
        s = CreateInput(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        team = scoped(Team, m, d["team_id"])
        if m.role != "admin" and team.id not in team_ids(m):
            raise PermissionDenied()
        customer = (
            scoped(Customer, m, d["customer_id"]) if d.get("customer_id") else None
        )
        if (
            customer
            and m.role != "admin"
            and not tickets_for(m).filter(customer=customer).exists()
        ):
            from rest_framework.exceptions import NotFound

            raise NotFound()
        if d.get("conversation_id"):
            conv = scoped(Conversation, m, d["conversation_id"])
            if (
                m.role != "admin"
                and not tickets_for(m).filter(conversation=conv).exists()
            ):
                raise PermissionDenied()
        else:
            conv = Conversation.objects.create(
                workspace=m.workspace,
                title=customer.name if customer else d["subject"],
                kind="manual",
                approved=True,
            )
        t = services.create_ticket(
            m.workspace, conv, team, d["subject"], customer, m.user, description=d["description"]
        )
        if d["attachments"]:
            services.send(m, t.pk, t.version, "Вложение при создании обращения", uuid4(),
                          note=True, attachment_ids=d["attachments"])
        return Response(
            TicketSerializer(
                hydrated(Ticket.objects.filter(id=t.id), m.user).get()
            ).data,
            status=201,
        )

    def result(self, t):
        return Response(TicketSerializer(self.get_queryset().get(id=t.id)).data)

    @extend_schema(request=TicketEditInput)
    def partial_update(self, request, pk=None):
        s = TicketEditInput(data=request.data)
        s.is_valid(raise_exception=True)
        return self.result(
            services.edit(
                member(request),
                pk,
                s.validated_data.pop("expected_version"),
                s.validated_data,
            )
        )

    @extend_schema(request=VersionInput)
    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        s = VersionInput(data=request.data)
        s.is_valid(raise_exception=True)
        return self.result(
            services.claim(
                member(request),
                pk,
                **{"expected": s.validated_data["expected_version"]},
            )
        )

    @extend_schema(request=AssignInput)
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        s = AssignInput(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        return self.result(
            services.assign(
                member(request),
                pk,
                d["expected_version"],
                d["team_id"],
                d["assignee_id"],
            )
        )

    @extend_schema(request=EscalateInput, responses={200: {"type": "object"}})
    @action(detail=True, methods=["post"])
    def escalate(self, request, pk=None):
        form = EscalateInput(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        ticket = services.escalate(member(request), pk, data["expected_version"], data["reason"])
        return Response({"id": ticket.pk, "escalation_level": ticket.escalation_level,
                         "team_name": ticket.team.name})

    @extend_schema(request=TransitionInput)
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        s = TransitionInput(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        return self.result(
            services.transition(
                member(request), pk, d["expected_version"], d["status"], d["resolution"]
            )
        )

    @extend_schema(request=SendInput, responses=MessageSerializer)
    @action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        t = self.get_object()
        if request.method == "GET":
            qs = (
                t.messages.select_related("job")
                .prefetch_related("attachments")
                .order_by("-created_at", "-id")
            )
            return self.get_paginated_response(
                MessageSerializer(self.paginate_queryset(qs), many=True).data
            )
        return self.send_message(request, pk, False)

    def send_message(self, request, pk, note):
        s = SendInput(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        result = services.send(
            member(request),
            pk,
            d["expected_version"],
            d["text"],
            d["operation_key"],
            note=note,
            reply_id=d.get("reply_id"),
            attachment_ids=d["attachments"],
        )
        return Response(MessageSerializer(result).data, status=201)

    @action(detail=True, methods=["post"], url_path="retry-delivery")
    @transaction.atomic
    def retry_delivery(self, request, pk=None):
        from apps.integrations.models import OutboundJob
        from apps.core.errors import Conflict

        m = member(request)
        ticket = services.locked(m, pk, request.data.get("expected_version"))
        require_write(m, ticket)
        job = get_object_or_404(
            OutboundJob.objects.select_for_update(),
            message_id=request.data.get("message_id"),
            message__ticket=ticket,
            message__visibility="public",
        )
        if ticket.status not in ACTIVE or job.state not in ["failed", "unknown"]:
            raise Conflict("Повторная отправка недоступна в текущем состоянии.")
        if job.state == "unknown" and request.data.get("confirm_duplicate") is not True:
            raise ValidationError(
                "Telegram мог доставить этот ответ. Проверьте чат и подтвердите риск дубликата."
            )
        previous = job.state
        job.state, job.next_attempt_at, job.error = "queued", None, ""
        job.save(update_fields=["state", "next_attempt_at", "error"])
        services.changed(
            ticket,
            m.user,
            "delivery.retry",
            {"message_id": str(job.message_id), "previous_state": previous},
        )
        return self.result(ticket)

    @extend_schema(request=SendInput, responses=MessageSerializer)
    @action(detail=True, methods=["post"])
    def notes(self, request, pk=None):
        return self.send_message(request, pk, True)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        t = self.get_object()
        message = get_object_or_404(t.messages, pk=request.data.get("message_id"))
        state, _ = TicketReadState.objects.get_or_create(
            ticket=t, user=request.user, defaults={"last_read_at": message.created_at}
        )
        TicketReadState.objects.filter(
            id=state.id, last_read_at__lt=message.created_at
        ).update(last_read_at=message.created_at)
        return Response({"ok": True})

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        qs = self.get_object().history.select_related("actor")
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(
            [
                {
                    "id": x.id,
                    "action": x.action,
                    "actor": x.actor.display_name if x.actor else "Система",
                    "changes": x.changes,
                    "created_at": x.created_at,
                }
                for x in page
            ]
        )

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def split(self, request, pk=None):
        m = member(request)
        t = services.locked(m, pk, request.data.get("expected_version"))
        require_write(m, t)
        ids = request.data.get("message_ids", [])
        messages = list(
            t.messages.select_for_update().filter(pk__in=ids, direction="inbound")
        )
        if not messages or len(messages) != len(set(ids)):
            raise ValidationError("Выберите входящие сообщения этого обращения.")
        new = services.create_ticket(
            m.workspace,
            t.conversation,
            t.team,
            request.data.get("subject") or messages[0].text or "Новое обращение",
            t.customer,
            m.user,
            previous=t,
            escalation_level=t.escalation_level,
        )
        for message in sorted(messages, key=lambda value: value.created_at):
            new.version += 1
            message.ticket, message.ticket_sequence = new, new.version
            message.save(update_fields=["ticket", "ticket_sequence"])
        new.last_customer_message_at = max(x.created_at for x in messages)
        threads = {x.thread_id for x in messages}
        new.thread_id = next(iter(threads)) if len(threads) == 1 else None
        new.save()
        sla.incoming(new, min(x.created_at for x in messages), sequence=2)
        services.audit(
            new,
            m.user,
            "ticket.split_from",
            {"source": str(t.id), "messages": [str(x.id) for x in messages]},
        )
        services.changed(
            t,
            m.user,
            "ticket.split",
            {"new_ticket": str(new.id), "messages": [str(x.id) for x in messages]},
        )
        return self.result(new)
