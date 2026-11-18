from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.utils import timezone
from .models import SLAPolicy, SLAInstance

DEFAULTS = {
    "low": (60, 120, 1440),
    "normal": (15, 30, 480),
    "high": (5, 10, 240),
    "critical": (2, 5, 60),
}


def policy(ticket):
    a, b, c = DEFAULTS[ticket.priority]
    return SLAPolicy.objects.get_or_create(
        workspace=ticket.workspace,
        priority=ticket.priority,
        defaults={
            "first_response_minutes": a,
            "next_response_minutes": b,
            "resolution_minutes": c,
        },
    )[0]


def intervals(day, schedule, tz):
    spans = schedule.get("exceptions", {}).get(
        str(day), schedule.get("week", {}).get(str(day.weekday()), [])
    )
    for start, end in spans:
        left = datetime.combine(day, time.fromisoformat(start), tz)
        right = (
            datetime.combine(day + timedelta(days=1), time.min, tz)
            if end == "24:00"
            else datetime.combine(day, time.fromisoformat(end), tz)
        )
        if right > left:
            yield left, right


def business_seconds(start, end, schedule, tzname):
    if end <= start:
        return 0
    if not schedule:
        return (end - start).total_seconds()
    tz = ZoneInfo(tzname)
    day, last = start.astimezone(tz).date(), end.astimezone(tz).date()
    total = 0
    while day <= last:
        for left, right in intervals(day, schedule, tz):
            total += max(0, (min(right, end) - max(left, start)).total_seconds())
        day += timedelta(days=1)
    return total


def add_work(start, seconds, schedule, tzname):
    if not schedule:
        return start + timedelta(seconds=seconds)
    tz = ZoneInfo(tzname)
    day = start.astimezone(tz).date()
    for _ in range(3660):
        for left, right in intervals(day, schedule, tz):
            left = max(left, start)
            available = max(0, (right - left).total_seconds())
            if available and seconds <= available:
                return left + timedelta(seconds=seconds)
            seconds -= available
        day += timedelta(days=1)
    raise ValueError("В расписании нет доступного рабочего времени.")


def new_instance(ticket, kind, start, sequence=None):
    p = policy(ticket)
    minutes = {
        "first_response": p.first_response_minutes,
        "next_response": p.next_response_minutes,
        "resolution": p.resolution_minutes,
    }[kind]
    return SLAInstance.objects.create(
        workspace=ticket.workspace,
        ticket=ticket,
        kind=kind,
        started_at=start,
        deadline=add_work(start, minutes * 60, p.schedule, ticket.workspace.timezone),
        budget_minutes=minutes,
        schedule=p.schedule,
        opened_version=ticket.version if sequence is None else sequence,
    )


def incoming(ticket, at, sequence=None, restart=False):
    active = ticket.deadlines.filter(completed_at=None, cancelled_at=None)
    if not active.filter(kind="resolution").exists():
        new_instance(ticket, "resolution", at, sequence)
    if not active.exclude(kind="resolution").exists():
        after_first = (
            not restart
            and ticket.deadlines.filter(
                kind="first_response", completed_at__isnull=False
            ).exists()
        )
        new_instance(
            ticket, "next_response" if after_first else "first_response", at, sequence
        )


def confirmed_response(ticket, covered_version, at):
    for s in ticket.deadlines.filter(
        completed_at=None, cancelled_at=None, opened_version__lte=covered_version
    ).exclude(kind="resolution"):
        s.breached = s.breached or at > s.deadline
        s.completed_at = at
        s.save()
    pending = (
        ticket.messages.filter(direction="inbound", ticket_sequence__gt=covered_version)
        .order_by("ticket_sequence")
        .first()
    )
    if pending:
        incoming(ticket, pending.created_at, sequence=pending.ticket_sequence)


def status_changed(ticket, old_status):
    now = timezone.now()
    for s in ticket.deadlines.filter(completed_at=None, cancelled_at=None):
        if not s.paused_at and now > s.deadline:
            s.breached = True
        if ticket.status in ["resolved", "closed"]:
            if s.kind == "resolution":
                s.completed_at = now
            else:
                s.cancelled_at = now
        elif s.kind == "resolution":
            if ticket.status == "waiting_customer" and not s.paused_at:
                account(s, now, ticket.workspace.timezone)
                s.paused_at = now
            elif old_status == "waiting_customer" and s.paused_at:
                account(s, s.paused_at, ticket.workspace.timezone)
                s.paused_seconds += int(
                    business_seconds(
                        s.paused_at, now, s.schedule, ticket.workspace.timezone
                    )
                )
                s.deadline = add_work(
                    now,
                    max(0, s.budget_minutes * 60 - s.spent_seconds),
                    s.schedule,
                    ticket.workspace.timezone,
                )
                s.paused_at = None
                s.accounted_at = now
        s.save()


def account(instance, at, tzname):
    start = instance.accounted_at or instance.started_at
    elapsed = business_seconds(start, at, instance.schedule, tzname)
    if instance.accounted_at is None:
        elapsed = max(0, elapsed - instance.paused_seconds)
    instance.spent_seconds += int(elapsed)
    instance.accounted_at = max(start, at)


def reprioritize(ticket):
    p = policy(ticket)
    now = timezone.now()
    for s in ticket.deadlines.filter(completed_at=None, cancelled_at=None):
        s.breached = s.breached or (not s.paused_at and now > s.deadline)
        account(s, s.paused_at or now, ticket.workspace.timezone)
        s.schedule = p.schedule
        s.budget_minutes = {
            "first_response": p.first_response_minutes,
            "next_response": p.next_response_minutes,
            "resolution": p.resolution_minutes,
        }[s.kind]
        s.deadline = add_work(
            now,
            max(0, s.budget_minutes * 60 - s.spent_seconds),
            s.schedule,
            ticket.workspace.timezone,
        )
        s.breached = s.breached or s.spent_seconds > s.budget_minutes * 60
        s.save()
