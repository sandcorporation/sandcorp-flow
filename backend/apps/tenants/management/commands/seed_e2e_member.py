"""E2E용 고객(멤버) 시드(#56). sandcorp-auth 콜백이 만드는 것과 같은 상태 — 일반 사용자
`User(iss, sub)`와 조직 `Membership` — 을 만들고 Flow 세션 Access Token을 파일에 쓴다.

`--accept-invite-from`을 주면 그 파일에 초대 원문이 쓰이길 기다렸다가, 콜백이 하는 것과 같이
`accept_invite`로 수락하고 수락한 사람의 세션 토큰을 `--token-file`에 쓴다(sandcorp-auth 대역).

`--issue-tickets-to`를 주면 멤버십 없는 새 사용자를 만들어 콜백이 심는 `member_ticket` 표를 그 파일에 쓰고,
스펙이 파일을 가져가면(지우면) 또 새 사용자의 표를 쓴다(#41 온보딩 — 매 실행이 새 사람이라 생성 상한에 안 걸린다).

`--serve-tenants-from DIR`을 주면 DIR에 떨어지는 `<id>.req`(내용: 조직 이름, 둘째 줄에 `default-flow`가 있으면 기본 흐름도)마다 그 이름의 새 조직과 그 조직 Admin
사용자를 만들고 세션 토큰을 `<id>.token`에 쓴다(#65 — 스펙마다 자기 조직이 필요하다. 운영자 API로 만든 조직은 멤버가 없다).

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
        parser.add_argument("--serve-tenants-from", default="", help="조직 요청(<id>.req)을 받아 <id>.token을 쓴다")
        parser.add_argument("--issue-tickets-to", default="", help="온보딩용 member_ticket을 이 파일에 계속 채운다")

    def handle(self, *args: object, **options: object) -> None:
        serve_dir = str(options["serve_tenants_from"])
        if serve_dir:
            self._serve_tenants(Path(serve_dir), int(str(options["wait_seconds"])))
            return
        ticket_file = str(options["issue_tickets_to"])
        if ticket_file:
            self._serve_tickets(Path(ticket_file), int(str(options["wait_seconds"])))
            return
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

    def _serve_tenants(self, directory: Path, wait_seconds: int) -> None:
        """조직 요청 대역: `<id>.req`의 이름으로 조직+Admin 멤버십을 만들고 `<id>.token`을 쓴다(쓰고 나서 옮겨 반쯤 쓴 파일을 안 읽게)."""
        import uuid

        directory.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            for req in sorted(directory.glob("*.req")):
                lines = req.read_text().strip().splitlines()
                req.unlink(missing_ok=True)
                name = lines[0].strip() if lines else ""
                if not name:
                    continue
                with_flow = "default-flow" in [x.strip() for x in lines[1:]]  # 온보딩처럼 공개·배포 중 기본 흐름을 둔다(#31)
                sub = f"m_E2E{uuid.uuid4().hex[:22].upper()}"
                identity = IdentityClaims(sub=sub, name=f"{name} 관리자", email=f"{sub.lower()}@example.com")
                user = member_user(settings.AUTH_ISSUER, sub, identity)
                tenant = Tenant.objects.create_with_key(name=name, raw_key=secrets.token_urlsafe(32))
                membership = Membership.objects.create(user=user, tenant=tenant, role=Membership.ROLE_ADMIN)
                if with_flow:
                    from apps.flows.defaults import ensure_default_flow

                    ensure_default_flow(tenant.id)
                # 배포 승인(#31)은 요청자와 다른 Admin 이 해야 하므로 같은 조직의 두 번째 Admin 도 만든다.
                sub2 = f"m_E2E{uuid.uuid4().hex[:22].upper()}"
                user2 = member_user(settings.AUTH_ISSUER, sub2, IdentityClaims(sub=sub2, name=f"{name} 둘째 관리자", email=f"{sub2.lower()}@example.com"))
                membership2 = Membership.objects.create(user=user2, tenant=tenant, role=Membership.ROLE_ADMIN)
                for suffix, m in (("admin2.token", membership2), ("token", membership)):  # 첫 Admin 토큰이 마지막 — 이것이 보이면 둘 다 준비된 것
                    tmp = directory / f"{req.stem}.{suffix}.tmp"
                    tmp.write_text(create_member_access_token(m))
                    tmp.rename(directory / f"{req.stem}.{suffix}")
            time.sleep(0.3)

    def _serve_tickets(self, ticket_file: Path, wait_seconds: int) -> None:
        """표는 10분짜리라, 파일이 비면 그때 새 사용자의 표를 쓴다(오래된 표는 5분마다 갈아 끼운다)."""
        import uuid

        from apps.tenants.members import issue_member_ticket

        ticket_file.parent.mkdir(parents=True, exist_ok=True)
        ticket_file.unlink(missing_ok=True)
        deadline = time.monotonic() + wait_seconds
        written_at = 0.0
        while time.monotonic() < deadline:
            if not ticket_file.exists() or time.monotonic() - written_at > 300:
                sub = f"m_E2EONBOARD{uuid.uuid4().hex[:15].upper()}"
                identity = IdentityClaims(sub=sub, name="E2E 새 고객", email=f"{sub.lower()}@example.com")
                user = member_user(settings.AUTH_ISSUER, sub, identity)
                ticket_file.write_text(issue_member_ticket(user))
                written_at = time.monotonic()
            time.sleep(0.5)

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
