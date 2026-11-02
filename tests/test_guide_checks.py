"""길의 확인기(urgent_board/guide_checks.py — 순수)와 사실 모으기(GET /api/guide/facts). 글은 모두 지어낸 것이다.

확인기 서명: checker(facts, progress, step) -> guide.Verdict. 엔진이 답하지 않으면 hello · install 말고 모두 unknown + ENGINE_DOWN."""
import json
import shutil
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from urgent_board import config, guide, guide_checks as gc, settings_doc as sd
from urgent_board.models import Event, Source
from urgent_board.store import Store
from urgent_board.web import create_app

NOW = datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
RM = guide.load()
OK_PROBE = {"ok": True, "why": "", "reason": ""}
NO_ACCESS = {"ok": False, "why": "권한", "reason": "no_access"}
NO_DATA = {"ok": False, "why": "자료 없음", "reason": "no_data"}
MARKER = "해바라기 27"


def facts(**kw):
    base = {"healthz": 200, "connect": {"messages": OK_PROBE, "calls": OK_PROBE, "teams": OK_PROBE,
                                         "sources": {"imessage": {"state": "ok"}, "calls": {"state": "ok"}, "teams": {"state": "ok"},
                                                     "telegram": {"state": "off"}, "kakao": {"state": "off"}, "whatsapp": {"state": "off"}, "gmail": []}},
            "status": {"steps": [{"id": "me", "done": True}, {"id": "boxes", "done": False}, {"id": "criteria", "done": False},
                                 {"id": "hours", "done": True}], "left": 2, "next": "boxes"},
            "feed_whats": [], "tab5": None, "firmware_public": False}
    base.update(kw)
    return base


def prog(**kw):
    return guide.Progress(roadmap_version=RM.version, **kw)


def run(name, f, p=None, step_id=None):
    step = RM.step(step_id) if step_id else next(s for s in RM.steps if s.check == name)
    return gc.CHECKERS[name](f, p or prog(), step)


def test_checkers_match_known_checks():
    assert set(gc.CHECKERS) == set(guide.KNOWN_CHECKS)


def test_every_note_is_a_known_note():
    # 어느 확인기도 KNOWN_NOTES 밖의 note 를 내지 않는다(갈림길이 걸리는 말)
    probes = [facts(), facts(healthz=0), facts(connect=None), facts(status=None), facts(tab5={"connected": False}),
              facts(connect={"messages": NO_ACCESS, "calls": NO_DATA, "teams": NO_DATA, "sources": {}})]
    for f in probes:
        for s in RM.steps:
            n = run(s.check, f, prog(marker=MARKER), s.id).note
            assert n in ("", guide.ENGINE_DOWN, *guide.KNOWN_NOTES[s.check]), (s.id, n)


# ---- 엔진 꺼짐 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", [n for n in guide.KNOWN_CHECKS if n not in ("hello", "install")])
def test_engine_down_is_unknown_for_everything_but_hello_and_install(name):
    v = run(name, facts(healthz=0))
    assert (v.state, v.note) == ("unknown", guide.ENGINE_DOWN) and "install" in v.say


def test_hello_ignores_the_engine():
    p = guide.confirm(prog(profile=full_profile()), "hello", True, NOW)
    assert run("hello", facts(healthz=0), p).state == "ok"


def test_install():
    assert run("install", facts()).state == "ok"
    v = run("install", facts(healthz=0))
    assert (v.state, v.note) == ("fail", "not_running")


# ---- hello -------------------------------------------------------------------------------------------------------------

def full_profile():
    return {"messengers": ["sms"], "iphone": "yes", "tab5": "none", "ai": "a", "admin": "yes"}


