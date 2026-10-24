import json


def open_stream(client, tenant, visitor_id="v-1", slug=None):
    """slug를 보장한 뒤 slug+visitor_id로 stream 연결 응답을 반환한다 (issue 85 흐름).

    EmbedToken 폐지 후 테스트의 표준 연결 경로. tenant에 slug가 없으면 부여한다.
    """
    if slug is None:
        slug = tenant.slug or f"t-{str(tenant.id).split('-')[0]}"
    if tenant.slug != slug:
        tenant.slug = slug
        tenant.save(update_fields=["slug"])
    return client.get(f"/api/chat/stream?slug={slug}&visitor_id={visitor_id}")


async def aopen_stream(tenant, visitor_id="v-1", slug=None):
    """async SSE 연결(AsyncClient) — slug 보장 후 stream 응답을 반환한다(노드 async화 이후 표준)."""
    from asgiref.sync import sync_to_async
    from django.test import AsyncClient

    if slug is None:
        slug = tenant.slug or f"t-{str(tenant.id).split('-')[0]}"
    if tenant.slug != slug:
        def _save():
            tenant.slug = slug
            tenant.save(update_fields=["slug"])
        await sync_to_async(_save)()
    return await AsyncClient().get(f"/api/chat/stream?slug={slug}&visitor_id={visitor_id}")


async def aread_first_chunk(resp) -> str:
    """async SSE 응답의 첫 청크(connected 이벤트)를 디코딩해 반환한다."""
    async for chunk in resp.streaming_content:
        return chunk.decode()
    return ""


def get_redis_message(pubsub, timeout=2.0):
    """Read the next non-subscribe message from a pubsub handle."""
    msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
    if msg and msg["type"] == "message":
        return json.loads(msg["data"])
    return None


def make_membership(tenant, role="member", *, sub=None, name=None, email=None, is_active=True):
    """일반 사용자 `User(iss, sub)` + 이 Tenant의 `Membership`을 만든다(#40 — 옛 TenantAgent 자리)."""
    import uuid

    from apps.tenants.models import Membership, User

    user = User.objects.create(
        iss="https://auth.sandcorp.co.kr",
        sub=sub or f"m_{uuid.uuid4().hex[:26].upper()}",
        acct=User.Acct.MEMBER,
        name=name,
        email=email,
    )
    return Membership.objects.create(user=user, tenant=tenant, role=role, is_active=is_active)


def member_token(membership):
    from apps.tenants.auth import create_member_access_token

    return create_member_access_token(membership)
