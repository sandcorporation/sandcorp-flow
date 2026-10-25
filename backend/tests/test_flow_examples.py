"""예제 흐름 셋·지식 묶음·Sandcorp 테넌트 시드(#35).

- 세 예제(Sandcorp 홈·쇼핑몰·학원)가 스키마·테넌트 검증을 통과하고, Fake LLM으로 대표 경로를 끝까지 돈다.
- HTTP 노드는 예약 호스트(가짜 API, #131)를 프로세스 안에서 부른다 — ``net`` 대역으로 네트워크 0을 박는다.
- 시드(지식 묶음·Sandcorp 테넌트)는 두 번 돌려도 중복이 없다.
"""
from __future__ import annotations

import pytest
from django.core.management import call_command

from tests.test_flow_http_tool import Net, _http_entry, _path, _turn, net, tool_pki  # noqa: F401

EXAMPLE_IDS = ["sandcorp_home", "shop_support", "academy_reservation"]


def _no_network(n: Net) -> None:
    assert n.dns.calls == [] and n.route == [] and n.hits.items == []


def _classify(fake, **result) -> None:
    fake.by_schema["ClassifyResult"] = lambda messages, schema: schema(**result)


def _graph(example_id: str) -> dict:
    from apps.flows.example_catalog import example_graph

    return example_graph(example_id)


# ── 카탈로그·스키마 ──────────────────────────────────────────────────────────

def test_catalog_lists_the_three_examples():
    from apps.flows.example_catalog import EXAMPLES

    assert list(EXAMPLES) == EXAMPLE_IDS
    assert all(len(ex.docs) >= 3 for ex in EXAMPLES.values())


@pytest.mark.parametrize("example_id", EXAMPLE_IDS)
def test_example_passes_schema_and_compiles(example_id):
    from apps.flows.executor import compile_flow
    from apps.flows.schema import validate_flow

    graph = _graph(example_id)
    assert validate_flow(graph) == []
    compile_flow(graph)


@pytest.mark.django_db
@pytest.mark.parametrize("example_id", EXAMPLE_IDS)
def test_example_needs_no_secret_or_allowed_domain(tenant_with_key, example_id):
    """템플릿은 비밀 없이(unknown_secret 없이) 어느 테넌트에서나 저장된다."""
    from apps.flows.services import validate_for_tenant

    tenant, _ = tenant_with_key
    assert validate_for_tenant(tenant.id, _graph(example_id)) == []


@pytest.mark.parametrize("example_id", EXAMPLE_IDS)
def test_http_nodes_only_call_the_fake_api(example_id):
    import json

    from apps.flows import demo_api

    for node in _graph(example_id)["nodes"]:
        if node["kind"] == "http_tool":
            assert node["config"]["url"].startswith(f"https://{demo_api.HOST}/")
            assert "headers" not in node["config"]
    assert "secret." not in json.dumps(_graph(example_id))


# ── ② 쇼핑몰: 주문 조회 갈래 ─────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("order_no", "last", "status"), [
    ("10021", "say_shipping", "shipping"),
    ("10024", "say_delivered", "delivered"),
    ("10027", "say_preparing", "preparing"),
    ("10020", "say_missing", "not_found"),
])
async def test_shop_order_lookup_branches(tenant_with_key, fake_chat_llm, net, order_no, last, status):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="order_status", confidence=0.9, fields={"order_no": order_no})

    result, _ = await _turn(tenant, _graph("shop_support"), f"{order_no} 어디쯤이에요?")

    assert _path(result) == ["start", "classify", "route", "has_order", "order", "result", last]
    assert result["fields"]["order_status"] == status
    assert _http_entry(result)["record"]["request"]["url"].endswith(f"/orders/{order_no}")
    _no_network(net)


@pytest.mark.django_db(transaction=True)
async def test_shop_asks_for_the_order_number_when_missing(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="order_status", confidence=0.9, fields={"order_no": None})

    result, _ = await _turn(tenant, _graph("shop_support"), "배송 언제 와요?")

    assert _path(result) == ["start", "classify", "route", "has_order", "ask_order"]
    _no_network(net)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("order_no", ["9500", "9408"])