def test_hello_states():
    assert (run("hello", facts()).state, run("hello", facts()).note) == ("human", "no_consent")
    no = guide.confirm(prog(profile=full_profile()), "hello", False, NOW)
    assert (run("hello", facts(), no).state, run("hello", facts(), no).note) == ("fail", "said_no")
    yes_noprofile = guide.confirm(prog(), "hello", True, NOW)
    v = run("hello", facts(), yes_noprofile)
    assert (v.state, v.note) == ("human", "no_profile")
    half = guide.confirm(prog(profile={"messengers": ["sms"]}), "hello", True, NOW)
    assert run("hello", facts(), half).note == "no_profile"
    assert run("hello", facts(), guide.confirm(prog(profile=full_profile()), "hello", True, NOW)).state == "ok"


# ---- 허락 ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,key", [("permit_messages", "messages"), ("permit_calls", "calls"), ("permit_teams", "teams")])
def test_permit_states(name, key):
    def f(probe):
        c = facts()["connect"]
        return facts(connect={**c, key: probe})
    assert run(name, f(OK_PROBE)).state == "ok"
    v = run(name, f(NO_DATA))
    assert (v.state, v.note) == ("unknown", "no_data")
    v = run(name, f(NO_ACCESS))
    assert (v.state, v.note) == ("fail", "no_access")
    assert run(name, facts(connect=None)).note == guide.ENGINE_DOWN


# ---- 잇기 ----------------------------------------------------------------------------------------------------------------

def linked(**sources):
    c = facts()["connect"]
    return facts(connect={**c, "sources": {**c["sources"], **sources}})


def test_link_states_for_a_single_source():
    assert run("link", linked(imessage={"state": "ok"}), step_id="link-sms").state == "ok"
    v = run("link", linked(imessage={"state": "waiting"}), step_id="link-sms")
    assert (v.state, v.note) == ("unknown", "waiting")
    v = run("link", linked(imessage={"state": "stopped"}), step_id="link-sms")
    assert (v.state, v.note) == ("fail", "stopped")
    v = run("link", linked(imessage={"state": "off"}), step_id="link-sms")
    assert (v.state, v.note) == ("fail", "not_linked")
    assert run("link", linked(imessage={"state": "unknown"}), step_id="link-sms").state == "unknown"


def test_zero_past_messages_is_not_a_failure():
    # 수집이 정상이면 지난 글이 0건이어도 됨 — feed_whats 가 비어 있어도 잇기는 ok
    assert run("link", linked(imessage={"state": "ok"}, ), step_id="link-sms").state == "ok"
    assert facts()["feed_whats"] == []


def test_link_gmail_any_ok_wins_and_reconnect_is_named():
    one = lambda *st: linked(gmail=[{"state": s} for s in st])
    assert run("link", one("stopped", "ok"), step_id="link-gmail").state == "ok"
    v = run("link", one("reconnect", "waiting"), step_id="link-gmail")
    assert (v.state, v.note) == ("fail", "reconnect")
    v = run("link", one("waiting"), step_id="link-gmail")
    assert (v.state, v.note) == ("unknown", "waiting")
    v = run("link", one(), step_id="link-gmail")
    assert (v.state, v.note) == ("fail", "not_linked")
    assert run("link", one("stopped"), step_id="link-gmail").note == "stopped"


@pytest.mark.parametrize("sid,src", [("link-sms", "imessage"), ("link-calls", "calls"), ("link-teams", "teams"), ("link-telegram", "telegram")])
def test_link_reads_the_step_source(sid, src):
    assert run("link", linked(**{src: {"state": "reconnect"}}), step_id=sid).note == "reconnect"


# ---- 시험 낱말 ----------------------------------------------------------------------------------------------------------

def test_marker_found_ignoring_spaces_case_and_nfd():
    p = prog(marker=MARKER)
    assert run("try_marker", facts(feed_whats=["안녕 해바라기27 입니다"]), p).state == "ok"
    assert run("try_marker", facts(feed_whats=[unicodedata.normalize("NFD", "해바라기 27 보냄")]), p).state == "ok"
    assert run("try_marker", facts(feed_whats=["SUNFLOWER  7"]), prog(marker="sunflower 7")).state == "ok"
    v = run("try_marker", facts(feed_whats=["다른 글"]), p)
    assert (v.state, v.note) == ("unknown", "not_yet")
    v = run("try_marker", facts(feed_whats=[MARKER]), prog())
    assert (v.state, v.note) == ("unknown", "no_marker")


