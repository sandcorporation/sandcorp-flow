"""기존 테넌트(흐름 없음)에 기본 흐름을 만들어 배포 중·공개로 둔다(#23). 되돌리면 시드한 흐름만 지운다.

마이그레이션은 굳어 있어야 하므로 기본 흐름 JSON을 여기에 복사해 둔다(apps/flows/defaults.py가 나중에 바뀌어도
이 마이그레이션은 그대로). 역사 모델엔 FlowVersion.save의 검증·해시가 없어 해시를 직접 계산한다.
"""

from django.db import migrations

from apps.flows.schema import flow_hash

FLOW_NAME = "기본 흐름"

FLOW = {
    "nodes": [
        {"id": "start", "kind": "start", "code": "N-01", "name": "시작",
         "position": {"x": 0, "y": 0}, "config": {}},
        {"id": "search", "kind": "knowledge_search", "code": "N-02", "name": "지식 검색",
         "position": {"x": 240, "y": 0}, "config": {"top_k": 5}},
        {"id": "answer", "kind": "ai_answer", "code": "N-03", "name": "AI 답변",
         "position": {"x": 480, "y": 0}, "config": {}},
        {"id": "handoff", "kind": "handoff", "code": "N-04", "name": "사람 넘김",
         "position": {"x": 720, "y": 0}, "config": {}},
    ],
    "edges": [
        {"from": "start", "to": "search"},
        {"from": "search", "to": "answer"},
        {"from": "answer", "to": "handoff", "handle": "handoff"},
    ],
}


def seed_default_flows(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Flow = apps.get_model("flows", "Flow")
    FlowDraft = apps.get_model("flows", "FlowDraft")
    FlowVersion = apps.get_model("flows", "FlowVersion")
    digest = flow_hash(FLOW)

    for tenant in Tenant.objects.exclude(flows__isnull=False).only("id"):
        flow = Flow.objects.create(tenant_id=tenant.id, name=FLOW_NAME, is_public=True)
        FlowDraft.objects.create(flow=flow, graph=FLOW)
        version = FlowVersion.objects.create(flow=flow, number=1, graph=FLOW, hash=digest)
        Flow.objects.filter(pk=flow.pk).update(deployed_version=version)


def unseed_default_flows(apps, schema_editor):
    """시드 모양 그대로인 흐름(이름이 같고 버전이 기본 흐름 하나뿐)만 지운다. 거기에 고정된 대화는 고정을 푼다."""
    Flow = apps.get_model("flows", "Flow")
    FlowVersion = apps.get_model("flows", "FlowVersion")
    ChatSession = apps.get_model("chat", "ChatSession")
    digest = flow_hash(FLOW)

    seeded = [
        f.pk for f in Flow.objects.filter(name=FLOW_NAME)
        if list(FlowVersion.objects.filter(flow=f).values_list("hash", flat=True)) == [digest]
    ]
    versions = FlowVersion.objects.filter(flow_id__in=seeded)
    ChatSession.objects.filter(flow_version__in=versions).update(flow_version=None)
    Flow.objects.filter(pk__in=seeded).update(deployed_version=None)
    Flow.objects.filter(pk__in=seeded).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("flows", "0001_initial"),
        ("tenants", "0009_tenant_trial_onboarding"),
        ("chat", "0002_chatsession_flow_version"),
    ]

    operations = [migrations.RunPython(seed_default_flows, unseed_default_flows)]
