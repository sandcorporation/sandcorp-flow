"""예제 시드(#35): 지식 묶음 적재와 Sandcorp 테넌트(홈 상담 흐름).

- ``seed_example_knowledge``: 예제의 작은 문서들을 테넌트 지식으로 적재한다. 문서 id가 (테넌트, 예제, 이름)에서 정해져
  다시 돌려도 중복이 없다(이미 ready면 건너뜀, 실패·중단분만 다시).
- ``seed_sandcorp_tenant``: slug ``sandcorp`` 테넌트와 공개 흐름 「Sandcorp 홈 상담」을 만든다. 문의 접수 URL·키는
  설정값(``SANDCORP_INQUIRY_URL``, ``SANDCORP_INQUIRY_API_KEY``)에서 주입한다.
"""
from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings

from apps.flows.example_catalog import EXAMPLES, SANDCORP_HOME, example_graph
from apps.flows.schema import JSON

SANDCORP_SLUG = "sandcorp"
SANDCORP_NAME = "Sandcorp"
SANDCORP_FLOW_NAME = "Sandcorp 홈 상담"
INQUIRY_SECRET_NAME = "inquiry_api_key"
_DOC_NAMESPACE = uuid.UUID("5a2c3f0e-35b1-4f7e-9d0a-0f1e2d3c4b5a")


class UnknownExample(KeyError):
    pass


class KnowledgeSeedError(Exception):
    """일부 문서의 적재가 실패했다(Provider 미설정 등). 다시 돌리면 실패분만 이어 적재한다."""


def _doc_id(tenant_id: uuid.UUID, example_id: str, name: str) -> uuid.UUID:
    return uuid.uuid5(_DOC_NAMESPACE, f"{tenant_id}:{example_id}:{name}")


def example_doc_ids(tenant_id: uuid.UUID, example_id: str) -> list[uuid.UUID]:
    """그 테넌트에 적재된(또는 적재될) 예제 문서들의 id — 체험 테넌트의 지식 검색을 이 묶음으로 좁힐 때 쓴다(#134)."""
    if example_id not in EXAMPLES:
        raise UnknownExample(example_id)
    return [_doc_id(tenant_id, example_id, item.name) for item in EXAMPLES[example_id].docs]


def seed_example_knowledge(tenant_id: uuid.UUID, example_id: str, *, force: bool = False) -> list:
    """예제의 지식 문서를 테넌트에 적재하고 Document 목록을 돌려준다. ``force``면 ready여도 다시 적재."""
    from apps.rag.models import Document
    from apps.rag.tasks import ingest_document

    if example_id not in EXAMPLES:
        raise UnknownExample(example_id)
    media = os.path.join(settings.MEDIA_ROOT, "documents")
    os.makedirs(media, exist_ok=True)
    docs, failed = [], []
    for item in EXAMPLES[example_id].docs:
        doc_id = _doc_id(tenant_id, example_id, item.name)
        doc, _ = Document.objects.get_or_create(
            id=doc_id, defaults={"tenant_id": tenant_id, "name": item.name, "mime_type": "text/plain"}
        )
        if doc.status != Document.STATUS_READY or force:
            with open(os.path.join(media, str(doc_id)), "w", encoding="utf-8") as f:
                f.write(item.text)
            try:
                ingest_document.apply(args=(str(doc_id), str(tenant_id), "text/plain"), throw=True)
            except Exception:  # 태스크가 Document를 failed로 표시했다 — 나머지 문서를 계속 적재한다
                failed.append(item.name)
            doc.refresh_from_db()
        docs.append(doc)
    if failed:
        raise KnowledgeSeedError(f"{example_id}: {', '.join(failed)} 적재 실패 — 테넌트 Provider 설정을 확인하고 다시 돌리세요.")
    return docs


def sandcorp_flow_graph(inquiry_url: str, api_key: str) -> JSON:
    """예제 ①에 문의 접수 URL·(키가 있으면) Bearer 비밀 참조를 넣은 실제 흐름 JSON. 키 값은 흐름에 들어가지 않는다."""
    graph = example_graph(SANDCORP_HOME)
    for node in graph["nodes"]:
        if node["id"] == "submit":
            node["config"]["url"] = inquiry_url
            if api_key:
                node["config"]["headers"] = {"Authorization": f"Bearer {{{{secret.{INQUIRY_SECRET_NAME}}}}}"}
    return graph


@dataclass(frozen=True)
class SandcorpSeed:
    tenant_id: uuid.UUID
    flow_id: uuid.UUID
    created_tenant: bool
    flow_action: str  # "created" | "updated" | "kept"


def seed_sandcorp_tenant(*, update_flow: bool = False) -> SandcorpSeed:
    """Sandcorp 테넌트·공개 흐름을 만든다(있으면 그대로). ``update_flow``면 설정값으로 흐름을 다시 만들어 배포한다.

    관리 화면에서 고친 흐름을 덮어쓰지 않도록, 기본은 이미 있는 흐름을 건드리지 않는다.
    """
    from django.db import transaction

    from apps.flows import services
    from apps.flows.models import Flow
    from apps.tenants import tenant_secrets as ts
    from apps.tenants.models import Tenant

    url, key = settings.SANDCORP_INQUIRY_URL, settings.SANDCORP_INQUIRY_API_KEY
    host = urlsplit(url).hostname or ""
    with transaction.atomic():
        tenant = Tenant.objects.filter(slug=SANDCORP_SLUG).first()
        created = tenant is None
        if tenant is None:
            tenant = Tenant.objects.create_with_key(name=SANDCORP_NAME, raw_key=secrets.token_urlsafe(32))
            tenant.slug = SANDCORP_SLUG
            tenant.save(update_fields=["slug"])
        flow = Flow.objects.for_tenant(tenant.id).filter(name=SANDCORP_FLOW_NAME).first()
        if flow is not None and not update_flow:
            return SandcorpSeed(tenant.id, flow.id, created, "kept")

        from apps.flows.demo_api import HOST as DEMO_HOST

        if host and host != DEMO_HOST:  # 가짜 API는 허용 도메인이 필요 없다
            try:
                ts.add_domain(tenant.id, host)
            except ts.DomainExists:
                pass
        if key:
            ts.set_secret(tenant.id, INQUIRY_SECRET_NAME, key)
        graph = sandcorp_flow_graph(url, key)
        if flow is None:
            flow = services.create_flow(tenant.id, SANDCORP_FLOW_NAME, graph)
            action = "created"
        else:
            services.save_draft(tenant.id, flow.id, graph)
            action = "updated"
        version = services.save_version(tenant.id, flow.id, note="설정값으로 시드").version
        services.deploy_version(tenant.id, flow.id, version.number)
        Flow.objects.for_tenant(tenant.id).exclude(pk=flow.pk).filter(is_public=True).update(is_public=False)
        Flow.objects.filter(pk=flow.pk).update(is_public=True)
        return SandcorpSeed(tenant.id, flow.id, created, action)
