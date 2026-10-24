# 테넌트 비밀·허용 도메인(#33, ADR-0029). 새 표 둘만 만든다 — 되돌리면 표를 지운다(기존 데이터 영향 없음).
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0010_flow_deploy_approval'),
    ]

    operations = [
        migrations.CreateModel(
            name='TenantSecret',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=64)),
                ('ciphertext', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='secrets', to='tenants.tenant')),
            ],
            options={
                'db_table': 'tenant_secrets',
                'ordering': ['name'],
                'constraints': [models.UniqueConstraint(fields=('tenant', 'name'), name='tenant_secret_name_unique')],
            },
        ),
        migrations.CreateModel(
            name='TenantAllowedDomain',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('pattern', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='allowed_domains', to='tenants.tenant')),
            ],
            options={
                'db_table': 'tenant_allowed_domains',
                'ordering': ['pattern'],
                'constraints': [models.UniqueConstraint(fields=('tenant', 'pattern'), name='tenant_allowed_domain_unique')],
            },
        ),
    ]
