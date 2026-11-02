from datetime import datetime, timedelta, timezone
from pathlib import Path

from urgent_board import board, config
from urgent_board.models import Event, Source
from urgent_board.store import Store
from urgent_board.triage import business

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
NOW = datetime.now(timezone.utc)


def ev(i, src, chat, name, sender, text, read=False, me=False, group=False, account=""):
    return Event(source=src, chat_id=chat, message_id=str(i), chat_name=name, sender_id=sender, sender_name=sender.upper(), text=text,
                 received_at=NOW - timedelta(minutes=10 * i), is_from_me=me, is_read=read, is_group=group, account=account)


def test_explain_gives_the_reason_and_match_agrees():
    s = config.load(HOME)
    room = ev(1, Source.KAKAO, "100000000000003", "아무 이름", "7", "발권 확인 부탁드립니다", group=True)
    assert business.explain(room, s) == ("bookshop-team", True, "등록한 카톡 방")
    word = ev(2, Source.GMAIL, "t", "확장개점 기념 학술대회", "x@y.z", "확장개점 기념 학술대회 — 일정")
    assert business.explain(word, s) == ("bookshop-team", False, "낱말 '확장개점'(4자)")
    unconf = ev(3, Source.GMAIL, "t", "정책설명회 대행 제안", "x@y.z", "정책설명회 — 제안서")
    key, _, why = business.explain(unconf, s)
    assert key == "other" and why.startswith("확정 전 일 '") and "'정책설명회'" in why
    personal = ev(4, Source.GMAIL, "t", "안부", "x@y.z", "안부", account="snack@example.com")
    assert business.explain(personal, s) == ("personal", False, "개인 메일 계정")
    nothing = ev(5, Source.KAKAO, "1", "모르는 방", "9", "안녕하세요")
    assert business.explain(nothing, s) == ("other", False, "어디에도 안 걸림")
    for e in (room, word, unconf, personal, nothing):
        assert business.match(e, s) == business.explain(e, s)[:2]


def test_screen_is_what_the_board_shows(tmp_path):
    s = config.load(HOME)
    st = Store(tmp_path / "t.db")
    st.upsert_events([ev(1, Source.KAKAO, "100000000000003", "방", "7", "발권 확인 부탁드립니다")])     # 1:1 — 나에게 온 것
    sc = board.screen(st, s, NOW)
    assert [i.business for i in sc.items] == ["bookshop-team"]
    assert sc.cards[[c.key for c in sc.cards].index("bookshop-team")].urgent == 1 and sc.recipe


def test_catalog_lists_rooms_and_people_with_text_ids(tmp_path):
    st = Store(tmp_path / "t.db")
    st.upsert_events([ev(1, Source.KAKAO, "100000000000001123", "큰 번호 방", "7", "안녕", group=True),
                      ev(2, Source.KAKAO, "100000000000001123", "큰 번호 방(옛 이름)", "8", "확인", group=True, read=True),
                      ev(3, Source.TELEGRAM, "-100000000001", "단톡", "9", "공유", group=True),
                      ev(4, Source.GMAIL, "thread", "제목", "a@b.c", "메일"),
                      ev(5, Source.IMESSAGE, "12", "+821012345678", "+821012345678", "문자"),
                      ev(6, Source.KAKAO, "1", "내 방", "me", "내가 씀", me=True)])
    c = st.catalog()
    rooms = {(r["source"], r["chat_id"]): r for r in c["rooms"]}
    big = rooms[("kakao", "100000000000001123")]
    assert big["name"] == "큰 번호 방" and big["count"] == 2 and big["unread"] == 1 and big["group"] is True   # 이름은 가장 최근 글의 것
    assert ("gmail", "thread") not in rooms and ("telegram", "-100000000001") in rooms and ("imessage", "12") in rooms
    people = {(p["source"], p["id"]) for p in c["people"]}
    assert ("gmail", "a@b.c") in people and ("kakao", "me") not in people
    assert all(isinstance(r["chat_id"], str) for r in c["rooms"]) and [r["chat_id"] for r in c["rooms"]][0] == "100000000000001123"


