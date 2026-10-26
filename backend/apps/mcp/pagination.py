"""읽기 도구의 쪽 커서 — 위치(offset)를 불투명한 문자열로 감싼다."""

from __future__ import annotations

import base64
import binascii

from apps.mcp.registry import ToolError


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, _, number = raw.partition(":")
        offset = int(number)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ToolError("invalid_cursor", "cursor는 앞 결과의 next_cursor 값이어야 합니다.") from exc
    if prefix != "o" or offset < 0:
        raise ToolError("invalid_cursor", "cursor는 앞 결과의 next_cursor 값이어야 합니다.")
    return offset


def page_of[T](rows: list[T], cursor: str | None, limit: int) -> tuple[list[T], str | None]:
    """이미 읽은 목록을 쪽으로 자른다 → (이번 쪽, 다음 쪽 cursor 또는 None)."""
    offset = decode_cursor(cursor)
    page = rows[offset : offset + limit]
    return page, (encode_cursor(offset + limit) if len(rows) > offset + limit else None)
