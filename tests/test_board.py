"""화면에 무엇이 뜨는가(board.py) — 사업 상자 · 화면에 낼 항목 · 사업 요약에 넣을 글."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from urgent_board.config import load
from urgent_board.models import Event, Grade, Item, Source
from urgent_board.triage.pipeline import scan

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
NOW = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)        # 수요일 15:00 KST


def dm(mid: str, hours: float, read: bool = False) -> Event:
    return Event(source=Source.KAKAO, chat_id="77", message_id=mid, chat_name="가나다", sender_id="u1", sender_name="가나다",
                 text="견적서를 오늘 안에 확인해 주실 수 있을까요", received_at=NOW - timedelta(hours=hours),
                 is_from_me=False, is_read=read, is_group=False)


def test_item_remembers_when_the_oldest_unread_message_came():
    items, _ = scan([dm("1", 3), dm("2", 1)], load(HOME), NOW, set())
    assert len(items) == 1 and items[0].grade == Grade.URGENT
    assert items[0].received_at == NOW - timedelta(hours=1)        # 대표는 가장 최근 것
    assert items[0].since == NOW - timedelta(hours=3)              # 기다린 것은 가장 오래된 안 읽은 것부터


# ===== 사업 상자 =====
from urgent_board import board
from urgent_board.config import Business


def item(biz: str, src: Source = Source.GMAIL, grade: Grade = Grade.URGENT, waited: int = 30, hours: float = 1, n: int = 0,
         text: str = "보고서 초안을 내일 오전까지 보내 달라는 부탁입니다 확인 바랍니다") -> Item:
    at = NOW - timedelta(hours=hours)
    return Item(grade=grade, business=biz, source=src, chat_name="방", sender_name="가나다", summary=text[:80], received_at=at,
                waited_minutes=waited, deadline=None, reasons=[], key=f"{src.value}:{biz}:{grade.value}:{hours}:{n}", text=text, since=at)


def firms(n: int) -> list[Business]:
    return [Business(key=f"b{i}", name=f"사업{i}", color="") for i in range(1, n + 1)]


# ===== 할 일 상자(build_task_cards — 2026-10-16 할 일 현황판) =====
from datetime import date as _date
from urgent_board.models import Task

S = load(HOME)


def T(what, biz, unread=False, due=None, opened=None, src=Source.GMAIL):
    opened = opened or NOW - timedelta(hours=1)
    return Task(conv=what, key=what, business=biz, source=src, chat_id="t", chat_name="제목", sender_name="가", what=what, summary="",
                due=due, link="", unread=unread, opened_at=opened, last_at=opened)


def test_task_cards_order_state_and_counts():
    # 상자 안 차례 = 최근 것부터(마지막 글이 온 때) — 사용자 2026-10-16 "너무 과거내용만 있는데 · 최근 2개를 띄어줄수 있어?"
    ts = [T("오래됨", "bazaar2", opened=NOW - timedelta(days=20)), T("읽음", "bazaar2", opened=NOW - timedelta(hours=2)),
          T("기한", "bazaar2", due=NOW.date(), opened=NOW - timedelta(days=3)), T("안읽음", "bazaar2", unread=True, opened=NOW - timedelta(days=2)),
          T("개인", "personal")]
    cards = board.build_task_cards(ts, S.businesses, S.etc_name, NOW, S.rules.calendar)
    it4 = next(c for c in cards if c.key == "bazaar2")
    assert [t.what for t in it4.tasks] == ["읽음", "안읽음", "기한", "오래됨"]
    assert it4.state == "urgent" and it4.urgent == 2 and it4.waiting == 2              # 안 읽음 · 기한 임박이 주황 수
    assert next(c for c in cards if c.key == "etc").state == "waiting"                  # 읽은 할 일만 — 흰 수, 주황 테두리 없음
    assert [c.key for c in cards] == [c.key for c in board.build_task_cards([], S.businesses, S.etc_name, NOW, S.rules.calendar)]   # 차례 고정


def test_task_cards_keep_the_slots_and_drop_nothing():
    fs = firms(7)
    ts = [T("a", "other"), T("b", "personal"), T("c", "b6"), T("d", "b7"), T("e", "b1")]
    cards = board.build_task_cards(ts, fs, "개인 · 기타", NOW, S.rules.calendar)
    assert [c.key for c in cards] == ["b1", "b2", "b3", "b4", "b5", "etc"] and cards[-1].name == "그 밖"
    assert sum(len(c.tasks) for c in cards) == len(ts) and [c.state for c in cards[1:5]] == ["quiet"] * 4
    fs = firms(5); fs[1].active = False
    assert [c.key for c in board.build_task_cards([], fs, "개인 · 기타", NOW, S.rules.calendar)] == ["b1", "b3", "b4", "b5", "etc"]


def test_due_soon_and_old():
    cal = S.rules.calendar
    assert board.due_soon(T("x", "b1", due=NOW.date()), NOW, cal) and board.due_soon(T("x", "b1", due=_date(2026, 10, 1)), NOW, cal)
    assert not board.due_soon(T("x", "b1", due=NOW.date() + timedelta(days=10)), NOW, cal) and not board.due_soon(T("x", "b1"), NOW, cal)
    assert board.is_old(T("x", "b1", opened=NOW - timedelta(days=15)), NOW, 14) and not board.is_old(T("x", "b1"), NOW, 14)


def test_unread_task_is_never_old():                                  # 검토 I6: '오래됨'이 안 읽은 새 글까지 흐리게 묻었다
    old = NOW - timedelta(days=20)
    assert board.is_old(T("읽음", "bazaar2", opened=old), NOW) and not board.is_old(T("안읽음", "bazaar2", unread=True, opened=old), NOW)


def test_box_shows_the_latest_message_first_even_if_the_task_opened_long_ago():
    """열린 지 오래된 대화라도 새 글이 오면 앞으로 온다(차례는 마지막 글이 온 때)."""
    old_conv = T("새 글 온 옛 대화", "bazaar2", opened=NOW - timedelta(days=5))
    old_conv.last_at = NOW - timedelta(minutes=10)
    ts = [T("엿새 전 것", "bazaar2", opened=NOW - timedelta(days=6)), old_conv]
    it4 = next(c for c in board.build_task_cards(ts, S.businesses, S.etc_name, NOW, S.rules.calendar) if c.key == "bazaar2")
    assert [t.what for t in it4.tasks] == ["새 글 온 옛 대화", "엿새 전 것"]
