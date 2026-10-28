"""흐름별 임베드(#246, ADR-0034): ``embed_key``·``embed_enabled``. 기존 흐름에 테넌트 안에서 겹치지 않는 키를 발급한다."""

import secrets
import string

from django.db import migrations, models

_ALPHABET = string.ascii_lowercase + string.digits


def _new_key() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def issue_embed_keys(apps, schema_editor):
    """키가 빈 흐름에 8자 소문자·숫자 키를 발급한다. 이미 키가 있으면 그대로 둔다(멱등)."""
    Flow = apps.get_model("flows", "Flow")
    taken: dict[object, set[str]] = {}
    for tenant_id, key in Flow.objects.exclude(embed_key="").values_list("tenant_id", "embed_key"):
        taken.setdefault(tenant_id, set()).add(key)
    for flow in Flow.objects.filter(embed_key="").only("pk", "tenant_id"):
        keys = taken.setdefault(flow.tenant_id, set())
        key = _new_key()
        while key in keys:
            key = _new_key()
        keys.add(key)
        Flow.objects.filter(pk=flow.pk).update(embed_key=key)


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0006_flow_archived_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="flow",
            name="embed_key",
            field=models.CharField(default="", editable=False, max_length=8),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="flow",
            name="embed_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(issue_embed_keys, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="flow",
            constraint=models.UniqueConstraint(fields=("tenant", "embed_key"), name="flow_unique_embed_key_per_tenant"),
        ),
    ]
