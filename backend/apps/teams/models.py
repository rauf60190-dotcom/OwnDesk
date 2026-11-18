from django.db import models
from apps.core.models import Scoped, Entity


class Team(Scoped):
    name = models.CharField(max_length=150)


class TeamMember(Entity):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    membership = models.ForeignKey("workspaces.Membership", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "membership"], name="unique_team_member"
            )
        ]
