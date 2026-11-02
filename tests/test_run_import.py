import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"

def test_import_starts_no_threads():                       # Gotcha: import 만으로 스레드·네트워크가 일어나면 안 된다
    before = threading.active_count()
    import run_board  # noqa: F401
    assert threading.active_count() == before

def _settings(tmp_path, telegram=False, gmail=()):
    import json
    from urgent_board.config import load
    s = load(HOME)
    s.home = tmp_path
    s.accounts = {"gmail": [{"alias": a, "email": e, "token": f"file:gmail-{a}.json"} for a, e in gmail],
                  "telegram": {"enabled": telegram}, "kakao": {"enabled": True}, "imessage": {"enabled": True}}
    if telegram:
        (tmp_path / "auth").mkdir(exist_ok=True)
        (tmp_path / "auth" / "telegram.json").write_text(json.dumps({"api_id": 1, "api_hash": "h"}), encoding="utf-8")
    return s

def test_collectors_without_secrets_are_local_only(tmp_path):
    import run_board
    before = threading.active_count()
    names = [c.name for c, _ in run_board.collectors(_settings(tmp_path))]
    assert names == ["imessage", "kakao"] and threading.active_count() == before

def test_collectors_from_accounts_and_no_naver(tmp_path):
    import run_board
    cs = run_board.collectors(_settings(tmp_path, telegram=True, gmail=(("a", "a@x.com"), ("b", "b@x.com"))))
    names = [c.name for c, _ in cs]
    assert names == ["imessage", "kakao", "telegram", "gmail:a", "gmail:b"]
    assert [net for _, net in cs] == [False, False, True, True, True]      # 네트워크가 필요한 것만 DNS 를 기다린다
    assert not any("naver" in n for n in names)

def test_telegram_on_without_api_values_shows_as_stopped(tmp_path):
    import pytest
    import run_board
    s = _settings(tmp_path); s.accounts["telegram"] = {"enabled": True}
    c = next(c for c, _ in run_board.collectors(s) if c.name == "telegram")
    with pytest.raises(RuntimeError, match="telegram.json"):
        c.collect(None)                                    # 조용히 빠지지 않고 "수집 중단"으로 보인다(죽은 것을 없음으로 보이지 말 것)

def test_switched_off_sources_are_not_collected(tmp_path):
    import run_board
    s = _settings(tmp_path); s.accounts["kakao"] = {"enabled": False}; s.accounts["imessage"] = {"enabled": False}
    assert run_board.collectors(s) == []

def test_replay_rows_have_reasons(tmp_path):
    from datetime import datetime, timedelta, timezone
    from tools import replay
    from urgent_board.config import load
    from urgent_board.models import Event, Source
    from urgent_board.store import Store
    st = Store(tmp_path / "t.db")
    st.upsert_events([Event(source=Source.KAKAO, chat_id="1", message_id="9", chat_name="방", sender_id="u", sender_name="주하늘",
                            text="긴급 발권 확인 부탁드립니다", received_at=datetime.now(timezone.utc) - timedelta(hours=5),
                            is_from_me=False, is_read=False, is_group=False)])
    rows = replay.rows(st, load(HOME), datetime.now(timezone.utc))
    assert len(rows) == 1 and rows[0]["grade"] == "urgent" and "urgent_word" in rows[0]["reasons"] and rows[0]["sender"] == "주하늘"
    c = replay.counts(rows)
    assert c["grade"] == {"urgent": 1} and c["reason"]["urgent_word"] == 1

def test_wait_for_network_retries_then_succeeds():
    import socket
    import run_board
    calls = []
    def resolve(host, port):
        calls.append(host)
        if len(calls) < 3:
            raise socket.gaierror("nodename nor servname")
    assert run_board.wait_for_network(threading.Event(), resolve=resolve, delay=0) is True and len(calls) == 3

