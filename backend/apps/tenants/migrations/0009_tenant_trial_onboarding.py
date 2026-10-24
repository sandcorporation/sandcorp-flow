import django.db.models.deletion
from django.db import migrations, models

import apps.tenants.models


# #41: 체험 한도(trial_turns_remaining)와 온보딩으로 만든 사람(created_by).
# 기존 Tenant도 TRIAL_TURNS(기본 200)를 받는다 — 자기 LLM Provider가 있으면 BYOK라 쓰이지 않고,
# 없는(대표가 만든 시험 조직 등) 조직은 플랫폼 키 체험으로 돈다. created_by는 기존 행에 비어 있다.
# 두 칼럼 모두 nullable/기본값이라 데이터가 있는 DB에서도 forward·backward가 깨끗하다.
class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0008_auth_webhook_events"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="trial_turns_remaining",
            field=models.PositiveIntegerField(default=apps.tenants.models.default_trial_turns),
        ),
        migrations.AddField(
            model_name="tenant",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_tenants",
                to="tenants.user",
            ),
        ),
    ]
