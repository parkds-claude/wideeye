"""길의 확인기 — 모은 사실(Facts)을 단계마다 판정 하나(guide.Verdict)로 바꾼다(설계 2026-10-18 공개판 G4 · 작업 5).

순수: 파일 · 네트워크 · 하위 프로세스를 만지지 않는다. 사실은 부르는 쪽(tools/guide_cli, 작업 6)이 엔진의 GET /api/guide/facts 와
healthz 로 모아 넘긴다. 서명은 모두 `checker(facts, progress, step) -> Verdict` — step 은 단계 정의(params · id 를 읽는다).

Facts(사전) — 없는 칸은 모름으로 친다:
  healthz        int          엔진 /healthz 의 응답 코드(답이 없으면 0)
  connect        dict | None  /api/guide/facts 의 connect: messages · calls · teams = {ok, why, reason(no_data|no_access|"")},
                              sources = {imessage · calls · teams · telegram · kakao · whatsapp: {state}, gmail: [{state}…]}, telegram · google_client(있음 · 없음)
  status         dict | None  1판 설정 단계 {steps: [{id, done, …}], left, next}
  feed_whats     list[str]    알림 줄 글의 앞 20자(표식 찾기용)
  tab5           dict | None  상태 파일 tab5-link.json 의 일부 {connected, sent_seq, orient, age_s, tab5: {board, bseq} | None}.
                              age_s = 엔진 시계로 잰 파일의 나이(초, 모르면 None) — 확인기는 시계를 읽지 않고 이 값만 본다
  firmware_public bool        탭5 프로그램이 공개됐는가(조건 풀이용 — 확인기는 쓰지 않는다)

규칙(결정 G4 · R1): 엔진이 답하지 않으면(healthz != 200) hello · install 말고 모두 unknown + ENGINE_DOWN. note 는 guide.KNOWN_NOTES 안에서만.
탭5 상태 파일이 TAB5_FRESH_S(90초)보다 묵었거나 나이를 모르면 탭5 는 '연결 안 됨'이다(창이 죽어도 옛 live 가 남는다).
시험 낱말(표식)은 **알림 줄 글의 맨 앞에 오는 짧은 낱말이어야 한다** — feed_whats 는 말머리 · 인사말을 뗀 글의 앞 20자뿐이라 20자 밖의
낱말은 못 찾는다(작업 6 · 7: 표식을 만들 때 짧게 하고, 사람에게 '글의 맨 앞에 쓰라'고 말할 것).
탭5 시험(try_tab5)은 기계 부분(표식을 본 뒤 보낸 그림을 탭5 가 그렸다)이 먼저이고, 그것이 안 되면 사람의 '예'가 있어도 ok 가 아니다."""
from __future__ import annotations

import unicodedata
from dataclasses import replace
from datetime import datetime
from typing import Callable, Mapping

from . import guide
from .guide import Progress, Step, Verdict

Facts = Mapping
Checker = Callable[[Facts, Progress, Step], Verdict]

OK = "ok"
DOWN_SAY = "WideEye 프로그램이 답하지 않아요 — 먼저 install 단계(WideEye 가 돌고 있는지 보기)부터 해요."
TAB5_FRESH_S = 90                        # 탭5 상태 파일이 이보다 묵으면 연결 끊김으로 본다(창은 몇 초마다 파일을 쓴다 — 결정: 90초는 연결 쪽 그림 나이 상한과 같다)


def _down(facts: Facts) -> Verdict | None:
    if facts.get("healthz") != 200:
        return Verdict("unknown", DOWN_SAY, guide.ENGINE_DOWN)
    return None


def _need(facts: Facts, key: str) -> Verdict | None:
    """엔진이 답하지 않거나 그 사실을 못 받았으면 확인 불가."""
    return _down(facts) or (None if facts.get(key) is not None else Verdict("unknown", DOWN_SAY, guide.ENGINE_DOWN))


# ---- 시작 · 엔진 --------------------------------------------------------------------------------------------------------

def hello(facts: Facts, p: Progress, step: Step) -> Verdict:
    """진행 기록만 본다(엔진이 꺼져 있어도 된다): 허락 답이 없음 → 사람 확인, '아니요' → fail, 프로필 다섯 칸이 다 있어야 ok."""
    if "names" not in (p.consent or {}):
        return Verdict("human", "AI 에게 가는 것을 읽고 '예' 또는 '아니요'로 답해 주세요.", "no_consent")
    if not p.consent["names"]:
        return Verdict("fail", "알겠어요. AI 없이 설정 화면에서 직접 할 수 있어요.", "said_no")
    missing = [k for k in guide.PROFILE if k not in (p.profile or {})]
    if missing:
        return Verdict("human", "아직 답하지 않은 것이 있어요.", "no_profile")
    return Verdict("ok", "시작할 준비가 됐어요.")


def install(facts: Facts, p: Progress, step: Step) -> Verdict:
    if facts.get("healthz") == 200:
        return Verdict("ok", "WideEye 가 이 맥에서 돌고 있어요.")
    return Verdict("fail", "WideEye 가 이 맥에서 돌지 않아요.", "not_running")


