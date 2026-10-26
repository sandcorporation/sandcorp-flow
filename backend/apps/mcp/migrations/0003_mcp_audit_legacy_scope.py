# 옛 스코프(flow.write)로만 통과한 배포 호출 표시(#210, 우산 #207). 기본값 False라 기존 행은 그대로다.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mcp', '0002_mcp_confirm_use'),
    ]

    operations = [
        migrations.AddField(
            model_name='mcpauditevent',
            name='legacy_scope',
            field=models.BooleanField(default=False),
        ),
    ]
