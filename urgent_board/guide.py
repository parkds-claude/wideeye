"""길(로드맵) — 단계 정의(roadmap.yaml)와 진행 기록으로 '다음 한 단계'를 정한다(설계 2026-10-18 공개판 G4 · G5 · G8 · G16).

- 순서 · 건너뛰기 · 판정 저장은 이 코드가 정한다 — AI 는 그 출력을 사람 말로 풀어 전하고 묻기만 한다(G4).
- 순수: load 뒤의 함수들(next_step · finished · blocked · record · confirm · invalidate_after · pick · stuck_for)은 같은 입력이면
  늘 같은 답이고 받은 기록을 고치지 않는다(새 Progress 를 돌려준다). 시각은 부르는 쪽이 준다.
  파일을 만지는 것은 read_progress · write_progress 둘뿐. 사실 → 판정은 guide_checks(작업 5), 명령은 tools/guide_cli(작업 6).
- 건너뛰기는 저장하지 않고 그때그때 프로필 · 사실로 계산한다 — 프로필을 바꾸면 길이 곧바로 바뀐다.
- 조건(skip_if · 갈림길과 할 일 줄의 if)은 프로필 키(PROFILE)와 사실 키(FACTS)만 쓴다: `{tab5: none}` · `{tab5: [none, later]}` ·
  `{messengers: {lacks: sms}}`(그 메신저를 안 씀) · `{messengers: {has: [sms, gmail]}}`(하나라도 씀) · `{firmware_public: false}`.
  한 조건 안의 키는 모두 맞아야(그리고), 목록의 조건은 하나만 맞아도(또는).
- needs = 앞선 단계가 모두 됨 · 건너뜀, needs_any = 그 가운데 하나라도 '됨'(건너뛴 것은 치지 않는다 — 결정 R5: 첫 알림은 이은 메신저가 있어야)."""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Literal, Mapping

from . import paths

ROADMAP = Path(__file__).with_name("roadmap.yaml")
FILE = "guide.json"
FORMAT = 1                              # 진행 기록 파일의 꼴 판(길의 판 roadmap_version 과 따로)

STATES = ("ok", "fail", "unknown", "human")          # 판정 넷(G4) — 됨 · 안 됨 · 확인 불가 · 사람 확인 필요
SKIPPED = "skipped"
DONE = ("ok", SKIPPED)
WHO = ("ai", "person", "both")
# 확인기 이름(결정 R1 — 작업 5 의 guide_checks.CHECKERS 와 같아야 한다)
KNOWN_CHECKS = ("hello", "install", "permit_messages", "permit_calls", "permit_teams", "link", "try_marker", "tab5_flash", "tab5_show",
                "try_tab5", "setup_step")
