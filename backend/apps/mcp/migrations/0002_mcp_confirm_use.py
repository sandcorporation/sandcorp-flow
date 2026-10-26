# 2단계 확인 토큰 소비 기록(#209). 새 표 하나 — 기존 표·행은 건드리지 않는다.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mcp', '0001_mcp_audit_event'),
    ]

    operations = [
        migrations.CreateModel(
            name='McpConfirmUse',
            fields=[
                ('jti', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('expires_at', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'mcp_confirm_use',
            },
        ),
    ]
