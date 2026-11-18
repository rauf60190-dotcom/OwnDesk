from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workspaces", "0008_route_verified_at")]
    operations = [migrations.AddField(model_name="membership", name="removed_at",
                                     field=models.DateTimeField(blank=True, null=True))]