async def test_shop_lookup_failure_takes_the_error_branch(tenant_with_key, fake_chat_llm, net, order_no):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="order_status", confidence=0.9, fields={"order_no": order_no})

    result, _ = await _turn(tenant, _graph("shop_support"), order_no)

    assert _path(result) == ["start", "classify", "route", "has_order", "order", "say_error"]
    _no_network(net)


@pytest.mark.django_db(transaction=True)
async def test_shop_return_question_searches_the_policy_and_human_hands_off(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="return_or_faq", confidence=0.9, fields={"order_no": None})
    result, _ = await _turn(tenant, _graph("shop_support"), "반품하고 싶어요")
    assert _path(result) == ["start", "classify", "route", "policy", "policy_answer"]

    _classify(fake_chat_llm, label="human_request", confidence=0.9, fields={"order_no": None})
    result, _ = await _turn(tenant, _graph("shop_support"), "사람이랑 얘기할래요")
    assert _path(result) == ["start", "classify", "route", "handoff"]
    _no_network(net)


# ── ③ 학원: 레벨 테스트 예약 ─────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_academy_reservation_runs_to_the_confirmation(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="level_test", confidence=0.9,
              fields={"date": "2026-11-03", "level": "초급", "name": "김하늘"})

    result, _ = await _turn(tenant, _graph("academy_reservation"), "11월 3일 초급 레벨 테스트 예약해 주세요")

    assert _path(result) == ["start", "classify", "route", "ready", "reserve", "done", "confirm"]
    assert result["fields"]["reserved"] is True
    assert result["fields"]["time"] == "15:00" and result["fields"]["level_label"] == "초급"
    assert result["fields"]["reservation_no"].startswith("LT-20261103-")
    _no_network(net)


@pytest.mark.django_db(transaction=True)
async def test_academy_asks_for_missing_info_and_reports_a_bad_booking(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="level_test", confidence=0.9, fields={"date": None, "level": "중급", "name": None})
    result, _ = await _turn(tenant, _graph("academy_reservation"), "중급 테스트 보고 싶어요")
    assert _path(result) == ["start", "classify", "route", "ready", "ask"]

    # 날짜 모양이 틀리면 가짜 API가 400 — 오류 갈래
    _classify(fake_chat_llm, label="level_test", confidence=0.9, fields={"date": "다음 주", "level": "중급", "name": None})
    result, _ = await _turn(tenant, _graph("academy_reservation"), "다음 주 중급")
    assert _path(result) == ["start", "classify", "route", "ready", "reserve", "say_error"]
    _no_network(net)


