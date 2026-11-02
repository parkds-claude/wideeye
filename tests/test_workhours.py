from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import date, time
from urgent_board.workhours import WorkCalendar, business_hours_until, business_minutes
KST = ZoneInfo("Asia/Seoul")

def test_same_day_excludes_lunch():
    a = datetime(2026, 10, 13, 10, 0, tzinfo=KST); b = datetime(2026, 10, 13, 14, 0, tzinfo=KST)
    assert business_minutes(a, b) == 180          # 10~12 + 13~14

def test_friday_evening_to_monday_morning():        # Review Focus 3
    a = datetime(2026, 10, 16, 17, 0, tzinfo=KST); b = datetime(2026, 10, 19, 9, 30, tzinfo=KST)
    assert business_minutes(a, b) == 90           # 금 17~18 + 월 09:00~09:30

def test_hangul_day_is_holiday():
    a = datetime(2026, 10, 8, 17, 0, tzinfo=KST); b = datetime(2026, 10, 12, 10, 0, tzinfo=KST)   # 10/9 한글날(금) 쉼
    assert business_minutes(a, b) == 60 + 60      # 목 17~18 + 월 09~10

def test_extra_workday_makes_a_weekend_count():                 # 주말 행사 운영일(설정 workhours.extra_workdays)
    a = datetime(2026, 10, 17, 10, 0, tzinfo=KST); b = datetime(2026, 10, 17, 11, 30, tzinfo=KST)
    assert business_minutes(a, b) == 0
    assert business_minutes(a, b, WorkCalendar(extra_workdays=frozenset({date(2026, 10, 17)}))) == 90


def test_extra_holiday_and_hours_from_settings():
    mon = datetime(2026, 10, 19, 9, 0, tzinfo=KST); mon_end = datetime(2026, 10, 19, 20, 0, tzinfo=KST)
    assert business_minutes(mon, mon_end) == 480                                   # 09~18 − 점심 1시간
    assert business_minutes(mon, mon_end, WorkCalendar(extra_holidays=frozenset({date(2026, 10, 19)}))) == 0
    late = WorkCalendar(start=time(10, 0), lunch_start=time(12, 30), lunch_end=time(13, 30), end=time(19, 0))
    assert business_minutes(mon, mon_end, late) == 480                             # 10~12:30 + 13:30~19
    no_lunch = WorkCalendar(lunch_start=time(12, 0), lunch_end=time(12, 0))
    assert business_minutes(mon, mon_end, no_lunch) == 540                         # 점심이 비었으면 통째로


def test_country_holidays_and_none():
    hangul = datetime(2026, 10, 9, 9, 0, tzinfo=KST); hangul_end = datetime(2026, 10, 9, 18, 0, tzinfo=KST)   # 한글날(금)
    assert business_minutes(hangul, hangul_end) == 0
    assert business_minutes(hangul, hangul_end, WorkCalendar(country="")) == 480   # 나라를 비우면 공휴일을 보지 않는다
    assert business_minutes(hangul, hangul_end, WorkCalendar(country="없는나라")) == 480   # 모르는 나라 — 죽지 않고 주말만


def test_hours_until_uses_the_calendar():
    now = datetime(2026, 10, 19, 17, 0, tzinfo=KST); due = datetime(2026, 10, 20, 10, 0, tzinfo=KST)
    assert business_hours_until(due, now) == 2.0                                    # 월 17~18 + 화 09~10
