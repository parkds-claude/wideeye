"""설정 한눈에(/setup)와 설정 명령이 묻는 API(설계 2026-10-16 AI 와 함께 설정 2-3 · 2-5). 읽기만 한다 — 쓰기는 설정 명령이 파일로
(settings_doc.save), 자세한 고치기는 설정 화면(/settings).

- GET /setup           사람이 보는 한 쪽: 단계 · AI 에게 줄 말 · 연결 · 여섯 상자와 기준 · 나. 서버가 그린다(스크립트는 복사 단추 하나).
- GET /api/setup       단계 · 쉬운 말 요약 · AI 에게 줄 말(JSON) — 설정 명령의 status · show 가 쓴다.
- GET /api/setup/places?detail=0|1   지난 7일 받은 곳과 지금 어느 상자인지(설정 명령의 who · try). 이름 · 주소가 들어 있다 —
  모두 접속 열쇠 안쪽이다(web.need_token). 제목 · 글 앞부분은 detail=1 일 때만.
- GET /api/guide/facts   길(설정 안내)의 확인기가 쓰는 사실(urgent_board/guide_checks.py). 비밀 값 · 메시지 내용 없이 — 아래 facts() 참고."""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, render_template, request

from . import board, paths, settings_doc, setup_steps
from .settings_doc import SettingsError

bp = Blueprint("setup", __name__)
PLACES_DAYS = 7


def _ub() -> dict:
    return current_app.config["UB"]


def current() -> tuple[dict, str]:
    """(설정 문서, 읽기 오류) — 파일이 없으면 예시 문서(처음 깐 사람), 깨졌으면 빈 문서와 오류 글."""
    try:
        return settings_doc.read(_ub()["home"])[0], ""
    except FileNotFoundError:
        return settings_doc.example(), ""
    except (OSError, SettingsError) as e:
        return {}, str(e)


def sources(settings) -> dict:
    from .connect import probes
    return probes.status(settings, _ub()["store"].source_status())


def data(claude: bool = True) -> dict:
    """설정 한눈에 · /api/setup 의 자료. claude=True 면 요약을 켰을 때 claude 명령이 있는지 본다(하위 프로세스 — 메뉴 막대의 10초 묻기에서는 부르지 않는다)."""
    s = _ub()["live"].get()
    doc, error = current()
    src = sources(s)
    found = None
    if claude and (doc.get("summary") or {}).get("enabled"):
        found = claude_found()
    st = setup_steps.steps(doc, src, engine_ok=True, claude_found=found, cmd=setup_steps.command(paths.CODE_ROOT))
    return {"steps": st, "describe": setup_steps.describe(doc, src, people_names()), "prompt": setup_steps.prompt_text(paths.CODE_ROOT),
            "command": setup_steps.command(paths.CODE_ROOT), "settings_error": error or s.settings_error, "version": s.version}


def people_names() -> dict[str, str]:
    """보낸 사람 번호(소문자) → 이름 — 지난 30일 저장된 글에서(설정 한눈에가 번호 대신 이름을 보이게)."""
    try:
        return {p["id"].strip().lower(): p["name"] for p in _ub()["store"].catalog(days=30)["people"] if p["name"]}
    except Exception:                                            # noqa: BLE001 — 이름은 덤이다
        return {}


_claude = {"at": 0.0, "found": False}
CLAUDE_TTL = 60                          # 초 — claude --version(하위 프로세스)을 AI 가 status 를 부를 때마다 돌리지 않게


def claude_found(clock=time.monotonic) -> bool:
    if clock() - _claude["at"] > CLAUDE_TTL or not _claude["at"]:
        from .connect import probes
        _claude.update(at=clock(), found=bool(probes.claude().get("found")))
    return _claude["found"]


def left() -> int | None:
    """남은 필수 단계 수 — /api/status(메뉴 막대)가 쓴다. 문서와 수집기 상태 표만 본다. 읽지 못하면 None."""
    try:
        doc, error = current()
        if error:
            return None
        return setup_steps.left_fast(doc, sources(_ub()["live"].get()))
    except Exception:                                            # noqa: BLE001 — 메뉴 막대의 상태 응답을 이것 때문에 깨지 않는다
        return None


@bp.get("/setup")
def page():
    return render_template("setup.html", d=data())


@bp.get("/api/setup")
def api():
    return jsonify(data())


