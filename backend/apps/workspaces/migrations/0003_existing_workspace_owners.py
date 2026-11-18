from django.db import migrations


def assign_owners(apps, schema_editor):
    Membership = apps.get_model("workspaces", "Membership")
    for workspace_id in Membership.objects.values_list("workspace_id", flat=True).distinct():
        members = Membership.objects.filter(workspace_id=workspace_id)
        if not members.filter(is_owner=True).exists():
            first = members.filter(role="admin", active=True, user__is_active=True).order_by("created_at", "id").first()
            if first:
                members.filter(pk=first.pk).update(is_owner=True)


class Migration(migrations.Migration):
    dependencies = [("workspaces", "0002_invitation_membership_is_owner_and_more")]
    operations = [migrations.RunPython(assign_owners, migrations.RunPython.noop)]
