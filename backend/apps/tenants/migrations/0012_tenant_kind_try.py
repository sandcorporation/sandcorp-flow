"""테넌트 종류(standard·try·try_library)와 체험 테넌트의 예제·만료 시각(#134, ADR-0030).

무중단 롤링 배포(ADR-0015): 칸마다 DB 기본값(db_default)을 둬서, 이 칸을 모르는 옛 코드가 Tenant를 만들어도
kind=standard로 들어간다. 만료 시각은 null 허용.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0011_tenant_secrets_allowed_domains"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="kind",
            field=models.CharField(db_default="standard", default="standard", max_length=16),
        ),
        migrations.AddField(
            model_name="tenant",
            name="try_example_id",
            field=models.CharField(blank=True, db_default="", default="", max_length=64),
        ),
        migrations.AddField(
            model_name="tenant",
            name="try_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="tenant",
            index=models.Index(fields=["kind", "try_expires_at"], name="tenants_kind_expires_idx"),
        ),
    ]
