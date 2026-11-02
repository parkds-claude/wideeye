"""등급: 긴급 / 대기 / 없음 (스펙 §5-3). 기한은 korean-datetime 으로 뽑는다."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from ..models import Event, Grade, Source
from ..workhours import DEFAULT, KST, business_hours_until

try:
    from korean_datetime import ParseOptions, parse_all          # MIT
except Exception:                                                # 패키지가 없으면 기한 추출만 건너뛴다
    parse_all = None


@dataclass
class GradeCtx:
    now: datetime
    waited_min: int            # 업무시간 기준 분
    nudges: int                # 같은 상대가 답 없이 잇달아 보낸 수
    is_dm: bool
    is_read: bool
    needs_reply: bool = False  # 질문·요청 꼴인가


def _deadline_moment(start: datetime, end: datetime | None, day_end: time = DEFAULT.end) -> datetime:
    """표현 하나의 '기한 시각'. 한 시각이면 그 시각, 구간이면 구간 끝, 날짜만 있으면 그날 업무 종료."""
    if end is None or end - start <= timedelta(hours=1):
        return start
    if end.time() == time(0, 0):                      # 날짜 단위 구간의 끝(다음 날 0시) → 마지막 날 업무 종료
        return datetime.combine((end - timedelta(days=1)).date(), day_end, tzinfo=end.tzinfo)
    return end


def find_deadlines(text: str, ref: datetime, rules) -> list[datetime]:
    """본문의 기한들(글에 나온 차례). 날짜·시각 표현 뒤에 기한 꼬리말(까지·내로·중으로…)이 있을 때만 기한으로 본다.
    ref 는 그 글을 받은 때 — "내일까지"는 받은 날의 다음 날이다(검토 I1, 2026-10-16: 지금을 기준으로 풀면 날마다 하루씩 밀렸다).
    받은 날보다 앞선 것은 뺀다."""
    if not parse_all or not text:
        return []
    try:
        found = parse_all(text, now=ref.astimezone(KST), options=ParseOptions(timezone=KST))
    except Exception:
        return []
    day0 = datetime.combine(ref.astimezone(KST).date(), time(0, 0), tzinfo=KST)
    out = []
    for r in found:
        if not r.start:
            continue
        tail = text[r.span[1]:r.span[1] + 12]
        if not (rules.deadline_suffix.search(tail) or rules.deadline_inner.search(r.text or "")):
            continue
        start = r.start if r.start.tzinfo else r.start.replace(tzinfo=KST)
        end = r.end if (r.end is None or r.end.tzinfo) else r.end.replace(tzinfo=KST)
        dt = _deadline_moment(start, end, rules.calendar.end)
        if dt >= day0:
            out.append(dt)
    return out


def find_deadline(text: str, now: datetime, rules) -> datetime | None:
    """now 를 받은 때로 보고 푼 기한 가운데 now 뒤의 첫 것."""
    return next((d for d in find_deadlines(text, now, rules) if d > now), None)


def grade(ev: Event, ctx: GradeCtx, rules) -> tuple[Grade, list[str], datetime | None]:
    """긴급 = 내가 아직 확인하지 않은 것(사용자 정의 2026-10-13). 대기 = 읽었지만 답하지 않은 질문·요청.

    이 함수에 오는 메시지는 이미 '나에게 온 것'이고 스팸·광고·잡음이 아니며 내가 답하지 않은 것이다(pipeline).
    재촉 낱말·임박한 기한·재촉·발주처 호칭은 등급을 올리지 않는다 — 까닭으로 남기고, 읽은 것이면 문턱을 기다리지 않고 바로 대기로 올린다."""
    t = rules.thresholds
    text = ev.text or ""
    cal = rules.calendar
    # 기한은 받은 때를 기준으로 푼다. 등급의 까닭에는 아직 오지 않은 기한만 쓰고, 지난 기한도 알려 준다(할 일의 "지남")
    found = find_deadlines(text, ev.received_at, rules)
    upcoming = next((d for d in found if d > ctx.now), None)
    deadline = upcoming or (max(found) if found else None)
    notes: list[str] = []
    if rules.urgent_ko.search(text) or rules.urgent_en.search(text):
        notes.append("urgent_word")
    if upcoming and business_hours_until(upcoming, ctx.now, cal) <= t["deadline_within_hours"]:
        notes.append("deadline_soon")
    if ctx.nudges >= t["nudge_count"]:
        notes.append("nudged")
    if ctx.needs_reply and any(c in text for c in rules.client_titles):
        notes.append("client")
    if not ctx.is_read:
        return Grade.URGENT, ["unread"] + notes, deadline
    if not ctx.needs_reply:
        return Grade.NONE, [], deadline
    if notes:
        return Grade.WAITING, notes, deadline
    limit_min = t["unanswered_dm_minutes"] if ctx.is_dm else t["unanswered_group_minutes"]     # 업무시간 분(2026-10-16 부터 분으로)
    if ctx.is_dm and ev.source == Source.IMESSAGE:                 # 1:1 문자는 따로(사용자 2026-10-15)
        limit_min = t.get("unanswered_sms_minutes", limit_min)
    if ctx.waited_min >= limit_min:
        return Grade.WAITING, ["waited"], deadline
    if upcoming and business_hours_until(upcoming, ctx.now, cal) <= t["waiting_deadline_days"] * 9:
        return Grade.WAITING, ["deadline"], deadline
    if rules.soft_ko.search(text):
        return Grade.WAITING, ["soft_word"], deadline
    return Grade.NONE, [], deadline