# ---- 맥의 허락 ----------------------------------------------------------------------------------------------------------

def _permit(key: str, what: str) -> Checker:
    def check(facts: Facts, p: Progress, step: Step) -> Verdict:
        if (v := _need(facts, "connect")):
            return v
        probe = (facts["connect"] or {}).get(key) or {}
        if probe.get("ok"):
            return Verdict("ok", f"맥이 {what}를 읽게 허락돼 있어요.")
        if probe.get("reason") == "no_access":
            return Verdict("fail", f"맥이 {what}를 읽게 아직 허락하지 않았어요.", "no_access")
        return Verdict("unknown", f"이 맥이 {what} 자료를 아직 안 만들었어요.", "no_data")
    return check


# ---- 잇기 ---------------------------------------------------------------------------------------------------------------

def link(facts: Facts, p: Progress, step: Step) -> Verdict:
    """params.source 의 수집 상태: 정상 → ok(지난 글 0건도 정상), 수집 전 → unknown waiting, 중단 → fail stopped, 다시 연결 → fail reconnect,
    꺼짐 · 이은 계정 없음 → fail not_linked. Gmail 은 계정이 여럿이라 하나라도 정상이면 ok."""
    if (v := _need(facts, "connect")):
        return v
    src = (step.params or {}).get("source", "")
    got = ((facts["connect"] or {}).get("sources") or {}).get(src)
    states = [str((g or {}).get("state", "")) for g in got] if isinstance(got, list) else [str((got or {}).get("state", "off"))]
    states = [s for s in states if s]
    if "ok" in states:
        return Verdict("ok", "읽기 시작했어요.")
    if "reconnect" in states:
        return Verdict("fail", "연결이 풀렸어요. 다시 로그인해야 해요.", "reconnect")
    if "stopped" in states:
        return Verdict("fail", "읽다가 멈췄어요.", "stopped")
    if "waiting" in states:
        return Verdict("unknown", "첫 글을 읽는 중이에요.", "waiting")
    if not states or all(s == "off" for s in states):
        return Verdict("fail", "아직 이어져 있지 않아요.", "not_linked")
    return Verdict("unknown", "상태를 알 수 없어요.")


# ---- 첫 알림 시험 -------------------------------------------------------------------------------------------------------

def norm(text) -> str:
    """표식 비교용 — NFC, 소문자, 모든 빈칸 제거(받은 글의 NFD · 띄어쓰기 · 대소문자 차이를 무시한다)."""
    return "".join(unicodedata.normalize("NFC", str(text or "")).casefold().split())


def marker_seen(facts: Facts, p: Progress) -> bool:
    m = norm(p.marker)
    return bool(m) and any(m in norm(w) for w in facts.get("feed_whats") or [])


def try_marker(facts: Facts, p: Progress, step: Step) -> Verdict:
    if (v := _need(facts, "connect")):
        return v
    if not norm(p.marker):
        return Verdict("unknown", "시험 낱말이 아직 없어요.", "no_marker")
    if marker_seen(facts, p):
        return Verdict("ok", "시험 글이 WideEye 에 떴어요.")
    return Verdict("unknown", "아직 안 왔어요. 보낸 뒤 1분쯤 걸려요.", "not_yet")


# ---- 탭5 ----------------------------------------------------------------------------------------------------------------

def _tab5(facts: Facts) -> tuple[dict | None, dict | None]:
    """(상태 파일, 탭5 가 알린 상태) — 탭5 가 USB 에 없으면 (None, None)."""
    t = facts.get("tab5")
    age = t.get("age_s") if isinstance(t, Mapping) else None
    if (not isinstance(t, Mapping) or not t.get("connected") or isinstance(age, bool)
            or not isinstance(age, (int, float)) or age < 0 or age > TAB5_FRESH_S):
        return None, None
    return t, (t.get("tab5") if isinstance(t.get("tab5"), Mapping) else None)


def tab5_flash(facts: Facts, p: Progress, step: Step) -> Verdict:
    if (v := _down(facts)):
        return v
    t, st = _tab5(facts)
    if t is None:
        return Verdict("fail", "맥이 탭5 를 찾지 못했어요.", "no_tab5")
    if st is None or "board" not in st:
        return Verdict("unknown", "탭5 가 WideEye 프로그램의 신호를 아직 보내지 않았어요.", "not_flashed")
    return Verdict("ok", "탭5 에 WideEye 프로그램이 들어 있어요.")


def tab5_show(facts: Facts, p: Progress, step: Step) -> Verdict:
    if (v := _down(facts)):
        return v
    t, st = _tab5(facts)
    if t is None:
        return Verdict("fail", "맥이 탭5 를 찾지 못했어요.", "no_tab5")
    if (st or {}).get("board") == "live":
        return Verdict("ok", "탭5 가 WideEye 그림을 받고 있어요.")
    return Verdict("unknown", "탭5 가 아직 그림을 받지 않아요.", "not_live")


