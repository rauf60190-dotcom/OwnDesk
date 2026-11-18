from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Exists, OuterRef, Q, Subquery, UUIDField
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.core.access import member, tickets_for
from apps.core.contracts import documented


class ReportDates(serializers.Serializer):
    period = serializers.ChoiceField(choices=("today", "yesterday", "week", "month"), required=False)
    from_date = serializers.DateField(required=False, input_formats=["%Y-%m-%d"])
    to_date = serializers.DateField(required=False, input_formats=["%Y-%m-%d"])


def report_scope(request):
    membership = member(request)
    if membership.role not in ("admin", "manager"):
        raise PermissionDenied()
    tz = ZoneInfo(membership.workspace.timezone)
    today = timezone.localdate(timezone=tz)
    params = request.query_params
    dates = ReportDates(data={
        key: value for key, value in (
            ("period", params.get("period")), ("from_date", params.get("from")),
            ("to_date", params.get("to")),
        ) if value
    })
    dates.is_valid(raise_exception=True)
    values = dates.validated_data
    period = values.get("period")
    first, last = values.get("from_date", today - timedelta(days=30)), values.get("to_date", today)
    if period == "today":
        first = last = today
    elif period == "yesterday":
        first = last = today - timedelta(days=1)
    elif period == "week":
        first, last = today - timedelta(days=today.weekday()), today
    elif period == "month":
        first, last = today.replace(day=1), today
    if last < first or last == date.max:
        raise ValidationError("Проверьте начало и конец периода.")
    return tickets_for(membership), tz, first, last


class ClosureFilters(serializers.Serializer):
    employee = serializers.UUIDField(required=False, allow_null=True)
    bucket = serializers.CharField(required=False, max_length=10)
    page = serializers.IntegerField(required=False, min_value=1)


@documented
class ClosuresView(APIView):
    def get(self, request):
        tickets, tz, first, last = report_scope(request)
        params = request.query_params
        filter_data = {key: params[key] for key in ("employee", "bucket", "page") if params.get(key)}
        if filter_data.get("employee") == "unknown":
            filter_data["employee"] = None
        filters = ClosureFilters(data=filter_data)
        filters.is_valid(raise_exception=True)
        options = filters.validated_data
        page_size = 25
        offset = (options.get("page", 1) - 1) * page_size
        hourly = first == last
        start = datetime.combine(first, time.min, tz)
        end = datetime.combine(last + timedelta(days=1), time.min, tz)
        events = AuditEvent.objects.filter(
            workspace_id=OuterRef("workspace_id"), ticket_id=OuterRef("pk"),
            action="ticket.status", changes__to="closed",
        )
        # A ticket counts once per range. Use closed_at only for legacy rows without closure history.
        latest = events.filter(created_at__gte=start, created_at__lt=end).order_by("-created_at", "-pk")
        rows = tickets.annotate(
            closure_at=Subquery(latest.values("created_at")[:1]),
            closure_actor=Subquery(latest.values("actor_id")[:1], output_field=UUIDField()),
        ).filter(
            Q(closure_at__isnull=False) |
            (Q(closed_at__gte=start, closed_at__lt=end) & ~Exists(events))
        ).annotate(
            closure_time=Coalesce("closure_at", "closed_at"),
        ).order_by("-closure_time", "-pk").values(
            "id", "number", "subject", "closure_time", "closure_actor",
        )

        employee_counts = Counter()
        bucket_counts = Counter()
        total = detail_count = 0
        details = []
        for row in rows.iterator():
            actor = row["closure_actor"]
            closed_at = row["closure_time"].astimezone(tz)
            bucket = closed_at.strftime("%H") if hourly else closed_at.date().isoformat()
            employee_counts[actor] += 1
            if "employee" in options and actor != options["employee"]:
                continue
            bucket_counts[bucket] += 1
            total += 1
            if options.get("bucket") and bucket != options["bucket"]:
                continue
            if offset <= detail_count < offset + page_size:
                details.append({
                    "id": row["id"], "number": row["number"], "subject": row["subject"],
                    "closed_at": closed_at, "employee_id": actor,
                })
            detail_count += 1

        users = User.objects.in_bulk([actor for actor in employee_counts if actor is not None])
        employees = [
            {"id": actor, "name": (users[actor].display_name or users[actor].username)
             if actor in users else "Автор неизвестен", "count": count}
            for actor, count in employee_counts.items()
        ]
        employees.sort(key=lambda item: (-item["count"], item["name"].casefold()))
        if hourly:
            buckets = [f"{hour:02d}" for hour in range(24)]
        elif (last - first).days < 62:
            buckets = [(first + timedelta(days=day)).isoformat() for day in range((last - first).days + 1)]
        else:
            buckets = sorted(bucket_counts)
        return Response({
            "from": first, "to": last, "timezone": str(tz), "hourly": hourly, "total": total,
            "employees": employees, "buckets": [{"key": key, "count": bucket_counts[key]} for key in buckets],
            "details": {"count": detail_count, "results": details, "page_size": page_size},
        })
