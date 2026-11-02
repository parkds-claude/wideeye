"""길 명령(tools/guide_cli.py — `wideeye guide …`). 엔진은 실제 Flask 앱을 네트워크 없이 붙이고(test_setup_cli 의 Engine),
이 맥의 메시지 · 통화 · 알림 자료는 열지 않는다(점검은 지어낸 값). 자료 폴더는 임시 폴더. 글은 모두 지어낸 것이다."""
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_setup_cli import PORT, TOKEN, Engine
from tools import guide_cli as gc, ub
from urgent_board import guide, paths, settings_doc as sd
from urgent_board.models import Event, Source

ROOT = Path(__file__).resolve().parents[1]
RM = guide.load()
MARKER = "해바라기 27"
FULL_PROFILE = {"messengers": ["sms"], "iphone": "yes", "tab5": "none", "ai": "a", "admin": "yes"}
OK_PROBE = {"ok": True, "why": ""}
PROBE = {}


@pytest.fixture(autouse=True)
def fake_probes(monkeypatch):
    """이 맥의 메시지 · 통화 · 알림 자료(개인 정보)를 시험이 열지 않게 — 점검은 PROBE 에 적은 값(없으면 됨)."""
    from urgent_board.connect import probes
    PROBE.clear()
    for n in ("messages_access", "calls_access", "teams_access", "contacts_access"):
        monkeypatch.setattr(probes, n, lambda *a, _n=n, **k: dict(PROBE.get(_n, OK_PROBE)))
    monkeypatch.setenv(ub.NO_OPEN, "1")


