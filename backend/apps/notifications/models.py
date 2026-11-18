from django.db import models
from apps.core.models import Scoped


class Notification(Scoped):
    recipient = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    ticket = models.ForeignKey(
        "tickets.Ticket", null=True, blank=True, on_delete=models.CASCADE
    )
    kind = models.CharField(max_length=40)
    text = models.CharField(max_length=300)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "recipient", "ticket", "kind"],
                condition=models.Q(read_at__isnull=True, ticket__isnull=False),
                name="one_unread_ticket_notification",
            )
        ]
