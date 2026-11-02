from datetime import datetime, timedelta, timezone
from urgent_board.models import Event, Source
from urgent_board.store import Store

def ev(mid, **kw):
    base = dict(source=Source.KAKAO, chat_id="1", message_id=mid, chat_name="방", sender_id="u1", sender_name="홍길동",
                text="안녕", received_at=datetime.now(timezone.utc), is_from_me=False, is_read=False, is_group=True)
    base.update(kw); return Event(**base)

def test_upsert_is_idempotent(tmp_path):
    s = Store(tmp_path / "t.db")
    assert s.upsert_events([ev("a"), ev("b")]) == 2
    assert s.upsert_events([ev("a")]) == 0
    assert len(s.recent_events(days=1)) == 2

def test_upsert_updates_read_state(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_events([ev("a", is_read=False)])
    s.upsert_events([ev("a", is_read=True)])
    assert s.recent_events(days=1)[0].is_read is True

def test_dismiss_and_status(tmp_path):
    s = Store(tmp_path / "t.db"); now = datetime.now(timezone.utc)
    s.dismiss("kakao:1:a", now + timedelta(days=1))
    assert "kakao:1:a" in s.dismissed_keys(now)
    assert "kakao:1:a" not in s.dismissed_keys(now + timedelta(days=2))
    s.set_source_status("kakao", False, "db locked", now)
    assert s.source_status()["kakao"]["ok"] is False


def test_store_closes_every_connection_it_opens(tmp_path):
    # 실측 2026-10-13(다른 세션의 자원 감시기가 알림): 요청 300번에 열린 파일이 70 → 226개. `with sqlite3.connect()` 는 닫지 않는다 —
    # 치우는 것은 파이썬의 쓰레기 수거에 달려 있었다. launchd 가 띄운 프로세스의 한도는 256 이라, 닿으면 살아 있는데 답을 못 한다.
    import gc, os
    from datetime import datetime, timezone
    st = Store(tmp_path / "t.db"); now = datetime.now(timezone.utc)
    def open_files(): return len(os.listdir("/dev/fd"))
    gc.disable()                                   # 수거에 기대지 않고도 닫히는지 본다
    try:
        before = open_files()
        for i in range(120):
            st.recent_events(days=7); st.dismissed_keys(now); st.source_status(); st.get_summaries(["k"]); st.get_verdicts(["k"])
            st.touched_at(["a"]); st.get_scan_pos(["p"]); st.put_summary(f"k{i}", "h", "s", now); st.count_summaries_since(now)
        assert open_files() - before <= 3, open_files() - before
    finally:
        gc.enable()


def test_mark_read_upto_settles_old_messages_the_collector_did_not_see_again(tmp_path):
    # 텔레그램 수집기는 방마다 최근 글만 다시 본다 — 30건 넘게 쌓인 뒤 읽으면 그 앞의 글은 '안 읽음'으로 남았다(2026-10-14 검토).
    # 방의 '여기까지 읽음' 번호로 그 앞의 글을 모두 읽음으로 맞춘다. 다른 방 · 다른 출처 · 더 뒤의 글은 건드리지 않는다.
    s = Store(tmp_path / "t.db")
    s.upsert_events([ev(str(i), source=Source.TELEGRAM, chat_id="-5") for i in (8, 9, 10, 11)]
                    + [ev("9", source=Source.TELEGRAM, chat_id="-6"), ev("9", source=Source.KAKAO, chat_id="-5")])
    assert s.mark_read_upto(Source.TELEGRAM, {"-5": 10}) == 3
    read = {(e.source.value, e.chat_id, e.message_id): e.is_read for e in s.recent_events(days=1)}
    assert [read[("telegram", "-5", m)] for m in ("8", "9", "10", "11")] == [True, True, True, False]
    assert read[("telegram", "-6", "9")] is False and read[("kakao", "-5", "9")] is False
    assert s.mark_read_upto(Source.TELEGRAM, {}) == 0


def test_upsert_updates_names_seen_later(tmp_path):
    # 문자는 처음에 번호로 들어왔다가 연락처 이름을 알게 된다(2026-10-14) — 다시 보면 이름을 고친다. 본문은 고치지 않는다.
    s = Store(tmp_path / "t.db")
    s.upsert_events([ev("a", chat_name="+821000000000", sender_name="+821000000000")])
    s.upsert_events([ev("a", chat_name="협의회 가나다 연구관", sender_name="협의회 가나다 연구관", text="바뀐 글")])
    e = s.recent_events(days=1)[0]
    assert (e.chat_name, e.sender_name, e.text) == ("협의회 가나다 연구관", "협의회 가나다 연구관", "안녕")
