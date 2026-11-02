"""부재중 전화 · 팀즈 알림 수집기(2026-10-17 알림 2단계 — 설계 docs/exec-plans/2026-10-17-notice-feed-design.md D6~D10).
맥의 통화 기록 · 알림 센터 DB 를 흉내 낸 작은 DB 를 임시 폴더에 만들어 읽는다. 번호 · 이름 · 글은 모두 지어낸 것이다."""
import plistlib
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from urgent_board.collectors import calls, teams
from urgent_board.collectors.base import run_loop
from urgent_board.models import Source
from urgent_board.store import Store

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)


def apple(at: datetime) -> float:
    return at.timestamp() - calls.APPLE_EPOCH


def call_db(path, rows):
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE ZCALLRECORD (Z_PK INTEGER PRIMARY KEY, ZUNIQUE_ID TEXT, ZDATE REAL, ZADDRESS TEXT, ZANSWERED INTEGER, ZORIGINATED INTEGER,
                 ZSERVICE_PROVIDER TEXT, ZFILTERED_OUT_REASON INTEGER, ZJUNKCONFIDENCE INTEGER, ZDURATION REAL, ZREAD INTEGER)""")
    for i, (hours_ago, address, answered, originated, extra) in enumerate(rows, 1):
        c.execute("INSERT INTO ZCALLRECORD VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                  (i, f"uid-{i}", apple(NOW - timedelta(hours=hours_ago)), address, answered, originated,
                   extra.get("service", "com.apple.Telephony"), extra.get("filtered", 0), extra.get("junk", 0), 0.0))
    c.commit(); c.close()


def test_missed_calls_are_events_and_read_when_called_back_or_a_day_old(tmp_path):
    db = tmp_path / "CallHistory.storedata"
    call_db(db, [
        (3, "01011112222", 0, 0, {}),          # 받지 않음 · 다시 걸지 않음 → ●
        (5, "01033334444", 0, 0, {}),          # 받지 않음 → 그 뒤에 내가 걺 → ○
        (4, "010-3333-4444", 0, 1, {}),
        (30, "0212345678", 0, 0, {}),          # 받지 않음 · 하루 넘음 → ○
        (2, "01055556666", 1, 0, {}),          # 받은 전화 — 알림이 아니다
        (1, "01077778888", 0, 0, {"junk": 1}),  # 스팸으로 걸러진 것
        (6, "01099990000", 0, 0, {"service": "com.apple.FaceTime"}),
        (200, "01011112222", 0, 0, {}),        # 7일 밖
    ])
    names = {"+821011112222": "가나다"}.get
    evs = calls.CallsCollector(db, names=lambda h: names(h) or "", clock=lambda: NOW).collect(SINCE)
    by = {e.sender_id: e for e in evs}
    assert set(by) == {"+821011112222", "+821033334444", "+82212345678", "+821099990000"}
    assert not by["+821011112222"].is_read and by["+821011112222"].sender_name == "가나다"       # 이름은 맥 연락처에서(문자와 같은 꼴의 번호로)
    assert by["+821033334444"].is_read and by["+82212345678"].is_read                            # 다시 걺 · 하루 넘음
    assert by["+821099990000"].text == "부재중 전화 (FaceTime)" and by["+821099990000"].sender_name == "+821099990000"
    assert all(e.source == Source.CALLS and not e.is_group and not e.is_from_me for e in evs)


def test_calls_number_has_the_same_shape_as_the_messages_app():
    assert calls.handle("01012345678") == "+821012345678" and calls.handle("010-1234-5678") == "+821012345678"
    assert calls.handle("+82 10 1234 5678") == "+821012345678" and calls.handle("821012345678") == "+821012345678"
    assert calls.handle("15881234") == "15881234" and calls.handle("") == "" and calls.handle(None) == ""
    assert calls.handle("+82 010-1234-5678") == "+821012345678"                     # 0 을 남긴 국제 표기(검토 2026-10-17)
    assert calls.handle("00182212345678") == "00182212345678"                       # 국제 전화 접두는 그대로(나라 번호를 가르지 않는다)


def test_a_mac_without_the_files_is_empty_not_broken(tmp_path):
    assert calls.CallsCollector(tmp_path / "없음").collect(SINCE) == []
    t = teams.TeamsCollector(tmp_path / "없음")
    assert t.collect(SINCE) == [] and t.present_ids() is None                      # 모른다 — 읽음을 맞추지 않는다


def note(title="가나다", body="자료 확인 부탁드려요", subt=None) -> bytes:
    req = {"titl": title, "body": body, "iden": "x", "cate": "message"}
    if subt:
        req["subt"] = subt
    return plistlib.dumps({"app": "com.microsoft.teams2", "date": 0.0, "req": req, "uuid": b"0" * 16}, fmt=plistlib.FMT_BINARY)


def notice_db(path, rows):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE app (app_id INTEGER PRIMARY KEY, identifier TEXT, badge INTEGER)")
    c.execute("CREATE TABLE record (rec_id INTEGER PRIMARY KEY, app_id INTEGER, uuid BLOB, data BLOB, request_date REAL, request_last_date REAL, "
              "delivered_date REAL, presented INTEGER, style INTEGER, snooze_fire_date REAL)")
    c.execute("INSERT INTO app VALUES (1, 'com.microsoft.teams2', 0), (2, 'com.kakao.KakaoTalkMac', 0)")
    for i, (app_id, hours_ago, data) in enumerate(rows, 1):
        c.execute("INSERT INTO record (rec_id, app_id, uuid, data, delivered_date, presented) VALUES (?,?,?,?,?,0)",
                  (i, app_id, bytes([i]) * 16, data, apple(NOW - timedelta(hours=hours_ago))))
    c.commit(); c.close()


def test_teams_notifications_are_events_while_they_stay_in_the_notification_center(tmp_path):
    db = tmp_path / "db"
    notice_db(db, [(1, 2, note()), (1, 1, note("나다라", "회의 자료 올렸습니다", subt="준비방")), (2, 1, note("카톡", "다른 앱")),
                   (1, 3, b"not a plist"), (1, 300, note("오래됨", "7일 밖")), (1, 5, note("다라마", "어제 공유한 표"))])
    t = teams.TeamsCollector(db, clock=lambda: NOW)
    evs = t.collect(SINCE)
    assert [(e.sender_name, e.text) for e in sorted(evs, key=lambda e: e.received_at)] == [("다라마", "어제 공유한 표"), ("가나다", "자료 확인 부탁드려요"), ("나다라", "준비방 — 회의 자료 올렸습니다")]
    assert all(e.source == Source.TEAMS and not e.is_group for e in evs)              # 팀즈만
    assert {e.sender_name: e.is_read for e in evs} == {"다라마": True, "가나다": False, "나다라": False}   # 3시간 넘은 것은 남아 있어도 ○(D8 — 실측: 알림 센터는 비워지지 않는다)
    assert len(t.present_ids()) == 5                                                                  # 꼴이 깨진 것 · 7일 밖도 '아직 살아 있다'(지운 것이 아니다)


def run_once(collector, store):
    """run_loop 한 바퀴(수집 → 저장 → 읽음 맞추기 → 상태)를 이 스레드에서 — 수집이 끝나면 멈춤 신호를 켜 두어 루프가 한 번만 돈다."""
    stop = threading.Event()
    real = collector.collect

    def collect_once(since):
        try:
            return real(SINCE)                                                       # 시험의 시각으로 자른다
        finally:
            stop.set()
    collector.collect = collect_once
    try:
        run_loop(collector, store, stop)
    finally:
        collector.collect = real


def test_a_notification_that_left_the_notification_center_becomes_read(tmp_path):
    db = tmp_path / "db"
    notice_db(db, [(1, 2, note("가나다", "첫 글")), (1, 1, note("나다라", "둘째 글"))])
    st = Store(tmp_path / "t.db")
    t = teams.TeamsCollector(db, clock=lambda: NOW)
    run_once(t, st)
    assert sorted(e.is_read for e in st.recent_events(3650)) == [False, False] and st.source_status()["teams"]["ok"]
    c = sqlite3.connect(db); c.execute("DELETE FROM record WHERE rec_id = 1"); c.commit(); c.close()     # 팀즈에서 읽었다 — 알림이 사라진다
    run_once(t, st)
    assert {e.sender_name: e.is_read for e in st.recent_events(3650)} == {"가나다": True, "나다라": False}
    assert st.mark_read_missing(Source.TEAMS, set()) == 1                            # 알림 센터를 다 비우면 모두 읽음


def test_a_collector_that_does_not_know_what_is_present_marks_nothing(tmp_path):
    st = Store(tmp_path / "t.db")
    t = teams.TeamsCollector(tmp_path / "없음")                                       # 파일이 없는 맥 — present_ids 는 None
    run_once(t, st)
    assert st.source_status()["teams"]["ok"]                                          # 멈춤이 아니다(D10)



def test_aux_sources_do_not_make_the_board_partly_blind():
    # 독립 검토(2026-10-17): 권한이 막힌 맥에서 켠 적도 없는 곁가지(전화 · 팀즈)가 본 화면을 '일부 멈춤'으로 만들었다 — 연결 칸 · doctor 에만
    from urgent_board import web
    ok = {"ok": True, "stale": False}
    bad = {"ok": False, "stale": False, "note": "OperationalError: authorization denied"}
    assert web.blindness({"imessage": ok, "calls": bad, "teams": bad}) == "" and web.down_labels({"imessage": ok, "calls": bad, "teams": bad}) == []
    assert web.blindness({"imessage": bad, "calls": ok}) == "all" and web.down_labels({"imessage": bad, "calls": ok}) == ["문자"]


def test_notice_only_messages_are_not_places_to_pick_in_settings(tmp_path):
    # 독립 검토(2026-10-17): 설정 화면의 '받은 곳' · 고르기 목록에 전화번호 · 팀즈 제목 · 봇 방이 떠 상자에 등록할 수 있었다
    from pathlib import Path as P
    from urgent_board import setup_steps
    from urgent_board.config import load
    from urgent_board.models import Event
    s = load(P(__file__).resolve().parents[0] / "fixtures" / "home")
    mk = lambda src, cid, sid, text, raw=None: Event(source=src, chat_id=cid, message_id=cid + text, chat_name=cid, sender_id=sid, sender_name=sid,
                                                     text=text, received_at=NOW - timedelta(hours=1), is_from_me=False, is_read=False, is_group=False, raw=raw or {})
    evs = [mk(Source.CALLS, "+821011112222", "+821011112222", "부재중 전화"), mk(Source.TEAMS, "준비방", "준비방", "자료 올렸습니다"),
           mk(Source.TELEGRAM, "777", "777", "오늘의 보고서", {"bot": True}), mk(Source.KAKAO, "c1", "u1", "자료 확인 부탁드립니다")]
    got = setup_steps.places(evs, s, NOW)
    assert [(p["source"], p["id"]) for p in got] == [("kakao", "u1")]
    st = Store(tmp_path / "t.db")
    st.upsert_events([e for e in evs])
    cat = st.catalog(days=36500)
    assert {(r["source"], r["id"]) for r in cat["people"]} == {("kakao", "u1")} and [r["source"] for r in cat["rooms"]] == ["kakao"]
