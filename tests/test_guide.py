"""길 정의(urgent_board/roadmap.yaml)와 길 엔진(urgent_board/guide.py) — 순수 부분(설계 2026-10-18 공개판 G4 · G5 · G8 · G16,
계획 1부 작업 4). 순서 · 건너뛰기 · 판정 저장은 코드가 정한다 — AI 는 설명하고 묻기만 한다. 같은 입력이면 늘 같은 답이어야 한다."""
import copy
import json
import os
import re
import shlex
import stat
from datetime import datetime

import pytest

from urgent_board import guide, setup_steps

NOW = datetime(2026, 10, 18, 9, 0, 0)
OK = guide.Verdict("ok", "됐어요")
FAIL = guide.Verdict("fail", "안 됐어요", "no_access")

# 설계 표의 차례 — link-first / link-more 는 메신저마다 한 단계로 펼쳤다(결정 R5: 쉬운 셋은 try 앞, 어려운 둘은 탭5 뒤)
DESIGN_ORDER = ["hello", "install", "permit-messages", "permit-calls", "permit-teams", "link-sms", "link-calls", "link-teams",
                "try", "tab5-flash", "tab5-show", "try-tab5", "link-gmail", "link-telegram", "me", "boxes", "criteria", "extras"]
CHECKS_R1 = ("hello", "install", "permit_messages", "permit_calls", "permit_teams", "link", "try_marker", "tab5_flash", "tab5_show",
             "try_tab5", "setup_step")


@pytest.fixture(scope="module")
def rm():
    return guide.load()


def fresh(rm, **profile):
    return guide.Progress(roadmap_version=rm.version, profile=profile)


def walk(rm, p, facts=None, limit=60):
    """다음 단계마다 '됨'을 적어 가며 끝까지 — 지나간 단계 id 들과 마지막 기록."""
    seen = []
    while (s := guide.next_step(rm, p, facts)) is not None:
        assert len(seen) < limit, seen
        seen.append(s.id)
        p = guide.record(p, s.id, OK, NOW)
    return seen, p


# ---- 다음 단계 ---------------------------------------------------------------------------------------------------------

def test_next_step_is_deterministic_and_respects_needs_and_skip(rm):
    p = guide.Progress(roadmap_version=rm.version)                                   # 아무것도 모름 — 프로필도 없다
    assert guide.next_step(rm, p).id == "hello"
    assert guide.next_step(rm, copy.deepcopy(p)).id == "hello"                       # 같은 입력 → 같은 답
    failed = guide.record(p, "hello", FAIL, NOW)
    assert guide.next_step(rm, failed).id == "hello"                                 # 안 된 단계는 넘어가지 않는다
    assert p.steps == {}                                                              # record 는 받은 기록을 고치지 않는다

    # 문자 · 탭5 꽂음, 탭5 프로그램이 공개 전이면(1부 — v0.1.0) 탭5 단계 셋(굽기 · 잇기 · 첫 알림)을 모두 건너뛴다(준비 중 — 마지막 검토 I1)
    unpublished = {"firmware_public": False, "healthz": 200}
    seen, _ = walk(rm, fresh(rm, messengers=["sms"], iphone="yes", tab5="plugged", ai="a", admin="yes"), facts=unpublished)
    assert seen == ["hello", "install", "permit-messages", "link-sms", "try", "me", "boxes", "criteria", "extras"]
    for sid in ("tab5-flash", "tab5-show", "try-tab5"):
        assert "준비 중" in guide.skip_reason(rm.step(sid), fresh(rm, tab5="plugged"), unpublished), sid
    assert guide.FACTS["firmware_public"] is True                                     # v0.2.0 부터 공개 — 기본 길에 탭5 단계가 들어온다
    seen, _ = walk(rm, fresh(rm, messengers=["sms"], tab5="plugged"), facts={"firmware_public": True, "healthz": 200})
    assert seen.index("try") < seen.index("tab5-flash") < seen.index("tab5-show") < seen.index("try-tab5")  # 공개된 뒤에는 셋 다 길에 들어온다

    # Gmail 만: 쉬운 잇기는 모두 건너뛰고 Gmail(어려운 길)을 이은 뒤에야 첫 알림 — needs_any
    p = fresh(rm, messengers=["gmail"], tab5="none")
    p = guide.record(guide.record(p, "hello", OK, NOW), "install", OK, NOW)
    assert guide.next_step(rm, p).id == "link-gmail"
    seen, _ = walk(rm, fresh(rm, messengers=["gmail"], tab5="none"))
    assert seen == ["hello", "install", "link-gmail", "try", "me", "boxes", "criteria", "extras"]

    # 다 씀 · 탭5 는 나중: 쉬운 셋 → 첫 알림 → 어려운 둘
    seen, _ = walk(rm, fresh(rm, messengers=["sms", "gmail", "teams", "calls", "telegram", "other"], tab5="later"))
    assert seen == ["hello", "install", "permit-messages", "permit-calls", "permit-teams", "link-sms", "link-calls", "link-teams",
                    "try", "link-gmail", "link-telegram", "me", "boxes", "criteria", "extras"]

    # 앞 단계가 안 됐으면 뒤 단계로 가지 않는다
    p = guide.record(guide.record(fresh(rm, messengers=["sms"]), "hello", OK, NOW), "install", FAIL, NOW)
    assert guide.next_step(rm, p).id == "install"