# ---- 탭5 ----------------------------------------------------------------------------------------------------------------

def tab5(connected=True, board="live", bseq=10, sent_seq=10, orient=2, age_s=5):
    return {"connected": connected, "sent_seq": sent_seq, "orient": orient, "age_s": age_s, "tab5": {"board": board, "bseq": bseq}}


def test_tab5_flash_and_show():
    assert run("tab5_flash", facts(tab5=None)).note == "no_tab5"
    assert run("tab5_flash", facts(tab5=tab5(connected=False))).note == "no_tab5"
    assert run("tab5_flash", facts(tab5=tab5())).state == "ok"
    v = run("tab5_flash", facts(tab5={"connected": True, "sent_seq": 0, "orient": None, "age_s": 5, "tab5": None}))
    assert v.note == "not_flashed" and v.state != "ok"
    assert run("tab5_show", facts(tab5=tab5())).state == "ok"
    for b in ("lost", "sleep", "none", "off"):
        v = run("tab5_show", facts(tab5=tab5(board=b)))
        assert (v.state, v.note) == ("unknown", "not_live")
    assert run("tab5_show", facts(tab5=tab5(connected=False))).note == "no_tab5"
    assert run("tab5_show", facts(tab5=None)).note == "no_tab5"


def seen(p, f, now=NOW):
    return gc.remember_seq(f, p, "try-tab5", now)


def test_try_tab5_waits_for_a_frame_drawn_after_the_marker():
    p = prog(marker=MARKER)
    f0 = facts(feed_whats=[MARKER], tab5=tab5(bseq=10, sent_seq=10))
    assert run("try_tab5", f0, p).note == "not_drawn"                         # 아직 seq 를 기록하지 않았다
    p = seen(p, f0)
    assert p.steps["try-tab5"]["marker_seq"] == 10
    assert run("try_tab5", f0, p).note == "not_drawn"                         # 표식 뒤 새 그림이 아직 안 갔다 · 안 그려졌다
    f1 = facts(feed_whats=[MARKER], tab5=tab5(bseq=10, sent_seq=11))
    assert run("try_tab5", f1, p).note == "not_drawn"                         # 보냈지만 탭5 가 아직 못 따라왔다
    f2 = facts(feed_whats=[MARKER], tab5=tab5(bseq=11, sent_seq=11))
    v = run("try_tab5", f2, p)
    assert (v.state, v.note) == ("human", "")
    assert seen(p, f2).steps["try-tab5"]["marker_seq"] == 10                  # 한 번 기록하면 바뀌지 않는다


def test_remember_seq_needs_the_marker_and_resets_for_a_new_one():
    p = prog(marker=MARKER)
    assert "try-tab5" not in seen(p, facts(feed_whats=["다른 글"], tab5=tab5())).steps
    assert "try-tab5" not in seen(p, facts(feed_whats=[MARKER], tab5=None)).steps
    p1 = seen(p, facts(feed_whats=[MARKER], tab5=tab5(sent_seq=4)))
    p2 = guide.Progress(**{**p1.__dict__, "marker": "달맞이꽃 31"})
    p3 = seen(p2, facts(feed_whats=["달맞이꽃 31"], tab5=tab5(sent_seq=9)))
    assert p3.steps["try-tab5"]["marker_seq"] == 9
    assert run("try_tab5", facts(feed_whats=["달맞이꽃 31"], tab5=tab5(bseq=9, sent_seq=9)), p2).note == "not_drawn"   # 옛 seq 는 새 낱말에 쓰지 않는다


