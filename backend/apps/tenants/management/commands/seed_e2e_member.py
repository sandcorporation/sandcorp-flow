"""E2E용 고객(멤버) 시드(#56). sandcorp-auth 콜백이 만드는 것과 같은 상태 — 일반 사용자
`User(iss, sub)`와 조직 `Membership` — 을 만들고 Flow 세션 Access Token을 파일에 쓴다.

`--accept-invite-from`을 주면 그 파일에 초대 원문이 쓰이길 기다렸다가, 콜백이 하는 것과 같이
`accept_invite`로 수락하고 수락한 사람의 세션 토큰을 `--token-file`에 쓴다(sandcorp-auth 대역).

운영에서 쓰지 않는다(시험용 긴 수명 토큰).
"""

import secrets
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from apps.tenants.auth import create_member_access_token
from apps.tenants.members import InviteRejected, accept_invite, member_user
from apps.tenants.models import Membership, Tenant, User
from apps.tenants.operator_login import IdentityClaims


class Command(BaseCommand):
    help = "E2E용 멤버를 시드하고 Flow 세션 Access Token을 파일에 쓴다."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--sub", default="m_E2EADMIN000000000000000001")
        parser.add_argument("--name", default="E2E 관리자")
        parser.add_argument("--email", default="e2e-admin@example.com")
        parser.add_argument("--tenant-name", default="E2E 고객사")
        parser.add_argument("--role", default=Membership.ROLE_ADMIN)
        parser.add_argument("--token-file", required=True)
        parser.add_argument("--accept-invite-from", default="")
        parser.add_argument("--wait-seconds", type=int, default=86400)
        parser.add_argument("--once", action="store_true", help="한 번 수락하면 끝낸다(기본은 계속 기다린다)")

    def handle(self, *args: object, **options: object) -> None:
        token_file = Path(str(options["token_file"]))
        token_file.parent.mkdir(parents=True, exist_ok=True)
        sub = str(options["sub"])
        identity = IdentityClaims(sub=sub, name=str(options["name"]), email=str(options["email"]))
        user = member_user(settings.AUTH_ISSUER, sub, identity)

        invite_file = str(options["accept_invite_from"])
        if invite_file:
            self._serve_invites(
                user, Path(invite_file), token_file, int(str(options["wait_seconds"])), bool(options["once"])
            )
            return
        else:
            tenant = Tenant.objects.filter(name=str(options["tenant_name"])).first() or (
                Tenant.objects.create_with_key(
                    name=str(options["tenant_name"]), raw_key=secrets.token_urlsafe(32)
                )
            )
            membership, _ = Membership.objects.get_or_create(
                user=user, tenant=tenant, defaults={"role": str(options["role"])}
            )
        token_file.write_text(create_member_access_token(membership))
        self.stdout.write(self.style.SUCCESS(f"Seeded member {user.sub} → {token_file}"))

    def _serve_invites(self, user: User, invite_file: Path, token_file: Path, wait_seconds: int, once: bool) -> None:
        """파일에 떨어진 초대 원문을 수락하는 대역. 다시 수락해도(내보낸 뒤 재초대) 같은 사람으로 돌아온다."""
        invite_file.unlink(missing_ok=True)  # 지난 실행이 남긴 원문은 쓰지 않는다
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            raw = invite_file.read_text().strip() if invite_file.exists() else ""
            if not raw:
                time.sleep(0.5)
                continue
            invite_file.unlink(missing_ok=True)
            try:
                membership = accept_invite(user, raw)
            except InviteRejected:
                continue
            token_file.write_text(create_member_access_token(membership))
            self.stdout.write(self.style.SUCCESS(f"Accepted invite as {user.sub} → {token_file}"))
            if once:
                return