def test_a_dead_end_is_reported_not_called_finished(rm):
    """부재중 전화 · 목록에 없는 메신저만 고른 사람은 첫 알림 시험을 할 수 없다 — '끝'이 아니라 막힘으로(실패를 성공처럼 넘기지 않는다)."""
    for messengers, linked in ((["calls"], ["permit-calls", "link-calls"]), (["other"], []), ([], [])):
        seen, p = walk(rm, fresh(rm, messengers=messengers, tab5="none"))
        assert seen == ["hello", "install", *linked]
        assert guide.next_step(rm, p) is None and not guide.finished(rm, p)
        step, v = guide.blocked(rm, p)
        assert step.id == "try" and v.state == "fail" and v.note == guide.UNREACHABLE
        assert guide.stuck_for(step, v, p), "막힌 까닭을 사람 말로 알려 줄 갈림길이 있어야 한다"
        assert guide.blocked(rm, copy.deepcopy(p)) == (step, v)
    seen, p = walk(rm, fresh(rm, messengers=["sms"], tab5="none"))
    assert guide.finished(rm, p) and guide.blocked(rm, p) is None


def test_lines_and_branches_follow_the_profile(rm):
    link = rm.step("link-sms")
    iphone = guide.pick(link.do, fresh(rm, iphone="yes"))
    no_iphone = guide.pick(link.do, fresh(rm, iphone="no"))
    assert len(iphone) == len(no_iphone) + 1 and any("아이폰" in s for s in iphone)
    permit = rm.step("permit-messages")
    v = guide.Verdict("fail", "권한이 없어요", "no_access")
    admin = [b["say"] for b in guide.stuck_for(permit, v, fresh(rm, admin="no"))]
    assert any("관리자" in s for s in admin)
    assert not any("관리자에게" in b["say"] for b in guide.stuck_for(permit, v, fresh(rm, admin="yes")))
    p = guide.Progress(roadmap_version=rm.version, marker="해바라기 27")
    assert "해바라기 27" in guide.fill(rm.step("try-tab5").confirm, p)
    assert guide.MARKER not in guide.fill(rm.step("try-tab5").confirm, guide.Progress(roadmap_version=rm.version))


# ---- 진행 기록 파일 ----------------------------------------------------------------------------------------------------

def test_unknown_or_broken_progress_file_starts_fresh_and_keeps_a_copy(tmp_path):
    f = tmp_path / "state" / "guide.json"
    empty = guide.Progress(roadmap_version=3)
    assert guide.read_progress(f, roadmap_version=3) == empty                       # 없음 → 빈 기록, 폴더도 만들지 않는다
    assert not f.parent.exists()
    good = {"version": guide.FORMAT, "roadmap_version": 3, "profile": {}, "steps": {}, "marker": None, "consent": {}}
    cases = [b"{not json", b"\xff\xfe\x00", b"[]", b"null",
             json.dumps({**good, "version": 99}).encode(),                                   # 모르는 꼴 판
             json.dumps({**good, "profile": {"tab5": "maybe"}}).encode(),                    # 모르는 값
             json.dumps({**good, "profile": {"colour": "blue"}}).encode(),                   # 모르는 키
             json.dumps({**good, "steps": {"hello": {"state": "great"}}}).encode(),
             json.dumps({**good, "steps": {"hello": "ok"}}).encode(),
             json.dumps({**good, "marker": 27}).encode(),
             json.dumps({**good, "roadmap_version": True}).encode()]
    f.parent.mkdir()
    for i, raw in enumerate(cases):
        f.write_bytes(raw)
        assert guide.read_progress(f, roadmap_version=3, now=datetime(2026, 10, 18, 9, 0, i % 2)) == empty, raw
        assert not f.exists(), raw                                                        # 옮겨 두었다(다음 쓰기가 덮지 않게)
    kept = sorted(f.parent.glob("guide.json.bad-*"))
    assert len(kept) == len(cases)                                                          # 같은 초에 깨져도 덮어쓰지 않는다
    assert sorted(k.read_bytes() for k in kept) == sorted(cases)

    f.write_text(json.dumps(good), encoding="utf-8")                                        # 멀쩡한 파일은 그대로 읽고 옮기지 않는다
    assert guide.read_progress(f, roadmap_version=3) == empty and f.exists()


