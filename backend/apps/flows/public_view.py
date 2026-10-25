"""공개 흐름 보기(#39) — 익명에게 보이는 흐름 투영 (deep module).

테넌트가 「공개 흐름 보기」(``TenantConfig.public_flow_view``)를 켜면 공개 흐름(``Flow.is_public``)의 **배포 버전**을
누구나 읽기 전용으로 본다. 익명 표면이라 **허용 목록**으로만 직렬화한다 — 노드 종류마다 보여도 되는 config 필드만 담고
나머지(모르는 종류·모르는 필드 포함)는 버린다. 거부 목록 방식은 쓰지 않는다(새 필드가 생기면 조용히 새어 나간다).

- 비밀 참조(``{{secret.*}}``)가 든 값은 값째 뺀다(목록이면 그 항목만).
- HTTP 도구: ``method``만. ``url``은 가짜 API 예약 호스트(``demo_api.HOST``)일 때만 경로까지(쿼리·조각 뺌) — 그 밖의 주소는
  호스트조차 테넌트 내부 정보라 뺀다. 헤더·바디·응답 저장 경로는 늘 뺀다.
- 가드레일 금지어 목록은 뺀다(테넌트가 막으려는 말 자체가 정보다).
- 응답에 테넌트·흐름 id, 테넌트 이름, Provider·웹훅·허용 도메인·지식 문서는 없다(그 값들은 그래프 밖이라 애초에 담지 않는다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from apps.flows import demo_api
from apps.flows.example_catalog import EXAMPLES
from apps.flows.schema import DEFAULT_HANDLE, JSON, NODE_CONFIG_MODELS, NodeKind, flow_hash

PUBLIC_FIELDS: dict[NodeKind, tuple[str, ...]] = {
    NodeKind.START: (),
    NodeKind.AI_ANSWER: ("instructions", "await_reply"),
    NodeKind.CONDITION: ("branches",),
    NodeKind.HANDOFF: ("reason",),
    NodeKind.KNOWLEDGE_SEARCH: ("top_k",),
    NodeKind.AI_CLASSIFY: ("instructions", "labels", "fields"),
    NodeKind.GUARDRAIL: ("rules", "action", "target", "block_message"),
    NodeKind.HTTP_TOOL: ("method", "url"),
}
"""노드 종류 → 공개해도 되는 config 필드. 새 종류는 여기 넣기 전까지 config가 비어 나간다(시험이 전수를 본다)."""

_ITEM_FIELDS: dict[tuple[NodeKind, str], tuple[str, ...]] = {
    (NodeKind.CONDITION, "branches"): ("name", "when"),
    (NodeKind.AI_CLASSIFY, "fields"): ("name", "description", "type"),
}
"""목록 안 객체의 허용 필드. 여기 없는 목록은 글자·숫자 항목만 남긴다."""

_SECRET_RE = re.compile(r"\{\{[^}]*secret", re.IGNORECASE)
_DROP = object()

Scalar = str | int | float | bool | None


def _scalar(value: JSON) -> Scalar | object:
    """공개 가능한 스칼라면 그대로, 비밀 참조가 든 글자나 스칼라가 아닌 값이면 ``_DROP``."""
    if isinstance(value, str):
        return _DROP if _SECRET_RE.search(value) else value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _DROP


def _pick(source: dict, allowed: tuple[str, ...]) -> dict[str, Scalar]:
    out: dict[str, Scalar] = {}
    for key in allowed:
        if key in source:
            value = _scalar(source[key])
            if value is not _DROP:
                out[key] = value  # type: ignore[assignment]  # _DROP을 걸렀다
    return out


def _public_url(value: JSON) -> str | object:
    if not isinstance(value, str) or _SECRET_RE.search(value) or not demo_api.is_reserved(value):
        return _DROP
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _field(kind: NodeKind, key: str, value: JSON) -> JSON | object:
    if kind is NodeKind.HTTP_TOOL and key == "url":
        return _public_url(value)
    if isinstance(value, list):
        item_fields = _ITEM_FIELDS.get((kind, key))
        items: list[JSON] = []
        for item in value:
            if item_fields is not None:
                if isinstance(item, dict):
                    items.append(_pick(item, item_fields))
            else:
                picked = _scalar(item)
                if picked is not _DROP:
                    items.append(picked)  # type: ignore[arg-type]  # _DROP을 걸렀다
        return items
    return _scalar(value)


def _config(kind_raw: JSON, config: JSON) -> dict[str, JSON]:
    try:
        kind = NodeKind(kind_raw)
    except ValueError:
        return {}
    source = config if isinstance(config, dict) else {}
    try:  # 기본값까지 채운 모양으로(배포 버전은 검증을 통과했다). 못 읽으면 원문에서 허용 필드만.
        source = NODE_CONFIG_MODELS[kind].model_validate(source).model_dump(mode="json")
    except ValidationError:
        pass
    out: dict[str, JSON] = {}
    for key in PUBLIC_FIELDS.get(kind, ()):
        if key in source:
            value = _field(kind, key, source[key])
            if value is not _DROP:
                out[key] = value  # type: ignore[assignment]  # _DROP을 걸렀다
    return out


def _position(value: JSON) -> dict[str, float]:
    pos = value if isinstance(value, dict) else {}
    return {axis: float(v) if isinstance(v := pos.get(axis), (int, float)) else 0.0 for axis in ("x", "y")}


def public_graph(graph: JSON) -> dict[str, list[dict[str, JSON]]]:
    """흐름 그래프의 공개 투영(허용 목록). 노드는 id·kind·code·name·position·config만, 선은 from·to·handle만."""
    raw = graph if isinstance(graph, dict) else {}
    nodes: list[dict[str, JSON]] = []
    for node in raw.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        out = _pick(node, ("id", "kind", "code", "name"))
        out_node: dict[str, JSON] = dict(out)
        out_node["position"] = _position(node.get("position"))
        out_node["config"] = _config(node.get("kind"), node.get("config"))
        nodes.append(out_node)
    edges: list[dict[str, JSON]] = []
    for edge in raw.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source, target = edge.get("from", edge.get("source")), edge.get("to", edge.get("target"))
        handle = edge.get("handle")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        edges.append({"from": source, "to": target,
                      "handle": handle if isinstance(handle, str) and not _SECRET_RE.search(handle) else DEFAULT_HANDLE})
    return {"nodes": nodes, "edges": edges}


def template_for(graph: JSON) -> str | None:
    """배포 그래프가 예제 그대로면 그 체험 템플릿 id(「고쳐 보기」 → ``/try?template=``). 고친 흐름은 None."""
    digest = flow_hash(graph)
    return next((ex_id for ex_id, ex in EXAMPLES.items() if flow_hash(ex.graph) == digest), None)


@dataclass(frozen=True)
class PublicFlow:
    slug: str
    brand_name: str
    flow_name: str
    version: int
    graph: dict[str, list[dict[str, JSON]]]
    template: str | None


def public_flow(slug: str) -> PublicFlow | None:
    """공개 보기가 켜진 활성 테넌트의 공개 흐름 배포 버전. 없거나 꺼졌거나 보관·미배포면 모두 None(같은 404)."""
    from apps.flows.models import Flow
    from apps.tenants.models import Tenant, TenantConfig

    tenant = Tenant.resolve_slug(slug)
    if tenant is None or tenant.kind != "standard":
        return None
    config = TenantConfig.objects.filter(tenant_id=tenant.id).first()
    if config is None or not config.public_flow_view:
        return None
    flow = (Flow.objects.for_tenant(tenant.id).active().filter(is_public=True)
            .select_related("deployed_version").first())
    if flow is None or flow.deployed_version is None:
        return None
    graph = flow.deployed_version.graph
    return PublicFlow(
        slug=tenant.slug or slug,
        brand_name=config.brand_name or "",
        flow_name=flow.name,
        version=flow.deployed_version.number,
        graph=public_graph(graph),
        template=template_for(graph),
    )
