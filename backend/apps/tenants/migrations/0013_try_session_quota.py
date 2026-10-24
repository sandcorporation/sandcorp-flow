"""Try Session 쿠키·IP 하루 세션 수·하루 비용 누적 표(#135, ADR-0030).

무중단 롤링 배포(ADR-0015): 새 표만 더한다(기존 표·칸은 그대로). 옛 코드는 이 표를 모르고 쓰지도 않는다.
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0012_tenant_kind_try"),
    ]

    operations = [
        migrations.CreateModel(
            name="TrySession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("flow_id", models.UUIDField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "tenant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE, related_name="try_session", to="tenants.tenant"
                    ),
                ),
            ],
            options={"db_table": "try_sessions"},
        ),
        migrations.CreateModel(
            name="TryIpDay",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ip_hash", models.CharField(max_length=64)),
                ("day", models.DateField()),
                ("count", models.PositiveIntegerField(default=0)),
            ],
            options={
                "db_table": "try_ip_days",
                "constraints": [models.UniqueConstraint(fields=("ip_hash", "day"), name="try_ip_day_unique")],
            },
        ),
        migrations.CreateModel(
            name="TrySpendDay",
            fields=[
                ("day", models.DateField(primary_key=True, serialize=False)),
                ("cost_usd", models.DecimalField(decimal_places=6, default=0, max_digits=14)),
            ],
            options={"db_table": "try_spend_days"},
        ),
    ]
