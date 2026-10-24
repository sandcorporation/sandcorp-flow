from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("flows", "0002_seed_default_flows")]

    operations = [
        migrations.AddField(
            model_name="flowversion",
            name="note",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
