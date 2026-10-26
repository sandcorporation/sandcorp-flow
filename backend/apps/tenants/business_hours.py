"""상담 가능 시간(영업시간) 판정 — deep module (issue 135).

테넌트 타임존 + 요일별 시간창 + 휴일 캘린더로 "지금이 상담 시간인가"를 결정한다.
미설정(타임존·스케줄 비어 있음)이면 항상 open(24/7, opt-in 하위호환). 순수 함수라
고정 now_utc를 주입해 결정적으로 테스트한다. 그래프 선택(issue 136)이 이 신호를 쓴다.
"""
import datetime as dt
import logging
import re
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# 월요일=0 … 일요일=6 인덱스를 스케줄 키로 매핑.
_WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_hhmm(value: str) -> dt.time:
    h, m = value.split(":")
    return dt.time(int(h), int(m))


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _timezone_error(value: object) -> str | None:
    if value == "":
        return None
    if not isinstance(value, str):
        return "hitl_timezone은 IANA 시간대 문자열이어야 합니다(예: Asia/Seoul)."
    try:
        ZoneInfo(value)
    except Exception:  # ZoneInfoNotFoundError·ValueError(경로 문자)·OSError 모두 「없는 시간대」
        return f"hitl_timezone '{value}'은(는) 알 수 없는 시간대입니다(예: Asia/Seoul)."
    return None


def _day_error(key: str, day: object) -> str | None:
    where = f"hitl_schedule.{key}"
    if not isinstance(day, dict):
        return f'{where}은(는) {{"enabled": true, "start": "09:00", "end": "18:00"}} 모양이어야 합니다.'
    if not isinstance(day.get("enabled", False), bool):
        return f"{where}.enabled는 true/false여야 합니다."
    for name in ("start", "end"):
        value = day.get(name, "")
        if not isinstance(value, str) or (value != "" and not _HHMM.match(value)):
            return f"{where}.{name}은(는) HH:MM(24시간) 형식이어야 합니다."
    if day.get("enabled"):
        start, end = day.get("start", ""), day.get("end", "")
        if not start or not end:
            return f"{where}은(는) 켜려면 start와 end가 필요합니다."
        if start >= end:
            return f"{where}.start는 end보다 빨라야 합니다(자정을 넘기는 시간창은 쓸 수 없음)."
    return None


def _schedule_error(value: object) -> str | None:
    if not isinstance(value, dict):
        return "hitl_schedule은 요일(mon~sun)별 객체여야 합니다."
    for key, day in value.items():
        if key not in _WEEKDAY_KEYS:
            return f"hitl_schedule의 요일 '{key}'을(를) 알 수 없습니다(mon·tue·wed·thu·fri·sat·sun)."
        error = _day_error(key, day)
        if error:
            return error
    return None


def _holidays_error(value: object) -> str | None:
    if not isinstance(value, list):
        return "hitl_holidays는 YYYY-MM-DD 문자열의 목록이어야 합니다."
    for item in value:
        try:
            ok = isinstance(item, str) and bool(_DATE.match(item)) and dt.date.fromisoformat(item).isoformat() == item
        except ValueError:
            ok = False
        if not ok:
            return f"hitl_holidays의 '{item}'은(는) YYYY-MM-DD 형식의 실제 날짜가 아닙니다."
    return None


def hours_error(timezone: object = "", schedule: object = None, holidays: object = None) -> str | None:
    """상담 시간 세 칸의 형식을 본다. 빈 값(``""``·``{}``·``[]``·None)은 「설정 안 함」이라 통과.

    저장 경로(HTTP PATCH·MCP update_settings)가 저장 전에 부르고, ``is_open``도 이미 저장된 값에 부른다.
    고칠 수 있는 문구(칸 이름 포함)를 돌려주고, 문제가 없으면 None.
    """
    return (
        _timezone_error(timezone or "")
        or _schedule_error(schedule if schedule is not None else {})
        or _holidays_error(holidays if holidays is not None else [])
    )


def is_open(config, now_utc: dt.datetime) -> bool:
    """now_utc(타임존 인지 datetime) 기준으로 상담 가능 시간이면 True.

    미설정 → True. 휴일 → False. 요일 off → False. 시간창 [start, end) 밖 → False.
    """
    timezone = getattr(config, "hitl_timezone", "") or ""
    schedule = getattr(config, "hitl_schedule", None) or {}
    if not timezone or not schedule:
        return True  # opt-in: 미설정이면 24/7

    # 저장 때 검증하지만 옛 값·직접 수정으로 나쁜 값이 남아 있을 수 있다. 예외로 사람 넘김 경로를
    # 막지 않고 「제한 없음」(= 미설정과 같은 24/7)으로 본다: 상담원 연결을 막는 쪽이 방문자에게 더 해롭다.
    problem = hours_error(timezone, schedule, getattr(config, "hitl_holidays", None))
    if problem:
        logger.warning("hitl 상담 시간 설정이 올바르지 않아 제한 없음으로 처리합니다: %s", problem)
        return True

    local = now_utc.astimezone(ZoneInfo(timezone))

    holidays = getattr(config, "hitl_holidays", None) or []
    if local.date().isoformat() in holidays:
        return False  # 휴일은 요일을 덮어쓴다

    day = schedule.get(_WEEKDAY_KEYS[local.weekday()])
    if not day or not day.get("enabled"):
        return False

    start = _parse_hhmm(day["start"])
    end = _parse_hhmm(day["end"])
    return start <= local.time() < end  # start 포함, end 제외