def human_ready(answer=None):
    p = seen(prog(marker=MARKER), facts(feed_whats=[MARKER], tab5=tab5(bseq=10, sent_seq=10)))
    if answer is not None:
        p = guide.confirm(p, "try-tab5", answer, NOW)
    return p


F_OK = facts(feed_whats=[MARKER], tab5=tab5(bseq=12, sent_seq=12))


def test_try_tab5_human_answers():
    assert run("try_tab5", F_OK, human_ready()).state == "human"
    assert run("try_tab5", F_OK, human_ready(True)).state == "ok"
    v = run("try_tab5", F_OK, human_ready(False))
    assert (v.state, v.note) == ("fail", "said_no")


@pytest.mark.parametrize("bad", [
    facts(feed_whats=[MARKER], tab5=None),
    facts(feed_whats=[MARKER], tab5=tab5(connected=False, bseq=12, sent_seq=12)),
    facts(feed_whats=[MARKER], tab5=tab5(board="lost", bseq=12, sent_seq=12)),
    facts(feed_whats=[MARKER], tab5=tab5(bseq=10, sent_seq=12)),
    facts(feed_whats=["다른 글"], tab5=tab5(bseq=12, sent_seq=12)),
])
def test_human_yes_cannot_rescue_a_failed_machine_part(bad):
    assert run("try_tab5", bad, human_ready(True)).state != "ok"
    assert run("try_tab5", bad, human_ready(False)).state != "ok"


# ---- 1판 설정 단계 -----------------------------------------------------------------------------------------------------

def test_setup_step_reads_the_first_edition_steps():
    assert run("setup_step", facts(), step_id="me").state == "ok"
    v = run("setup_step", facts(), step_id="boxes")
    assert (v.state, v.note) == ("fail", "left")
    assert run("setup_step", facts(), step_id="extras").state == "ok"          # params step: hours
    assert run("setup_step", facts(status=None), step_id="me").note == guide.ENGINE_DOWN
    odd = facts(status={"steps": [], "left": 0, "next": None})
    assert run("setup_step", odd, step_id="me").state == "unknown"


# ---- 사실 모으기 -------------------------------------------------------------------------------------------------------

SECRET_API = "API-HASH-d41d8cd98f00b204e9800998ecf8427e"
SECRET_CLIENT = "GOOGLE-CLIENT-SECRET-Zx81QpLm"
SUBJECT = "해바라기 27 그리고 비밀스러운 뒷부분 제목 TAILTAIL-9f31"
BODY = "본문에만 있는 BODYONLY-77aa 내용"


@pytest.fixture(autouse=True)
def no_real_probes(monkeypatch):
    """이 맥의 메시지 · 통화 · 알림 자료(개인 정보)를 시험이 열지 않게 — 점검은 지어낸 값으로."""
    from urgent_board.connect import probes
    for n in ("messages_access", "calls_access", "teams_access", "contacts_access"):
        monkeypatch.setattr(probes, n, lambda *a, **k: {"ok": True, "why": ""})


def app(tmp_path, token=None, now=NOW):
    d = sd.example()
    sd.save(tmp_path, d, None)
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    st = Store(tmp_path / "t.db")
    return create_app(live, st, clock=lambda: now, token=token).test_client(), st