def test_roadmap_version_change_drops_step_states_but_keeps_profile(tmp_path):
    f = tmp_path / "guide.json"
    old = guide.Progress(roadmap_version=1, profile={"messengers": ["sms", "gmail"], "tab5": "plugged", "admin": "yes"},
                         steps={"hello": {"state": "ok", "at": "2026-10-18T09:00:00", "note": "", "confirmed": True}},
                         marker="해바라기 27", consent={"names": True, "at": "2026-10-18T09:00:00"})
    guide.write_progress(f, old)
    assert guide.read_progress(f, roadmap_version=1) == old                                 # 같은 판이면 그대로
    new = guide.read_progress(f, roadmap_version=2)
    assert new.roadmap_version == 2 and new.steps == {} and new.profile == old.profile and new.marker == old.marker
    assert new.consent == {}                                                                # 길이 바뀌면 허락은 다시 묻는다(무엇이 AI 로 가는지 바뀌었을 수 있다)
    assert not list(tmp_path.glob("guide.json.bad-*"))                                      # 판이 다른 것은 깨진 것이 아니다


def test_write_progress_is_private_and_leaves_no_temp_file(tmp_path):
    f = tmp_path / "state" / "guide.json"
    p = guide.Progress(roadmap_version=1, profile={"messengers": ["sms"]}, marker="해바라기 27")
    guide.write_progress(f, p)
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert [x.name for x in f.parent.iterdir()] == ["guide.json"]
    assert guide.read_progress(f, roadmap_version=1) == p
    assert guide.progress_file().name == "guide.json" and guide.progress_file().parent.name == "state"


# ---- 판정 기록 · 사람 확인 · 되돌림 -----------------------------------------------------------------------------------

def test_record_and_confirm_keep_each_other():
    with pytest.raises(ValueError):
        guide.Verdict("great", "?")
    p = guide.Progress(roadmap_version=1)
    p = guide.record(p, "try-tab5", guide.Verdict("human", "탭5 에 보이나요?", ""), NOW)
    p2 = guide.confirm(p, "try-tab5", True, NOW)
    assert p.steps["try-tab5"].get("confirmed") is None                                     # 받은 기록은 그대로
    assert p2.steps["try-tab5"]["confirmed"] is True and p2.steps["try-tab5"]["state"] == "human"
    p3 = guide.record(p2, "try-tab5", OK, NOW)
    assert p3.steps["try-tab5"]["confirmed"] is True and p3.steps["try-tab5"]["state"] == "ok"  # 다시 판정해도 사람 답은 남는다
    assert p3.steps["try-tab5"]["at"] == "2026-10-18T09:00:00"
    h = guide.confirm(p, "hello", False, NOW)
    assert h.consent == {"names": False, "at": "2026-10-18T09:00:00"}                     # 시작의 허락은 허락 기록에도
    assert guide.confirm(p, "try-tab5", True, NOW).consent == {}


