"""업무시간 셈: 평일의 근무 시각(기본 09~18), 점심(기본 12~13) 제외, 공휴일 · 추가 휴일 제외, 추가 근무일(행사 운영일)은 넣는다.

달력(WorkCalendar)은 설정의 workhours 로 만든다(config.py). 공휴일은 holidays 패키지(나라 — 기본 KR, 대체공휴일 포함).
예전에는 다른 프로젝트의 공용 달력 도구를 불러왔다 — 같은 holidays.KR 이고, 그쪽의 회사 휴무 파일은 옮기기가 설정의
workhours.extra_holidays 로 옮긴다. 이 모듈은 config 층이다(표준 라이브러리와 holidays 만)."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


@lru_cache(maxsize=64)
def _public_holidays(country: str, year: int) -> frozenset:
    try:
        import holidays
        return frozenset(holidays.country_holidays(country, years=year))
    except Exception:                                    # noqa: BLE001 — 패키지가 없거나 모르는 나라: 주말만 쉰다
        return frozenset()


@dataclass(frozen=True)
class WorkCalendar:
    start: time = time(9, 0)
    lunch_start: time = time(12, 0)
    lunch_end: time = time(13, 0)
    end: time = time(18, 0)
    country: str = "KR"                                  # 비우면 공휴일을 보지 않는다
    extra_holidays: frozenset = frozenset()              # 회사 휴무(종무식 · 창립기념일)
    extra_workdays: frozenset = frozenset()              # 주말 · 공휴일이어도 근무일(행사 운영일)

    def is_workday(self, d: date) -> bool:
        if d in self.extra_workdays:
            return True
        if d.weekday() >= 5 or d in self.extra_holidays:
            return False
        return not (self.country and d in _public_holidays(self.country, d.year))

    def spans(self) -> tuple[tuple[time, time], ...]:
        """하루의 근무 구간 — 점심이 근무 시각 안에 있으면 그 앞뒤 둘, 아니면(비었거나 밖) 통째로 하나."""
        if self.start < self.lunch_start < self.lunch_end < self.end:
            return ((self.start, self.lunch_start), (self.lunch_end, self.end))
        return ((self.start, self.end),)


DEFAULT = WorkCalendar()


def _day_minutes(d: date, lo: datetime, hi: datetime, cal: WorkCalendar) -> int:
    """하루 d 안에서 [lo, hi) 와 근무 구간의 겹침(분)."""
    total = 0
    for s, e in cal.spans():
        a = max(lo, datetime.combine(d, s, tzinfo=KST))
        b = min(hi, datetime.combine(d, e, tzinfo=KST))
        if b > a:
            total += int((b - a).total_seconds() // 60)
    return total


def business_minutes(start: datetime, end: datetime, cal: WorkCalendar = DEFAULT) -> int:
    start, end = start.astimezone(KST), end.astimezone(KST)
    if end <= start:
        return 0
    total, d = 0, start.date()
    while d <= end.date():
        if cal.is_workday(d):
            total += _day_minutes(d, start, end, cal)
        d += timedelta(days=1)
    return total


def business_hours_until(deadline: datetime, now: datetime, cal: WorkCalendar = DEFAULT) -> float:
    return business_minutes(now, deadline, cal) / 60.0
