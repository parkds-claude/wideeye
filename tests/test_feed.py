"""알림(탭5, 2026-10-17 사용자 "제목을 처리할 일이 아니라 알림으로 · 전체 내용을 최근 알림 내용으로") — pipeline.feed.

알림 = 지난 7일 나에게 온 글을 읽음과 관계없이 최근 것부터, 대화마다 한 줄(설계 docs/exec-plans/2026-10-17-notice-feed-design.md D2).
거르는 규칙은 할 일 판정과 같다(나에게 온 것 · 광고 · 자동 발송 · 잡음 · 내 글 · 다른 메일함 사본 · 처리됨 · 지켜보는 방 묶음).
글은 모두 지어낸 것이다(실제 본문 · 이름을 넣지 않는다)."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from urgent_board.config import load
from urgent_board.models import Event, Source
from urgent_board.triage import pipeline

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parents[1]
S = load(ROOT / "tests" / "fixtures" / "home")
NOW = datetime(2026, 10, 13, 15, 0, tzinfo=KST)


def ev(text, *, src=Source.KAKAO, chat="방", chat_id="c1", sender="김과장", sid="u9", me=False, group=False, mins=60, read=True, mid=None, **kw):
    return Event(source=src, chat_id=chat_id, message_id=mid or str(abs(hash((text, sid, mins, chat_id)))), chat_name=chat, sender_id=sid,
                 sender_name=sender, text=text, received_at=NOW - timedelta(minutes=mins), is_from_me=me, is_read=read, is_group=group, **kw)


def mail(subject, *, sid="kim@client.example", account="minji@example.com", mins=60, read=True, mid=None, labels=(), date="Mon, 12 Oct 2026 10:00:00 +0900",
         to=("minji@example.com",), group=False):
    return Event(source=Source.GMAIL, chat_id="t" + (mid or subject), message_id=mid or subject, chat_name=subject, sender_id=sid, sender_name="김과장",
                 text=subject + " — 본문 앞부분입니다", received_at=NOW - timedelta(minutes=mins), is_from_me=False, is_read=read, is_group=group,
                 account=account, raw={"subject": subject, "headers": {"date": date}, "to": list(to), "labels": list(labels)})


def feed(events, dismissed=frozenset()):
    return pipeline.feed(events, S, NOW, set(dismissed))


def test_read_messages_stay_as_read_lines_and_unread_ones_are_counted():
    # D2: 읽은 글도 ○ 로 남는다(까먹지 않게) — 사라지지 않는다. 안 읽은 글은 수를 센다
    a = ev("오늘 일정 공유드립니다", chat_id="c1", sid="u1", sender="가나다", read=True, mins=30)
    b = ev("내일 뵙겠습니다", chat_id="c2", sid="u2", sender="라마바", read=False, mins=10)
    lines = feed([a, b])
    assert [(l.sender_name, l.unread) for l in lines] == [("라마바", 1), ("가나다", 0)]


def test_newest_first_and_one_line_per_conversation():
    old = ev("첫 글입니다", chat_id="c1", sid="u1", sender="가나다", read=True, mins=300)
    mid = ev("둘째 글입니다", chat_id="c1", sid="u1", sender="가나다", read=False, mins=200)
    new = ev("셋째 글입니다", chat_id="c1", sid="u1", sender="가나다", read=False, mins=100)
    other = ev("다른 사람 글입니다", chat_id="c2", sid="u2", sender="라마바", read=True, mins=150)
    lines = feed([old, mid, new, other])
    assert [l.sender_name for l in lines] == ["가나다", "라마바"]                 # 대화마다 한 줄, 가장 최근 글이 온 때 차례
    top = lines[0]
    assert top.text == "셋째 글입니다" and top.unread == 2 and top.count == 3 and top.key == new.key and top.at == new.received_at


def test_what_is_not_a_notification_is_left_out():
    mine = ev("제가 보낸 글입니다", sid="me", me=True, mins=5)
    ad = ev("(광고) 가을 특가 안내입니다", chat_id="c3", sid="u3", read=False, mins=6)
    noise = ev("ㅋㅋㅋ", chat_id="c4", sid="u4", read=False, mins=7)
    group = ev("오늘 몇 시에 모이나요?", chat_id="g1", sid="u5", group=True, read=False, mins=8)           # 나를 부르지 않은 단톡 글
    cc = mail("참조로 받은 공문", to=("other@client.example",), group=True, read=False, mins=9)          # 나에게 온 메일이 아니다(참조 · 단체)
    called = ev("대장님 오늘 몇 시에 모이나요?", chat_id="g1", sid="u6", sender="사바아", group=True, read=False, mins=20)
    lines = feed([mine, ad, noise, group, cc, called])
    assert [l.sender_name for l in lines] == ["사바아"]                          # 나를 부른 단톡 글은 알림이다


def test_mail_copies_in_two_boxes_are_one_line_and_read_in_one_is_read():
    a = mail("견적 회신 부탁", account="minji@example.com", read=True, mid="m1")
    b = mail("견적 회신 부탁", account="main@example.com", read=False, mid="m2")
    lines = feed([a, b])
    assert len(lines) == 1 and lines[0].unread == 0 and lines[0].count == 1
    assert lines[0].text == "견적 회신 부탁"                                       # 메일은 제목


def test_dismissed_sender_is_hidden_until_they_write_again():
    a = ev("처리한 글입니다", sid="u1", sender="가나다", read=True, mins=100)
    assert feed([a], dismissed={a.key}) == []
    b = ev("새로 온 글입니다", sid="u1", sender="가나다", read=False, mins=10)
    lines = feed([a, b], dismissed={a.key})
    assert len(lines) == 1 and lines[0].text == "새로 온 글입니다" and lines[0].count == 1


def test_watched_room_is_one_line_while_it_has_unread_posts():
    room = ("telegram", "-100000000001")                                             # 시험 설정에서 watch: true 인 방
    assert room in S.watch_rooms
    posts = [ev(f"버스 시간표를 올립니다 {n}", src=Source.TELEGRAM, chat="준비방", chat_id="-100000000001", sid=f"p{n}", sender=f"사람{n}",
                group=True, read=False, mins=50 - n) for n in range(3)]
    line, = feed(posts)
    assert line.room and line.unread == 3 and line.count == 3 and line.business == "bazaar2" and "버스 시간표를 올립니다 2" in line.text
    read = [ev(p.text, src=p.source, chat=p.chat_name, chat_id=p.chat_id, sid=p.sender_id, sender=p.sender_name, group=True, read=True, mins=50 - n)
            for n, p in enumerate(posts)]
    assert feed(read) == []                                                         # 묶음은 안 읽은 글이 있는 동안만(나를 부르지 않은 글이다)


def test_business_comes_from_the_same_rules_as_the_todo_board():
    a = ev("서예 동호회 일정 공유드립니다", chat_id="c1", sid="u1", read=True)
    b = ev("점심 맛있게 드세요", chat_id="c2", sid="u2", read=True, mins=10)
    by = {l.text: l.business for l in feed([a, b])}
    assert by["서예 동호회 일정 공유드립니다"] == "calligraphy-club" and by["점심 맛있게 드세요"] == "other"


def test_business_notice_is_shown_only_while_unread():
    # 사업에 걸린 자동 알림은 안 읽은 동안만(사용자 2026-10-16 — 할 일 판정과 같다). 낱말로만 걸린 것은 표시해 둔다(board 가 Claude 동의를 본다)
    n = mail("서예 동호회 등록비 결제 완료", sid="noreply@pay.example", labels=("CATEGORY_UPDATES",), read=False)
    line, = feed([n])
    assert line.business == "calligraphy-club" and line.word_only and line.unread == 1
    assert feed([mail("서예 동호회 등록비 결제 완료", sid="noreply@pay.example", labels=("CATEGORY_UPDATES",), read=True)]) == []
    assert feed([mail("가을 이벤트 안내", sid="noreply@shop.example", labels=("CATEGORY_UPDATES",), read=False)]) == []   # 사업에 안 걸린 자동 알림


def test_conversation_key_matches_the_todo_board():
    # 같은 대화의 할 일과 알림 줄은 같은 열쇠(conv)다 — 사업 고르기(Claude) · 다룬 것 빼기를 할 일 쪽에서 가져온다(board.screen)
    a = ev("자료 확인 부탁드립니다", sid="u1", read=False)
    line, = feed([a])
    assert line.conv == pipeline.conv_id(a)


# --- 독립 검토(2026-10-17) 반영
def test_unread_message_i_answered_afterwards_counts_as_read_like_the_todo_board():
    # 폰에서 답했는데 읽음 반영이 늦다 — 할 일 쪽(judge)은 '봤다'로 치는데 알림 쪽은 주황으로 세어 두 화면이 어긋났다
    q = ev("자료 확인 부탁드립니다", chat_id="c1", sid="u1", sender="가나다", read=False, mins=30)
    me = ev("네 확인했습니다 보내드릴게요", chat_id="c1", sid="me", me=True, mins=20)
    line, = feed([q, me])
    assert line.unread == 0 and line.text == "자료 확인 부탁드립니다"


def test_the_feed_and_the_todo_board_agree_on_what_is_unread():
    # 두 화면이 같은 거름을 따로 적고 있다(pipeline.feed · judge) — 한쪽만 고치면 말없이 어긋나지 않게, 여러 꼴을 섞어 견준다
    room = "-100000000001"
    evs = [
        ev("견적 회신 부탁드립니다", chat_id="d1", sid="u1", sender="가", read=False, mins=10),                      # 1:1 안 읽음
        ev("대장님 오늘 몇 시에 모이나요?", chat_id="g1", sid="u2", sender="나", group=True, read=False, mins=11),   # 나를 부른 단톡
        ev("오늘 몇 시에 모이나요?", chat_id="g1", sid="u3", sender="다", group=True, read=False, mins=12),          # 나를 부르지 않은 단톡
        ev("(광고) 특가 안내", chat_id="d2", sid="u4", read=False, mins=13),                                     # 광고
        ev("ㅋㅋㅋ", chat_id="d3", sid="u5", read=False, mins=14),                                               # 잡음
        ev("자료 보내 주세요", chat_id="d4", sid="u6", sender="라", read=False, mins=40),                          # 안 읽음인데 내가 그 뒤에 답함
        ev("네 보냈습니다", chat_id="d4", sid="me", me=True, mins=30),
        ev("버스 시간표 올립니다", src=Source.TELEGRAM, chat="준비방", chat_id=room, sid="p1", sender="마", group=True, read=False, mins=15),
        mail("서예 동호회 등록비 결제 완료", sid="noreply@pay.example", labels=("CATEGORY_UPDATES",), read=False, mid="n1"),      # 사업 자동 알림
        mail("견적 회신 부탁", account="minji@example.com", read=True, mid="m1"), mail("견적 회신 부탁", account="main@example.com", read=False, mid="m2"),
        mail("회의 자료 송부", read=False, mid="m3", mins=16),                                                    # 메일 안 읽음
    ]
    j = pipeline.judge(evs, S, NOW, set())
    unread_items = {i.conv for i in j.items if i.grade.value == "urgent"}
    unread_lines = {line.conv for line in feed(evs) if line.unread}
    assert unread_items == unread_lines and len(unread_lines) == 5


def test_settle_feed_keeps_claudes_box_after_the_message_is_read(tmp_path):
    from urgent_board import board
    from urgent_board.models import Grade, Item
    from urgent_board.store import Store
    st = Store(tmp_path / "t.db")
    a = ev("다음 주 일정 정리해서 보내드립니다", chat_id="c1", sid="u1", read=True)
    line, = feed([a])
    assert line.free and line.business == "other"
    st.put_pick(a.key, "옛 해시", "calligraphy-club", NOW)                             # 안 읽은 동안 Claude 가 고른 사업(글의 해시는 그때 것)
    picked = board.saved_picks(st, S, [line.key])
    seen = board.Visible([], set(), {})
    out, = board.settle_feed([line], [], seen, picked)                                # 읽어서 할 일 항목은 없다
    assert out.business == "calligraphy-club"                                            # 읽어도 같은 상자에 남는다
    st.put_pick(a.key, "옛 해시", "gone-business", NOW)
    assert board.saved_picks(st, S, [line.key]) == {}                                 # 진행 사업이 아니면 얹지 않는다


def test_settle_feed_drops_word_only_notices_claude_did_not_agree_with_and_handled_conversations():
    from urgent_board import board
    from urgent_board.models import Grade, Item
    n = mail("서예 동호회 등록비 결제 완료", sid="noreply@pay.example", labels=("CATEGORY_UPDATES",), read=False)
    talk = ev("자료 확인 부탁드립니다", chat_id="c9", sid="u9", read=False)
    lines = feed([n, talk])
    assert {line.word_only for line in lines} == {True, False}
    item = lambda e, reasons: Item(grade=Grade.URGENT, business="calligraphy-club", source=e.source, chat_name="", sender_name="", summary="",
                                   received_at=e.received_at, waited_minutes=0, deadline=None, reasons=reasons, key=e.key, conv=pipeline.conv_id(e))
    seen = board.Visible([], set(), {})
    assert [line.text for line in board.settle_feed(feed([n, talk]), [], seen)] == ["자료 확인 부탁드립니다"]       # Claude 가 동의하지 않았다
    agreed = board.settle_feed(feed([n, talk]), [item(n, ["business_notice", "word_only"])], seen)
    assert len(agreed) == 2                                                           # 동의한 것은 남는다
    handled = board.settle_feed(feed([n, talk]), [item(talk, [])], board.Visible([], set(), {talk.key: NOW}))
    assert [line.text for line in handled] == []                                      # 다룬 대화 · 동의 없는 알림 모두 빠진다


# --- 2단계: 알림에만 들어가는 출처(부재중 전화 · 팀즈 · 텔레그램 봇 — 설계 D6 · D9)
def test_calls_teams_and_bots_go_to_the_notice_board_only():
    call = ev("부재중 전화", src=Source.CALLS, chat="+821011112222", chat_id="+821011112222", sid="+821011112222", sender="가나다", read=False, mins=5)
    note = ev("자료 확인 부탁드려요", src=Source.TEAMS, chat="라마바", chat_id="라마바", sid="라마바", sender="라마바", read=False, mins=6)
    bot = ev("오늘의 입찰 보고서입니다", src=Source.TELEGRAM, chat="입찰봇", chat_id="777", sid="777", sender="입찰봇", read=False, mins=7, raw={"bot": True})
    j = pipeline.judge([call, note, bot], S, NOW, set())
    assert j.items == [] and j.skipped == [] and j.awaiting == []                   # 할 일 · 목록에는 없다(조용히 빠진 것도 아니다 — 처음부터 판정 밖)
    lines = feed([call, note, bot])
    assert [(line.source, line.unread) for line in lines] == [(Source.CALLS, 1), (Source.TEAMS, 1), (Source.TELEGRAM, 1)]


def test_bots_can_be_left_off_the_notice_board():
    from dataclasses import replace
    bot = ev("오늘의 입찰 보고서입니다", src=Source.TELEGRAM, chat="입찰봇", chat_id="777", sid="777", sender="입찰봇", read=False, raw={"bot": True})
    off = replace(S, display={**S.display, "feed_bots": False})
    assert pipeline.feed([bot], off, NOW, set()) == [] and len(feed([bot])) == 1


def test_a_missed_call_lands_in_the_same_box_as_texts_from_that_number():
    # 전화는 문자와 같은 번호 꼴(+82…) — 같은 사람으로 묶여 사업을 물려받는다(문자에 사업 낱말이 있으면 전화도 그 상자)
    sms = ev("서예 동호회 일정 공유드립니다", src=Source.IMESSAGE, chat="+821011112222", chat_id="7", sid="+821011112222", sender="가나다", mins=60)
    call = ev("부재중 전화", src=Source.CALLS, chat="가나다", chat_id="+821011112222", sid="+821011112222", sender="가나다", read=False, mins=5)
    by = {line.source: line.business for line in feed([sms, call])}
    assert by == {Source.IMESSAGE: "calligraphy-club", Source.CALLS: "calligraphy-club"}



def test_bot_reports_show_only_while_unread_and_go_to_the_etc_box_unless_the_bot_room_is_registered():
    # 적용 뒤 실측(2026-10-17): 봇 방 9곳 · 132건이 모두 읽은 것인데 ○ 로 남았고, Claude 를 잇는 봇 · 스레드 보고서 봇이 낱말 때문에 사업 상자 맨 위에 떴다
    from dataclasses import replace
    unread = ev("책방운영단 관련 글 3건을 찾았습니다", src=Source.TELEGRAM, chat="쓰레드봇", chat_id="777", sid="777", sender="쓰레드봇", read=False, mins=5, raw={"bot": True})
    read = ev("어제 보고서입니다", src=Source.TELEGRAM, chat="쓰레드봇", chat_id="778", sid="778", sender="입찰봇", read=True, mins=6, raw={"bot": True})
    line, = feed([unread, read])
    assert line.business == "other" and not line.free and line.unread == 1               # 낱말('책방운영단')이 있어도 개인 · 기타
    box = next(b for b in S.businesses if b.key == "bookshop-team")
    reg = replace(S, businesses=[replace(b, telegram_chat_ids=[*b.telegram_chat_ids, 777]) if b is box else b for b in S.businesses])
    assert [l.business for l in pipeline.feed([unread], reg, NOW, set())] == ["bookshop-team"]   # 상자에 등록한 봇 방이면 그 상자


def test_greeting_strip_uses_configured_organisation_names():
    from urgent_board import board
    text = "[한빛] 안녕하세요 한빛재단 가나다입니다 일정 확인 부탁"
    line = type("L", (), {"text": text})()
    assert "한빛재단" in board.feed_what(line)                                   # 이름을 모르면 기관 이름은 남는다
    assert "한빛재단" not in board.feed_what(line, orgs=("한빛재단",))
    assert board.feed_what(line, orgs=("한빛재단",)).startswith("일정 확인")