def test_wait_for_network_gives_up_and_stops_on_event():
    import socket
    import run_board
    def never(host, port):
        raise socket.gaierror("x")
    assert run_board.wait_for_network(threading.Event(), resolve=never, tries=2, delay=0) is False
    stop = threading.Event(); stop.set()
    assert run_board.wait_for_network(stop, resolve=never, tries=99, delay=0) is False

def test_collectors_take_poll_seconds_from_settings(tmp_path):
    import run_board
    s = _settings(tmp_path); s.poll_seconds = {"imessage": 7, "kakao": 33}
    assert [c.poll_seconds for c, _ in run_board.collectors(s)] == [7, 33]

def test_announce_makes_a_hung_first_sweep_show_as_down(tmp_path):         # C1 — 한 번도 기록 못 한 수집기도 '수집 중단'
    from datetime import datetime, timedelta, timezone
    import run_board
    from urgent_board.store import Store
    from urgent_board.web import create_app
    s = _settings(tmp_path); st = Store(tmp_path / "t.db"); now = datetime.now(timezone.utc)
    cs = [c for c, _ in run_board.collectors(s)]
    assert create_app(s, st).test_client().get("/api/items").get_json()["down"] == []      # 알리기 전: 아무것도 안 보인다(결함의 꼴)
    run_board.announce(st, cs, now)
    j = create_app(s, st).test_client().get("/api/items").get_json()
    assert set(j["sources"]) == {"imessage", "kakao"} and j["down"] == []                  # 막 떴다 — 아직 정상
    run_board.announce(st, cs, now - timedelta(minutes=30))                                # 30분 전에 떴고 그 뒤 아무도 기록 못 함
    assert create_app(s, st).test_client().get("/api/items").get_json()["down"] == ["문자", "카톡"]

def test_announce_forgets_sources_that_no_longer_exist(tmp_path):          # 뺀 계정이 영원히 '수집 중단'으로 남지 않게
    from datetime import datetime, timezone
    import run_board
    from urgent_board.store import Store
    s = _settings(tmp_path); st = Store(tmp_path / "t.db"); now = datetime.now(timezone.utc)
    st.set_source_status("gmail:removed", False, "old", now)
    run_board.announce(st, [c for c, _ in run_board.collectors(s)], now)
    assert set(st.source_status()) == {"imessage", "kakao"}

def test_summarizer_is_made_only_when_enabled(tmp_path):
    import run_board
    s = _settings(tmp_path)
    assert run_board.make_summarizer(s) is not None
    s.summary = {**s.summary, "enabled": False}
    assert run_board.make_summarizer(s) is None

def test_summary_cycle_summarizes_what_the_board_shows(tmp_path):
    from datetime import datetime, timedelta, timezone
    import run_board
    from urgent_board.models import Event, Source
    from urgent_board.store import Store
    st = Store(tmp_path / "t.db"); now = datetime.now(timezone.utc)
    st.upsert_events([Event(source=Source.KAKAO, chat_id="1", message_id="9", chat_name="김하늘", sender_id="u", sender_name="김하늘",
                            text="안녕하세요 대장님, 다음 주 수요일 답사 차량은 9시에 출발하면 될지 확인 부탁드립니다. 인원은 12명입니다.",
                            received_at=now - timedelta(minutes=5), is_from_me=False, is_read=False, is_group=False)])
    class Fake:
        def summarize(self, sender, where, text, when=None): return "답사 차량 9시 출발 확인 요청"
        def digest(self, text, max_chars): raise AssertionError("한 건뿐인 상자에는 사업 요약을 만들지 않는다")
    out = run_board.summary_cycle(st, _settings(tmp_path), Fake(), now)
    assert out.made == 1 and not out.failed                                    # 항목 요약 하나(사업 요약은 없다 — 2026-10-16)
    assert list(st.get_summaries(["kakao:1:9"]).values())[0][1] == "답사 차량 9시 출발 확인 요청"
    assert run_board.summary_cycle(st, _settings(tmp_path), Fake(), now).made == 0
    from urgent_board.web import create_app                          # 만든 요약이 화면에 실린다(만드는 쪽과 보이는 쪽이 같은 표식을 쓴다)
    it = create_app(_settings(tmp_path), st).test_client().get("/api/items").get_json()["items"][0]
    assert it["ai"] is True and it["summary"] == "답사 차량 9시 출발 확인 요청"