def _entry(p: Progress, step_id: str) -> dict:
    return dict((p.steps or {}).get(step_id) or {})


def _drawn(facts: Facts, p: Progress, e: Mapping) -> bool:
    """기계 부분 — 같은 낱말로 남긴 번호보다 새 그림을 탭5 가 그렸고 live 이며 표식이 지금도 보인다."""
    t, st = _tab5(facts)
    seq = e.get("marker_seq") if e.get("marker_for") == norm(p.marker) else None
    return bool(t is not None and isinstance(seq, int) and marker_seen(facts, p) and (st or {}).get("board") == "live"
                and isinstance((st or {}).get("bseq"), int) and st["bseq"] > seq and isinstance(t.get("sent_seq"), int) and t["sent_seq"] > seq)


def _valid_answer(e: Mapping):
    """사람 답은 이번 기계 통과(marker_at) 뒤에 한 것만 친다."""
    if "confirmed" not in e:
        return None
    try:
        if datetime.fromisoformat(e["confirmed_at"]) < datetime.fromisoformat(e["marker_at"]):
            return None
    except (KeyError, ValueError, TypeError):
        return None
    return e["confirmed"]


def remember_seq(facts: Facts, p: Progress, step_id: str, now: datetime) -> Progress:
    """탭5 시험의 기록을 맞춘 새 Progress(순수 — 파일에 쓰는 것은 부르는 쪽). 작업 6 은 try_tab5 를 돌리기 전에 매번 부르고 바뀌면 저장한다.
    - 표식이 처음 보이면 그때 보낸 그림 번호(sent_seq)를 marker_seq 로, 때를 marker_at 으로 남긴다. 낱말이 바뀌었거나 번호가 되감겼으면
      (sent_seq < marker_seq — 창을 다시 띄우면 번호가 처음부터) 현재 번호로 다시 남긴다.
    - 사람 답(confirmed)은 한 번의 기계 통과에만 쓴다: 번호를 새로 남길 때, 그리고 기계 부분이 안 될 때(탭5 묵음 · 표식 사라짐 …) 지운다."""
    e = _entry(p, step_id)
    t, _ = _tab5(facts)
    seq = t.get("sent_seq") if t else None
    seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else None
    mark = norm(p.marker)
    old = e.get("marker_seq") if e.get("marker_for") == mark else None
    fresh = marker_seen(facts, p) and seq is not None and (not isinstance(old, int) or seq < old)
    if fresh:
        e.update(marker_seq=seq, marker_for=mark, marker_at=now.isoformat(timespec="seconds"))
        e.pop("confirmed", None)
        e.pop("confirmed_at", None)
    elif "confirmed" in e and not _drawn(facts, p, e):
        e.pop("confirmed", None)
        e.pop("confirmed_at", None)
    else:
        return p
    steps = {k: dict(v) for k, v in (p.steps or {}).items()}
    steps[step_id] = e
    return replace(p, steps=steps)


def try_tab5(facts: Facts, p: Progress, step: Step) -> Verdict:
    """기계 부분(_drawn)이 먼저, 그다음 사람 확인 — 기계 부분이 안 되면 사람의 '예'가 있어도 ok 가 아니다."""
    if (v := _down(facts)):
        return v
    t, _ = _tab5(facts)
    if t is None:
        return Verdict("fail", "맥이 탭5 를 찾지 못했어요.", "no_tab5")
    e = _entry(p, step.id)
    if not _drawn(facts, p, e):
        return Verdict("unknown", "탭5 가 시험 낱말이 든 새 그림을 아직 받지 않았어요.", "not_drawn")
    answer = _valid_answer(e)
    if answer is None:
        return Verdict("human", "탭5 화면에 시험 낱말이 보이는지 물어봐 주세요.")
    if not answer:
        return Verdict("fail", "맥에는 떴는데 탭5 에는 안 보여요.", "said_no")
    return Verdict("ok", "탭5 에서 첫 알림을 봤어요.")


# ---- 1판 설정 단계(나 · 상자 · 기준 · 더 다듬기) -----------------------------------------------------------------------

def setup_step(facts: Facts, p: Progress, step: Step) -> Verdict:
    if (v := _need(facts, "status")):
        return v
    want = (step.params or {}).get("step", "")
    row = next((s for s in (facts["status"] or {}).get("steps") or [] if s.get("id") == want), None)
    if row is None:
        return Verdict("unknown", "이 설정 단계를 WideEye 에서 찾지 못했어요.")
    if row.get("done"):
        return Verdict("ok", "정해져 있어요.")
    return Verdict("fail", "아직 정하지 않은 것이 있어요.", "left")


CHECKERS: dict[str, Checker] = {
    "hello": hello, "install": install,
    "permit_messages": _permit("messages", "문자"), "permit_calls": _permit("calls", "부재중 전화"), "permit_teams": _permit("teams", "팀즈 알림"),
    "link": link, "try_marker": try_marker, "tab5_flash": tab5_flash, "tab5_show": tab5_show, "try_tab5": try_tab5, "setup_step": setup_step,
}