# 확인기가 판정에 붙이는 note — 갈림길(stuck.when)이 이 말에 걸린다. 작업 5 는 이 목록 밖의 note 를 내지 않는다(빈 글은 된다).
ENGINE_DOWN = "engine_down"             # WideEye 가 답하지 않음 — install 말고 모든 확인기가 낼 수 있다
UNREACHABLE = "unreachable"             # 길 엔진이 붙인다(blocked): needs_any 가 모두 건너뛰어져 이 단계로 갈 수 없다
NEEDS_FIRST = "needs_first"             # 길 엔진이 붙인다(blocked): 앞 단계가 먼저
KNOWN_NOTES = {
    "hello": ("no_consent", "said_no", "no_profile"),
    "install": ("not_running",),
    "permit_messages": ("no_data", "no_access"),
    "permit_calls": ("no_data", "no_access"),
    "permit_teams": ("no_data", "no_access"),
    "link": ("not_linked", "waiting", "stopped", "reconnect"),
    "try_marker": ("no_marker", "not_yet"),
    "tab5_flash": ("no_tab5", "not_flashed"),
    "tab5_show": ("no_tab5", "not_live"),
    "try_tab5": ("no_tab5", "not_drawn", "said_no"),
    "setup_step": ("left",),
}
GUIDE_COMMANDS = ("next", "check", "confirm", "profile", "status", "marker")    # `wideeye guide …`(작업 6)
# 프로필(G5) — 작업 6 의 `wideeye guide profile` 이 이 값만 받는다
PROFILE = {
    "messengers": ("sms", "gmail", "teams", "calls", "telegram", "other"),
    "iphone": ("yes", "no"),
    "tab5": ("plugged", "none", "later"),
    "ai": ("a", "b", "c"),
    "admin": ("yes", "no"),
}
LIST_KEYS = ("messengers",)
FACTS = {"firmware_public": False}      # 1부: 탭5 프로그램은 아직 공개 전 — 굽기 단계는 '준비 중'으로 건너뛴다(G11)
MESSENGER_SOURCE = {"sms": "imessage", "gmail": "gmail", "teams": "teams", "calls": "calls", "telegram": "telegram"}   # 프로필 → 잇기 확인기가 볼 곳
CONSENT_STEP = "hello"                  # 이 단계의 사람 확인은 허락 기록(consent)에도 남는다(G8)
MARKER = "{marker}"                     # 사람 글에서 채우는 자리 — 시험 낱말(try · try-tab5)
NO_MARKER = "시험 낱말"
SKIP_SAY = "이 단계는 지금 할 필요가 없어요."
UNREACHABLE_SAY = "앞에서 고른 것으로는 이 단계를 할 수 없어요."

REQUIRED = ("id", "title", "why", "who", "do", "check")
OPTIONAL = ("tell", "run", "after", "params", "needs", "needs_any", "skip_if", "stuck", "confirm")
STUCK_KEYS = ("when", "say", "then", "if")
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
COMMAND = "wideeye "


