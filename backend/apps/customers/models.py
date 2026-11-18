from django.db import models
from apps.core.models import Scoped


class Company(Scoped):
    name = models.CharField(max_length=200)
    tax_id = models.CharField(max_length=32, blank=True)
    note = models.TextField(blank=True)


class Customer(Scoped):
    name = models.CharField(max_length=200)
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.SET_NULL
    )
    phone = models.CharField(max_length=40, blank=True)
    language = models.CharField(max_length=10, default="ru")
    extra = models.JSONField(default=dict)


class CustomerIdentity(Scoped):
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="identities"
    )
    platform = models.CharField(max_length=20, default="telegram")
    external_id = models.CharField(max_length=40)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "platform", "external_id"], name="unique_identity"
            )
        ]


class SupportAccess(Scoped):
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE)
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
