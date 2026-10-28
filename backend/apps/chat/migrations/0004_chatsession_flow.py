"""흐름별 임베드(#246, ADR-0034): 세션 재사용 키를 (tenant, visitor, flow)로. 기존 세션의 ``flow``는 고정 버전의 흐름으로 채운다."""

import django.db.models.deletion
from django.db import migrations, models


def fill_session_flow(apps, schema_editor):
    """``flow``가 빈 세션을 ``flow_version.flow``로 채운다. 고정 버전이 없는 세션(기본 흐름 없는 테넌트·시험 세션)은 비워 둔다."""
    ChatSession = apps.get_model("chat", "ChatSession")
    FlowVersion = apps.get_model("flows", "FlowVersion")
    flow_of = dict(FlowVersion.objects.values_list("pk", "flow_id"))
    pending = ChatSession.objects.filter(flow__isnull=True, flow_version__isnull=False)
    for version_id in pending.values_list("flow_version_id", flat=True).distinct():
        pending.filter(flow_version_id=version_id).update(flow_id=flow_of[version_id])


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0003_chatsession_is_test"),
        ("flows", "0007_flow_embed_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="flow",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sessions", to="flows.flow"
            ),
        ),
        migrations.AddField(
            model_name="chatsession",
            name="opened_by_key",
            field=models.BooleanField(default=False),
        ),
        migrations.RemoveIndex(
            model_name="chatsession",
            name="chat_sessio_tenant__f3ac56_idx",
        ),
        migrations.AddIndex(
            model_name="chatsession",
            index=models.Index(fields=["tenant_id", "visitor_id", "flow"], name="chat_session_visitor_flow_idx"),
        ),
        migrations.RunPython(fill_session_flow, migrations.RunPython.noop),
    ]