class RoadmapError(ValueError):
    """길 정의가 틀렸다 — problems 에 한 줄씩."""

    def __init__(self, problems: list[str]):
        super().__init__("길 정의(roadmap.yaml)가 틀렸다:\n" + "\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    why: str
    who: str
    do: tuple
    check: str
    tell: tuple = ()
    run: tuple = ()
    after: tuple = ()                    # 사람이 '했어요' 한 뒤, 확인하기 전에 AI 가 부를 명령(예: 허락 뒤 다시 켜기 — G7)
    params: dict = field(default_factory=dict)
    needs: tuple = ()
    needs_any: tuple = ()
    skip_if: tuple = ()
    stuck: tuple = ()
    confirm: str = ""


@dataclass(frozen=True)
class Roadmap:
    version: int
    steps: tuple

    def step(self, sid: str) -> Step:
        for s in self.steps:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def index(self, sid: str) -> int:
        return [s.id for s in self.steps].index(sid)

    def __contains__(self, sid) -> bool:
        return any(s.id == sid for s in self.steps)


@dataclass(frozen=True)
class Verdict:
    state: Literal["ok", "fail", "unknown", "human"]
    say: str
    note: str = ""

    def __post_init__(self):
        if self.state not in STATES:
            raise ValueError(f"판정은 {'/'.join(STATES)} 가운데 하나: {self.state!r}")


@dataclass(frozen=True)
class Progress:
    """진행 기록 — steps: {단계 id: {state, at, note[, confirmed, confirmed_at][, marker_seq, marker_for]}}(marker_* = 탭5 시험 — 표식을 처음 본 때 보낸 그림 번호와 그 낱말, guide_checks.remember_seq), consent: {names, at}(G8 — 받은 곳 이름을 AI 에게 보여도 되나)."""
    version: int = FORMAT
    roadmap_version: int | None = None
    profile: dict = field(default_factory=dict)
    steps: dict = field(default_factory=dict)
    marker: str | None = None
    consent: dict = field(default_factory=dict)


# ---- 길 정의 읽기 ------------------------------------------------------------------------------------------------------

def _strs(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def _cond_problems(cond, where: str, extra: tuple = ()) -> list[str]:
    """조건 하나 — 키는 프로필 · 사실 키만, 값은 그 키가 받는 값만(따옴표 없는 yes/no 는 YAML 이 참/거짓으로 읽는다)."""
    if not isinstance(cond, dict) or not cond:
        return [f"{where}: 조건은 키: 값 꼴"]
    out = []
    for k, v in cond.items():
        if k in extra:
            continue
        if k in LIST_KEYS:
            op = next(iter(v)) if isinstance(v, dict) and len(v) == 1 else None
            vals = v[op] if op in ("has", "lacks") else None
            vals = [vals] if isinstance(vals, str) else vals
            if not (isinstance(vals, list) and vals and all(isinstance(x, str) and x in PROFILE[k] for x in vals)):
                out.append(f"{where}: {k} 는 {{has|lacks: {'|'.join(PROFILE[k])} 또는 그 목록}}")
        elif k in PROFILE:
            vals = v if isinstance(v, list) else [v]
            if not vals or not all(isinstance(x, str) and x in PROFILE[k] for x in vals):
                out.append(f"{where}: {k} 는 {'|'.join(PROFILE[k])}(따옴표로 — YAML 은 yes/no 를 참/거짓으로 읽는다): {v!r}")
        elif k in FACTS:
            if not isinstance(v, bool):
                out.append(f"{where}: {k} 는 true|false: {v!r}")
        else:
            out.append(f"{where}: 모르는 조건 키 {k!r}(프로필 {', '.join(PROFILE)} · 사실 {', '.join(FACTS)})")
    return out


def _line_problems(items, where: str) -> list[str]:
    """사람에게 할 말 줄들 — 글 또는 {if: 조건, say: 글}."""
    if not isinstance(items, list):
        return [f"{where}: 목록이어야 한다"]
    out = []
    for i, x in enumerate(items):
        if isinstance(x, str):
            if not x.strip():
                out.append(f"{where}[{i}]: 빈 글")
        elif isinstance(x, dict) and set(x) == {"if", "say"} and isinstance(x["say"], str) and x["say"].strip():
            out += _cond_problems(x["if"], f"{where}[{i}].if")
        else:
            out.append(f"{where}[{i}]: 글 또는 {{if: 조건, say: 글}}")
    return out


def _step_problems(d, i: int, earlier: list[str]) -> list[str]:
    where = f"steps[{i}]"
    if not isinstance(d, dict):
        return [f"{where}: 단계는 키: 값 꼴"]
    where = f"steps[{i}]({d.get('id')})"
    out = [f"{where}: 알 수 없는 키 {k!r}" for k in d if k not in REQUIRED + OPTIONAL]
    out += [f"{where}: {k} 가 없다" for k in REQUIRED if k not in d]
    sid = d.get("id")
    if not (isinstance(sid, str) and ID_RE.match(sid)):
        out.append(f"{where}: id 는 소문자 · 숫자 · '-'")
    elif sid in earlier:
        out.append(f"{where}: 같은 id 가 이미 있다")
    for k in ("title", "why"):
        if k in d and not (isinstance(d[k], str) and d[k].strip()):
            out.append(f"{where}: {k} 는 빈 글이 아니어야")
    if "confirm" in d and not (isinstance(d["confirm"], str) and d["confirm"].strip()):
        out.append(f"{where}: confirm 은 사람에게 물을 한 문장")
    if "who" in d and d["who"] not in WHO:
        out.append(f"{where}: who 는 {'|'.join(WHO)}")
    if "check" in d and d["check"] not in KNOWN_CHECKS:
        out.append(f"{where}: check {d['check']!r} 는 모르는 확인기({', '.join(KNOWN_CHECKS)})")
    for k in ("do", "tell"):
        if k in d:
            out += _line_problems(d[k], f"{where}.{k}")
    for k in ("run", "after"):
        if k in d and not (_strs(d[k]) and all(c.startswith(COMMAND) for c in d[k])):
            out.append(f"{where}: {k} 는 'wideeye …' 명령 목록")
    if "params" in d and not (isinstance(d["params"], dict) and all(isinstance(k, str) for k in d["params"])):
        out.append(f"{where}: params 는 키: 값 꼴")
    if "needs" in d and not (_strs(d["needs"]) and all(n in earlier for n in d["needs"])):
        out.append(f"{where}: needs 는 앞선 단계 id 목록: {d['needs']!r}")
    if "needs_any" in d and not (_strs(d["needs_any"]) and d["needs_any"]):
        out.append(f"{where}: needs_any 는 단계 id 목록(하나 이상)")
    if "skip_if" in d:
        if not isinstance(d["skip_if"], list):
            out.append(f"{where}: skip_if 는 조건 목록")
        else:
            for j, c in enumerate(d["skip_if"]):
                out += _cond_problems(c, f"{where}.skip_if[{j}]", extra=("say",))
                if isinstance(c, dict) and "say" in c and not (isinstance(c["say"], str) and c["say"].strip()):
                    out.append(f"{where}.skip_if[{j}]: say 는 빈 글이 아니어야")
    if "stuck" in d:
        if not isinstance(d["stuck"], list):
            out.append(f"{where}: stuck 은 갈림길 목록")
        else:
            for j, b in enumerate(d["stuck"]):
                w = f"{where}.stuck[{j}]"
                if not (isinstance(b, dict) and {"when", "say"} <= set(b) and set(b) <= set(STUCK_KEYS)):
                    out.append(f"{w}: 갈림길은 when · say(· then · if)")
                    continue
                if not (isinstance(b["when"], str) and b["when"] and isinstance(b["say"], str) and b["say"].strip()):
                    out.append(f"{w}: when · say 는 빈 글이 아니어야")
                if "then" in b and not (isinstance(b["then"], str) and b["then"].strip()):
                    out.append(f"{w}: then 은 단계 id 또는 'wideeye …' 명령")
                if "if" in b:
                    out += _cond_problems(b["if"], f"{w}.if")
    return out


def _graph_problems(raw: list[dict]) -> list[str]:
    """needs_any · then 이 가리키는 단계가 있는가, needs + needs_any 에 고리가 없는가."""
    ids = [d["id"] for d in raw]
    out = []
    for d in raw:
        for n in d.get("needs_any") or []:
            if n not in ids or n == d["id"]:
                out.append(f"{d['id']}: needs_any {n!r} 는 없는 단계")
        for b in d.get("stuck") or []:
            t = b.get("then")
            if isinstance(t, str) and not t.startswith(COMMAND) and t not in ids:
                out.append(f"{d['id']}: then {t!r} 는 없는 단계(단계 id 또는 'wideeye …')")
    if out:
        return out
    deps = {d["id"]: list(d.get("needs") or []) + list(d.get("needs_any") or []) for d in raw}
    seen, stack = set(), set()

    def visit(n) -> bool:
        if n in stack:
            return True
        if n in seen:
            return False
        seen.add(n)
        stack.add(n)
        cyc = any(visit(m) for m in deps[n])
        stack.discard(n)
        return cyc

    return [f"{n}: needs · needs_any 가 고리를 이룬다" for n in ids if visit(n)][:1]


def load(path: Path | str | None = None) -> Roadmap:
    """길 정의를 읽고 꼴을 검사한다 — 틀리면 RoadmapError(무엇이 틀렸는지 한 줄씩)."""
    import yaml
    f = Path(path) if path is not None else ROADMAP
    try:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise RoadmapError([f"{f.name}: 읽지 못했다 — {type(e).__name__}"]) from None
    if not isinstance(doc, dict) or set(doc) != {"version", "steps"}:
        raise RoadmapError(["맨 위는 version 과 steps 둘"])
    v, raw = doc["version"], doc["steps"]
    problems = [] if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else ["version 은 1 이상의 정수"]
    if not (isinstance(raw, list) and raw):
        raise RoadmapError(problems + ["steps 는 단계 목록(하나 이상)"])
    earlier: list[str] = []
    for i, d in enumerate(raw):
        problems += _step_problems(d, i, earlier)
        if isinstance(d, dict) and isinstance(d.get("id"), str):
            earlier.append(d["id"])
    if not problems:
        problems = _graph_problems(raw)
    if problems:
        raise RoadmapError(problems)
    steps = tuple(Step(id=d["id"], title=d["title"], why=d["why"], who=d["who"], do=tuple(d["do"]), check=d["check"],
                       tell=tuple(d.get("tell") or ()), run=tuple(d.get("run") or ()), after=tuple(d.get("after") or ()),
                       params=dict(d.get("params") or {}), needs=tuple(d.get("needs") or ()), needs_any=tuple(d.get("needs_any") or ()),
                       skip_if=tuple(d.get("skip_if") or ()), stuck=tuple(d.get("stuck") or ()), confirm=d.get("confirm") or "")
                  for d in raw)
    return Roadmap(version=v, steps=steps)


# ---- 조건 · 건너뛰기 · 다음 단계 ---------------------------------------------------------------------------------------

def _ctx(p: Progress, facts: Mapping | None) -> dict:
    """조건을 풀 자리 — 프로필 + 사실(FACTS 키만, 없으면 기본값). facts 에 다른 키(작업 5 의 Facts 통째)가 있어도 된다."""
    ctx = {k: v for k, v in (p.profile or {}).items() if k in PROFILE}
    for k, default in FACTS.items():
        v = facts.get(k, default) if isinstance(facts, Mapping) else default
        ctx[k] = v if isinstance(v, bool) else default
    return ctx


def _matches(cond: Mapping, ctx: Mapping) -> bool:
    for k, want in cond.items():
        if k == "say":
            continue
        have = ctx.get(k)
        if k in LIST_KEYS:
            op, vals = next(iter(want.items()))
            vals = {vals} if isinstance(vals, str) else set(vals)
            got = set(have or ())
            ok = not (got & vals) if op == "lacks" else bool(got & vals)
        elif isinstance(want, list):
            ok = have in want
        else:
            ok = have == want
        if not ok:
            return False
    return True


def _skip(step: Step, ctx: Mapping) -> str | None:
    for c in step.skip_if:
        if _matches(c, ctx):
            return c.get("say") or SKIP_SAY
    return None


def skip_reason(step: Step, p: Progress, facts: Mapping | None = None) -> str | None:
    """프로필 · 사실로 이 단계를 건너뛰면 그 까닭(사람 말), 아니면 None."""
    return _skip(step, _ctx(p, facts))


def state(p: Progress, sid: str) -> str | None:
    e = (p.steps or {}).get(sid)
    return e.get("state") if isinstance(e, dict) else None


def _done(rm: Roadmap, p: Progress, ctx: Mapping, sid: str) -> bool:
    return _skip(rm.step(sid), ctx) is not None or state(p, sid) in DONE


def _real_ok(rm: Roadmap, p: Progress, ctx: Mapping, sid: str) -> bool:
    """needs_any 에 치는 '됨' — 기록이 ok 이고 지금 프로필로 건너뛰지 않는 것(빼 버린 메신저의 옛 '됨'은 치지 않는다)."""
    return state(p, sid) == "ok" and _skip(rm.step(sid), ctx) is None


def _ready(rm: Roadmap, p: Progress, ctx: Mapping, s: Step) -> bool:
    return (all(_done(rm, p, ctx, n) for n in s.needs)
            and (not s.needs_any or any(_real_ok(rm, p, ctx, n) for n in s.needs_any)))


def next_step(rm: Roadmap, p: Progress, facts: Mapping | None = None) -> Step | None:
    """앞에서부터 — 건너뛰지 않고, needs 가 모두 됨 · 건너뜀이고, needs_any 가 있으면 하나라도 됨이며, 자기가 됨 · 건너뜀이 아닌 첫 단계.
    없으면 None — 다 끝났는지 막혔는지는 finished · blocked 로 가린다('끝'이라고 말하기 전에 반드시)."""
    ctx = _ctx(p, facts)
    for s in rm.steps:
        if not _done(rm, p, ctx, s.id) and _ready(rm, p, ctx, s):
            return s
    return None


def finished(rm: Roadmap, p: Progress, facts: Mapping | None = None) -> bool:
    """모든 단계가 됨 · 건너뜀 — 이때만 '끝'이다."""
    ctx = _ctx(p, facts)
    return all(_done(rm, p, ctx, s.id) for s in rm.steps)


def blocked(rm: Roadmap, p: Progress, facts: Mapping | None = None) -> tuple[Step, Verdict] | None:
    """할 단계가 없는데 끝나지도 않았으면 — 막힌 첫 단계와 그 까닭(판정 꼴, note UNREACHABLE · NEEDS_FIRST). 그 밖에는 None.
    예: 부재중 전화만 이은 사람의 첫 알림 시험(글이 없어 시험 낱말을 못 싣는다)."""
    if next_step(rm, p, facts) is not None or finished(rm, p, facts):
        return None
    ctx = _ctx(p, facts)
    s = next(s for s in rm.steps if not _done(rm, p, ctx, s.id))
    if s.needs_any and all(_skip(rm.step(n), ctx) is not None or state(p, n) == SKIPPED for n in s.needs_any):
        return s, Verdict("fail", UNREACHABLE_SAY, UNREACHABLE)
    first = [rm.step(n).title for n in (*s.needs, *s.needs_any) if not _done(rm, p, ctx, n)]
    return s, Verdict("unknown", "앞 단계가 먼저예요: " + " · ".join(first), NEEDS_FIRST)


# ---- 사람 말 고르기 ----------------------------------------------------------------------------------------------------

def fill(text: str, p: Progress) -> str:
    """사람 글의 자리 {marker} 를 채운다(format 을 쓰지 않는다 — 글에 든 다른 중괄호를 건드리지 않게)."""
    return str(text).replace(MARKER, p.marker or NO_MARKER)


def pick(items, p: Progress, facts: Mapping | None = None) -> list[str]:
    """do · tell 줄 가운데 이 사람에게 맞는 것만(조건 줄은 맞을 때만), 자리는 채워서."""
    ctx = _ctx(p, facts)
    out = []
    for x in items:
        if isinstance(x, str):
            out.append(fill(x, p))
        elif _matches(x["if"], ctx):
            out.append(fill(x["say"], p))
    return out


def stuck_for(step: Step, v: Verdict, p: Progress, facts: Mapping | None = None) -> list[dict]:
    """이 판정에 맞는 갈림길 — when 이 판정의 note 또는 state 와 같고, if 가 있으면 프로필이 맞는 것. 적힌 차례대로, {say[, then]}."""
    ctx = _ctx(p, facts)
    out = []
    for b in step.stuck:
        if b["when"] in (v.note, v.state) and ("if" not in b or _matches(b["if"], ctx)):
            out.append({"say": fill(b["say"], p), **({"then": b["then"]} if "then" in b else {})})
    return out


def human_texts(rm: Roadmap) -> list[tuple[str, str, str]]:
    """사람에게 보이는 글 모두 — (단계 id, 칸, 글). 금지 낱말 시험(작업 7)이 이것으로 훑는다."""
    out = []
    for s in rm.steps:
        out += [(s.id, "title", s.title), (s.id, "why", s.why)]
        for k in ("tell", "do"):
            out += [(s.id, k, x if isinstance(x, str) else x["say"]) for x in getattr(s, k)]
        if s.confirm:
            out.append((s.id, "confirm", s.confirm))
        out += [(s.id, "stuck", b["say"]) for b in s.stuck]
        out += [(s.id, "skip", c["say"]) for c in s.skip_if if "say" in c]
    return out


# ---- 판정 기록 · 사람 확인 · 되돌림 -----------------------------------------------------------------------------------

def _iso(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def record(p: Progress, step_id: str, v: Verdict, now: datetime) -> Progress:
    """판정을 적는다 — 그 단계의 사람 확인(confirmed)은 남긴다(다시 판정해도 사람 답은 그대로)."""
    steps = copy.deepcopy(p.steps)
    e = dict(steps.get(step_id) or {})
    e.update(state=v.state, at=_iso(now), note=v.note)
    steps[step_id] = e
    return replace(p, steps=steps)


def confirm(p: Progress, step_id: str, yes: bool, now: datetime) -> Progress:
    """사람 확인을 적는다(판정은 확인기를 다시 돌려서 — 이것만으로 '됨'이 되지 않는다). 시작 단계의 답은 허락 기록에도."""
    steps = copy.deepcopy(p.steps)
    e = dict(steps.get(step_id) or {})
    e.update(confirmed=bool(yes), confirmed_at=_iso(now))
    steps[step_id] = e
    consent = {"names": bool(yes), "at": _iso(now)} if step_id == CONSENT_STEP else copy.deepcopy(p.consent)
    return replace(p, steps=steps, consent=consent)


def invalidate_after(rm: Roadmap, p: Progress, step_id: str, facts: Mapping | None = None) -> Progress:
    """그 단계에 기대는 뒤 단계의 기록을 지운다(설정이 바뀌어 앞 단계가 '됨'이 아니게 됐을 때). 그 단계 자신과 기대지 않는 단계는 그대로.
    needs 로 기대면 늘, needs_any 로 기대면 다른 '됨'이 하나도 남지 않을 때만 — 줄줄이(지운 것에 기대는 것도)."""
    if step_id not in rm:
        raise ValueError(f"모르는 단계: {step_id!r}")
    ctx = _ctx(p, facts)
    lost, changed = {step_id}, True
    while changed:
        changed = False
        for s in rm.steps:
            if s.id in lost:
                continue
            hit = any(n in lost for n in s.needs)
            if not hit and any(n in lost for n in s.needs_any):
                hit = not any(n not in lost and _real_ok(rm, p, ctx, n) for n in s.needs_any)
            if hit:
                lost.add(s.id)
                changed = True
    steps = {k: copy.deepcopy(v) for k, v in p.steps.items() if k == step_id or k not in lost}
    return replace(p, steps=steps)


# ---- 진행 기록 파일 ----------------------------------------------------------------------------------------------------

def progress_file(h: Path | None = None) -> Path:
    """자료 폴더의 state/guide.json."""
    return paths.state_dir(h) / FILE


def profile_problems(profile) -> list[str]:
    """프로필 값 검사 — 모르는 키 · 값은 한 줄씩(작업 6 의 `guide profile` 도 이것으로 거절한다)."""
    if not isinstance(profile, dict):
        return ["프로필은 키: 값 꼴"]
    out = []
    for k, v in profile.items():
        if k not in PROFILE:
            out.append(f"모르는 키 {k!r}({', '.join(PROFILE)})")
        elif k in LIST_KEYS:
            if not (isinstance(v, list) and len(set(v)) == len(v) and all(isinstance(x, str) and x in PROFILE[k] for x in v)):
                out.append(f"{k} 는 {','.join(PROFILE[k])} 가운데 고른 것(겹치지 않게): {v!r}")
        elif not (isinstance(v, str) and v in PROFILE[k]):
            out.append(f"{k} 는 {'|'.join(PROFILE[k])}: {v!r}")
    return out


class _Broken(Exception):
    pass


def _is_str_map(d) -> bool:
    return isinstance(d, dict) and all(isinstance(k, str) for k in d)


def _parse(raw: bytes) -> Progress:
    try:
        d = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _Broken from None
    if not isinstance(d, dict) or d.get("version") != FORMAT or isinstance(d.get("version"), bool):
        raise _Broken
    rv = d.get("roadmap_version")
    if rv is not None and (not isinstance(rv, int) or isinstance(rv, bool)):
        raise _Broken
    profile, steps, consent, marker = d.get("profile", {}), d.get("steps", {}), d.get("consent", {}), d.get("marker")
    if profile_problems(profile) or not _is_str_map(steps) or not _is_str_map(consent):
        raise _Broken
    if marker is not None and not isinstance(marker, str):
        raise _Broken
    clean = {}
    for sid, e in steps.items():
        if not isinstance(e, dict):
            raise _Broken
        if "state" in e and e["state"] not in (*STATES, SKIPPED):
            raise _Broken
        if any(k in e and not isinstance(e[k], str) for k in ("at", "note", "confirmed_at", "marker_for", "marker_at")):
            raise _Broken
        if "marker_seq" in e and (not isinstance(e["marker_seq"], int) or isinstance(e["marker_seq"], bool)):
            raise _Broken
        if "confirmed" in e and not isinstance(e["confirmed"], bool):
            raise _Broken
        clean[sid] = {k: e[k] for k in ("state", "at", "note", "confirmed", "confirmed_at", "marker_seq", "marker_for", "marker_at") if k in e}
    if not all(isinstance(v, (bool, str)) for v in consent.values()):
        raise _Broken
    return Progress(version=FORMAT, roadmap_version=rv, profile=profile, steps=clean, marker=marker, consent=consent)


def _set_aside(path: Path, now: datetime) -> None:
    """깨진 파일을 같은 폴더의 <이름>.bad-<시각> 으로 옮겨 둔다 — 덮어쓰지 않는다(같은 초면 -2, -3 …)."""
    base = f"{path.name}.bad-{now.strftime('%Y%m%d-%H%M%S')}"
    dest, n = path.with_name(base), 1
    while dest.exists():
        n += 1
        dest = path.with_name(f"{base}-{n}")
    os.rename(path, dest)


def read_progress(path: Path | str, roadmap_version: int | None = None, now: datetime | None = None) -> Progress:
    """진행 기록을 읽는다 — 없으면 빈 기록, 깨졌으면(읽지 못함 · UTF-8 아님 · 꼴이 틀림) 옆에 옮겨 두고 빈 기록.
    길의 판이 다르면 단계 기록과 허락은 버리고 프로필 · 시험 낱말만 남긴다(단계가 바뀌었다 — 허락은 다시 묻는다).
    roadmap_version 이 None 이면 지금 길 정의의 판."""
    path = Path(path)
    rv = load().version if roadmap_version is None else roadmap_version
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Progress(roadmap_version=rv)
    except OSError:                                          # 폴더 · 권한 — 옮겨 둘 수 있으면 옮기고 새로, 못 옮기면 그대로 올린다
        _set_aside(path, now or datetime.now())
        return Progress(roadmap_version=rv)
    try:
        p = _parse(raw)
    except _Broken:
        _set_aside(path, now or datetime.now())
        return Progress(roadmap_version=rv)
    if p.roadmap_version != rv:
        return Progress(roadmap_version=rv, profile=p.profile, marker=p.marker)
    return p


def write_progress(path: Path | str, p: Progress) -> None:
    """임시 파일에 다 쓰고 디스크에 내린 뒤 바꿔 놓는다 — 나만 읽게(0600). 받은 사람 이름은 들어 있지 않지만 프로필은 개인 정보다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"version": p.version, "roadmap_version": p.roadmap_version, "profile": p.profile, "steps": p.steps,
                       "marker": p.marker, "consent": p.consent}, ensure_ascii=False, indent=1, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