def make(tmp_path, imessage=True):
    home = tmp_path / "home"
    paths.ensure(home)
    d = sd.example()
    d["server"] = {"port": PORT}
    if imessage:
        d["accounts"]["imessage"] = {"enabled": True}
    sd.save(home, d, None)
    (paths.auth_dir(home) / "api-token").write_text(TOKEN + "\n")
    eng = Engine(home)
    out = []
    ctx = ub.Ctx(home=home, opener=eng.open, say=lambda s="": out.append(str(s)), sleep=lambda s: None,
                 run=lambda cmd, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    return ctx, eng, out


def run(ctx, out, *argv):
    out.clear()
    return gc.main(list(argv), ctx), "\n".join(out)


def machine(text: str) -> dict:
    last = text.splitlines()[-1]
    assert last.startswith("기계: "), last
    return json.loads(last[len("기계: "):])


def save(ctx, **kw):
    p = guide.Progress(roadmap_version=RM.version, **kw)
    guide.write_progress(guide.progress_file(ctx.home), p)
    return p


def load(ctx):
    return guide.read_progress(guide.progress_file(ctx.home), RM.version)


def ok(*ids):
    return {i: {"state": "ok", "at": "2026-10-18T10:00:00+09:00", "note": ""} for i in ids}


def consent():
    return {"names": True, "at": "2026-10-18T10:00:00+09:00"}


def tab5_file(ctx, seq, board="live", connected=True, age=3):
    (paths.state_dir(ctx.home) / "tab5-link.json").write_text(json.dumps(
        {"connected": connected, "sent_seq": seq, "orient": 2, "updated_at": time.time() - age, "port": "/dev/cu.SECRETPORT",
         "last_error": "ERR-SECRET-TEXT", "tab5": {"board": board, "bseq": seq}}), encoding="utf-8")


def mail(eng, subject, body="", sender="김", addr="kim@example.org"):
    eng.store.upsert_events([Event(source=Source.GMAIL, chat_id="t1", message_id="m1", chat_name=subject, sender_id=addr,
                                   sender_name=sender, text=subject + (" — " + body if body else ""),
                                   received_at=datetime.now(timezone.utc) - timedelta(minutes=5), is_from_me=False, is_read=False,
                                   is_group=False, account="me@example.org", raw={"subject": subject})])


def imessage_ok(eng):
    eng.store.set_source_status("imessage", True, "3건", datetime.now(timezone.utc))


# ---- next ----------------------------------------------------------------------------------------------------------------

def test_next_on_a_fresh_mac_starts_with_hello(tmp_path):
    ctx, eng, out = make(tmp_path)
    rc, text = run(ctx, out, "next")
    assert rc == 0
    first = text.splitlines()[0]
    assert re.match(r"^\[단계 1/\d+\] 시작 — ", first), first
    assert "\n왜: " in text and "\n할 일:\n 1. " in text and "\n 2. " in text
    assert "메시지 본문은 기본으로 가지 않아요" in text                    # tell — AI 가 먼저 그대로 전할 것(작업 7 검토 5 의 문장)
    assert "이것을 알고 시작할까요?" in text                             # 사람에게 물을 한 문장
    assert f"{gc.CMD} guide confirm hello yes" in text
    assert f"확인: {gc.CMD} guide check hello" in text
    assert f"{gc.CMD} guide profile messengers=" in text                 # run 은 전체 경로로
    m = machine(text)
    assert m["step"] == "hello" and m["needs_person"] is True and m["for_chat"] is False
    assert m["check"] == f"{gc.CMD} guide check hello"
    assert gc.DONE_SAY not in text
    eng.down = "refused"                                                 # 엔진이 꺼져 있어도 시작 단계부터
    rc, text = run(ctx, out, "next")
    assert rc == 0 and machine(text)["step"] == "hello"
    j = json.loads(run(ctx, out, "next", "--json")[1])
    assert j["step"] == "hello" and j["n"] == 1 and j["do"] and j["check"].endswith("guide check hello")


def test_next_rechecks_and_steps_back_when_permission_is_lost(tmp_path):
    ctx, eng, out = make(tmp_path)
    imessage_ok(eng)
    done = ("hello", "install", "permit-messages", "link-sms", "try")
    save(ctx, profile=FULL_PROFILE, consent=consent(), marker=MARKER, steps=ok(*done))
    rc, text = run(ctx, out, "next")
    assert rc == 0 and machine(text)["step"] == "me"                    # 앞 단계가 다시 확인해도 됨 — '나'가 다음
    PROBE["messages_access"] = {"ok": False, "why": "권한"}             # 허락은 아니고 자료가 없다고만(확인 불가) — ok 를 지킨다
    rc, text = run(ctx, out, "next")
    assert machine(text)["step"] == "me" and load(ctx).steps["permit-messages"]["state"] == "ok"
    from urgent_board.connect import probes
    PROBE["messages_access"] = {"ok": False, "why": probes.NO_ACCESS}   # 허락이 풀렸다
    rc, text = run(ctx, out, "next")
    assert rc == 0 and machine(text)["step"] == "permit-messages"
    assert "맥이 문자를 읽게 허락하기" in text and "다시 확인했더니" in text
    p = load(ctx)
    assert p.steps["permit-messages"]["state"] == "fail" and p.steps["permit-messages"]["note"] == "no_access"
    assert "link-sms" not in p.steps and "try" not in p.steps            # 그에 기대는 뒤 단계는 지웠다
    assert p.steps["hello"]["state"] == "ok" and p.steps["install"]["state"] == "ok" and p.marker == MARKER
    assert "아직 허락이 안 됐어요" in text                                  # 지난 판정의 갈림길


def test_engine_down_steps_back_to_install_without_erasing(tmp_path):
    """결정 D2 — 엔진이 다시 켜지는 몇 초 사이의 next 가 첫 알림 시험까지 지우지 않는다."""
    ctx, eng, out = make(tmp_path)
    save(ctx, profile=FULL_PROFILE, consent=consent(), marker=MARKER, steps=ok("hello", "install", "permit-messages", "link-sms", "try"))
    eng.down = "refused"
    rc, text = run(ctx, out, "next")
    assert rc == 0 and machine(text)["step"] == "install"
    p = load(ctx)
    assert p.steps["install"]["state"] == "fail"
    assert all(p.steps[i]["state"] == "ok" for i in ("permit-messages", "link-sms", "try"))
    eng.down = None
    imessage_ok(eng)
    assert run(ctx, out, "check", "install")[0] == 0
    assert machine(run(ctx, out, "next")[1])["step"] == "me"           # 다시 켜지면 그 자리부터


def test_for_chat_output_is_one_copyable_line_per_step(tmp_path):
    ctx, eng, out = make(tmp_path)
    save(ctx, profile=FULL_PROFILE, consent=consent(), steps=ok("hello", "install"))
    rc, text = run(ctx, out, "next", "--for-chat")
    assert rc == 0 and machine(text)["step"] == "permit-messages" and machine(text)["for_chat"] is True
    lines = text.splitlines()
    blocks, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "```":
            end = next(j for j in range(i + 1, len(lines)) if lines[j].strip() == "```")
            assert gc.CHAT in lines[i - 1], lines[i - 1]                # 블록마다 바로 앞에 그 한 문장
            blocks.append(lines[i + 1:end])
            i = end + 1
        else:
            i += 1
    assert len(blocks) >= 3                                             # 허락 화면 열기 · 다시 켜기 · 확인
    for b in blocks:
        assert len(b) == 1 and b[0].strip().startswith(gc.CMD), b       # 한 블록에 한 줄
    commands = [b[0].strip() for b in blocks]
    assert f"{gc.CMD} setup permissions" in commands and f"{gc.CMD} restart engine" in commands
    assert f"{gc.CMD} guide check permit-messages" in commands
    outside = [ln for ln in lines[:-1] if gc.CMD in ln and ln.strip() not in commands]
    assert outside == []                                                # 명령은 모두 블록 안에(마지막 기계 줄만 빼고)


# ---- check · confirm -----------------------------------------------------------------------------------------------------

def test_check_exit_codes_match_verdicts(tmp_path):
    ctx, eng, out = make(tmp_path)
    rc, text = run(ctx, out, "check", "hello")                          # 사람 확인 차례 — 물을 문장을 낸다
    assert rc == 0 and "이것을 알고 시작할까요?" in text and machine(text)["state"] == "human" and machine(text)["needs_person"] is True
    save(ctx, profile=FULL_PROFILE, consent=consent(), steps=ok("hello"))
    rc, text = run(ctx, out, "check", "install")
    assert rc == 0 and "됨" in text and f"다음: {gc.CMD} guide next" in text
    rc, text = run(ctx, out, "check", "permit-messages")
    assert rc == 0 and load(ctx).steps["permit-messages"]["state"] == "ok"
    rc, text = run(ctx, out, "check", "link-sms")                       # 아직 첫 수집 전
    assert rc == 3 and "첫 문자를 읽는 중이에요" in text and machine(text)["note"] == "waiting"
    from urgent_board.connect import probes
    PROBE["messages_access"] = {"ok": False, "why": probes.NO_ACCESS}
    rc, text = run(ctx, out, "check", "permit-messages")
    assert rc == 1 and "아직 허락이 안 됐어요" in text
    assert f"{gc.CMD} restart engine" in text                           # 허락 뒤에 다시 켜기를 했는지
    eng.down = "refused"
    rc, text = run(ctx, out, "check", "install")
    assert rc == 1 and "WideEye 가 아직 돌지 않아요" in text and f"{gc.CMD} start" in text
    rc, text = run(ctx, out, "check", "permit-messages")                # 엔진이 꺼져 있으면 확인 불가 — 죽지 않는다
    assert rc == 3 and "답하지 않아요" in text and f"{gc.CMD} guide check install" in text
    rc, text = run(ctx, out, "check", "nope")
    assert rc == 2 and "hello" in text and "try-tab5" in text
    rc, text = run(ctx, out, "check", "link-gmail")                     # 이 사람은 Gmail 을 안 쓴다
    assert rc == 0 and "건너뛰" in text
    eng.down = None
    rc, text = run(ctx, out, "check", "try")                            # 잇기가 먼저
    assert rc == 3 and "앞 단계가 먼저예요" in text                     # D5 — 돌려 보되 차례를 알린다


def test_confirm_records_and_rechecks(tmp_path):
    ctx, eng, out = make(tmp_path)
    rc, text = run(ctx, out, "confirm", "hello", "yes")
    assert rc == 0 and machine(text)["state"] == "human" and machine(text)["note"] == "no_profile"
    assert "messengers" in text and f"{gc.CMD} guide profile" in text
    p = load(ctx)
    assert p.consent["names"] is True and p.steps["hello"]["confirmed"] is True and p.steps["hello"]["state"] == "human"
    rc, text = run(ctx, out, "profile", "messengers=sms,calls", "iphone=yes", "tab5=none", "ai=a", "admin=yes")
    assert rc == 0
    rc, text = run(ctx, out, "check", "hello")
    assert rc == 0 and load(ctx).steps["hello"]["state"] == "ok"
    rc, text = run(ctx, out, "confirm", "hello", "no")                  # 마음을 바꾸면 그 답으로 다시 판정
    assert rc == 1 and "설정 화면에서 직접" in text and f"{gc.CMD} open settings" in text
    assert load(ctx).consent["names"] is False
    rc, text = run(ctx, out, "confirm", "install", "yes")               # 사람 확인이 없는 단계
    assert rc == 2 and "guide check install" in text
    rc, text = run(ctx, out, "confirm", "hello", "maybe")
    assert rc == 2


@pytest.fixture
def firmware_public(monkeypatch):
    """탭5 프로그램이 공개된 뒤의 길 — 탭5 단계(잇기 · 첫 알림)가 길에 들어온다(1부에서는 '준비 중'으로 건너뛴다 — 마지막 검토 I1)."""
    monkeypatch.setitem(guide.FACTS, "firmware_public", True)


def test_try_tab5_remembers_the_picture_number_and_takes_the_answer_only_after_it(tmp_path, firmware_public):
    ctx, eng, out = make(tmp_path)
    prof = {**FULL_PROFILE, "tab5": "plugged"}
    save(ctx, profile=prof, consent=consent(), marker=MARKER, steps=ok("hello", "install", "permit-messages", "link-sms", "try", "tab5-show"))
    mail(eng, f"{MARKER} 시험이에요")
    tab5_file(ctx, 7)
    rc, text = run(ctx, out, "confirm", "try-tab5", "yes")              # 그림 번호를 처음 남긴 때 — 아직 새 그림 전이라 답을 적지 않는다
    assert rc == 3 and "답은 적지 않았어요" in text
    e = load(ctx).steps["try-tab5"]
    assert e["marker_seq"] == 7 and "confirmed" not in e
    tab5_file(ctx, 8)                                                    # 그 뒤 새 그림을 탭5 가 그렸다
    rc, text = run(ctx, out, "check", "try-tab5")
    assert rc == 0 and machine(text)["state"] == "human" and f"탭5 화면에 '{MARKER}' 가 보이나요?" in text
    rc, text = run(ctx, out, "confirm", "try-tab5", "yes")
    assert rc == 0 and load(ctx).steps["try-tab5"]["state"] == "ok"


# ---- profile · status · marker -------------------------------------------------------------------------------------------

def test_profile_rejects_unknown_keys_and_values(tmp_path):
    ctx, eng, out = make(tmp_path)
    f = guide.progress_file(ctx.home)
    for argv in (["foo=bar"], ["tab5=maybe"], ["messengers=sms,fax"], ["iphone"], ["iphone=yes", "admin=perhaps"],
                 ["messengers=<고른것_쉼표로>"]):
        rc, text = run(ctx, out, "profile", *argv)
        assert rc == 2, argv
        assert text.startswith("거절: ") and not f.exists(), (argv, text)
    rc, text = run(ctx, out, "profile", "foo=bar")
    assert "messengers" in text and "admin" in text                     # 받는 키를 알려 준다
    rc, text = run(ctx, out, "profile", "messengers=SMS, gmail,sms", "tab5=Later")
    assert rc == 0 and load(ctx).profile == {"messengers": ["sms", "gmail"], "tab5": "later"}
    rc, text = run(ctx, out, "profile")                                 # 인자 없이 — 지금 프로필과 적는 법
    assert rc == 0 and "messengers=sms,gmail" in text and "tab5=later" in text and "iphone" in text


def test_status_says_every_step_and_why_it_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setitem(guide.FACTS, "firmware_public", False)          # 공개 전(1부)의 건너뛰는 까닭이 보이는지
    ctx, eng, out = make(tmp_path)
    save(ctx, profile={**FULL_PROFILE, "tab5": "plugged"}, consent=consent(), steps=ok("hello", "install"))
    rc, text = run(ctx, out, "status")
    assert rc == 0
    for s in RM.steps:
        assert f" {s.id} · " in text, s.id
    assert "탭5 에 넣을 프로그램은 아직 준비 중이에요" in text            # 건너뛰는 까닭
    assert re.search(r"✓ hello · .*됨", text) and re.search(r"permit-messages · .*← 지금", text)
    j = json.loads(run(ctx, out, "status", "--json")[1])
    assert j["next"] == "permit-messages" and j["finished"] is False
    assert {r["id"]: r["state"] for r in j["steps"]}["link-gmail"] == "skipped"
    eng.down = "refused"
    assert run(ctx, out, "status")[0] == 0                              # 엔진에 묻지 않는다


def test_marker_is_a_short_word_sent_first_by_someone_else(tmp_path):
    ctx, eng, out = make(tmp_path)
    rc, text = run(ctx, out, "marker")
    assert rc == 0
    m = load(ctx).marker
    assert m and len(m) <= 10 and re.fullmatch(r"[가-힣]{2,7} [1-9]\d", m), m
    assert m in text and "맨 앞" in text and ("가족" in text or "친구" in text) and "내가 나에게 보낸 글" in text
    assert f"{gc.CMD} guide check try" in text
    assert run(ctx, out, "marker")[0] == 0 and load(ctx).marker == m    # 다시 불러도 같은 낱말
    for _ in range(200):
        w = gc.make_marker()
        assert len(w) <= gc.MARKER_MAX and re.fullmatch(r"[가-힣]+ [1-9]\d", w)


def test_try_step_shows_the_marker_and_the_check_finds_it(tmp_path):
    ctx, eng, out = make(tmp_path)
    imessage_ok(eng)
    save(ctx, profile=FULL_PROFILE, consent=consent(), marker=MARKER, steps=ok("hello", "install", "permit-messages", "link-sms"))
    rc, text = run(ctx, out, "next")
    assert machine(text)["step"] == "try" and f"시험 낱말: {MARKER}" in text
    rc, text = run(ctx, out, "check", "try")
    assert rc == 3 and machine(text)["note"] == "not_yet"
    mail(eng, f"{MARKER} 시험이에요")
    rc, text = run(ctx, out, "check", "try")
    assert rc == 0 and load(ctx).steps["try"]["state"] == "ok"


# ---- 끝 · 막힘 ------------------------------------------------------------------------------------------------------------

def _facts(**kw):
    base = {"healthz": 200, "firmware_public": False,
            "connect": {"messages": {"ok": True, "reason": ""}, "calls": {"ok": True, "reason": ""}, "teams": {"ok": True, "reason": ""},
                        "sources": {"imessage": {"state": "ok"}, "calls": {"state": "ok"}, "teams": {"state": "ok"},
                                    "telegram": {"state": "off"}, "kakao": {"state": "off"}, "gmail": []}},
            "status": {"steps": [{"id": i, "done": True} for i in ("me", "boxes", "criteria", "hours")], "left": 0, "next": None},
            "feed_whats": [], "tab5": None}
    base.update(kw)
    return base


def test_a_blocked_path_is_never_called_done(tmp_path, monkeypatch):
    ctx, eng, out = make(tmp_path)
    monkeypatch.setattr(gc, "gather", lambda ctx: (_facts(), [], True))
    prof = {**FULL_PROFILE, "messengers": ["calls", "other"]}           # 글이 오는 메신저를 하나도 잇지 않았다
    save(ctx, profile=prof, consent=consent(), steps=ok("hello", "install", "permit-calls", "link-calls"))
    rc, text = run(ctx, out, "next")
    assert rc == 1 and gc.DONE_SAY not in text and "끝" not in text.splitlines()[0]
    assert "시험 글을 띄울 수 없어요" in text and f"{gc.CMD} guide profile" in text
    assert "messengers=calls,other" in text                             # 지금 프로필 — 무엇을 고칠지
    m = machine(text)
    assert m["step"] == "try" and m["blocked"] == guide.UNREACHABLE and m["done"] is False


def test_all_steps_done_says_done(tmp_path, monkeypatch):
    ctx, eng, out = make(tmp_path)
    monkeypatch.setattr(gc, "gather", lambda ctx: (_facts(feed_whats=[MARKER]), [], True))
    save(ctx, profile=FULL_PROFILE, consent=consent(), marker=MARKER,
         steps=ok("hello", "install", "permit-messages", "link-sms", "try", "me", "boxes", "criteria", "extras"))
    rc, text = run(ctx, out, "next")
    assert rc == 0 and gc.DONE_SAY in text and machine(text)["done"] is True and machine(text)["step"] is None


# ---- 깨진 진행 기록 · 모르는 단계 ------------------------------------------------------------------------------------------

def test_an_unreadable_progress_file_does_not_crash(tmp_path, monkeypatch):
    ctx, eng, out = make(tmp_path)

    def boom(*a, **k):
        raise PermissionError(13, "denied")
    monkeypatch.setattr(guide, "read_progress", boom)
    rc, text = run(ctx, out, "next")
    assert rc == 0 and "진행 기록을 읽지 못했어요" in text and machine(text)["step"] == "hello"
    rc, text = run(ctx, out, "status")
    assert rc == 0 and "진행 기록을 읽지 못했어요" in text
    for argv in (["profile", "tab5=none"], ["marker"], ["confirm", "hello", "yes"]):
        rc, text = run(ctx, out, *argv)
        assert rc == 1 and "진행 기록을 읽지 못했어요" in text, argv
    assert not guide.progress_file(ctx.home).exists()                   # 읽지 못한 기록을 덮어쓰지 않는다


def test_guide_commands_match_the_roadmap_list():
    assert set(gc.parser()._subparsers._group_actions[0].choices) == set(guide.GUIDE_COMMANDS)


# ---- 비밀 · 메시지 내용 --------------------------------------------------------------------------------------------------

SECRET_API = "API-HASH-SECRET-8c1f2e"
SECRET_CLIENT = "GOOGLE-CLIENT-SECRET-Zx81QpLm"
SUBJECT = MARKER + " 그리고비밀스러운 제목 TAILTAIL-9f31"
BODY = "본문에만 있는 BODYONLY-77aa 내용"


def test_guide_output_never_contains_message_text_or_secrets(tmp_path):
    ctx, eng, out = make(tmp_path)
    auth = paths.auth_dir(ctx.home)
    (auth / "telegram.json").write_text(json.dumps({"api_id": 1234567, "api_hash": SECRET_API}), encoding="utf-8")
    (auth / "google-client.json").write_text(json.dumps({"installed": {"client_secret": SECRET_CLIENT}}), encoding="utf-8")
    (auth / "telethon.session").write_text("SESSION-STRING-ABC", encoding="utf-8")
    mail(eng, SUBJECT, BODY, sender="비밀이름", addr="kim@secretdomain.example")
    imessage_ok(eng)
    tab5_file(ctx, 5)
    texts = []
    for stage in (dict(), dict(profile=FULL_PROFILE, consent=consent(), marker=MARKER, steps=ok("hello", "install", "permit-messages", "link-sms")),
                  dict(profile={**FULL_PROFILE, "tab5": "plugged"}, consent=consent(), marker=MARKER,
                       steps=ok("hello", "install", "permit-messages", "link-sms", "try", "tab5-show"))):
        save(ctx, **stage)
        for argv in (["next"], ["next", "--for-chat"], ["next", "--json"], ["status"], ["status", "--json"], ["profile"], ["marker"],
                     *[["check", s.id] for s in RM.steps], *[["check", s.id, "--json"] for s in RM.steps],
                     ["confirm", "try-tab5", "yes"], ["confirm", "hello", "yes"]):
            texts.append(run(ctx, out, *argv)[1])
    allout = "\n".join(texts)
    assert MARKER in allout                                             # 시험 낱말은 우리가 만든 것이라 보여도 된다
    for s in (SECRET_API, SECRET_CLIENT, "SESSION-STRING-ABC", "1234567", TOKEN, "TAILTAIL", "BODYONLY", "그리고비밀", "secretdomain",
              "비밀이름", "SECRETPORT", "ERR-SECRET-TEXT"):
        assert s not in allout, s


# ---- 실행 파일 ------------------------------------------------------------------------------------------------------------

def test_bin_wideeye_and_urgentboard_run_the_same_tool(tmp_path):
    a, b = ROOT / "bin" / "wideeye", ROOT / "bin" / "urgentboard"
    assert os.access(a, os.X_OK) and os.access(b, os.X_OK)
    body = lambda f: [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]   # noqa: E731
    assert body(a) == body(b) and any('tools/ub.py' in ln and ln.startswith("exec ") for ln in body(a))
    home = tmp_path / "home"
    env = {**os.environ, "URGENT_BOARD_HOME": str(home), ub.NO_OPEN: "1"}
    outs = []
    for f in (a, b):
        r = subprocess.run([str(f), "guide", "status"], capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0, r.stderr
        outs.append(r.stdout)
    assert outs[0] == outs[1] and " hello · " in outs[0]


def test_ub_routes_guide(tmp_path, capsys):
    assert ub.main(["guide", "status"]) == 0 and " hello · " in capsys.readouterr().out


def test_recheck_leaves_person_confirmed_steps_alone(monkeypatch):
    """결정 D1 — 사람 확인 단계(시작 · 탭5 첫 알림)는 다시 돌리지 않는다(시험 글이 알림에서 밀려나면 사람의 '예'까지 지우게 된다)."""
    from urgent_board import guide_checks
    seen = []
    for name in ("hello", "try_tab5"):
        monkeypatch.setitem(guide_checks.CHECKERS, name, lambda f, p, s: seen.append(s.id) or guide.Verdict("fail", "x", "said_no"))
    p = guide.Progress(roadmap_version=RM.version, profile={**FULL_PROFILE, "tab5": "plugged"}, consent=consent(), marker=MARKER,
                       steps=ok("hello", "install", "permit-messages", "link-sms", "try", "tab5-show", "try-tab5"))
    p2, dropped = gc.recheck(RM, p, _facts(feed_whats=[MARKER], tab5={"connected": True, "age_s": 1, "sent_seq": 9,
                                                                        "tab5": {"board": "live", "bseq": 9}}), True, datetime.now().astimezone())
    assert seen == [] and dropped == [] and p2 == p


def test_sandbox_and_older_engine_are_named_and_erase_nothing(tmp_path, monkeypatch):
    ctx, eng, out = make(tmp_path)
    steps = ok("hello", "install", "permit-messages", "link-sms", "try")
    save(ctx, profile=FULL_PROFILE, consent=consent(), marker=MARKER, steps=steps)
    eng.down = "blocked"                                                # AI 의 샌드박스가 127.0.0.1 을 막았다
    rc, text = run(ctx, out, "check", "install")
    assert rc == 1 and gc.REACH_BLOCKED in text and "허락" in text
    eng.down = None
    from tools import setup_cli
    real = setup_cli._call
    monkeypatch.setattr(setup_cli, "_call", lambda ctx, m, path, *a, **k: (404, None) if path == "/api/guide/facts" else real(ctx, m, path, *a, **k))
    rc, text = run(ctx, out, "next")                                    # 사실 길이 없는 옛 엔진 — 다시 켜라고, 진행은 그대로
    assert rc == 0 and "옛 판" in text and f"{gc.CMD} restart engine" in text
    assert all(load(ctx).steps[i]["state"] == "ok" for i in ("permit-messages", "link-sms", "try"))   # 엔진 단계는 위의 check 가 안 됨으로 적었다


# ---- 검토 1 고침: 탭5 잠깐 빠짐(R8) · 채울 자리가 든 명령(R9) · 진행 기록 잠금 · 같은 되돌림 길 -------------------------------

def test_unplugging_the_tab5_for_a_moment_keeps_the_first_notice_test(tmp_path, firmware_public):
    """결정 R8 — 탭5 가 빠졌다(no_tab5 — 묵은 상태 파일도 같다)는 적기만 하고, 사람이 확인한 탭5 첫 알림을 지우지 않는다."""
    ctx, eng, out = make(tmp_path)
    imessage_ok(eng)
    save(ctx, profile={**FULL_PROFILE, "tab5": "plugged"}, consent=consent(), marker=MARKER,
         steps=ok("hello", "install", "permit-messages", "link-sms", "try", "tab5-flash", "tab5-show", "try-tab5"))
    rc, text = run(ctx, out, "next")                                    # 탭5 상태 파일이 없다 = 뽑혀 있다 — 탭5 의 첫 단계(굽기)로 돌아간다
    assert rc == 0 and machine(text)["step"] == "tab5-flash" and "다시 꽂아 주세요" in text
    p = load(ctx)
    assert p.steps["tab5-flash"]["state"] == "fail" and p.steps["tab5-flash"]["note"] == "no_tab5"
    assert p.steps["tab5-show"]["note"] == "no_tab5" and p.steps["try-tab5"]["state"] == "ok"   # 적기만 하고 사람이 확인한 것은 지우지 않는다
    tab5_file(ctx, 9, age=600)                                          # 창이 죽어 옛 live 가 남은 파일 — 이것도 빠진 것
    rc, text = run(ctx, out, "check", "tab5-show")
    assert rc == 1 and "다시 꽂아 주세요" in text and load(ctx).steps["try-tab5"]["state"] == "ok"
    tab5_file(ctx, 9)                                                   # 다시 꽂음 — next 가 그 단계를 다시 보고 지나간다
    rc, text = run(ctx, out, "next")
    assert rc == 0 and machine(text)["step"] == "me"
    p = load(ctx)
    assert p.steps["tab5-flash"]["state"] == "ok" and p.steps["tab5-show"]["state"] == "ok" and p.steps["try-tab5"]["state"] == "ok"


def test_check_fail_steps_back_the_same_way_as_next(tmp_path):
    """check 의 안 됨도 next 와 같은 길로 뒤 단계를 지운다 — 다만 사람이 '아니요'라고 한 것은 적기만(D7)."""
    ctx, eng, out = make(tmp_path)
    imessage_ok(eng)
    save(ctx, profile=FULL_PROFILE, consent=consent(), marker=MARKER, steps=ok("hello", "install", "permit-messages", "link-sms", "try"))
    from urgent_board.connect import probes
    PROBE["messages_access"] = {"ok": False, "why": probes.NO_ACCESS}
    assert run(ctx, out, "check", "permit-messages")[0] == 1
    p = load(ctx)
    assert "link-sms" not in p.steps and "try" not in p.steps and p.steps["install"]["state"] == "ok"
    rc, text = run(ctx, out, "confirm", "hello", "no")
    assert rc == 1 and load(ctx).steps["install"]["state"] == "ok"   # 마음을 바꿔도 해 둔 것은 남는다


def _copyable(text: str) -> list[str]:
    """그대로 붙여 넣을 줄 — 코드 블록 안, 그리고 줄 맨 앞이나 '말: ' 바로 뒤에 명령이 오는 줄."""
    out, inside = [], False
    for ln in text.splitlines():
        if ln.strip() == "```":
            inside = not inside
            continue
        if inside or re.match(r"^\s*(?:[^「」]*?: )?" + re.escape(gc.CMD), ln):
            out.append(ln)
    return out


def test_commands_with_blanks_to_fill_are_never_given_as_lines_to_paste(tmp_path, monkeypatch):
    """결정 R9 — <…> 자리가 남은 명령은 붙여 넣을 줄 · 코드 블록으로 내지 않는다(셸은 < 를 입력 돌리기로 읽는다). 형식 말로만."""
    ctx, eng, out = make(tmp_path)
    live = {"connected": True, "age_s": 1, "sent_seq": 9, "tab5": {"board": "live", "bseq": 9}}
    monkeypatch.setattr(gc, "gather", lambda ctx: (_facts(feed_whats=[MARKER], tab5=live), [], True))
    path = ["hello", "install", "permit-messages", "link-sms", "try", "tab5-show", "try-tab5", "me", "boxes", "criteria", "extras"]
    texts = [run(ctx, out, "next")[1], run(ctx, out, "next", "--for-chat")[1], run(ctx, out, "confirm", "hello", "yes")[1],
             run(ctx, out, "profile")[1]]
    for i in range(1, len(path)):
        save(ctx, profile={**FULL_PROFILE, "tab5": "plugged"}, consent=consent(), marker=MARKER, steps=ok(*path[:i]))
        texts += [run(ctx, out, "next")[1], run(ctx, out, "next", "--for-chat")[1]]
    save(ctx, profile={**FULL_PROFILE, "messengers": ["calls", "other"]}, consent=consent(), steps=ok("hello", "install", "permit-calls", "link-calls"))
    texts += [run(ctx, out, "next")[1], run(ctx, out, "next", "--for-chat")[1]]          # 막힌 길 — 프로필 고치는 형식
    allout = "\n".join(texts)
    bad = [ln for t in texts for ln in _copyable(t) if "<" in ln]
    assert bad == [], bad[:5]
    assert allout.count(gc.FORM) >= 5 and "「" in allout                # 형식은 말로 — 비어 있는 시험이 아니다
    chat = "\n".join(texts[1::2])
    assert gc.FORM_CHAT in chat                                         # 채팅 AI 에게: 채운 한 줄을 만들어 건네라


def _hold_lock_in_another_process(home):
    lock = paths.state_dir(home) / gc.LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    code = ("import fcntl, os, sys, time\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "print('held', flush=True)\n"
            "time.sleep(60)\n")
    p = subprocess.Popen([sys.executable, "-c", code, str(lock)], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_progress_is_read_and_written_by_one_command_at_a_time(tmp_path, monkeypatch):
    """진행 기록의 읽기 → 고치기 → 쓰기는 state/guide.lock 을 쥔 한 명령만 — 못 잡으면 한 줄 말하고 쓰지 않고 1."""
    ctx, eng, out = make(tmp_path)
    monkeypatch.setattr(gc, "LOCK_WAIT", 0.3)
    holder = _hold_lock_in_another_process(ctx.home)                     # 다른 프로세스(다른 AI 창의 명령)가 쥐고 있다
    try:
        for argv in (["profile", "tab5=none"], ["marker"], ["next"], ["check", "hello"], ["confirm", "hello", "yes"]):
            rc, text = run(ctx, out, *argv)
            assert rc == 1 and text.strip() == gc.BUSY, (argv, text)
        assert not guide.progress_file(ctx.home).exists()
        assert run(ctx, out, "status")[0] == 0                          # 읽기만 하는 status 는 기다리지 않는다
    finally:
        holder.kill()
        holder.wait()
    assert run(ctx, out, "profile", "tab5=none")[0] == 0 and load(ctx).profile == {"tab5": "none"}


def test_a_command_waits_for_the_lock_and_then_goes_on(tmp_path, monkeypatch):
    ctx, eng, out = make(tmp_path)
    monkeypatch.setattr(gc, "LOCK_WAIT", 5)
    lock = paths.state_dir(ctx.home) / gc.LOCK_FILE
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    threading.Timer(0.3, os.close, (fd,)).start()                        # 0.3초 뒤 놓는다
    t0 = time.monotonic()
    rc, _ = run(ctx, out, "profile", "iphone=no")
    assert rc == 0 and time.monotonic() - t0 >= 0.25 and load(ctx).profile == {"iphone": "no"}