def test_facts_api_carries_no_secrets_or_message_text(tmp_path):
    # 저장소는 '지난 7일'을 진짜 시계로 센다(Store.recent_events) — 고정 시각(NOW)의 글은 그 7일이 지나면 빠진다(2026-11-02 합칠 때 찾음).
    # 그래서 이 시험만 오늘 시각으로 돈다.
    NOW = datetime.now(timezone.utc).replace(microsecond=0)
    c, st = app(tmp_path, now=NOW)
    auth = tmp_path / "auth"
    auth.mkdir(exist_ok=True)
    (auth / "telegram.json").write_text(json.dumps({"api_id": 1234567, "api_hash": SECRET_API}), encoding="utf-8")
    (auth / "google-client.json").write_text(json.dumps({"installed": {"client_secret": SECRET_CLIENT}}), encoding="utf-8")
    (auth / "telethon.session").write_text("SESSION-STRING-ABC", encoding="utf-8")
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "tab5-link.json").write_text(json.dumps(
        {"connected": True, "port": "/dev/cu.SECRETPORT", "last_error": "ERR-SECRET-TEXT", "sent_seq": 7, "orient": 2,
         "updated_at": NOW.timestamp() - 12, "tab5": {"board": "live", "bseq": 7, "scr": 1, "bage": 2, "temp": 31.0}}), encoding="utf-8")
    st.upsert_events([Event(source=Source.GMAIL, chat_id="t1", message_id="m1", chat_name=SUBJECT, sender_id="kim@secretdomain.example",
                            sender_name="비밀이름", text=SUBJECT + " — " + BODY, received_at=NOW - timedelta(minutes=30), is_from_me=False,
                            is_read=False, is_group=False, account="me@secretdomain.example", raw={"subject": SUBJECT})])
    r = c.get("/api/guide/facts")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    for s in (SECRET_API, SECRET_CLIENT, "SESSION-STRING-ABC", "1234567", "TAILTAIL-9f31", "BODYONLY", "secretdomain", "비밀이름",
              "SECRETPORT", "ERR-SECRET-TEXT", "비밀스러운"):
        assert s not in body, s
    j = r.get_json()
    assert j["feed_whats"] == [SUBJECT[:20]] and len(j["feed_whats"][0]) <= 20 and MARKER in j["feed_whats"][0]
    assert j["connect"]["telegram"] == {"api": True, "session": True} and j["connect"]["google_client"] is True
    assert set(j["connect"]["messages"]) == {"ok", "why", "reason"}
    assert j["tab5"] == {"connected": True, "sent_seq": 7, "orient": 2, "age_s": 12.0, "tab5": {"board": "live", "bseq": 7}}
    assert {s["id"] for s in j["status"]["steps"]} >= {"me", "boxes", "criteria", "hours"}
    assert set(j) == {"connect", "status", "feed_whats", "tab5"}


def test_facts_api_needs_the_token_and_survives_missing_files(tmp_path):
    c, _ = app(tmp_path, token="T" * 40)
    assert c.get("/api/guide/facts").status_code == 401
    j = c.get("/api/guide/facts", headers={"X-UB-Token": "T" * 40}).get_json()
    assert j["tab5"] is None and j["feed_whats"] == [] and j["connect"]["telegram"] == {"api": False, "session": False}


def test_marker_seq_survives_a_progress_file_round_trip(tmp_path):
    p = seen(prog(marker=MARKER), facts(feed_whats=[MARKER], tab5=tab5(sent_seq=5)))
    p = guide.confirm(p, "try-tab5", True, NOW)
    f = tmp_path / "guide.json"
    guide.write_progress(f, p)
    q = guide.read_progress(f, RM.version)
    assert q.steps["try-tab5"]["marker_seq"] == 5 and q.steps["try-tab5"]["confirmed"] is True


# ---- 검토 고침 ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("age", [None, 91, 600, -1])
def test_stale_or_ageless_tab5_state_is_not_connected(age):
    f = facts(feed_whats=[MARKER], tab5=tab5(age_s=age))
    assert run("tab5_show", f).note == "no_tab5" and run("tab5_flash", f).note == "no_tab5"
    p = human_ready(True)
    assert run("try_tab5", facts(feed_whats=[MARKER], tab5=tab5(bseq=12, sent_seq=12, age_s=age)), p).state != "ok"
    assert run("tab5_show", facts(tab5=tab5(age_s=90))).state == "ok"                    # 90초는 아직 싱싱하다