def test_invalidate_after_clears_dependents_only(rm):
    pub = {"firmware_public": True}                                                          # 탭5 단계가 길에 있는 때(프로그램 공개 뒤)
    _, p = walk(rm, fresh(rm, messengers=["sms", "gmail", "calls"], iphone="yes", tab5="plugged", admin="yes"), facts=pub)
    p = guide.confirm(p, "try-tab5", True, NOW)
    before = copy.deepcopy(p.steps)
    # 문자가 떨어져도 Gmail 이 이어져 있으면 첫 알림 시험은 그대로 — 기대는 것이 아직 있다
    assert guide.invalidate_after(rm, p, "link-sms", pub).steps == before
    assert p.steps == before                                                                 # 받은 기록은 그대로

    _, p = walk(rm, fresh(rm, messengers=["sms", "calls"], tab5="plugged"), facts=pub)
    p = guide.confirm(p, "try-tab5", True, NOW)
    out = guide.invalidate_after(rm, p, "permit-messages", pub)
    cleared = {"link-sms", "try", "tab5-flash", "tab5-show", "try-tab5", "me", "boxes", "criteria", "extras"}
    assert set(p.steps) - set(out.steps) == cleared
    for sid in out.steps:
        assert out.steps[sid] == p.steps[sid]                                                 # 기대지 않는 것 · 그 단계 자신은 그대로
    assert "permit-messages" in out.steps and "link-calls" in out.steps and "hello" in out.steps
    assert guide.next_step(rm, out, pub).id == "link-sms"
    with pytest.raises(ValueError):
        guide.invalidate_after(rm, p, "no-such-step")


# ---- 길 정의 자체 ------------------------------------------------------------------------------------------------------

def test_roadmap_has_the_design_order(rm):
    assert [s.id for s in rm.steps] == DESIGN_ORDER
    assert isinstance(rm.version, int) and rm.version >= 1
    assert [rm.index(s) for s in DESIGN_ORDER] == list(range(len(DESIGN_ORDER)))


def test_every_step_in_the_roadmap_has_a_known_checker_and_known_needs(rm):
    assert guide.KNOWN_CHECKS == CHECKS_R1                                                    # 작업 5 의 확인기 이름(결정 R1)
    ids = [s.id for s in rm.steps]
    assert len(ids) == len(set(ids))
    for i, s in enumerate(rm.steps):
        assert s.check in guide.KNOWN_CHECKS, s.id
        assert s.who in guide.WHO, s.id
        assert all(n in ids[:i] for n in s.needs), (s.id, s.needs)                         # 앞선 단계만
        assert all(n in ids and n != s.id for n in s.needs_any), (s.id, s.needs_any)
        notes = set(guide.STATES) | {guide.ENGINE_DOWN} | set(guide.KNOWN_NOTES[s.check])
        if s.needs_any:
            notes.add(guide.UNREACHABLE)
        for b in s.stuck:
            assert b["when"] in notes, (s.id, b["when"])                                      # 확인기가 내는 말에만 걸린다
            if "then" in b and not b["then"].startswith("wideeye "):
                assert b["then"] in ids, (s.id, b["then"])
        if s.check == "link":                                                                  # 메신저마다 잇기 — 확인기가 볼 곳과 건너뛰기가 같은 메신저
            src = s.params.get("source")
            assert src in guide.MESSENGER_SOURCE.values(), s.id
            m = next(k for k, v in guide.MESSENGER_SOURCE.items() if v == src)
            assert {"messengers": {"lacks": m}} in s.skip_if, s.id
        elif s.check == "setup_step":
            assert s.params.get("step") in setup_steps.ORDER, s.id
        else:
            assert s.params == {}, s.id
    assert rm.step("try").needs_any and all(rm.step(n).check == "link" for n in rm.step("try").needs_any)
    assert rm.step("try-tab5").confirm and guide.MARKER in rm.step("try-tab5").confirm         # 마지막은 사람이 눈으로(G16)
    assert rm.step("hello").confirm


def test_every_command_in_the_roadmap_exists(rm, capsys):
    """run · after · 갈림길의 then 이 부르는 것은 모두 실제 `wideeye` 하위 명령이다(어느 AI 든 그대로 따라 한다)."""
    from tools import setup_cli, tab5_cli, ub
    lines = [(s.id, c) for s in rm.steps for c in (*s.run, *s.after)]
    lines += [(s.id, b["then"]) for s in rm.steps for b in s.stuck if b.get("then", "").startswith("wideeye ")]
    assert lines
    for sid, line in lines:
        words = shlex.split(line)
        assert words[0] == "wideeye" and len(words) > 1, (sid, line)
        args = words[1:]
        if args[0] == "guide":
            assert len(args) > 1 and args[1] in guide.GUIDE_COMMANDS, (sid, line)
            continue
        if args[0] == "setup":
            setup_cli.parser().parse_args(args[1:])                                              # 틀리면 SystemExit
            continue
        if args[0] == "tab5":
            tab5_cli.parser().parse_args(args[1:])                                               # 굽기 명령(v0.2.0 — FL-D5)
            continue
        with pytest.raises(SystemExit) as e:
            ub.main([*args, "--help"])
        assert e.value.code == 0, (sid, line)
    capsys.readouterr()


