"""탭5 화면이 받는 자료의 상자(cards) — 할 일 상자(2026-10-16, 약속은 docs/exec-plans/2026-10-16-todo-board-plan.md Task 4)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from urgent_board import board, summarize, web
from urgent_board.config import load
from urgent_board.models import Event, Source
from urgent_board.store import Store
from urgent_board.triage.pipeline import scan
from urgent_board.web import create_app
from urgent_board.workhours import KST

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"


def recent_workday_3pm() -> datetime:
    """가장 가까운 지난 화~금요일 15:00(KST). 날짜를 박아 두면 일주일 뒤 저장소가 '7일 안'으로 자를 때 표본이 전부 빠진다
    (저장소는 실제 시계로 자른다). 화~금이면 그 전날도 평일이다."""
    now = datetime.now(KST)
    for back in range(8):
        d = (now - timedelta(days=back)).replace(hour=15, minute=0, second=0, microsecond=0)
        if d <= now and d.weekday() in (1, 2, 3, 4):
            return d.astimezone(timezone.utc)
    raise AssertionError("닿지 않는 곳")


NOW = recent_workday_3pm()


def setup(tmp_path, **conf):
    s = load(HOME)
    s.handled = {**s.handled, "claude_sessions": False}
    s.summary = {**s.summary, **conf}
    st = Store(tmp_path / "t.db")
    return create_app(s, st, clock=lambda: NOW), st, s


def dm(mid, hours, src=Source.KAKAO, sender="u1", name="가나다", read=False, text="견적서를 오늘 안에 확인해 주실 수 있을까요 회신 부탁드립니다"):
    return Event(source=src, chat_id=f"c-{sender}", message_id=mid, chat_name=name, sender_id=sender, sender_name=name, text=text,
                 received_at=NOW - timedelta(hours=hours), is_from_me=False, is_read=read, is_group=False)


def test_six_quiet_cards_when_nothing_came(tmp_path):
    a, _, s = setup(tmp_path)
    j = a.test_client().get("/api/items").get_json()
    assert len(j["cards"]) == 6 and j["cards"][-1]["key"] == "etc" and j["cards"][-1]["name"] == "개인 · 기타"
    assert [c["key"] for c in j["cards"][:5]] == [b.key for b in s.businesses if b.active][:5]
    assert all(c == {**c, "state": "quiet", "n": 0, "tasks": [], "more": 0, "urgent": 0, "waiting": 0} for c in j["cards"])
    assert j["totals"] == {"tasks": 0, "unread": 0, "awaiting": 0}


def test_unknown_business_lands_in_the_last_card_with_where_it_came_from(tmp_path):
    a, st, _ = setup(tmp_path)
    st.upsert_events([dm("1", 1), dm("2", 2, src=Source.TELEGRAM, sender="u2", name="라마바")])
    j = a.test_client().get("/api/items").get_json()
    etc = j["cards"][-1]
    assert etc["state"] == "urgent" and etc["urgent"] == 2 and etc["n"] == 2 and j["totals"] == {"tasks": 2, "unread": 2, "awaiting": 0}
    assert [t["who"].split(" ")[0] for t in etc["tasks"]] == ["카톡", "텔레그램"]           # 어디로 왔는지는 할 일 둘째 줄에(최근 것부터 — 카톡이 1시간 전)
    assert all(t["unread"] and t["what"] for t in etc["tasks"])


def test_room_bundle_says_how_many_posts_it_holds(tmp_path):
    a, st, s = setup(tmp_path)
    s.watch_rooms = frozenset({("telegram", "-100")})
    posts = [Event(source=Source.TELEGRAM, chat_id="-100", message_id=str(n), chat_name="준비방", sender_id=f"p{n}", sender_name=f"사람{n}",
                   text=f"버스 시간을 정리한 표를 올립니다 {n}", received_at=NOW - timedelta(minutes=50 - n), is_from_me=False, is_read=False,
                   is_group=True) for n in range(3)]
    st.upsert_events(posts)
    j = a.test_client().get("/api/items").get_json()
    card = next(c for c in j["cards"] if c["n"])
    assert card["n"] == 1 and card["tasks"][0]["who"] == "텔레그램 3건"              # 묶음은 할 일 하나 — 받은 때와 함께 상자 한 줄에 들게(실제 자료 "글 8건 · 오늘 10:17" 은 잘려 건수가 안 보였다)


def test_due_label():
    from datetime import date
    now = datetime(2026, 10, 16, 6, tzinfo=timezone.utc)                       # 금요일 15:00 KST
    assert web.due_label(date(2026, 10, 16), now) == "오늘까지" and web.due_label(date(2026, 10, 17), now) == "내일까지"
    assert web.due_label(date(2026, 10, 23), now) == "10/23까지" and web.due_label(date(2026, 10, 1), now) == "10/1 지남"
    assert web.due_label(None, now) == ""


def test_card_sender_is_kept_short():
    assert web.card_sender("가나다") == "가나다" and web.card_sender("가나다 가상협회 사무국장") == "가나다"      # 이름 뒤의 소속·직함은 뗀다
    assert web.card_sender("가상통번역 나다라") == "가상통번역 나다라"                                               # 열 자까지는 그대로
    assert web.card_sender("Dr. Tanaka Hiroshi") == "Dr. Tanaka" and web.card_sender("Alexandria Wolfeschlegelstein") == "Alexandria"      # 낱말 사이에서 자른다
    assert web.card_sender("가상국제회의기획사업단본부") == "가상국제회의기획사업…"                                   # 한 낱말이 길면 열 자에서
    assert web.card_sender("+821012345678") == "010-1234-5678" and web.card_sender("") == "(이름 없음)"          # 전화번호는 줄이지 않는다




# --- 검토 고침(2026-10-16): 장부 고장 · 내보내는 자료 · 메뉴 막대의 상자 이름
def test_a_broken_book_is_a_fault_not_a_calm_screen(tmp_path, monkeypatch):
    """검토 I3: 장부를 못 읽으면 초록 '처리할 일 없음'이 될 수 있었다 — 적어도 '일부 멈춤'(회색 · 메뉴 막대 !)으로, 문구는 '할 일 장부 오류'."""
    import sqlite3
    a, st, _ = setup(tmp_path)

    def broken(state="open"):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(st, "tasks", broken)
    j = a.test_client().get("/api/items").get_json()
    assert j["book_error"] is True and j["blind"] == "some" and "할 일 장부" not in j["down"]
    s = a.test_client().get("/api/status").get_json()
    assert s["book_error"] is True and s["blind"] == "some"
    assert "할 일 장부 오류" in a.test_client().get("/list").get_data(as_text=True)


def test_tab5_feed_leaves_out_the_mac_only_lists(tmp_path):
    """작은 것 ①: 탭5 는 상자 · 머리줄만 쓴다 — 최근 끝난 것 · 할 일 전체 줄은 보내지 않는다(WebView 메모리)."""
    a, st, _ = setup(tmp_path)
    st.upsert_events([dm("1", 1)])
    j = a.test_client().get("/api/items").get_json()
    assert "closed" not in j and "tasks" not in j and j["book_error"] is False
    assert a.test_client().get("/api/status").get_json()["tasks"]                      # 메뉴 막대는 그대로 받는다


def test_menu_bar_names_the_box_the_task_sits_in(tmp_path):
    """작은 것 ⑩: 넘친 사업의 할 일은 여섯째 상자('그 밖')에 있다 — 메뉴 막대도 그 이름으로."""
    from urgent_board.config import Business
    a, st, s = setup(tmp_path)
    s.businesses.append(Business(key="special", name="특별행사", keywords=["특별행사"]))
    st.upsert_events([dm("1", 1, text="특별행사 준비물 목록 확인 부탁드립니다 회신 바랍니다")])
    t, = a.test_client().get("/api/status").get_json()["tasks"]
    assert t["box"] == "그 밖"


# --- 답 기다림 · 사업에 걸린 자동 알림(2026-10-16)
def _mine(mid="w1", subject="서예 동호회 버스 견적 요청", hours=2):
    return Event(source=Source.GMAIL, chat_id="t" + mid, message_id=mid, chat_name=subject, sender_id="minji@example.com", sender_name="나",
                 text=subject + " — 견적서 회신 부탁드립니다", received_at=NOW - timedelta(hours=hours), is_from_me=True, is_read=True, is_group=False,
                 thread_id="t" + mid, account="minji@example.com", raw={"subject": subject, "headers": {}, "to": ["kim@client.com"], "labels": ["SENT"]})


def test_awaiting_lines_stay_on_the_mac_list_but_not_on_the_tab5_boxes(tmp_path):
    """답 기다림(내가 보낸 것)은 맥의 전체 목록에만 — 탭5 상자 · 머리줄 · 메뉴 막대에는 없다(사용자 2026-10-17 \"내가 보낸건데\" → \"탭5에서 빼고 목록에만\")."""
    a, st, _ = setup(tmp_path)
    st.upsert_events([_mine()])
    c = a.test_client()
    j = c.get("/api/items").get_json()
    calligraphy = next(x for x in j["cards"] if x["key"] == "calligraphy-club")
    assert j["totals"] == {"tasks": 0, "unread": 0, "awaiting": 1}
    assert calligraphy["n"] == 0 and calligraphy["state"] == "quiet" and calligraphy["tasks"] == []          # 탭5 상자에는 없다(totals 의 수는 목록 · 진단용으로 남는다)
    assert c.get("/api/status").get_json()["tasks"] == []                               # 메뉴 막대는 할 일만
    html = c.get("/list").get_data(as_text=True)
    assert "답 기다림 1" in html and 'data-key="gmail:tw1:w1"' in html
    c.post("/api/dismiss", json={"key": "gmail:tw1:w1"}, headers={"Origin": "http://localhost"})
    assert c.get("/api/items").get_json()["totals"]["awaiting"] == 0                   # 처리됨으로 지운다


# --- 알림(탭5, 2026-10-17 — 사용자 "제목을 처리할 일이 아니라 알림으로 · 전체 내용을 최근 알림 내용으로")
def test_feed_is_six_quiet_cards_in_the_same_order_when_nothing_came(tmp_path):
    a, _, s = setup(tmp_path)
    f = a.test_client().get("/api/items").get_json()["feed"]
    assert [c["key"] for c in f["cards"]] == [b.key for b in s.businesses if b.active][:5] + ["etc"]
    assert all(c["state"] == "quiet" and c["n"] == 0 and c["tasks"] == [] for c in f["cards"]) and f["totals"] == {"unread": 0, "today": 0}


def test_feed_keeps_read_messages_and_puts_the_newest_first(tmp_path):
    a, st, _ = setup(tmp_path)
    st.upsert_events([dm("1", 2, read=True, text="오늘 회의록 공유드립니다"), dm("2", 1, src=Source.TELEGRAM, sender="u2", name="라마바", text="자료 잘 받았습니다 고맙습니다")])
    f = a.test_client().get("/api/items").get_json()["feed"]
    etc = f["cards"][-1]
    assert etc["state"] == "urgent" and etc["n"] == 1                               # 상자의 수는 안 읽은 줄 수
    assert [(t["src"], t["who"], t["unread"]) for t in etc["tasks"]] == [("텔레", "라마바", True), ("카톡", "가나다", False)]     # 읽은 글도 ○ 로 남는다, 최근 것부터 · 출처는 두 글자 꼬리표(2026-10-17)
    assert etc["tasks"][1]["what"] == "오늘 회의록 공유드립니다" and etc["tasks"][1]["when"].endswith("13:00")
    assert f["totals"] == {"unread": 1, "today": 2}


def test_feed_line_counts_unread_messages_and_strips_the_subject_tag(tmp_path):
    a, st, _ = setup(tmp_path)
    st.upsert_events([dm("1", 3), dm("2", 2), dm("3", 1)])
    mail = Event(source=Source.GMAIL, chat_id="t9", message_id="m9", chat_name="[가상상사] RE: 견적서 송부드립니다", sender_id="kim@client.example",
                 sender_name="김과장 가상상사 과장", text="[가상상사] RE: 견적서 송부드립니다 — 본문", received_at=NOW - timedelta(minutes=30), is_from_me=False,
                 is_read=False, is_group=False, account="minji@example.com", raw={"subject": "[가상상사] RE: 견적서 송부드립니다", "headers": {}, "to": ["minji@example.com"]})
    st.upsert_events([mail])
    lines = a.test_client().get("/api/items").get_json()["feed"]["cards"][-1]["tasks"]
    assert lines[0]["what"] == "견적서 송부드립니다"                                 # 말머리 · 그 뒤의 RE: 를 뗀다
    assert (lines[0]["src"], lines[0]["who"]) == ("메일", "김과장") and (lines[1]["src"], lines[1]["who"]) == ("카톡", "가나다 3건")     # 안 읽은 글이 여럿이면 그 수


def test_unread_line_is_never_pushed_out_of_the_shown_lines_by_newer_read_ones(tmp_path):
    # 실제 자료로 뽑아 보다 찾음(2026-10-17): 최근 것부터만 세우면 읽은 새 글들이 안 읽은 옛 글을 밀어내 주황 "1"과 보이는 줄이 어긋났다
    # 상자에 보이는 줄은 셋(D14 — 사용자 2026-10-18 "각 셀(사업)의 본문을 3개 알림이 들어가도록", 그 전 둘)
    a, st, _ = setup(tmp_path)
    st.upsert_events([dm("1", 6, sender="u1", name="가나다", text="어제 말씀드린 자료입니다"),
                      dm("2", 3, sender="u2", name="라마바", read=True, text="회의 잘 마쳤습니다"),
                      dm("3", 2, sender="u3", name="사아자", read=True, text="사진 보내드립니다"),
                      dm("4", 1, sender="u4", name="차카타", read=True, text="일정표 공유드립니다")])
    etc = a.test_client().get("/api/items").get_json()["feed"]["cards"][-1]
    assert web.FEED_ON_CARD == 3
    assert etc["n"] == 1 and [(t["who"], t["unread"]) for t in etc["tasks"]] == [("가나다", True), ("차카타", False), ("사아자", False)] and etc["more"] == 1



def test_every_source_tag_on_the_board_is_two_letters():
    # 사용자 2026-10-17 "텔레그램-->텔레 로 2글자로 모두 줄일 것. 카카오톡 -> 카톡 식으로" — 맥의 목록 · 메뉴 막대는 긴 이름 그대로
    assert all(len(v) == 2 for v in board.SHORT_LABEL.values()) and board.SHORT_LABEL["telegram"] == "텔레"
    assert board.SOURCE_LABEL["telegram"] == "텔레그램"