def _one(tmp_path):
    st = Store(tmp_path / "t.db")
    st.upsert_events([ev(1, Source.KAKAO, "100000000000003", "방", "7", "발권 확인 부탁드립니다")])
    return st


def test_preview_screen_does_not_write_the_book(tmp_path):
    s, st = config.load(HOME), _one(tmp_path)
    sc = board.screen(st, s, NOW)                                    # write 기본값 False — 미리 보기 · 옮기기
    assert [t.business for t in sc.tasks] == ["bookshop-team"] and st.tasks() == []
    board.screen(st, s, NOW, write=True)                             # 엔진(웹)만 장부를 쓴다
    assert [r["business"] for r in st.tasks()] == ["bookshop-team"]
    assert next(c for c in sc.cards if c.key == "bookshop-team").urgent == 1


def test_screen_survives_a_broken_book(tmp_path, monkeypatch):
    import sqlite3
    s, st = config.load(HOME), _one(tmp_path)

    def broken(state="open"):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(st, "tasks", broken)
    sc = board.screen(st, s, NOW, write=True)
    assert sc.book_error and [t.business for t in sc.tasks] == ["bookshop-team"]    # 7일 안의 할 일은 보인다


# --- Claude 가 고른 사업을 화면에 얹는다(2026-10-16 분류 키우기) — 규칙이 아무 데도 못 건 항목에만
def test_claude_pick_moves_a_free_item_into_a_box(tmp_path):
    from urgent_board import summarize
    s, st = config.load(HOME), Store(tmp_path / "t.db")
    st.upsert_events([ev(1, Source.KAKAO, "c-free", "가나다", "u-free", "다음 주 회의 자료 확인 부탁드립니다 회신 바랍니다"),
                      ev(2, Source.KAKAO, "c-word", "라마바", "u-word", "서예 동호회 자료 확인 부탁드립니다 회신 바랍니다")])
    sc = board.screen(st, s, NOW)
    free = next(i for i in sc.items if i.business_free)
    word = next(i for i in sc.items if not i.business_free)
    st.put_pick(free.key, summarize.text_hash(free.text, sc.recipe), "bazaar2", NOW)
    st.put_pick(word.key, summarize.text_hash(word.text, sc.recipe), "bazaar2", NOW)       # 규칙이 건 것에는 얹지 않는다
    sc = board.screen(st, s, NOW)
    got = {t.key: (t.business, t.guessed) for t in sc.tasks}
    assert got[free.key] == ("bazaar2", True) and got[word.key] == ("calligraphy-club", False)
    assert next(c for c in sc.cards if c.key == "bazaar2").tasks[0].key == free.key


def test_a_pick_for_a_resting_box_is_ignored(tmp_path):
    from urgent_board import summarize
    s, st = config.load(HOME), Store(tmp_path / "t.db")
    st.upsert_events([ev(1, Source.KAKAO, "c-free", "가나다", "u-free", "다음 주 회의 자료 확인 부탁드립니다 회신 바랍니다")])
    sc = board.screen(st, s, NOW)
    st.put_pick(sc.items[0].key, summarize.text_hash(sc.items[0].text, sc.recipe), "없는-상자", NOW)
    assert [t.business for t in board.screen(st, s, NOW).tasks] == ["other"]


# --- 답 기다림의 문구와 거르기(2026-10-16): 같은 Claude 호출이 "상대가 할 일"을 준다 · 상대가 할 일이 없다고 본 것(안내 · 공지)은 뺀다
def _my_text(i, chat, text):
    return Event(source=Source.IMESSAGE, chat_id=chat, message_id=f"m{i}", chat_name="봉미르 교수", sender_id="me", sender_name="나", text=text,
                 received_at=NOW - timedelta(hours=i), is_from_me=True, is_read=True, is_group=False)