def test_summary_loop_backs_off_when_claude_keeps_failing():          # 실패해도 15초마다 다시 부르면 하루 수천 번 프로세스가 뜬다
    import run_board
    wait, back = run_board.next_wait(failed=True, backoff=0)
    assert wait == run_board.SUMMARY_BACKOFF and back == run_board.SUMMARY_BACKOFF
    wait2, back2 = run_board.next_wait(failed=True, backoff=back)
    assert wait2 == 2 * run_board.SUMMARY_BACKOFF
    for _ in range(10):
        wait2, back2 = run_board.next_wait(failed=True, backoff=back2)
    assert wait2 == run_board.SUMMARY_BACKOFF_MAX
    assert run_board.next_wait(failed=False, backoff=back2) == (run_board.SUMMARY_EVERY, 0)


def test_handled_cycle_reads_session_records_into_the_store(tmp_path):
    import json
    from datetime import datetime, timezone
    import run_board
    from urgent_board.store import Store
    s = _settings(tmp_path); s.handled = {**s.handled, "sessions_dir": str(tmp_path / "projects")}
    f = tmp_path / "projects" / "p" / "s.jsonl"; f.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    f.write_text(json.dumps({"type": "assistant", "entrypoint": "claude-vscode", "timestamp": now.isoformat().replace("+00:00", "Z"),
                             "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "x 19a1b2c3d4e5f607"}}]}}) + "\n")
    st = Store(tmp_path / "t.db")
    assert run_board.handled_cycle(st, s, now) == 1 and "19a1b2c3d4e5f607" in st.touched_at(["19a1b2c3d4e5f607"])


def test_server_ends_itself_when_its_own_web_stops_answering():
    # 봇 대시보드의 감시는 프로세스가 있는지만 본다. 열린 파일 한도에 닿거나 교착하면 "살아 있는데 답을 못 하는" 채로 남는다(2026-10-13 다른 봇에서 실제로 몇 시간)
    import run_board
    class Stop:
        def __init__(self, rounds): self.left = rounds
        def wait(self, _s): self.left -= 1; return self.left < 0
    died = []
    run_board.health_loop(5091, Stop(10), check=lambda port: False, die=died.append, every=0)
    assert died == [1]                                                          # 연달아 답이 없으면 한 번 끝낸다(감시가 다시 띄운다)
    answers = iter([False, False, True, False, False, True, True, True, True, True])
    died = []
    run_board.health_loop(5091, Stop(10), check=lambda port: next(answers), die=died.append, every=0)
    assert died == []                                                           # 사이에 한 번이라도 답하면 처음부터 다시 센다
    assert run_board.health_ok(1, timeout=0.2) is False                         # 닫힌 포트
    # 홈 CLAUDE.md 함정(2026-10-13): Flask 개발 서버는 상대가 본문을 다 읽지 않고 끊으면 요청마다 kqueue 하나를 못 닫는다 —
    # 60초마다 묻는 이 점검이 제 서버의 열린 파일을 늘리면 안 된다. 본문까지 읽고 닫는다
    import inspect
    assert ".read()" in inspect.getsource(run_board.health_ok)
    assert "ProxyHandler({})" in inspect.getsource(run_board)                   # 제 서버에 묻는 요청은 프록시 설정을 타지 않는다(검수)


def test_summary_cycle_makes_no_card_digests_any_more(tmp_path):
    """2026-10-16 할 일 현황판: 상자는 할 일 줄을 보인다 — 사업 요약(묶음 한 줄)을 부르지 않는다. 항목 요약만."""
    from datetime import datetime, timedelta, timezone
    import run_board
    from urgent_board import summarize
    from urgent_board.models import Event, Source
    from urgent_board.store import Store
    s = _settings(tmp_path)
    s.handled = {**s.handled, "claude_sessions": False}
    st = Store(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    st.upsert_events([Event(source=Source.KAKAO, chat_id=f"c-{w}", message_id=m, chat_name=w, sender_id=w, sender_name=w,
                            text="견적서를 오늘 안에 확인해 주실 수 있을까요 회신 부탁드립니다", received_at=now - timedelta(minutes=k),
                            is_from_me=False, is_read=False, is_group=False) for m, w, k in (("1", "가나다", 60), ("2", "라마바", 30))])
    calls = []

    class Fake:
        def summarize(self, sender, where, text, when=None):
            calls.append("item")
            return summarize.Reading("견적서 오늘 안에 확인 요청", True, "견적서 확인 회신", None)

        def digest(self, text, max_chars):
            calls.append("digest")
            return "x"
    out = run_board.summary_cycle(st, s, Fake(), now)
    assert calls == ["item", "item"] and out.made == 2 and not out.failed


def test_new_task_settings_have_defaults_and_bounds(tmp_path):
    from urgent_board import config, settings_doc as sd
    s = config.from_doc(sd.example(), tmp_path)
    assert s.summary["task_max_chars"] == 14 and s.display["old_task_days"] == 14
    bad = sd.example(); bad["summary"]["task_max_chars"] = 200; bad["display"]["old_task_days"] = 0
    paths_ = {p.path for p in config.problems(bad) if p.level == "error"}
    assert {"summary.task_max_chars", "display.old_task_days"} <= paths_
    assert run_board_key_changes_with_task_chars(s)


def run_board_key_changes_with_task_chars(s) -> bool:
    import run_board
    a = run_board.summarizer_key(s)
    s.summary = {**s.summary, "task_max_chars": 10}
    return run_board.summarizer_key(s) != a

def test_whatsapp_collector_only_when_enabled(tmp_path):     # 왓츠앱 설계 D2 — 적지 않으면 끔
    import run_board
    assert "whatsapp" not in [c.name for c, _ in run_board.collectors(_settings(tmp_path))]
    s = _settings(tmp_path); s.accounts["whatsapp"] = {"enabled": True}
    cs = run_board.collectors(s)
    assert [c.name for c, _ in cs] == ["imessage", "kakao", "whatsapp"] and [net for _, net in cs] == [False, False, False]


def test_kakao_helper_dir_comes_from_the_account_setting(tmp_path, monkeypatch):
    import run_board
    from urgent_board.collectors import kakao
    s = _settings(tmp_path)
    kc = next(c for c, _ in run_board.collectors(s) if c.name == "kakao")
    assert kc.helper == Path.home() / "k-skill" / "scripts" / "kakaotalk_mac.py"          # 적지 않으면 이 맥의 지금 자리 그대로
    s.accounts["kakao"]["helper_dir"] = "   "                                                  # 빈칸뿐이면 적지 않은 것으로 본다
    kc = next(c for c, _ in run_board.collectors(s) if c.name == "kakao")
    assert kc.helper == Path.home() / "k-skill" / "scripts" / "kakaotalk_mac.py"
    s.accounts["kakao"]["helper_dir"] = str(tmp_path / "helperdir")
    kc = next(c for c, _ in run_board.collectors(s) if c.name == "kakao")
    assert kc.helper == tmp_path / "helperdir" / "scripts" / "kakaotalk_mac.py"
    seen = []
    monkeypatch.setattr(kakao, "find_kakaocli", lambda: "/bin/kakaocli")
    monkeypatch.setattr(kakao, "load_auth", lambda cache=kakao.K_SKILL_CACHE, helper=None, **k: seen.append(helper) or None)
    monkeypatch.setattr(kakao, "query", lambda sql, auth, binary, **k: [[1]])
    kc._ensure()
    assert seen == [tmp_path / "helperdir" / "scripts" / "kakaotalk_mac.py"]
