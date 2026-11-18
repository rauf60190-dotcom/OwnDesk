from apps.core.contracts import documented
from datetime import timedelta, datetime, time
from statistics import median
from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.core.access import member, tickets_for
from apps.core.reports import report_scope
from apps.tickets.models import ACTIVE
from apps.tickets.views import in_queue
from apps.integrations.views import untriaged_for
from apps.notifications.models import Notification
from apps.integrations.models import WorkerHeartbeat, InboundEvent, OutboundJob
from apps.sla.models import SLAInstance


@documented
class CountsView(APIView):
    def get(self, request):
        m = member(request)
        qs = tickets_for(m)
        return Response(
            {
                **{
                    q: in_queue(qs, q, m).count()
                    for q in ["active", "mine", "free", "overdue", "all"]
                },
                "untriaged": untriaged_for(m).count(),
            }
        )


@documented
class QueueOptionsView(APIView):
    def get(self, request):
        from apps.customers.models import Customer, Company
        from apps.integrations.models import ChannelConnection

        m = member(request)
        visible = tickets_for(m)
        q = str(request.query_params.get("q", ""))[:100]
        customers = Customer.objects.filter(workspace=m.workspace, name__icontains=q)
        companies = Company.objects.filter(workspace=m.workspace, name__icontains=q)
        connections = ChannelConnection.objects.filter(workspace=m.workspace)
        if m.role != "admin":
            customers = customers.filter(id__in=visible.values("customer_id"))
            companies = companies.filter(id__in=visible.values("customer__company_id"))
            connections = connections.filter(
                id__in=visible.values("conversation__connection_id")
            )
        return Response(
            {
                "customer": list(customers.order_by("name").values("id", "name")[:50]),
                "company": list(companies.order_by("name").values("id", "name")[:50]),
                "connection": [
                    {"id": bot.id, "name": "@" + bot.username} for bot in connections
                ],
            }
        )


@documented
class NotificationsView(APIView):
    def get(self, request):
        m = member(request)
        qs = Notification.objects.filter(
            workspace=m.workspace, recipient=request.user
        ).filter(Q(ticket=None) | Q(ticket__in=tickets_for(m)))
        return Response(
            {
                "results": list(
                    qs.order_by("-created_at").values(
                        "id", "kind", "text", "ticket_id", "created_at", "read_at"
                    )[:100]
                )
            }
        )


@documented
class NotificationReadView(APIView):
    def post(self, request, pk):
        m = member(request)
        Notification.objects.filter(
            workspace=m.workspace, recipient=request.user, pk=pk
        ).update(read_at=timezone.now())
        return Response({"ok": True})


@documented
class ReportsView(APIView):
    def get(self, request):
        qs, tz, start_date, end_date = report_scope(request)
        start = datetime.combine(start_date, time.min, tz)
        end = datetime.combine(end_date + timedelta(days=1), time.min, tz)
        cohort = qs.filter(created_at__gte=start, created_at__lt=end)
        s = SLAInstance.objects.filter(ticket__in=cohort)
        replies = sorted(
            (x.completed_at - x.started_at).total_seconds() / 60
            for x in s.filter(kind="first_response", completed_at__isnull=False)
        )
        resolution = [
            (x.completed_at - x.started_at).total_seconds() / 60
            for x in s.filter(kind="resolution", completed_at__isnull=False)
        ]
        completed = s.filter(completed_at__isnull=False)
        return Response(
            {
                "from": start_date,
                "to": end_date,
                "date_basis": "Дата создания обращения, часовой пояс пространства",
                "unit": "минуты, календарное время",
                "created": cohort.count(),
                "resolved": cohort.filter(status="resolved").count(),
                "closed": cohort.filter(status="closed").count(),
                "active_now": qs.filter(status__in=ACTIVE).count(),
                "free_now": qs.filter(status__in=ACTIVE, assignee=None).count(),
                "first_response_median": median(replies) if replies else None,
                "first_response_p90": replies[
                    max(0, __import__("math").ceil(len(replies) * 0.9) - 1)
                ]
                if replies
                else None,
                "resolution_median": median(resolution) if resolution else None,
                "without_response": cohort.filter(last_agent_message_at=None).count(),
                "sla_percent": round(
                    completed.filter(breached=False).count() * 100 / completed.count(),
                    1,
                )
                if completed.exists()
                else None,
                "reopened": cohort.filter(history__action="ticket.reopened")
                .distinct()
                .count(),
                "load": list(
                    qs.filter(status__in=ACTIVE)
                    .values("assignee__user__display_name", "team__name")
                    .annotate(count=Count("id"))
                ),
            }
        )


@documented
class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            return Response({"status": "unavailable"}, status=503)
        return Response({"status": "ok"})


@documented
class LiveView(HealthView):
    def get(self, request):
        return Response({"status": "alive"})


@documented
class OperationsView(APIView):
    def get(self, request):
        m = member(request)
        if m.role != "admin":
            raise PermissionDenied()
        beat = WorkerHeartbeat.objects.filter(name="worker").first()
        return Response(
            {
                "worker": "healthy"
                if beat and beat.seen_at > timezone.now() - timedelta(seconds=60)
                else "unavailable",
                "worker_seen_at": beat.seen_at if beat else None,
                "pending_events": InboundEvent.objects.filter(
                    connection__workspace=m.workspace, state="pending"
                ).count(),
                "outbox": list(
                    OutboundJob.objects.filter(message__workspace=m.workspace)
                    .values("state")
                    .annotate(count=Count("id"))
                ),
            }
        )