DEV_WORDS = ("엔진", "수집기", "출처", "판정", "TCC", "launchd", "plist", "venv", "api_hash", "토큰", "일련번호", "펌웨어", "서버")
BRANDS = ("카톡", "카카오", "텔레그램", "Telegram", "팀즈", "Teams", "Gmail", "지메일", "왓츠앱", "WhatsApp", "슬랙", "Slack",
          "iMessage", "FaceTime")
GENERAL = ("hello", "install", "try", "tab5-flash", "tab5-show", "try-tab5", "me", "boxes", "criteria", "extras")
ASK_SECRET = re.compile(r"(비밀번호|암호|코드|열쇠)\S*\s*(를|을)?\s*(AI\s*에게\s*)?(알려|말해|붙여)\s*(줘|주세요|줘요)")


def test_human_text_is_plain_and_never_asks_for_secrets(rm):
    texts = guide.human_texts(rm)
    fields = {f for _, f, _ in texts}
    assert {"title", "why", "do", "confirm", "stuck", "skip"} <= fields
    for sid, field, text in texts:
        assert text.strip(), (sid, field)
        for w in DEV_WORDS:
            assert w not in text, (sid, field, w, text)
        if sid in GENERAL and not (sid == "hello" and text.startswith("쓰는 것을 모두 골라요")):   # 두루 쓰는 글에는 메신저 상표를 내세우지 않는다
            for b in BRANDS:                                                                     # (시작의 '쓰는 것' 질문만 이번 판에 이을 수 있는 곳을
                assert b not in text, (sid, field, b, text)                                      #  이름 그대로 — 마지막 검토 I3, 카톡은 그 질문에도 없다)
        assert "카톡" not in text and "카카오" not in text, (sid, field, text)
        assert not ASK_SECRET.search(text), (sid, field, text)                                  # 비밀은 채팅으로 받지 않는다(G8)
        assert not re.search(r"\{(?!marker\})[^}]*\}", text), (sid, field, text)                # 채울 자리는 {marker} 하나
    hello = " ".join(t for sid, _, t in texts if sid == "hello")
    for must in ("이름", "메일 주소", "상자 이름", "메시지 본문", "파일"):                          # 무엇이 AI 로 가는지 · 남는 위험(G8)
        assert must in hello, must


def test_load_rejects_a_broken_roadmap(tmp_path):
    def bad(text):
        f = tmp_path / "r.yaml"
        f.write_text(text, encoding="utf-8")
        with pytest.raises(guide.RoadmapError) as e:
            guide.load(f)
        return "\n".join(e.value.problems)

    base = ("version: 1\nsteps:\n"
            "  - {id: a, title: 가, why: 나, who: ai, do: [\"다\"], check: install}\n")
    f = tmp_path / "ok.yaml"
    f.write_text(base, encoding="utf-8")
    assert [s.id for s in guide.load(f).steps] == ["a"]
    assert "check" in bad(base.replace("check: install", "check: nope"))
    assert "needs" in bad(base + "  - {id: b, title: 가, why: 나, who: ai, do: [\"다\"], check: install, needs: [c]}\n"
                                 "  - {id: c, title: 가, why: 나, who: ai, do: [\"다\"], check: install}\n")
    assert "skip_if" in bad(base.replace("check: install", "check: install, skip_if: [{iphone: no}]"))   # 따옴표 없는 no 는 YAML 에서 거짓
    assert "skip_if" in bad(base.replace("check: install", "check: install, skip_if: [{colour: blue}]"))
    assert "id" in bad(base + base.split("\n", 2)[2])                                                     # 같은 id 둘
    assert "who" in bad(base.replace("who: ai", "who: robot"))
    assert "run" in bad(base.replace("check: install", "check: install, run: [\"rm -rf ~\"]"))
    assert "알 수 없는" in bad(base.replace("check: install", "check: install, colour: blue"))
