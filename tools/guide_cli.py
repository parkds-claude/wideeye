"""길 명령 — `wideeye guide <명령>`(설계 2026-10-18 공개판 G4 · G5 · G8 · G16, 작업 6). **어떤 AI 든 이 명령의 출력만 따라** 사람과 함께
WideEye 설정을 끝낸다: 명령을 실행하는 AI(터미널 · VS Code), Claude 앱 확장, 명령을 못 돌리는 채팅 AI(사람이 한 줄씩 옮겨 붙인다 — --for-chat).

    next [--for-chat] [--json]       다음 한 단계(됐던 단계를 먼저 다시 확인한다) — 맨 끝 `기계: {json}` 한 줄
    check <id> [--json]              그 단계의 확인기를 돌려 판정 · 기록(사람의 "했어요"만으로 끝내지 않는다)
    confirm <id> yes|no [--json]     사람 확인을 적고 곧바로 check 와 같은 일
    profile [키=값 …]                 그 사람에 대한 답(쓰는 메신저 · 아이폰 · 탭5 · AI 갈래 · 관리자 암호) — 인자 없이 부르면 지금 답
    status [--json]                  단계마다 한 줄(엔진에 묻지 않는다)
    marker                           첫 알림 시험의 시험 낱말(없으면 만든다)

- 순서 · 건너뛰기 · 판정은 길 엔진(urgent_board/guide.py)과 확인기(guide_checks.py)가 정한다 — 둘 다 순수. 이 파일은 사실을 모으고
  (엔진의 GET /healthz · /api/guide/facts), 진행 기록(자료 폴더 state/guide.json)을 읽고 쓰고, 사람 말과 기계 줄을 찍는다.
- 비밀 값 · 메시지 내용은 싣지 않는다(G8): 사실 가운데 feed_whats(알림 글 앞 20자)는 확인기만 보고 어디에도 찍지 않는다. 찍는 글은 모두
  setup_steps.clean 을 거친다(진행 기록 파일 · 엔진에서 온 글이 안내 줄을 흉내 내지 못하게). 접속 열쇠는 어디에도 찍지 않는다.
- 사람 말은 존댓말(AI 가 사람에게 그대로 옮긴다). 종료값: 0 = 됨 · 사람에게 물을 차례 · 다음 단계를 보임 · 건너뜀 / 1 = 안 됨 · 막힘 ·
  기록을 쓰지 못함 / 2 = 잘못 부름(모르는 단계 · 키 · 값) / 3 = 확인 불가.

결정
- D1 다시 확인(next): 기록이 ok 이고 지금 건너뛰지 않으며 사람 확인이 없는(confirm 칸이 없는) 단계만 확인기를 다시 돌린다. fail 이면 그 단계를
  fail 로 적고 invalidate_after(그에 기대는 뒤 단계 기록을 지운다), unknown 은 ok 를 지킨다(결정 R7 — 흔들리는 읽기가 진행을 지우지 않게).
  사람 확인 단계(시작 · 탭5 첫 알림)는 다시 돌리지 않는다 — 시험 글이 알림에서 밀려나면 사람의 '예'까지 지우게 된다.
  비용: 묵은 ok 가 unknown 한 번을 넘긴다. 되돌리기: recheck() 의 조건.
- D2 엔진이 답하지 않거나 사실을 주지 않으면(up=False) 엔진 확인(install)만 다시 돌리고, 떨어져도 적기만 하고 뒤 단계를 지우지 않는다 —
  허락 뒤 다시 켜는 몇 초 사이에 next 를 부르면 첫 알림 시험까지 지워져 가족에게 다시 보내 달라고 해야 한다. 그사이 next 는 엔진 단계로
  돌아가 보이고, 엔진이 돌아오면 다음 next 가 뒤 단계를 다시 본다. 비용: 엔진이 꺼진 동안은 뒤 단계를 다시 보지 않는다.
  되돌리기: recheck() 의 up 조건을 지운다.
- D3 명령은 bin/wideeye 의 전체 경로로 찍는다 — PATH 에 없다(1판 setup 명령과 같은 까닭). 길 정의의 'wideeye …' 를 바꿔 찍는다.
- D4 confirm 은 확인기가 '사람 차례'(human) · 이미 사람 답으로 판정됨(ok · said_no)일 때만 답을 적는다 — 탭5 가 아직 새 그림을 받지 않았는데
  '예'를 적어 두면 나중 그림에 그 답이 쓰인다(guide_checks.remember_seq 와 같은 뜻). 되돌리기: _answer_counts().
- D5 check 는 앞 단계가 안 됐어도 확인기를 돌리고 적는다(차례는 next 가 정한다) — 앞 단계가 먼저라는 말은 함께 낸다.
- D6 진행 기록을 읽지 못했고 옆으로 옮겨 두지도 못하면(guide.read_progress 의 OSError) 빈 기록으로 보이기만 한다 — 쓰는 명령(check ·
  confirm · profile · marker)은 덮어쓰지 않고 종료값 1(읽지 못한 기록을 지우지 않게).
- R8(검토 판정) 탭5 가 빠졌다(no_tab5 — 묵은 상태 파일도 확인기가 같은 말로 낸다)는 엔진 꺼짐처럼 잠깐의 일이다: 적기만 하고 뒤 단계를
  지우지 않는다(사람이 확인한 탭5 첫 알림이 남는다). next 는 이렇게 '잠깐 안 됨'(TRANSIENT)으로 적힌 단계도 다시 돌려, 다시 꽂거나
  다시 켜면 그 단계를 ok 로 되돌리고 지나간다. 되돌리기: KEEP_AFTER · TRANSIENT.
- D7 사람이 '아니요'라고 한 판정(said_no)도 적기만 한다 — 마음을 바꿔 시작을 거절해도 해 둔 단계는 남는다(다시 '예'면 그 자리부터).
  check 의 안 됨은 next 와 같은 길(settle)로 뒤 단계를 지운다. 되돌리기: KEEP_AFTER 에서 said_no 를 뺀다.
- R9(검토 판정) <…> 자리가 남은 명령은 붙여 넣을 줄 · 코드 블록으로 내지 않는다(셸은 < 를 입력 돌리기로 읽는다) — "명령 형식: 「…」"
  말 한 줄로만, --for-chat 이면 채팅 AI 에게 사람의 답으로 채운 한 줄을 만들어 건네라고 덧붙인다(Out.form).
- D8 진행 기록의 읽기 → 고치기 → 쓰기는 한 번에 한 명령만 — 자료 폴더 state/guide.lock 에 flock(LOCK_WAIT 초 기다림). 못 잡으면 BUSY 한 줄과
  종료값 1, 쓰지 않는다. 엔진에 묻는 일(gather — 길게는 30초)은 잠그기 전에 해서 잠금을 짧게 쥔다. status 는 읽기만 해서 잠그지 않는다
  (기록 파일은 늘 통째로 바꿔 놓으므로 반쯤 쓴 파일을 읽지 않는다). 비용: 두 AI 창이 함께 부르면 늦은 쪽이 다시 불러야 한다."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import random
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import setup_cli as sc, ub                                    # noqa: E402
from urgent_board import guide, guide_checks, paths, setup_steps        # noqa: E402
from urgent_board.guide import Progress, Roadmap, Step, Verdict         # noqa: E402

BIN = ROOT / "bin" / "wideeye"
CMD = f'"{BIN}"' if any(c.isspace() for c in str(BIN)) else str(BIN)    # D3
CHAT = "이 한 줄을 터미널에 붙여 넣고 결과를 붙여 주세요"
DONE_SAY = "끝 — WideEye 가 준비됐어요"
LINE = 600                                                               # 한 줄 글자 수 상한(길 정의의 가장 긴 줄보다 넉넉히)
SHORT = 60
STATE_NAME = {"ok": "됨", "fail": "안 됨", "unknown": "확인 불가", "human": "사람 확인 차례", guide.SKIPPED: "건너뜀"}
EXIT = {"ok": 0, "human": 0, "unknown": 3, "fail": 1}
MARK = {"ok": "✓", guide.SKIPPED: "–", "fail": "✗", "unknown": "?", "human": "…", "todo": "○"}
YES, NO = ("yes", "y", "예", "네"), ("no", "n", "아니요", "아니오")
# 사람에게 건네는 글에는 파일 이름 · 주소 같은 개발자 낱말을 쓰지 않는다(tests/test_guide_docs.py — 금지 낱말). 진행 기록 파일은
# 자료 폴더의 state/guide.json, 길 정의는 urgent_board/roadmap.yaml, 엔진 접속은 127.0.0.1:<포트>.
READ_FAIL = ("진행 기록을 읽지 못했어요(자료 폴더 안 state 폴더의 guide 기록 파일) — 이번에는 처음부터인 것처럼 보여요. "
             "그 파일을 다른 곳으로 옮기거나 지운 뒤 다시 해 주세요.")
NOT_WRITTEN = "진행 기록을 읽지 못해 이번 결과는 적지 않았어요 — 그 파일을 옮기거나 지운 뒤 다시 해 주세요."
WRITE_BLOCKED = ("진행 기록을 쓰지 못했어요 — AI 의 실행 권한이 자료 폴더 쓰기를 막았을 수 있어요. "
                 "AI 가 실행해도 되는지 물으면 화면의 허락 단추(예: Yes · Allow)를 고르거나, 이 명령을 터미널에서 직접 돌려 주세요.")
WRITE_FAIL = "진행 기록을 쓰지 못했어요(자료 폴더 안 state 폴더의 guide 기록 파일) — 디스크 자리 · 권한을 봐 주세요."
REACH_BLOCKED = ("AI 의 실행 권한이 이 맥 안의 WideEye 에 닿는 것을 막았을 수 있어요 — "
                 "AI 가 실행해도 되는지 물으면 화면의 허락 단추(예: Yes · Allow)를 고르거나, 이 명령을 터미널에서 직접 돌려 주세요.")
DOWN_FALLBACK = "먼저 WideEye 가 이 맥에서 돌고 있는지 볼게요."
FORM = "AI 가 <…> 자리를 채워 부를 명령 형식(그대로 붙여 넣지 말 것)"                          # R9
FORM_CHAT = ("<…> 자리를 사람의 답(또는 앞 명령의 결과)으로 채운 한 줄을 만들어, '이 한 줄을 터미널에 붙여 넣고 결과를 붙여 주세요'와 "
             "함께 코드 블록 하나로 건네 주세요.")
KEEP_AFTER = ("no_tab5", "said_no")       # 안 됨이어도 뒤 단계를 지우지 않는 말(R8 · D7) — 엔진 꺼짐은 up 으로 가린다(D2)
TRANSIENT = ("no_tab5", "not_running")    # 다시 꽂거나 다시 켜면 돌아오는 '잠깐 안 됨' — next 가 그 단계를 다시 본다(R8 · D2)
LOCK_FILE = "guide.lock"
LOCK_WAIT = 5.0                           # 초(D8)
BUSY = "다른 길 명령이 진행 기록을 쓰는 중이에요 — 몇 초 뒤 같은 명령을 다시 불러 주세요."
PROFILE_ASK = {"messengers": "쓰는 것(문자 · 부재중 전화 · Microsoft Teams 알림 · Gmail · 텔레그램 · 그 밖의 메신저)", "iphone": "아이폰을 쓰는지",
               "tab5": "탭5 가 지금 이 맥에 꽂혀 있는지(없음 · 나중에)", "ai": "어떤 AI 인지(a 명령을 실행 · b Claude 앱 확장 · c 채팅만 — AI 가 스스로 안다)",
               "admin": "이 맥의 관리자 암호를 아는지"}
# 시험 낱말 — 흔하고 치기 쉬운 우리말 낱말(상표 · 사람 이름이 아닌 것). + 빈칸 + 두 자리 수 = 10자 이하(가장 긴 낱말 4자 → 7자).
# 글의 맨 앞에 와야 한다(guide_checks — 알림 글 앞 20자에서 찾는다).
MARKER_WORDS = ("해바라기", "무지개", "고슴도치", "다람쥐", "코끼리", "수박", "참외", "펭귄", "기린", "앵두", "개나리", "진달래", "민들레",
                "도토리", "반딧불", "솜사탕", "눈사람", "붕어빵", "호랑이", "거북이", "소나무", "단풍잎", "옥수수", "고구마")
MARKER_MAX = 10
_rng = random.SystemRandom()
assert all(len(w) + 3 <= MARKER_MAX for w in MARKER_WORDS)


def now() -> datetime:
    """진행 기록의 시각 — 시간대가 붙은 이 맥의 시각(guide_checks 가 사람 답과 그림 번호를 남긴 때를 견준다)."""
    return datetime.now().astimezone()


def _c(text, limit: int = LINE) -> str:
    return setup_steps.clean(text, limit)


def _line(text) -> str:
    """한 줄 — 앞의 들여쓰기는 남기고 나머지는 clean(clean 은 앞뒤 빈칸을 지운다)."""
    s = str(text)
    pad = len(s) - len(s.lstrip(" "))
    return " " * pad + _c(s)


def full(cmd: str) -> str:
    """길 정의의 'wideeye …' → 실행할 수 있는 전체 경로(D3)."""
    return CMD + cmd[len("wideeye"):] if cmd == "wideeye" or cmd.startswith(guide.COMMAND) else cmd


def check_cmd(sid: str) -> str:
    return f"{CMD} guide check {sid}"


NEXT_CMD = f"{CMD} guide next"


def make_marker() -> str:
    return f"{_rng.choice(MARKER_WORDS)} {_rng.randint(10, 99)}"


def _good_marker(m) -> bool:
    return isinstance(m, str) and 0 < len(m) <= MARKER_MAX and _c(m) == m and bool(guide_checks.norm(m))


# ---- 진행 기록 ----------------------------------------------------------------------------------------------------------

def _load(ctx: ub.Ctx) -> tuple[Roadmap, Path, Progress, bool]:
    """(길, 기록 파일, 기록, 읽었는가) — 읽지도 옮겨 두지도 못했으면 빈 기록과 False(D6)."""
    rm = guide.load()
    path = guide.progress_file(ctx.home)
    try:
        return rm, path, guide.read_progress(path, rm.version, now()), True
    except OSError:
        return rm, path, Progress(roadmap_version=rm.version), False


def _save(path: Path, p: Progress) -> str | None:
    """쓰고 문제를 돌려준다(없으면 None)."""
    try:
        guide.write_progress(path, p)
        return None
    except PermissionError:
        return WRITE_BLOCKED
    except OSError:
        return WRITE_FAIL


def _lock(home: Path) -> tuple[int | None, str | None]:
    """D8 — (잠금 fd, 문제). 문제는 BUSY(LOCK_WAIT 안에 못 잡음) · WRITE_BLOCKED · WRITE_FAIL(잠금 파일을 못 만듦). fd 는 _unlock 으로 놓는다."""
    d = paths.state_dir(home)
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(d / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    except PermissionError:
        return None, WRITE_BLOCKED
    except OSError:
        return None, WRITE_FAIL
    end = time.monotonic() + LOCK_WAIT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, None
        except BlockingIOError:
            if time.monotonic() >= end:
                os.close(fd)
                return None, BUSY
            time.sleep(0.05)
        except OSError:
            os.close(fd)
            return None, WRITE_FAIL


def _unlock(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)                                                    # 닫으면 flock 이 풀린다


def settle(rm: Roadmap, p: Progress, s: Step, v: Verdict, facts: dict, up: bool, when: datetime) -> Progress:
    """판정을 적고, 안 됨이면 그에 기대는 뒤 단계 기록을 지운다 — next 의 다시 확인과 check 가 같은 길(검토 1). 엔진이 사실을 주지 않았으면(D2) ·
    탭5 가 잠깐 빠졌으면(R8) · 사람이 '아니요'라고 했으면(D7) 적기만 한다."""
    p = guide.record(p, s.id, v, when)
    if v.state == "fail" and up and v.note not in KEEP_AFTER:
        p = guide.invalidate_after(rm, p, s.id, facts)
    return p


# ---- 사실 모으기 --------------------------------------------------------------------------------------------------------

def gather(ctx: ub.Ctx) -> tuple[dict, list[tuple[str, str | None]], bool]:
    """(사실, 알릴 말 [(글, 명령|None)], 엔진이 사실까지 주었는가). 엔진이 답하지 않으면 healthz 0 — 확인기가 가린다(install 은 안 됨,
    그 밖은 확인 불가). 사실은 엔진이 준 네 칸만 쓴다 — healthz · firmware_public 은 이쪽이 채우고 엔진이 덮지 못한다."""
    facts: dict = {"firmware_public": guide.FACTS["firmware_public"], "healthz": 0}
    notes: list[tuple[str, str | None]] = []
    try:
        code, _ = sc._call(ctx, "GET", "/healthz", timeout=5)
    except sc.Unreachable as u:
        if u.why == "blocked":
            notes.append((REACH_BLOCKED, None))
        return facts, notes, False
    facts["healthz"] = code
    if code != 200:
        return facts, notes, False
    try:
        code, d = sc._call(ctx, "GET", "/api/guide/facts", timeout=30)
    except sc.Unreachable as u:
        notes.append((REACH_BLOCKED, None) if u.why == "blocked" else ("WideEye 가 확인할 사실을 주는 중에 답을 멈췄어요.", None))
        return facts, notes, False
    if code == 200 and isinstance(d, dict):
        facts.update({k: d[k] for k in ("connect", "status", "feed_whats", "tab5") if k in d})
        return facts, notes, True
    if code == 404:
        notes.append(("WideEye 가 이 안내보다 옛 판으로 돌고 있어요. 다시 켠 뒤 다시 해요.", f"{CMD} restart engine"))
    elif code == 401:
        notes.append(("WideEye 의 접속 열쇠가 맞지 않아요. 다시 켠 뒤 다시 해요.", f"{CMD} restart engine"))
    else:
        notes.append((f"WideEye 가 확인할 사실을 주지 않았어요({code}). 무엇이 막혔는지 살펴봐요.", f"{CMD} doctor"))
    return facts, notes, False


def recheck(rm: Roadmap, p: Progress, facts: dict, up: bool, when: datetime) -> tuple[Progress, list[str]]:
    """D1 · D2 · R8 — 됐던 단계(와 '잠깐 안 됨'으로 적힌 단계)를 다시 확인한 새 기록과, 떨어진 단계 id 목록.
    됐던 단계가 안 됨이면 settle(뒤 단계를 지울지는 거기서), 잠깐 안 됨이던 단계가 됨이면 됨으로 되돌린다."""
    dropped = []
    for s in rm.steps:
        e = (p.steps or {}).get(s.id) or {}
        was_ok = e.get("state") == "ok"
        blip = e.get("state") == "fail" and e.get("note") in TRANSIENT
        if not (was_ok or blip) or s.confirm or guide.skip_reason(s, p, facts) is not None:
            continue
        if not up and s.check != "install":
            continue
        v = guide_checks.CHECKERS[s.check](facts, p, s)
        if was_ok and v.state == "fail":
            p = settle(rm, p, s, v, facts, up, when)
            dropped.append(s.id)
        elif blip and v.state == "ok":
            p = guide.record(p, s.id, v, when)
    return p, dropped


# ---- 출력 ---------------------------------------------------------------------------------------------------------------

class Out:
    """사람 말 줄과 명령 — --for-chat 이면 명령마다 "이 한 줄을 …" 한 문장 + 한 줄짜리 코드 블록(채팅 AI 가 사람에게 그대로 건넨다)."""

    def __init__(self, chat: bool = False):
        self.chat = chat
        self.lines: list[str] = []
        self.shown: set[str] = set()                                     # 이미 낸 명령 — 갈림길이 같은 명령을 되풀이하지 않게

    def say(self, text: str) -> None:
        self.lines.append(_line(text))

    def form(self, label: str, command: str) -> None:
        """R9 — <…> 자리가 남은 명령: 붙여 넣을 줄이 아니라 형식을 말로(「」 안). 채팅이면 채운 한 줄을 만들어 건네라고."""
        self.lines.append(_line(f"{label} — {FORM}: 「{_c(command)}」"))
        if self.chat:
            self.lines.append(_line(f"  {FORM_CHAT}"))

    def cmd(self, label: str, command: str) -> None:
        command = _c(command)
        self.shown.add(command)
        if "<" in command:
            self.form(label, command)
            return
        if self.chat:
            self.lines += [_line(f"{label} — {CHAT}"), "```", command, "```"]
        else:
            self.lines.append(_line(f"{label}: {command}"))

    def cmds(self, heading: str, commands: list[str]) -> None:
        if not commands:
            return
        if self.chat:
            for i, c in enumerate(commands, 1):
                self.cmd(f"{heading}" + (f" ({i}/{len(commands)})" if len(commands) > 1 else ""), c)
            return
        self.shown.update(_c(c) for c in commands)
        self.lines.append(_line(f"{heading}:"))
        for c in commands:
            if "<" in c:
                self.lines.append(_line(f"  {FORM}: 「{_c(c)}」"))
            else:
                self.lines.append(_line(f" {c}"))

    def notes(self, notes: list[tuple[str, str | None]]) -> None:
        for text, command in notes:
            self.say(text)
            if command:
                self.cmd(" 그다음", command)

    def machine(self, obj: dict) -> None:
        self.lines.append("기계: " + json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

    def emit(self, ctx: ub.Ctx) -> None:
        for ln in self.lines:
            ctx.say(ln)


def _json(ctx: ub.Ctx, obj: dict) -> None:
    ctx.say(json.dumps(obj, ensure_ascii=False, indent=1))


def _note_texts(notes) -> list[str]:
    return [_c(t + (f" → {c}" if c else "")) for t, c in notes]


def _pos(rm: Roadmap, p: Progress, facts: dict, s: Step) -> tuple[int, int]:
    """(몇째, 모두) — 이 사람이 실제로 할 단계(건너뛰지 않는 것) 가운데."""
    ids = [x.id for x in rm.steps if guide.skip_reason(x, p, facts) is None]
    return (ids.index(s.id) + 1 if s.id in ids else 0), len(ids)


def _then(rm: Roadmap, s: Step, then: str) -> tuple[str, str]:
    """갈림길의 then → (말, 명령). 명령이면 그대로, 같은 단계면 다시 확인, 다른 단계면 그 단계 확인."""
    if then.startswith(guide.COMMAND):
        return "→ 다음", full(then)
    if then == s.id:
        return "→ 조금 뒤 다시 확인", check_cmd(then)
    return f"→ '{rm.step(then).title}' 단계로", check_cmd(then)


def _stuck(rm: Roadmap, s: Step, v: Verdict, p: Progress, facts: dict) -> list[dict]:
    """맞는 갈림길 [{say, then?, label?, run?}] — 엔진이 답하지 않는데 그 단계에 갈림길이 없으면 엔진 단계로 보내는 한 줄을 더한다."""
    out = []
    for b in guide.stuck_for(s, v, p, facts):
        row = {"say": _c(b["say"])}
        if "then" in b:
            label, command = _then(rm, s, b["then"])
            row.update(then=b["then"], label=label, run=_c(command))
        out.append(row)
    if v.note == guide.ENGINE_DOWN and not out:
        label, command = _then(rm, s, "install")
        out.append({"say": DOWN_FALLBACK, "then": "install", "label": label, "run": _c(command)})
    return out


def _say_stuck(o: Out, rows: list[dict], heading: str = "막히면:") -> None:
    if not rows:
        return
    o.say(heading)
    for r in rows:
        o.say(f" - {r['say']}")
        if "run" in r and r["run"] not in o.shown:
            o.cmd(f"   {r['label']}", r["run"])


def _profile_text(profile: dict) -> str:
    parts = []
    for k in guide.PROFILE:
        if k in (profile or {}):
            v = profile[k]
            parts.append(f"{k}={','.join(v) if isinstance(v, list) else v}")
    return _c(" · ".join(parts) or "아직 없음")


def _profile_cmd(keys) -> str:
    """키마다 받는 값을 자리로 — 사람의 답으로 채워 부른다."""
    def slot(k):
        vals = guide.PROFILE[k]
        return f"{k}=<쉼표로:{','.join(vals)}>" if k in guide.LIST_KEYS else f"{k}=<{'|'.join(vals)}>"
    return f"{CMD} guide profile " + " ".join(slot(k) for k in keys)


def _missing_profile(p: Progress) -> list[str]:
    return [k for k in guide.PROFILE if k not in (p.profile or {})]


def _needs_first(rm: Roadmap, p: Progress, facts: dict, s: Step) -> list[str]:
    """그 단계의 앞 단계 가운데 아직 안 된 것의 제목(D5 — check 가 함께 알린다)."""
    def done(n):
        return guide.skip_reason(rm.step(n), p, facts) is not None or guide.state(p, n) in guide.DONE
    out = [rm.step(n).title for n in s.needs if not done(n)]
    if s.needs_any and not any(guide.state(p, n) == "ok" and guide.skip_reason(rm.step(n), p, facts) is None for n in s.needs_any):
        open_ = [rm.step(n).title for n in s.needs_any if guide.skip_reason(rm.step(n), p, facts) is None]
        out.append(" 또는 ".join(open_) if open_ else guide.UNREACHABLE_SAY)
    return out


# ---- next -----------------------------------------------------------------------------------------------------------------

def _step_view(rm: Roadmap, p: Progress, facts: dict, s: Step, chat: bool) -> dict:
    n, total = _pos(rm, p, facts, s)
    e = (p.steps or {}).get(s.id) or {}
    last = {"state": e["state"], "note": _c(e.get("note", ""), SHORT)} if e.get("state") in guide.STATES and e["state"] != "ok" else None
    ask = _c(guide.fill(s.confirm, p)) if s.confirm else ""
    view = {
        "step": s.id, "n": n, "of": total, "title": _c(s.title), "why": _c(s.why), "who": s.who,
        "tell": [_c(x) for x in guide.pick(s.tell, p, facts)], "do": [_c(x) for x in guide.pick(s.do, p, facts)],
        "run": [_c(full(c)) for c in s.run], "after": [_c(full(c)) for c in s.after],
        "run_forms": [_c(full(c)) for c in s.run if "<" in c],         # R9 — 사람의 답으로 채워 부를 것(그대로 붙여 넣지 않는다)
        "ask": ask, "answers": [f"{CMD} guide confirm {s.id} yes", f"{CMD} guide confirm {s.id} no"] if ask else [],
        "marker": _c(p.marker, SHORT) if s.check == "try_marker" and p.marker else None,      # 보낼 글에 쓸 낱말(탭5 단계는 할 일 줄에 채워진다)
        "last": last, "stuck": _stuck(rm, s, Verdict(last["state"], "", e.get("note", "")), p, facts) if last else [],
        "check": check_cmd(s.id), "needs_person": s.who != "ai" or bool(s.confirm), "for_chat": chat, "done": False, "blocked": None,
    }
    return view


def _say_step(o: Out, v: dict) -> None:
    o.say(f"[단계 {v['n']}/{v['of']}] {v['title']}")
    o.say(f"왜: {v['why']}")
    if v["tell"]:
        o.say("먼저 알릴 것(그대로 전해 주세요):")
        for t in v["tell"]:
            o.say(f" - {t}")
    o.say("할 일:")
    for i, d in enumerate(v["do"], 1):
        o.say(f" {i}. {d}")
    if v["marker"]:
        o.say(f"시험 낱말: {v['marker']} — 보내는 글의 맨 앞에 써야 해요.")
    if v["last"]:
        o.say(f"지난 확인: {STATE_NAME.get(v['last']['state'], v['last']['state'])}")
        _say_stuck(o, v["stuck"])
    if v["ask"]:                                                         # 사람의 답(시작 단계는 허락)이 명령보다 먼저
        o.say(f"사람에게 물을 것: {v['ask']}")
        o.cmd(" 답이 '예'면", v["answers"][0])
        o.cmd(" 답이 '아니요'면", v["answers"][1])
    o.cmds("터미널에서 할 것" if o.chat else "AI 가 할 명령", v["run"])
    o.cmds("사람이 '했어요' 하면 먼저", v["after"])
    o.cmd("확인", v["check"])


def _machine(v: dict) -> dict:
    return {"step": v.get("step"), "n": v.get("n"), "of": v.get("of"), "check": v.get("check"), "needs_person": v.get("needs_person", False),
            "for_chat": v.get("for_chat", False), "done": v.get("done", False), "blocked": v.get("blocked"),
            "last": (v.get("last") or {}).get("state")}


def cmd_next(ctx: ub.Ctx, chat: bool = False, as_json: bool = False) -> int:
    facts, notes, up = gather(ctx)                                      # D8 — 엔진에 묻는 일은 잠그기 전에
    fd, lerr = _lock(ctx.home)
    if lerr == BUSY:
        ctx.say(BUSY)
        return 1
    try:
        rm, path, p, readable = _load(ctx)
        if not readable:
            notes.insert(0, (READ_FAIL, None))
        if lerr:                                                        # 잠금 파일도 못 만든다(샌드박스) — 보이기만 하고 쓰지 않는다
            notes.append((lerr, None))
        p2, dropped = recheck(rm, p, facts, up, now())
        if p2 != p and readable and fd is not None and (err := _save(path, p2)):
            notes.append((err, None))
    finally:
        _unlock(fd)
    return _next_out(ctx, rm, p2, facts, notes, dropped, chat, as_json)


def _next_out(ctx: ub.Ctx, rm: Roadmap, p: Progress, facts: dict, notes: list, dropped: list[str], chat: bool, as_json: bool) -> int:
    if dropped:
        notes.append(("다시 확인했더니 됐던 단계가 지금은 안 돼요: " + " · ".join(rm.step(i).title for i in dropped) + " — 그 단계부터 다시 해요.", None))
    s = guide.next_step(rm, p, facts)
    rc = 0
    if s is not None:
        view = _step_view(rm, p, facts, s, chat)
    elif guide.finished(rm, p, facts):
        view = {"step": None, "check": None, "needs_person": False, "for_chat": chat, "done": True, "blocked": None, "say": DONE_SAY}
    else:
        bs, bv = guide.blocked(rm, p, facts)
        n, total = _pos(rm, p, facts, bs)
        view = {"step": bs.id, "n": n, "of": total, "title": _c(bs.title), "say": _c(bv.say), "stuck": _stuck(rm, bs, bv, p, facts),
                "profile": dict(p.profile or {}), "profile_text": _profile_text(p.profile), "profile_form": _profile_cmd(guide.PROFILE),
                "check": None, "needs_person": True, "for_chat": chat, "done": False, "blocked": bv.note}
        rc = EXIT.get(bv.state, 1)
    view["notes"] = _note_texts(notes)
    view["dropped"] = dropped
    if as_json:
        _json(ctx, view)
        return rc
    o = Out(chat)
    o.notes(notes)
    if view["done"]:
        o.say(DONE_SAY)
        o.say("더 다듬고 싶으면 이 맥의 자세한 설정 화면에서 할 수 있어요.")
        o.cmd(" 열기", f"{CMD} open settings")
    elif view["blocked"]:
        o.say(f"[단계 {view['n']}/{view['of']}] {view['title']}")
        o.say(f"막혔어요: {view['say']}")
        _say_stuck(o, view["stuck"], "이렇게 해요:")
        o.say(f"지금 답(프로필): {view['profile_text']}")
        o.cmd("답을 고치려면(바꿀 것만 적어요)", view["profile_form"])
    else:
        _say_step(o, view)
    o.machine(_machine(view))
    o.emit(ctx)
    return rc


# ---- check · confirm -----------------------------------------------------------------------------------------------------

def _step_arg(ctx: ub.Ctx, rm: Roadmap, sid: str) -> Step | None:
    if sid in rm:
        return rm.step(sid)
    ctx.say(f"거절: 모르는 단계 '{_c(sid, SHORT)}' — 있는 단계: " + " · ".join(s.id for s in rm.steps))
    return None


def _answer_counts(pre: Verdict) -> bool:
    """D4 — 지금이 사람의 답을 받을 때인가(확인기가 사람 차례라고 하거나, 이미 사람 답으로 판정했다)."""
    return pre.state in ("human", "ok") or pre.note == "said_no"


def cmd_check(ctx: ub.Ctx, sid: str, as_json: bool, answer: str | None = None) -> int:
    """check 와 confirm(answer 가 있으면) — 인자 검사 → 사실 모으기 → 잠금 → 읽기 · 판정 · 쓰기(D8)."""
    if answer is not None:
        answer = str(answer).strip().lower()
        if answer not in YES + NO:
            ctx.say(f"거절: 답은 yes 또는 no — 받은 것: '{_c(answer, SHORT)}'")
            return 2
    rm = guide.load()
    s = _step_arg(ctx, rm, sid)
    if s is None:
        return 2
    if answer is not None and not s.confirm:
        ctx.say(f"거절: 이 단계에는 사람에게 묻는 것이 없어요 — 확인은 {check_cmd(s.id)}")
        return 2
    facts, notes, up = gather(ctx)
    fd, lerr = _lock(ctx.home)
    if lerr:
        ctx.say(lerr)
        return 1
    try:
        rm, path, p, readable = _load(ctx)
        if not readable:
            if answer is not None:                                      # D6 — 읽지 못한 기록에 답을 덮어쓰지 않는다
                ctx.say(READ_FAIL)
                ctx.say(NOT_WRITTEN)
                return 1
            notes.insert(0, (READ_FAIL, None))
        return _check(ctx, rm, path, p, readable, s, facts, notes, up, as_json, None if answer is None else answer in YES)
    finally:
        _unlock(fd)


def _check(ctx: ub.Ctx, rm: Roadmap, path: Path, p: Progress, readable: bool, s: Step, facts: dict, notes: list, up: bool,
           as_json: bool, answer: bool | None = None) -> int:
    """확인기를 돌리고 적는다(answer 가 있으면 D4 에 따라 사람 답을 먼저). 잠금을 쥔 채 부른다."""
    o = Out()
    head = f"[확인] {s.id} · {_c(s.title)}"
    why = guide.skip_reason(s, p, facts)
    if why is not None:
        view = {"step": s.id, "state": guide.SKIPPED, "say": _c(why), "check": check_cmd(s.id), "next": NEXT_CMD, "needs_person": False,
                "notes": _note_texts(notes)}
        if as_json:
            _json(ctx, view)
            return 0
        o.notes(notes)
        o.say(f"{head} — 건너뜀")
        o.say(f"이 단계는 건너뛰어요: {view['say']}")
        o.cmd("다음", NEXT_CMD)
        o.machine({k: view[k] for k in ("step", "state", "check", "next", "needs_person")})
        o.emit(ctx)
        return 0
    when = now()
    if s.check == "try_tab5":                                           # 표식을 처음 본 때의 그림 번호를 판정보다 먼저(guide_checks)
        p = guide_checks.remember_seq(facts, p, s.id, when)
    checker = guide_checks.CHECKERS[s.check]
    kept = None
    if answer is not None:
        kept = _answer_counts(checker(facts, p, s))
        if kept:
            p = guide.confirm(p, s.id, answer, when)
    v = checker(facts, p, s)
    p = settle(rm, p, s, v, facts, up, when)                           # next 와 같은 되돌림 길
    err = _save(path, p) if readable else NOT_WRITTEN
    if err:
        notes.append((err, None))
    first = _needs_first(rm, p, facts, s)
    view = {"step": s.id, "title": _c(s.title), "state": v.state, "say": _c(v.say), "note": _c(v.note, SHORT),
            "stuck": [] if v.state == "ok" else _stuck(rm, s, v, p, facts), "check": check_cmd(s.id),
            "needs_person": v.state == "human", "needs_first": [_c(t) for t in first], "answer_kept": kept,
            "after": [_c(full(c)) for c in s.after] if v.state != "ok" else [], "next": NEXT_CMD if v.state == "ok" else None,
            "ask": "", "answers": [], "missing": [], "profile_form": None, "notes": _note_texts(notes)}
    if v.state == "human":
        if s.check == "hello" and v.note == "no_profile":
            view["missing"] = _missing_profile(p)
            view["profile_form"] = _profile_cmd(view["missing"])
        else:
            view["ask"] = _c(guide.fill(s.confirm, p)) if s.confirm else _c(v.say)
            view["answers"] = [f"{CMD} guide confirm {s.id} yes", f"{CMD} guide confirm {s.id} no"] if s.confirm else []
    rc = 1 if err else EXIT[v.state]
    if as_json:
        _json(ctx, view)
        return rc
    o.notes(notes)
    if kept is False:
        o.say("답은 적지 않았어요 — 아직 사람에게 물을 차례가 아니에요. 아래 확인 결과부터 봐요.")
    o.say(f"{head} — {STATE_NAME[v.state]}")
    o.say(view["say"])
    if first:
        o.say("참고: 앞 단계가 먼저예요: " + " · ".join(view["needs_first"]))
        o.cmd(" 차례 보기", NEXT_CMD)
    if view["missing"]:
        o.say("아직 답하지 않은 것: " + " · ".join(PROFILE_ASK[k] for k in view["missing"]))
        o.cmd("답을 들으면", view["profile_form"])
        o.cmd("그다음 다시 확인", check_cmd(s.id))
    elif view["ask"]:
        o.say(f"사람에게 물을 것: {view['ask']}")
        for label, c in zip((" 답이 '예'면", " 답이 '아니요'면"), view["answers"]):
            o.cmd(label, c)
    _say_stuck(o, view["stuck"])
    if view["after"]:
        o.say("사람이 '했어요' 한 뒤 이것을 했는지 봐 주세요 — 안 했으면 하고 나서 다시 확인해요:")
        for c in view["after"]:
            o.say(f" {c}")
        o.cmd("다시 확인", check_cmd(s.id))
    if view["next"]:
        o.cmd("다음", view["next"])
    o.machine({k: view[k] for k in ("step", "state", "note", "check", "needs_person", "next")})
    o.emit(ctx)
    return rc


def cmd_confirm(ctx: ub.Ctx, sid: str, answer: str, as_json: bool) -> int:
    return cmd_check(ctx, sid, as_json, answer=answer)


# ---- profile · status · marker -------------------------------------------------------------------------------------------

def _parse_profile(pairs: list[str]) -> tuple[dict, list[str]]:
    """키=값 … → (바꿀 것, 거절 까닭). 값은 소문자 · 앞뒤 빈칸 없이, 목록은 쉼표로 나눠 겹친 것을 한 번만."""
    changes, errors = {}, []
    for arg in pairs:
        if "=" not in arg:
            errors.append(f"키=값 꼴로 적어 주세요: '{_c(arg, SHORT)}'")
            continue
        k, val = (x.strip() for x in arg.split("=", 1))
        k = k.lower()
        if k not in guide.PROFILE:
            errors.append(f"모르는 키 '{_c(k, SHORT)}' — 받는 키: {', '.join(guide.PROFILE)}")
            continue
        if k in guide.LIST_KEYS:
            vals = list(dict.fromkeys(x.strip().lower() for x in val.split(",") if x.strip()))
            bad = [x for x in vals if x not in guide.PROFILE[k]]
            if bad:
                errors.append(f"{k} 에 모르는 값 '{_c(','.join(bad), SHORT)}' — 고를 수 있는 것(쉼표로 여럿): {','.join(guide.PROFILE[k])}")
                continue
            changes[k] = vals
        elif val.lower() not in guide.PROFILE[k]:
            errors.append(f"{k} 는 {'|'.join(guide.PROFILE[k])} 가운데 하나 — 받은 것: '{_c(val, SHORT)}'")
        else:
            changes[k] = val.lower()
    return changes, errors


def cmd_profile(ctx: ub.Ctx, pairs: list[str]) -> int:
    if not pairs:                                                       # 보이기만 — 잠그지 않는다
        rm, path, p, readable = _load(ctx)
        o = Out()
        if not readable:
            o.say(READ_FAIL)
        o.say(f"지금 답(프로필): {_profile_text(p.profile)}")
        missing = _missing_profile(p)
        if missing:
            o.say("아직 답하지 않은 것: " + " · ".join(PROFILE_ASK[k] for k in missing))
        o.cmd("적는 법(바꿀 것만 적어요)", _profile_cmd(guide.PROFILE))
        o.emit(ctx)
        return 0
    changes, errors = _parse_profile(pairs)
    if not errors:
        errors = guide.profile_problems(changes)
    if errors:
        for e in errors:
            ctx.say(f"거절: {_c(e)}")
        return 2
    fd, lerr = _lock(ctx.home)
    if lerr:
        ctx.say(lerr)
        return 1
    try:
        rm, path, p, readable = _load(ctx)
        if not readable:
            ctx.say(READ_FAIL)
            ctx.say(NOT_WRITTEN)
            return 1
        new = {**(p.profile or {}), **changes}
        p = replace(p, profile=new)
        if (err := _save(path, p)):
            ctx.say(err)
            return 1
    finally:
        _unlock(fd)
    facts = dict(guide.FACTS)
    skipped = [s.title for s in rm.steps if guide.skip_reason(s, p, facts) is not None]
    ctx.say(f"저장했어요 — {_profile_text(new)}")
    ctx.say(f"이 답으로 건너뛰는 단계 {len(skipped)}개 · 할 단계 {len(rm.steps) - len(skipped)}개")
    missing = _missing_profile(p)
    if missing:
        ctx.say("아직 답하지 않은 것: " + " · ".join(PROFILE_ASK[k] for k in missing))
    nxt = check_cmd(guide.CONSENT_STEP) if guide.state(p, guide.CONSENT_STEP) != "ok" else NEXT_CMD
    ctx.say(f"다음: {nxt}")
    ctx.say("기계: " + json.dumps({"profile": new, "missing": missing, "next": nxt}, ensure_ascii=False, separators=(",", ":")))
    return 0


def cmd_status(ctx: ub.Ctx, as_json: bool) -> int:
    """엔진에 묻지 않는다 — 기록과 프로필만(다시 확인은 next 가 한다)."""
    rm, path, p, readable = _load(ctx)
    facts = dict(guide.FACTS)
    nxt = guide.next_step(rm, p, facts)
    fin = nxt is None and guide.finished(rm, p, facts)
    blk = guide.blocked(rm, p, facts) if nxt is None and not fin else None
    rows = []
    for s in rm.steps:
        why = guide.skip_reason(s, p, facts)
        st = guide.SKIPPED if why is not None else (guide.state(p, s.id) or "todo")
        e = (p.steps or {}).get(s.id) or {}
        rows.append({"id": s.id, "title": _c(s.title), "state": st, "why": _c(why) if why else "", "note": _c(e.get("note", ""), SHORT),
                     "now": nxt is not None and s.id == nxt.id})
    view = {"steps": rows, "next": nxt.id if nxt else None, "finished": fin,
            "blocked": {"step": blk[0].id, "note": blk[1].note, "say": _c(blk[1].say)} if blk else None, "readable": readable}
    if as_json:
        _json(ctx, view)
        return 0
    if not readable:
        ctx.say(READ_FAIL)
    count = lambda st: sum(1 for r in rows if r["state"] == st)            # noqa: E731
    ctx.say(f"WideEye 설정 길 — 됨 {count('ok')} · 건너뜀 {count(guide.SKIPPED)} · 남음 {len(rows) - count('ok') - count(guide.SKIPPED)}")
    for r in rows:
        state = f"건너뜀 — {r['why']}" if r["state"] == guide.SKIPPED else STATE_NAME.get(r["state"], "아직")
        ctx.say(_c(f"{MARK.get(r['state'], '○')} {r['id']} · {r['title']} — {state}" + (" ← 지금" if r["now"] else "")))
    if fin:
        ctx.say(DONE_SAY)
    elif blk:
        ctx.say(_c(f"막혔어요: {blk[1].say} — 자세히: {NEXT_CMD}"))
    else:
        ctx.say(f"다음: {NEXT_CMD}")
    return 0


def cmd_marker(ctx: ub.Ctx) -> int:
    fd, lerr = _lock(ctx.home)
    if lerr:
        ctx.say(lerr)
        return 1
    try:
        rm, path, p, readable = _load(ctx)
        if not readable:
            ctx.say(READ_FAIL)
            ctx.say(NOT_WRITTEN)
            return 1
        m, made = p.marker, False
        if not _good_marker(m):
            m, made = make_marker(), True
            if (err := _save(path, replace(p, marker=m))):
                ctx.say(err)
                return 1
    finally:
        _unlock(fd)
    o = Out()
    o.say(f"시험 낱말: {m}")
    o.say(f"가족이나 친구에게 부탁해요: 나에게 보내는 글의 맨 앞에 이 낱말을 그대로 써 달라고요(예: '{m} 시험이에요').")
    o.say("글의 맨 앞이어야 해요 — 인사말 뒤나 글 끝에 쓰면 WideEye 가 찾지 못할 수 있어요.")
    o.say("내가 나에게 보낸 글은 '내가 쓴 글'로 보여 치지 않아요. 다른 사람(또는 내 다른 번호 · 메일 주소)이 보내야 해요.")
    o.cmd("보낸 뒤 1분쯤 기다렸다가 확인", check_cmd("try"))
    o.machine({"marker": m, "made": made, "check": check_cmd("try")})
    o.emit(ctx)
    return 0


# ---- 진입점 ------------------------------------------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wideeye guide", description="WideEye 설정 길 — 차례는 wideeye guide next")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("next", help="다음 한 단계")
    n.add_argument("--for-chat", action="store_true", dest="for_chat", help="명령을 못 돌리는 채팅 AI 용 — 붙여 넣을 한 줄씩")
    n.add_argument("--json", action="store_true")
    c = sub.add_parser("check", help="그 단계가 됐는지 확인기로 확인")
    c.add_argument("step")
    c.add_argument("--json", action="store_true")
    f = sub.add_parser("confirm", help="사람의 답을 적고 다시 확인")
    f.add_argument("step")
    f.add_argument("answer", help="yes 또는 no")
    f.add_argument("--json", action="store_true")
    sub.add_parser("profile", help="그 사람에 대한 답 — 키=값 …").add_argument("pairs", nargs="*")
    sub.add_parser("status", help="단계마다 한 줄").add_argument("--json", action="store_true")
    sub.add_parser("marker", help="첫 알림 시험의 시험 낱말")
    return ap


def main(argv: list[str] | None = None, ctx: ub.Ctx | None = None) -> int:
    a = parser().parse_args(sys.argv[1:] if argv is None else argv)
    ctx = ctx or ub.Ctx()
    try:
        if a.cmd == "next":
            return cmd_next(ctx, a.for_chat, a.json)
        if a.cmd == "check":
            return cmd_check(ctx, a.step, a.json)
        if a.cmd == "confirm":
            return cmd_confirm(ctx, a.step, a.answer, a.json)
        if a.cmd == "profile":
            return cmd_profile(ctx, a.pairs)
        if a.cmd == "status":
            return cmd_status(ctx, a.json)
        if a.cmd == "marker":
            return cmd_marker(ctx)
    except guide.RoadmapError:
        ctx.say("설정 길의 단계 정의를 읽지 못했어요 — WideEye 를 다시 깔아 주세요.")        # urgent_board/roadmap.yaml
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