@bp.get("/api/setup/places")
def places():
    ub = _ub()
    s = ub["live"].get()
    detail = request.args.get("detail") in ("1", "true", "yes")
    now = ub["clock"]() if ub.get("clock") else datetime.now(timezone.utc)
    sc = board.screen(ub["store"], s, now, PLACES_DAYS)          # 화면과 같은 길(장부는 쓰지 않는다) — 곳마다 · 상자마다 열린 할 일 수
    keys = frozenset(t.key for t in sc.tasks)
    per_box = {c.name: sum(1 for t in c.tasks if t.kind == "todo") for c in sc.cards}
    rows = setup_steps.places(ub["store"].recent_events(days=PLACES_DAYS), s, now, PLACES_DAYS, detail, keys)
    return jsonify(rows=rows, sorting=setup_steps.sorting(rows, s, box_tasks=per_box), days=PLACES_DAYS, note=setup_steps.NOTE,
                   version=s.version)


@bp.get("/api/setup/version")
def version():
    """지금 엔진이 도는 설정의 판 — 설정 명령이 저장한 뒤 엔진이 읽었는지 볼 때(무거운 /api/status 대신)."""
    live = _ub()["live"]
    s = live.get()
    return jsonify(version=s.version, seen=getattr(live, "seen", s.version), settings_error=s.settings_error)


# ---- 길(설정 안내)의 사실 -----------------------------------------------------------------------------------------------

FEED_PREFIX = 20                         # 알림 줄 글의 앞 몇 자만(시험 낱말 찾기용 — 그 밖의 글은 내보내지 않는다)
GUIDE_STEP_KEYS = ("id", "done", "required")
SOURCE_KEYS = ("imessage", "calls", "teams", "telegram", "kakao", "whatsapp")


def _probe(p: dict, no_access: str) -> dict:
    """연결 점검 하나 → {ok, why, reason}. why 는 probes 가 쓴 고정 문구뿐(경로 · 오류 글이 없다)."""
    return {"ok": bool(p.get("ok")), "why": str(p.get("why") or ""), "reason": "" if p.get("ok") else ("no_access" if p.get("why") == no_access else "no_data")}


def facts() -> dict:
    """길의 사실 — connect(점검 셋 + 수집기 상태 + 열쇠 파일이 있는가), status(1판 설정 단계의 됨 여부), feed_whats(알림 줄 글의 앞 20자),
    tab5(상태 파일의 일부). 비밀 값 · 메일 주소 · 보낸 사람 · 제목 · 본문은 싣지 않는다. 하위 프로세스를 부르지 않는다(claude --version 도).
    이미 있는 만듦새를 쓴다: probes(/api/connect) · setup_steps.steps(/api/setup) · board.screen(/api/items 의 알림)."""
    from .connect import probes
    ub = _ub()
    s = ub["live"].get()
    auth = paths.auth_dir(s.home)
    src = sources(s)
    doc, _ = current()
    steps = setup_steps.steps(doc, src, engine_ok=True, claude_found=None, cmd="wideeye")
    now = ub["clock"]() if ub.get("clock") else datetime.now(timezone.utc)
    orgs = tuple(str(o) for o in s.display.get("greeting_orgs") or [])
    sc = board.screen(ub["store"], s, now, PLACES_DAYS)                    # 알림과 같은 길 · 장부는 쓰지 않는다
    tab5 = None
    try:
        st = json.loads((paths.state_dir(s.home) / "tab5-link.json").read_text(encoding="utf-8"))
        t = st.get("tab5") if isinstance(st.get("tab5"), dict) else None
        up = st.get("updated_at")
        age = round(now.timestamp() - up, 1) if isinstance(up, (int, float)) and not isinstance(up, bool) else None    # 엔진 시계로 잰 파일의 나이 — 창이 죽어도 옛 live 가 남는다
        tab5 = {"connected": bool(st.get("connected")), "sent_seq": st.get("sent_seq"), "orient": st.get("orient"), "age_s": age,
                "tab5": {"board": t.get("board"), "bseq": t.get("bseq")} if t else None}
    except (OSError, ValueError, AttributeError):
        pass
    return {
        "connect": {"messages": _probe(probes.messages_access(), probes.NO_ACCESS), "calls": _probe(probes.calls_access(), probes.NO_ACCESS),
                    "teams": _probe(probes.teams_access(), probes.NO_ACCESS),
                    "sources": {**{k: {"state": (src.get(k) or {}).get("state", "off")} for k in SOURCE_KEYS},
                                "gmail": [{"state": g.get("state", "unknown")} for g in src.get("gmail") or []]},
                    "telegram": {"api": (auth / "telegram.json").exists(), "session": (auth / "telethon.session").exists()},
                    "google_client": (auth / "google-client.json").exists()},
        "status": {"steps": [{k: x[k] for k in GUIDE_STEP_KEYS} for x in steps["steps"]], "left": steps["left"], "next": steps["next"]},
        "feed_whats": [board.feed_what(line, orgs)[:FEED_PREFIX] for line in sc.feed],
        "tab5": tab5,
    }


@bp.get("/api/guide/facts")
def guide_facts():
    return jsonify(facts())