def test_facts_api_reports_the_age_of_the_tab5_file(tmp_path):
    c, _ = app(tmp_path)
    (tmp_path / "state").mkdir(exist_ok=True)
    f = tmp_path / "state" / "tab5-link.json"
    f.write_text(json.dumps({"connected": True, "sent_seq": 1, "tab5": {"board": "live", "bseq": 1}}), encoding="utf-8")
    assert c.get("/api/guide/facts").get_json()["tab5"]["age_s"] is None                 # updated_at 가 없으면 모른다
    f.write_text(json.dumps({"connected": True, "sent_seq": 1, "updated_at": NOW.timestamp() - 300, "tab5": {"board": "live", "bseq": 1}}), encoding="utf-8")
    assert c.get("/api/guide/facts").get_json()["tab5"]["age_s"] == 300.0


def test_confirmation_from_before_this_pass_is_ignored():
    # (a) 같은 낱말을 새 시도에 다시 써도(번호를 다시 남김) 옛 '예'로 통과하지 않는다
    p = human_ready(True)
    assert run("try_tab5", F_OK, p).state == "ok"
    later = NOW + timedelta(minutes=5)
    p2 = gc.remember_seq(facts(feed_whats=[MARKER], tab5=tab5(bseq=1, sent_seq=2)), p, "try-tab5", later)   # 창을 다시 띄워 번호가 되감김
    assert "confirmed" not in p2.steps["try-tab5"]
    p3 = guide.Progress(**{**p.__dict__, "steps": {"try-tab5": {**p.steps["try-tab5"], "marker_at": later.isoformat()}}})
    assert run("try_tab5", F_OK, p3).state == "human"                                  # confirmed 가 남아 있어도 marker_at 앞이면 무효


def test_confirmation_is_cleared_when_the_machine_part_fails_then_recovers():
    # (b)
    p = human_ready(True)
    bad = facts(feed_whats=[MARKER], tab5=tab5(board="lost", bseq=12, sent_seq=12))
    p = gc.remember_seq(bad, p, "try-tab5", NOW)
    assert "confirmed" not in p.steps["try-tab5"]
    assert run("try_tab5", F_OK, p).state == "human"


def test_normal_yes_after_a_pass_is_ok_and_remember_seq_keeps_it():
    # (c)
    p = human_ready(True)
    assert gc.remember_seq(F_OK, p, "try-tab5", NOW) is p
    assert run("try_tab5", F_OK, p).state == "ok"


def test_seq_rewind_rerecords_instead_of_blocking_forever():
    p = seen(prog(marker=MARKER), facts(feed_whats=[MARKER], tab5=tab5(sent_seq=500, bseq=500)))
    assert p.steps["try-tab5"]["marker_seq"] == 500
    restarted = facts(feed_whats=[MARKER], tab5=tab5(sent_seq=3, bseq=3))
    p = gc.remember_seq(restarted, p, "try-tab5", NOW)
    assert p.steps["try-tab5"]["marker_seq"] == 3
    assert run("try_tab5", facts(feed_whats=[MARKER], tab5=tab5(sent_seq=4, bseq=4)), p).state == "human"


@pytest.mark.parametrize("probe,reason", [({"ok": True, "why": ""}, ""), ({"ok": False, "why": "자료가 없어요(x)."}, "no_data")])
def test_facts_api_probe_reason_mapping(tmp_path, monkeypatch, probe, reason):
    from urgent_board.connect import probes
    monkeypatch.setattr(probes, "messages_access", lambda *a, **k: probe)
    c, _ = app(tmp_path)
    assert c.get("/api/guide/facts").get_json()["connect"]["messages"] == {"ok": probe["ok"], "why": probe["why"], "reason": reason}


def test_facts_api_no_access_reason(tmp_path, monkeypatch):
    from urgent_board.connect import probes
    monkeypatch.setattr(probes, "calls_access", lambda *a, **k: {"ok": False, "why": probes.NO_ACCESS})
    c, _ = app(tmp_path)
    assert c.get("/api/guide/facts").get_json()["connect"]["calls"]["reason"] == "no_access"
