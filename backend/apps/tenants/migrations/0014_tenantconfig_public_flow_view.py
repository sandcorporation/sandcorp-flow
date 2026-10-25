"""「공개 흐름 보기」 설정(#39).

무중단 롤링 배포(ADR-0015): 칸 하나를 DB 기본값(False)과 함께 더한다 — 옛 코드가 이 칸 없이 행을 넣어도 꺼진 채로 들어간다.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0013_try_session_quota"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantconfig",
            name="public_flow_view",
            field=models.BooleanField(db_default=False, default=False),
        ),
    ]