@pytest.mark.django_db(transaction=True)
async def test_academy_course_question_is_searched_and_guarded(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    fake_chat_llm.by_schema["GuardrailVerdict"] = lambda m, schema: schema(violates=False)
    _classify(fake_chat_llm, label="course_info", confidence=0.9, fields={"date": None, "level": None, "name": None})

    result, _ = await _turn(tenant, _graph("academy_reservation"), "수강료가 얼마예요?")

    assert _path(result) == ["start", "classify", "route", "courses", "course_answer", "guard"]
    _no_network(net)


# ── ① Sandcorp 홈: 문의는 홈 문의 폼으로 안내 ───────────────────────────────

def test_sandcorp_template_has_no_http_tool_node():
    kinds = {n["kind"] for n in _graph("sandcorp_home")["nodes"]}
    assert "http_tool" not in kinds


@pytest.mark.django_db(transaction=True)
async def test_sandcorp_project_inquiry_ends_with_the_home_form_guide(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    fake_chat_llm.by_schema["GuardrailVerdict"] = lambda m, schema: schema(violates=False)
    _classify(fake_chat_llm, label="project_inquiry", confidence=0.9, fields={"requirements_complete": True})

    result, _ = await _turn(tenant, _graph("sandcorp_home"), "쇼핑몰을 한 달 안에 만들고 싶어요")

    assert _path(result) == ["start", "classify", "route", "enough", "cases", "draft", "guard", "contact"]
    assert not [t for t in result["trace"] if t["kind"] == "http_tool"]
    contact = next(n for n in _graph("sandcorp_home")["nodes"] if n["id"] == "contact")
    assert "https://sandcorp.co.kr/#home-inquiry" in contact["config"]["instructions"]  # 안내 답이 이 링크로 이어진다
    assert "@" not in contact["config"]["instructions"]  # 이메일 주소는 넣지 않는다
    _no_network(net)


_HOME_FORM = "https://sandcorp.co.kr/#home-inquiry"
_SANDCORP_FIELDS = {"requirements_complete": False}


def _node(example_id: str, node_id: str) -> dict:
    return next(n for n in _graph(example_id)["nodes"] if n["id"] == node_id)


@pytest.mark.django_db(transaction=True)
async def test_sandcorp_company_question_searches_knowledge_before_answering(tenant_with_key, fake_chat_llm, net):
    """#140: 「어떤 회사인가요?」(other)는 지식 검색을 거쳐 답한다 — 검색 없이 일반론을 지어내지 않는다."""
    from asgiref.sync import sync_to_async

    from apps.flows.example_seed import seed_example_knowledge

    tenant, _ = tenant_with_key
    await sync_to_async(seed_example_knowledge)(tenant.id, "sandcorp_home")
    _classify(fake_chat_llm, label="other", confidence=0.7, fields=_SANDCORP_FIELDS)

    result, _ = await _turn(tenant, _graph("sandcorp_home"), "샌드코프는 어떤 회사인가요?")

    assert _path(result) == ["start", "classify", "route", "kb", "general"]
    assert result["trace"][3]["kind"] == "knowledge_search"
    assert result["trace"][3]["record"]["output"]["chunks"] >= 1  # 판단 기록에 검색 단계와 찾은 건수가 남는다
    answer_prompt = "\n".join(str(m) for m in fake_chat_llm.captured[-1][1])
    assert "### Knowledge Base" in answer_prompt and "회사 소개" in answer_prompt  # 찾은 지식이 답 노드 프롬프트에 들어갔다
    general = _node("sandcorp_home", "general")["config"]["instructions"]
    assert "찾은 지식" in general and "모른다" in general
    _no_network(net)


def test_sandcorp_classify_labels_separate_contact_request_from_project_inquiry():
    classify = _node("sandcorp_home", "classify")["config"]
    assert "contact_request" in classify["labels"]
    for label in classify["labels"]:  # 라벨 경계는 지침에 설명으로 적는다
        assert label in classify["instructions"]


@pytest.mark.django_db(transaction=True)
async def test_sandcorp_contact_request_goes_straight_to_the_form_guide(tenant_with_key, fake_chat_llm, net):
    """#140: 「문의는 어떻게 하나요?」는 요구사항을 되묻지 않고 바로 문의 폼 안내."""
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="contact_request", confidence=0.9, fields=_SANDCORP_FIELDS)

    result, _ = await _turn(tenant, _graph("sandcorp_home"), "프로젝트 문의를 하고 싶은데 어떻게 하면 되나요?")

    assert _path(result) == ["start", "classify", "route", "contact"]
    assert _HOME_FORM in _node("sandcorp_home", "contact")["config"]["instructions"]
    _no_network(net)


@pytest.mark.django_db(transaction=True)
async def test_sandcorp_incomplete_requirements_still_ask_first(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="project_inquiry", confidence=0.9, fields=_SANDCORP_FIELDS)

    result, _ = await _turn(tenant, _graph("sandcorp_home"), "쇼핑몰을 만들고 싶어요")

    assert _path(result) == ["start", "classify", "route", "enough", "ask"]
    _no_network(net)


def test_sandcorp_knowledge_makes_no_chat_intake_email_or_reply_deadline_promise():
    from apps.flows.example_catalog import EXAMPLES

    docs = {d.name: d.text for d in EXAMPLES["sandcorp_home"].docs}
    for name, text in docs.items():
        for banned in ("상담창에서 남기면", "접수되고", "회신", "@", "이내"):
            assert banned not in text, f"{name}: {banned!r}"
    assert _HOME_FORM in docs["진행 방식과 문의"]
    assert "이 상담창에서는 문의를 접수하지 않습니다" in docs["진행 방식과 문의"]


# ── 지식 묶음 시드 ───────────────────────────────────────────────────────────

@pytest.mark.django_db
@pytest.mark.parametrize("example_id", EXAMPLE_IDS)
def test_seeding_knowledge_twice_leaves_one_ready_set(tenant_with_key, example_id):
    from apps.flows.example_catalog import EXAMPLES
    from apps.flows.example_seed import seed_example_knowledge
    from apps.rag.graph_store import GraphStore
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    expected = {d.name for d in EXAMPLES[example_id].docs}

    seed_example_knowledge(tenant.id, example_id)
    ids = sorted(str(d.id) for d in Document.objects.filter(tenant_id=tenant.id))
    mentions = sorted(m["name"] for m in GraphStore(str(tenant.id)).query_mentions())
    seed_example_knowledge(tenant.id, example_id)  # 두 번째

    docs = Document.objects.filter(tenant_id=tenant.id)
    assert {d.name for d in docs} == expected and docs.count() == len(expected)
    assert all(d.status == Document.STATUS_READY for d in docs)
    assert sorted(str(d.id) for d in docs) == ids
    assert sorted(m["name"] for m in GraphStore(str(tenant.id)).query_mentions()) == mentions
    assert expected <= set(mentions)  # 문서 레이블이 그래프에 올라 있다


@pytest.mark.django_db
def test_two_tenants_get_their_own_knowledge_copies():
    import secrets

    from apps.flows.example_seed import seed_example_knowledge
    from apps.rag.models import Document
    from apps.tenants.models import Tenant

    a = Tenant.objects.create_with_key(name="A", raw_key=secrets.token_urlsafe(32))
    b = Tenant.objects.create_with_key(name="B", raw_key=secrets.token_urlsafe(32))
    seed_example_knowledge(a.id, "shop_support")
    seed_example_knowledge(b.id, "shop_support")

    ids_a = set(Document.objects.filter(tenant_id=a.id).values_list("id", flat=True))
    ids_b = set(Document.objects.filter(tenant_id=b.id).values_list("id", flat=True))
    assert len(ids_a) == len(ids_b) == 3 and not ids_a & ids_b


@pytest.mark.django_db
def test_seed_examples_command_by_slug_and_unknown_example(tenant_with_key):
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    tenant.slug = "seed-shop"
    tenant.save(update_fields=["slug"])

    call_command("seed_examples", "--tenant", "seed-shop", "--example", "shop_support")
    call_command("seed_examples", "--tenant", "seed-shop", "--example", "shop_support")
    assert Document.objects.filter(tenant_id=tenant.id).count() == 3

    with pytest.raises(Exception, match="nope"):
        call_command("seed_examples", "--tenant", "seed-shop", "--example", "nope")


# ── Sandcorp 테넌트 시드 ─────────────────────────────────────────────────────

def _sandcorp_flow():
    from apps.flows.models import Flow
    from apps.tenants.models import Tenant

    tenant = Tenant.objects.get(slug="sandcorp")
    return tenant, Flow.objects.get(tenant_id=tenant.id)


@pytest.mark.django_db
def test_seed_sandcorp_creates_tenant_public_flow_and_knowledge_once():
    from apps.flows.models import Flow, FlowVersion
    from apps.rag.models import Document
    from apps.tenants.models import Tenant

    call_command("seed_sandcorp")
    call_command("seed_sandcorp")

    assert Tenant.objects.filter(slug="sandcorp").count() == 1
    tenant, flow = _sandcorp_flow()
    assert tenant.name == "Sandcorp"
    assert Flow.objects.filter(tenant_id=tenant.id).count() == 1
    assert FlowVersion.objects.filter(flow=flow).count() == 1
    assert flow.is_public and flow.deployed_version is not None
    assert _graph("sandcorp_home")["nodes"] == flow.deployed_version.graph["nodes"]  # 기본값은 템플릿 그대로
    assert Document.objects.filter(tenant_id=tenant.id, status=Document.STATUS_READY).count() == 3


@pytest.mark.django_db
def test_seed_sandcorp_creates_no_secret_or_allowed_domain():
    from apps.flows.services import validate_for_tenant
    from apps.tenants import tenant_secrets as ts

    call_command("seed_sandcorp", "--skip-knowledge")
    tenant, flow = _sandcorp_flow()

    assert list(ts.secret_names(tenant.id)) == [] and list(ts.list_domains(tenant.id)) == []
    assert validate_for_tenant(tenant.id, flow.deployed_version.graph) == []


@pytest.mark.django_db
def test_seed_sandcorp_update_flow_replaces_an_old_graph_with_inquiry_node():
    """운영에 옛 그래프(문의 접수 노드 있음)로 시드돼 있어도 --update-flow가 새 그래프로 바꿔 배포·공개한다."""
    from apps.flows import services
    from apps.flows.models import Flow, FlowVersion

    call_command("seed_sandcorp", "--skip-knowledge")
    tenant, flow = _sandcorp_flow()
    old = _graph("sandcorp_home")
    for n in old["nodes"]:  # 옛 그래프는 contact_request 갈래·회사 지식 노드가 없다
        if n["id"] == "route":
            n["config"]["branches"] = [br for br in n["config"]["branches"] if br["name"] != "contact"]
    old["nodes"] = [n for n in old["nodes"] if n["id"] not in ("contact", "kb")] + [
        {"id": "submit", "kind": "http_tool", "code": "N-09", "name": "문의 접수", "position": {"x": 0, "y": 0},
         "config": {"method": "POST", "url": "https://demo-api.flow.sandcorp.co.kr/inquiries"}}]
    old["edges"] = [e for e in old["edges"] if not {"contact", "kb"} & {e["from"], e["to"]}] + [
        {"from": "guard", "to": "submit"}, {"from": "route", "to": "general", "handle": "else"}]
    services.save_draft(tenant.id, flow.id, old)
    version = services.save_version(tenant.id, flow.id, note="옛 그래프").version
    services.deploy_version(tenant.id, flow.id, version.number)

    call_command("seed_sandcorp", "--skip-knowledge")  # 기본은 불변
    assert any(n["id"] == "submit" for n in Flow.objects.get(pk=flow.pk).deployed_version.graph["nodes"])
    call_command("seed_sandcorp", "--skip-knowledge", "--update-flow")

    flow = Flow.objects.get(pk=flow.pk)
    assert flow.is_public and FlowVersion.objects.filter(flow=flow).count() == 3
    assert flow.deployed_version.graph["nodes"] == _graph("sandcorp_home")["nodes"]
    assert flow.draft.graph == flow.deployed_version.graph


@pytest.mark.django_db
def test_seed_sandcorp_update_flow_reingests_changed_knowledge():
    """#140: 지식 문서 내용이 바뀌면 --update-flow가 이미 ready인 문서도 다시 적재한다(기본은 그대로)."""
    import os

    from django.conf import settings

    from apps.flows.example_catalog import EXAMPLES
    from apps.flows.example_seed import example_doc_ids

    call_command("seed_sandcorp")
    tenant, _ = _sandcorp_flow()
    doc_id = example_doc_ids(tenant.id, "sandcorp_home")[0]
    path = os.path.join(settings.MEDIA_ROOT, "documents", str(doc_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write("옛 내용")

    call_command("seed_sandcorp")  # 기본은 ready 문서를 건드리지 않는다
    assert open(path, encoding="utf-8").read() == "옛 내용"
    call_command("seed_sandcorp", "--update-flow")
    assert open(path, encoding="utf-8").read() == EXAMPLES["sandcorp_home"].docs[0].text