def test_awaiting_uses_claudes_wording_and_drops_what_needs_no_reply(tmp_path):
    from urgent_board import summarize
    s, st = config.load(HOME), Store(tmp_path / "t.db")
    st.upsert_events([_my_text(1, "+821011112222", "안녕하세요, 한울기획 김민수입니다. 내일 오전 통화 가능하실지 회신 부탁드립니다"),
                      _my_text(2, "+821033334444", "안녕하세요, 한울기획 김민수입니다. 다음 주 일정 안내드립니다 확인 부탁드립니다")])
    sc = board.screen(st, s, NOW)
    assert len(sc.awaiting) == 2 and all(t.what.startswith("안녕하세요") is False or t.what for t in sc.awaiting)
    a, b = sorted(sc.awaiting, key=lambda t: t.chat_id)
    r = sc.recipe
    st.put_task_line(a.key, summarize.text_hash(_text(sc, a.key), r), "통화 가능 회신", None, NOW)
    st.put_verdict(b.key, summarize.text_hash(_text(sc, b.key), r), False, NOW)
    sc = board.screen(st, s, NOW)
    assert [(t.chat_id, t.what) for t in sc.awaiting] == [("+821011112222", "통화 가능 회신")]


def _text(sc, key):
    from urgent_board.triage import pipeline
    return next(i.text for i in sc.awaiting_items if i.key == key)


# --- 사업 자동 알림의 확인(실제 자료 2026-10-16: OpenAI 홍보 메일 "항공 사진 아니면 인상주의?"가 낱말 '항공'으로 책방운영단에 떴다)
def _notice(i, sender, subject, text):
    return Event(source=Source.GMAIL, chat_id=f"n{i}", message_id=f"n{i}", chat_name=subject, sender_id=sender, sender_name=sender, text=f"{subject} — {text}",
                 received_at=NOW - timedelta(hours=i), is_from_me=False, is_read=False, is_group=False, account="main@example.com",
                 raw={"subject": subject, "headers": {}, "to": ["main@example.com"], "labels": ["CATEGORY_UPDATES", "INBOX", "UNREAD"]})


def test_a_notice_caught_only_by_a_word_needs_claude_to_agree(tmp_path):
    from urgent_board import summarize
    s, st = config.load(HOME), Store(tmp_path / "t.db")
    st.upsert_events([_notice(1, "noreply@email.openai.com", "새 이미지 스타일", "서예 사진 아니면 인상주의? 새 스타일을 만나보세요")])
    sc = board.screen(st, s, NOW)
    assert sc.tasks == [] and [x.reason for x in sc.skipped] == ["notice"]           # 아직 Claude 가 안 봤다 — 띄우지 않고 까닭을 남긴다
    from urgent_board.triage import pipeline
    it = next(i for i in pipeline.build_items(st.recent_events(7), s, NOW, set()) if "business_notice" in i.reasons)
    st.put_pick(it.key, summarize.text_hash(it.text, sc.recipe), "calligraphy-club", NOW)
    assert [t.business for t in board.screen(st, s, NOW).tasks] == ["calligraphy-club"]    # Claude 도 같은 사업이라고 보면 띄운다


def test_a_notice_from_a_registered_sender_shows_at_once(tmp_path):
    from urgent_board import settings_doc
    doc = settings_doc.read(HOME)[0]
    next(b for b in doc["boxes"] if b["key"] == "calligraphy-club")["senders"] = ["billing@example.com"]
    s, st = config.from_doc(doc, HOME), Store(tmp_path / "t.db")
    st.upsert_events([_notice(1, "billing@example.com", "결제대금 수령 출처: x@y.com", "$400.00 USD 의 결제대금을 받았습니다")])
    assert [t.business for t in board.screen(st, s, NOW).tasks] == ["calligraphy-club"]
