"""다른 Claude 세션이 이미 다룬 메일을 알아낸다 — Claude Code 의 세션 기록(~/.claude/projects/…/*.jsonl)을 읽기만 한다.

사용자(2026-10-13): "너와 대책을 세웠던 것은 빼줘". 사람이 쓰는 Claude 창이 어떤 메일을 **도구로 다뤘으면**
(스레드 열기 · 답장 초안 · 시트에 옮겨 적기 · 그 id 를 넣은 명령) 그 메일은 이미 챙긴 것으로 보고 화면에서 뺀다.

- 기록에서 꺼내는 것은 도구 호출의 입력에 적힌 16자리 id 와 그때의 시각뿐이다. 대화·본문은 저장하지 않는다.
- 봇의 `claude -p`(entrypoint 가 sdk-…)는 치지 않는다. 아침 브리핑 봇은 모든 메일을 프롬프트에 넣는다 — 그것을 '다룬 것'으로 치면 전부 사라진다.
  표식이 없는 줄도 치지 않는다(사람 세션인지 모르면 보이는 쪽).
- 도구의 **결과**에만 나온 id(목록에 보였을 뿐)와 말로만 언급한 id 는 치지 않는다.
- 파일을 쓰거나 고치거나 찾아보는 도구(Write·Edit·Read·Grep·Glob)에 적힌 id 도 치지 않는다 — 문서에 id 를 적은 것은 그 메일을 다룬 것이 아니다.
- 이 현황판 자체의 폴더에서 일하는 세션은 치지 않는다 — 현황판을 고치며 특정 메일의 id 로 저장소를 들여다본 것이 "다룬 것"이 되면 안 된다.
  다른 폴더에서 한 일이라도 **명령에 현황판의 표식(폴더 이름 · 패키지 이름 · 저장소 파일 이름 · 포트)이 들어 있으면** 치지 않는다.
- 그 밖의 `Bash` 와 시트·메일 도구는 친다. 사용자의 세션이 메일을 다루는 길이 실제로 그것이다(2026-10-13 사흘치 73건: Bash 46 · 시트 쓰기 11 · Gmail 13 · 파일 3) —
  Gmail 도구만 치면 사용자가 빼 달라고 한 것의 대부분을 못 뺀다. 넓게 치는 값은 화면의 "Claude 와 다룬 것 N건 뺌" 표시와 전체 목록으로 치른다.
- 메일이 **도착한 뒤에** 다룬 것만 친다. 같은 대화를 어제 다뤘어도 오늘 새로 온 메일은 아직 챙기지 않은 것이다.
- 파일마다 어디까지 읽었는지 적어 두고 늘어난 만큼만 읽는다(기록은 수백 MB 다).

한계: 창을 열어 둔 채 Claude 가 혼자 되풀이해 돌며 메일을 처리했다면 사용자가 보지 않았어도 '다룬 것'이 된다.
그래서 뺀 것은 전체 목록(/list)에 다룬 시각과 함께 남긴다.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from ..models import Item
from ..store import Store

ID = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{16}(?![0-9a-fA-F])")      # Gmail 의 메시지·스레드 id 꼴(16자리 소문자 16진수)
MARK = b'"tool_use"'
FILE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "Read", "Grep", "Glob"})     # 파일을 쓰거나 찾아보는 도구 — 메일을 다룬 것이 아니다
RULE = 4                                                             # 무엇을 '다룬 것'으로 치는지의 판. 바꾸면 적어 둔 것을 버리고 처음부터 다시 읽는다
RULE_KEY = "__rule__"
PATTERNS = ("*/*.jsonl", "*/*/subagents/*.jsonl")                    # 세션 기록과 그 세션이 시킨 하위 에이전트의 기록


def _inside(path: str, folders) -> bool:
    return any(path == f.rstrip("/") or path.startswith(f.rstrip("/") + "/") for f in folders)


def acted(line: str, ignore_dirs=(), ignore_marks=()) -> tuple[datetime, set[str]] | None:
    """기록 한 줄 → (때, 그 줄의 도구 호출이 입력에 적은 id 들). 사람이 쓰는 세션의 도구 호출이 아니면 None.
    ignore_dirs 아래에서 일하는 세션(이 현황판 자체)과, 입력에 ignore_marks(현황판의 표식)가 든 도구 호출은 치지 않는다."""
    if '"tool_use"' not in line:
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    entry = str(d.get("entrypoint") or "")
    if not entry or entry.startswith("sdk"):
        return None
    if ignore_dirs and _inside(str(d.get("cwd") or ""), ignore_dirs):
        return None
    parts = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
    if not isinstance(parts, list):
        return None
    ids: set[str] = set()
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "tool_use" and p.get("name") not in FILE_TOOLS:
            text = json.dumps(p.get("input"), ensure_ascii=False)
            if any(m and m in text for m in ignore_marks):        # 현황판 자체를 들여다본 명령이다(빈 표식은 어느 글에나 들어 있으니 무시한다)
                continue
            ids.update(ID.findall(text))
    if not ids:
        return None
    try:
        at = datetime.fromisoformat(str(d.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (at, ids) if at.tzinfo else None


def _files(root: Path, since: datetime) -> list[Path]:
    out = []
    for pattern in PATTERNS:
        for f in root.glob(pattern):
            try:
                if f.stat().st_mtime >= since.timestamp():
                    out.append(f)
            except OSError:
                continue
    return out


def scan(root: Path, store: Store, now: datetime, days: int = 7, ignore_dirs=(), ignore_marks=()) -> int:
    """늘어난 만큼만 읽어 '다룬 id → 마지막으로 다룬 때'를 저장한다. 이번에 적은 id 수를 돌려준다."""
    since = now - timedelta(days=days)
    if store.get_scan_pos([RULE_KEY]).get(RULE_KEY) != RULE:      # 치는 기준이 바뀌었다 — 옛 기준으로 적은 것을 버리고 처음부터
        store.reset_touched()
        store.set_scan_pos({RULE_KEY: RULE})
    paths = _files(Path(root), since)
    pos = store.get_scan_pos([str(p) for p in paths])
    found: dict[str, datetime] = {}
    new_pos: dict[str, int] = {}
    for p in paths:
        try:
            size = p.stat().st_size
            start = pos.get(str(p), 0)
            if size < start:                          # 파일이 줄었다(새로 쓰였다) — 처음부터
                start = 0
            if size == start:
                continue
            done = start
            with open(p, "rb") as fh:
                fh.seek(start)
                for raw in fh:
                    if not raw.endswith(b"\n"):       # 마지막 줄이 아직 쓰이는 중 — 다음에 읽는다
                        break
                    done += len(raw)
                    if MARK not in raw:
                        continue
                    got = acted(raw.decode("utf-8", "replace"), ignore_dirs, ignore_marks)
                    if got and got[0] >= since:
                        for i in got[1]:
                            found[i] = max(found.get(i, got[0]), got[0])
            new_pos[str(p)] = done
        except OSError:
            continue
    store.touch_many(found)
    store.set_scan_pos(new_pos)
    store.prune_touched(since)
    return len(found)


def handled(store: Store, items: list[Item]) -> dict[str, datetime]:
    """항목 key → Claude 세션이 그 메일을 다룬 때. 도착한 뒤에 다룬 것만(메일이 아닌 항목은 알 길이 없어 넣지 않는다)."""
    refs = sorted({r for i in items for r in i.refs})
    seen = store.touched_at(refs)
    out: dict[str, datetime] = {}
    for i in items:
        at = max((seen[r] for r in i.refs if r in seen), default=None)
        if at is not None and at > i.received_at:
            out[i.key] = at
    return out
