"""설정 단계 · 쉬운 말 요약 · 받은 곳 — 설정 명령(`urgentboard setup status|show|who|try`)과 설정 한눈에(/setup)가 함께 쓴다
(설계 2026-10-16 AI 와 함께 설정 2-3 · 2-6). 모두 순수 함수다: 설정 문서 · 수집기 상태 · 저장된 글을 받아 사전을 돌려준다.

- steps: 단계 일곱(엔진 · 나 · 연결 · 상자 · 기준 + 선택 셋)의 됨 · 남음 · 한 줄 · 다음에 할 일. `/api/status` 는 left 만 쓴다(싼 계산만 —
  하위 프로세스를 부르지 않는다. claude 명령이 있는지는 부르는 쪽이 facts 로 넘길 때만 본다).
- places: 지난 7일 **받은 곳**(메일 = 보낸 주소, 1:1 대화 · 문자 = 사람, 카톡 · 텔레그램 단톡 = 방)과 지금 기준으로 어느 상자인지 · 왜.
  분류는 `business.explain` 하나(설정 화면의 분류 시험과 같은 길) — 같은 방 · 사람에게서 물려받기와 Claude 추정은 넣지 않는다(NOTE).
  광고 · 자동 알림만 보낸 곳은 noise 로 표시한다(기준을 잡을 때 보지 않아도 되는 곳)."""
from __future__ import annotations
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import setup_edit
from .models import Event, Source
from .triage import business, pipeline, preprocess

REQUIRED = ("engine", "connect", "me", "boxes", "criteria")
TITLES = {"engine": "엔진", "me": "나", "connect": "연결", "boxes": "상자", "criteria": "기준", "summary": "요약(선택)",
          "display": "탭5(선택)", "hours": "업무시간(선택)"}
ORDER = ("engine", "connect", "me", "boxes", "criteria", "summary", "display", "hours")      # 연결이 나보다 먼저 — 연결한 메일 주소를 '나'에 적는다
GUIDE_STEP = {"engine": "1", "connect": "2", "me": "3", "boxes": "4", "criteria": "5", "summary": "6", "display": "6", "hours": "6"}
SOURCE_NAME = {"gmail": "메일", "imessage": "문자", "telegram": "텔레그램", "kakao": "카톡", "naver": "메일", "calls": "부재중 전화", "teams": "팀즈 알림",
               "whatsapp": "왓츠앱"}
LOCAL_SOURCES = ("calls", "teams")     # 이 맥의 자료(2026-10-17 알림 2단계) — 설정에 적지 않았으면 켬(config.LOCAL_SOURCES 와 같다)
STATE_NAME = {"ok": "정상", "waiting": "수집 전", "stopped": "수집 중단", "reconnect": "다시 연결 필요", "off": "꺼짐"}
BAD = ("stopped", "reconnect")
NOTE = "Claude 가 고르는 사업(요약을 켰을 때 규칙이 아무 데도 못 건 글에만)은 이 목록에 들어 있지 않다 — 현황판에는 들어간다"
NO_NAME = ("", "(unknown)")            # 카톡이 이름 없는 단톡방에 주는 이름
SAMPLE_CHARS = 40
NAME_CHARS = 40                       # 받은 곳 이름(보낸 사람 · 방) — 남이 정한 글이라 자르고 줄 바꿈 · 제어 문자를 지운다(검토: 프롬프트 주입)
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u200b\u200c\u200e\u200f\u202a-\u202e\u2066-\u2069]+")   # 이모지를 잇는 U+200D 는 둔다


def clean(text, limit: int = NAME_CHARS) -> str:
    """남이 정한 글(보낸 사람 이름 · 방 이름 · 제목)을 AI 에게 보이기 전에 — 줄 바꿈 · 제어 · 방향 문자를 빈칸으로, limit 자로 자른다.
    이 출력은 AI 의 입력이 된다: 이름에 넣은 줄 바꿈으로 안내 줄을 흉내 내거나 긴 지시를 넣지 못하게."""
    s = re.sub(r"\s+", " ", CONTROL.sub(" ", str(text or ""))).strip()
    return s[:limit] + ("…" if len(s) > limit else "")


def _src_rows(doc: dict, sources: dict | None) -> list[dict]:
    """켠 출처마다 {이름, 상태} — 엔진이 답하지 않으면(sources None) 설정만 보고 상태는 '모름'."""
    acc = doc.get("accounts") or {}
    out = []
    gm = {g.get("alias"): g for g in (sources or {}).get("gmail") or []}
    for g in acc.get("gmail") or []:
        if not isinstance(g, dict):
            continue
        st = gm.get(g.get("alias"), {}) if sources is not None else {}
        out.append({"source": "gmail", "name": f"메일 {g.get('email', '')}", "state": _state(st), "note": st.get("note", "")})
    # 부재중 전화 · 팀즈 알림(LOCAL_SOURCES)은 적지 않았으면 켜지는 곁가지라 넣지 않는다 — '읽을 곳을 연결했다'로 치면 아무것도 잇지 않은
    # 새 설치가 연결 단계를 지나간다(2026-10-17 — 시험이 잡음). 그 상태는 setup check · doctor 가 보인다. 다만 enabled: true 를 직접 적었으면
    # (길의 잇기 · setup source … on) 넣는다 — web.has_sources 와 같은 규칙(코덱스 검수 2026-11-02, 팀즈만 이은 사람)
    for k in ("imessage", "telegram", "kakao", "whatsapp", *LOCAL_SOURCES):
        on = (acc.get(k) or {}).get("enabled")
        if on is True if k in LOCAL_SOURCES else on:
            st = (sources or {}).get(k) or {} if sources is not None else {}
            out.append({"source": k, "name": SOURCE_NAME[k], "state": _state(st), "note": st.get("note", "")})
    return out


STARTED = "시작"                      # 엔진이 뜰 때 수집기마다 먼저 적는 표시(run_board.announce) — 아직 한 번도 모으지 않았다


def _state(st: dict) -> str:
    """수집기 상태 → 단계 판정의 상태. '시작'만 적힌 것은 첫 수집 전(waiting) — 엔진이 뜨자마자 '정상'으로 보이지 않게(검토 3)."""
    if not st:
        return "unknown"
    if st.get("state") == "ok" and st.get("note") == STARTED:
        return "waiting"
    return str(st.get("state") or "unknown")


def _step(sid: str, done: bool, line: str, todo: str = "", notes: list[str] | None = None) -> dict:
    return {"id": sid, "title": TITLES[sid], "required": sid in REQUIRED, "done": bool(done), "line": line, "todo": "" if done else todo,
            "notes": notes or [], "guide": GUIDE_STEP[sid]}


def steps(doc: dict, sources: dict | None, *, engine_ok: bool = True, claude_found: bool | None = None, cmd: str = "urgentboard",
          lenient: bool = False) -> dict:
    """{"steps": [...], "next": 단계 id 또는 None, "left": 남은 필수 단계 수}. claude_found 가 None 이면 요약 단계에서 claude 를 보지 않는다.
    cmd: "다음"에 적을 관리 명령(전체 경로 — PATH 에 없다). lenient: 연결은 켠 출처가 있는지만 본다(메뉴 막대 — 멈춤은 '수집 중단' 줄의 몫).
    sources 가 None 이면(엔진이 답하지 않음) 연결 상태를 모른다 — 켠 출처가 있으면 됨으로 친다(엔진 단계가 남음으로 막는다)."""
    me = doc.get("me") or {}
    out = [_step("engine", engine_ok, "엔진이 답한다" if engine_ok else "엔진이 답하지 않는다",
                 f"{cmd} start 로 띄우고, 그래도 안 되면 {cmd} doctor")]

    rows = _src_rows(doc, sources)
    bad = [r for r in rows if r["state"] in BAD]
    early = [r for r in rows if r["state"] == "waiting"]
    line = " · ".join(f"{r['name']} {STATE_NAME.get(r['state'], '상태 모름')}" for r in rows) or "켠 출처가 없다"
    if not rows:
        todo = "읽을 곳을 하나 이상 연결한다(안내서 2단계)"
    elif bad:
        todo = f"{bad[0]['name']}: {STATE_NAME[bad[0]['state']]} — " + (bad[0]["note"] or f"{cmd} setup check")
    else:
        todo = f"{early[0]['name'] if early else ''} 첫 수집을 기다린다(문자 10초 · 카톡 30초 · 텔레그램 2분 · 메일 5분) — 뒤에 {cmd} setup status"
    done = bool(rows) and (lenient or sources is None or not (bad or early))
    out.append(_step("connect", done, line, todo))

    names = [str(x) for x in me.get("names") or []]
    missing = setup_edit.missing_addresses(doc)
    line = (f"나를 부르는 말: {', '.join(names)}" if names else "나를 부르는 말이 없다") + f" · 내 메일 주소 {len(me.get('addresses') or [])}개"
    todo = (f"나를 부르는 말을 적는다: {cmd} setup me --name <이름> --name <직함>" if not names else
            f"연결한 메일 주소를 '나'에 더한다: {cmd} setup me --email {missing[0]}" if missing else "")
    out.append(_step("me", names and not missing, line, todo))

    act = setup_edit.active(doc)
    real = [b for b in act if not setup_edit.is_untouched_example(b)]
    line = (f"진행 중 {len(real)}개: " + " · ".join(str(b.get('name')) for b in real) + f" + {setup_edit.etc_name(doc)}") if real else "진행 중인 상자가 없다"
    notes = [f"진행 중인 상자가 {len(act)}개 — 화면에는 다섯 칸, 넘친 것은 여섯째 칸('그 밖')에 함께 뜬다"] if len(act) > setup_edit.OWN_MAX else []
    out.append(_step("boxes", bool(real), line, f"상자를 만든다(안내서 4단계): {cmd} setup box add <이름>", notes))

    empty = [str(b.get("name")) for b in real if setup_edit._rules_of(b) == 0]
    no_about = [str(b.get("name")) for b in real if not str(b.get("about") or "").strip()]
    line = ("상자마다 기준이 있다" if real and not empty else f"기준이 없는 상자: {', '.join(empty)}" if empty else "상자부터")
    notes = [f"설명이 빈 상자: {', '.join(no_about)} — Claude 가 사업을 고를 때 쓴다(권장)"] if no_about and real else []
    out.append(_step("criteria", bool(real) and not empty, line, "상자마다 기준을 잡는다(안내서 5단계)", notes))

    summ = doc.get("summary") or {}
    on = bool(summ.get("enabled"))
    if on and claude_found is False:
        out.append(_step("summary", False, "켬 — 그런데 claude 명령이 없다", f"Claude Code 를 깔고 로그인하거나 {cmd} setup summary off"))
    else:
        out.append(_step("summary", True, "켬 — 화면에 뜰 메시지의 글이 Claude 로 간다" if on else "꺼짐 — 할 일 문구는 글 앞부분으로"))
    serial = str((doc.get("display") or {}).get("tab5_usb_serial") or "")
    out.append(_step("display", True, f"탭5 일련번호 {serial}" if serial else f"쓰지 않음 — 현황판은 브라우저로({cmd} open board)"))
    wh = doc.get("workhours") or {}
    lunch = wh.get("lunch") or ["12:00", "13:00"]
    out.append(_step("hours", True, f"평일 {wh.get('start', '09:00')}~{wh.get('end', '18:00')} · 점심 {lunch[0]}~{lunch[1]} · 공휴일 {wh.get('holidays', 'KR') or '없음'}"))

    left = [s for s in out if s["required"] and not s["done"]]
    nxt = left[0]["id"] if left else next((s["id"] for s in out if not s["done"]), None)
    return {"steps": out, "next": nxt, "left": len(left)}


def left_fast(doc: dict, sources: dict | None) -> int:
    """메뉴 막대용 — 남은 필수 단계 수. 엔진은 답하고 있고, 연결은 켠 출처가 있는지만 본다(잠깐 멈춘 수집기로 "설정이 남았다"를 띄우지 않는다)."""
    return steps(doc, sources, engine_ok=True, lenient=True)["left"]


def command(code_root) -> str:
    """관리 명령의 전체 경로(빈칸이 있어도 셸에 그대로 붙여 넣을 수 있게 따옴표)."""
    p = f"{code_root}/bin/urgentboard"
    return f'"{p}"' if any(c.isspace() for c in p) else p


def _q(p: str) -> str:
    return f'"{p}"' if any(c.isspace() for c in p) else p


def prompt_text(code_root) -> str:
    """AI 에게 줄 말 — 사람이 쓰는 AI 창에 붙여 넣는 한 단락(설정 한눈에 · `setup prompt` · 메뉴 막대가 같은 글을 쓴다).
    START.md 의 첫 한마디와 같은 길: 길 명령의 출력만 따른다 → GUIDE.md → `wideeye guide next`(마지막 검토 I2 — 처음 설정 입구는 하나).
    길이 끝난 뒤 상자 · 기준을 고칠 때만 `setup guide`(SETUP.md). 접속 열쇠 · 개인 자료는 넣지 않는다(코드 폴더 경로뿐)."""
    we = _q(f"{code_root}/bin/wideeye")
    guide_md = _q(f"{code_root}/GUIDE.md")
    return ("길 명령의 출력만 따라 주세요. WideEye 의 길 명령(wideeye guide)이 내는 출력만 따르고, 당신이 아는 다른 방법으로 길을 만들지 마세요.\n"
            f"1. 먼저 이 맥에 있는 안내서를 읽어 주세요: {guide_md} — WideEye 는 이미 깔려 있으니 설치 한 줄은 건너뛰어요.\n"
            f"2. `{we} guide next` 를 불러 그 출력이 시키는 차례대로, 나에게 한 번에 하나씩 쉬운 말로 물어봐 주세요.\n"
            f"3. 길이 '끝'이라고 한 뒤에 상자 · 기준을 고치고 싶으면 `{we} setup guide` 로 설정 안내서를 읽고 `{we} setup …` 명령으로만 고쳐 주세요.\n"
            "4. 비밀번호와 로그인 코드는 나에게 묻지 마세요. 그런 것은 내가 이 맥의 화면에 직접 넣을게요.\n"
            f"5. 명령을 직접 실행할 수 없는 AI 라면 `{we} guide next --for-chat` 부터 한 줄씩 주세요 — 내가 터미널에서 돌리고 결과를 붙여 넣을게요"
            "(안내서 파일을 열 수 없으면 그렇다고 말해 주세요. 내가 붙여 넣을게요).")


# ---- 쉬운 말 요약 -----------------------------------------------------------------------------------------------------

def _named(ids, names: dict[str, str]) -> list[dict]:
    """보낸 사람 값 → {id, name} — 이름은 저장된 글에서 본 것(텔레그램 번호 · 전화번호만으로는 누군지 모른다). 모르면 빈 글."""
    return [{"id": str(x), "name": clean(names.get(str(x).strip().lower(), ""))} for x in ids or []]


def describe(doc: dict, sources: dict | None, names: dict[str, str] | None = None) -> dict:
    """지금 설정을 사람이 읽을 꼴로 — 상자 여섯(차례는 화면과 같다: 진행 중 다섯 + 개인 · 기타). names: 보낸 사람 번호(소문자) → 이름."""
    names = names or {}
    me = doc.get("me") or {}
    own, extra = [], []
    for b in setup_edit.boxes(doc):
        rooms = [{"source": src, "id": str(r.get("id") or ""), "name": clean(r.get("name")), "watch": bool(r.get("watch"))}
                 for src, f in (("kakao", "kakao_rooms"), ("telegram", "telegram_rooms")) for r in b.get(f) or [] if isinstance(r, dict)]
        row = {"key": str(b.get("key")), "name": str(b.get("name") or b.get("key")), "color": str(b.get("color") or ""),
               "active": b.get("active", True) is True, "about": str(b.get("about") or ""), "accounts": list(b.get("mail_accounts") or []),
               "senders": list(b.get("senders") or []), "senders_named": _named(b.get("senders"), names), "rooms": rooms,
               "keywords": list(b.get("keywords") or []),
               "example": setup_edit.is_untouched_example(b)}
        (own if row["active"] and len(own) < setup_edit.OWN_MAX else extra).append(row)
    return {
        "me": {"names": list(me.get("names") or []), "addresses": list(me.get("addresses") or [])},
        "sources": [{**r, "state_text": STATE_NAME.get(r["state"], "상태 모름")} for r in _src_rows(doc, sources)],
        "boxes": own,
        "extra": extra,                                         # 쉬는 상자 · 다섯을 넘친 상자(넘친 것은 여섯째 칸에 함께)
        "etc": {"name": setup_edit.etc_name(doc), "senders": list(me.get("personal_senders") or []),
                "senders_named": _named(me.get("personal_senders"), names),
                "accounts": list(me.get("personal_accounts") or []),
                "watch_rooms": [{"source": str(w.get("source")), "id": str(w.get("chat_id"))} for w in doc.get("watch_rooms") or [] if isinstance(w, dict)]},
        "unconfirmed": [{"name": str(u.get("name")), "keywords": list(u.get("keywords") or [])} for u in doc.get("unconfirmed") or [] if isinstance(u, dict)],
    }


# ---- 받은 곳 -----------------------------------------------------------------------------------------------------------

def _unit(e: Event) -> tuple[str, str, str]:
    """(종류, 출처, 번호) — 카톡 · 텔레그램 단톡은 방, 그 밖(메일 · 1:1 · 문자 — 문자 단톡도)은 보낸 사람(설정에 넣을 수 있는 단위)."""
    if e.is_group and e.source in (Source.KAKAO, Source.TELEGRAM):
        return "room", e.source.value, str(e.chat_id)
    return "sender", e.source.value, str(e.sender_id or e.chat_id)


def _sample(e: Event) -> str:
    """가장 최근 글의 제목(메일) · 앞부분 — 텔레그램은 내지 않는다(API 약관 — 요약도 기본에서 뺐다, config.SUMMARY_DEFAULTS)."""
    if e.source == Source.TELEGRAM:
        return ""
    raw = e.raw or {}
    return clean(raw.get("subject") or e.text or "", SAMPLE_CHARS)


def _room_name(es: list[Event]) -> str:
    """방 이름 — 이름 없는 단톡방(카톡의 '(unknown)')은 말한 사람으로 "이름 없는 방: 김 · 이 외 3명"."""
    name = clean(es[-1].chat_name)
    if name not in NO_NAME:
        return name
    who = list(dict.fromkeys(clean(e.sender_name or e.sender_id, 12) for e in reversed(es) if (e.sender_name or e.sender_id)))
    return "이름 없는 방: " + " · ".join(who[:2]) + (f" 외 {len(who) - 2}명" if len(who) > 2 else "") if who else "이름 없는 방"


def box_namer(settings) -> callable:
    """사업 key → 화면의 상자 이름 — 진행 사업 다섯까지는 제 이름, 그 밖 · 쉬는 사업 · 개인 · 기타는 여섯째 상자."""
    own = [b for b in settings.businesses if b.active][:setup_edit.OWN_MAX]
    names = {b.key: b.name for b in own}
    return lambda key: names.get(key, settings.etc_name)


def places(events: list[Event], settings, now: datetime, days: int = 7, detail: bool = False, task_keys: frozenset = frozenset()) -> list[dict]:
    """받은 곳 — 열린 할 일이 많은 곳 → 글이 많은 곳 → 최근 곳 차례. 곳마다 {kind, source, id, name, count, unread, tasks, last, box_key,
    box, why, unsorted, mixed, noise, sample?}. 사업은 판정과 같은 길(pipeline.business_map — 규칙 + 물려받기). task_keys 는 열린 할 일의
    마지막 글 key(화면과 같은 길 board.screen 이 준다) — 곳마다 '할 일이 된 수'를 센다."""
    since = now - timedelta(days=days)
    # 알림에만 들어가는 글(부재중 전화 · 팀즈 · 봇 — pipeline.feed_only)은 받은 곳이 아니다: 상자에 등록하면 같은 번호의 문자가 끌려간다(독립 검토 2026-10-17)
    evs = [e for e in events if e.received_at >= since and not pipeline.feed_only(e)]
    tags, replied = pipeline.business_map(evs, settings)
    watch = getattr(settings, "watch_rooms", None) or frozenset()
    name_of = box_namer(settings)
    groups: dict[tuple, list[Event]] = defaultdict(list)
    for e in evs:
        if not e.is_from_me:
            groups[_unit(e)].append(e)
    out = []
    for (kind, src, pid), es in groups.items():
        es.sort(key=lambda e: e.received_at)
        verdicts = [tags[e.key] for e in es]
        keys = Counter(v[0] for v in verdicts)
        top = max(keys, key=lambda k: (keys[k], max(i for i, v in enumerate(verdicts) if v[0] == k)))
        why = next(v[1] for v in reversed(verdicts) if v[0] == top)
        last = es[-1]
        watched = kind == "room" and (src, pid) in watch
        noise = not watched and all(preprocess.junk_reason(e, settings.rules, replied) for e in es)
        row = {"kind": kind, "source": src, "source_name": SOURCE_NAME.get(src, src), "id": clean(pid, 320),     # 설정에 넣을 값 — 줄 바꿈만 지우고 자르지 않는다(주소 최대 길이)
               "name": _room_name(es) if kind == "room" else clean(last.sender_name or pid),
               "count": len(es), "unread": sum(1 for e in es if not e.is_read), "tasks": sum(1 for e in es if e.key in task_keys),
               "last": last.received_at.isoformat(), "box_key": top, "box": name_of(top), "why": why, "unsorted": why == business.NOTHING,
               "mixed": {name_of(k): n for k, n in keys.items()} if len({name_of(k) for k in keys}) > 1 else {},
               "noise": noise, "watched": watched}
        if detail:
            row["sample"] = _sample(last)
        out.append(row)
    out.sort(key=lambda r: r["last"], reverse=True)              # 같으면 최근 곳이 위
    out.sort(key=lambda r: (-r["tasks"], -r["count"]))
    return out


def sorting(rows: list[dict], settings, top: int = 10, box_tasks: dict[str, int] | None = None) -> dict:
    """나뉜 모양 — 상자마다 곳 · 글 수 · 열린 할 일 수(화면의 상자와 같은 수 — box_tasks), 알림 · 광고만 보낸 곳은 따로,
    어디에도 안 걸린 곳 위 top(할 일이 된 곳 먼저)."""
    name_of = box_namer(settings)
    order = [name_of(b.key) for b in settings.businesses if b.active][:setup_edit.OWN_MAX] + [settings.etc_name]
    per = {n: {"box": n, "places": 0, "messages": 0, "tasks": (box_tasks or {}).get(n, 0)} for n in order}
    noise = {"places": 0, "messages": 0}
    for r in rows:
        if r["noise"]:
            noise["places"] += 1
            noise["messages"] += r["count"]
            continue
        p = per.setdefault(r["box"], {"box": r["box"], "places": 0, "messages": 0, "tasks": (box_tasks or {}).get(r["box"], 0)})
        p["places"] += 1
        p["messages"] += r["count"]
    people = [r for r in rows if not r["noise"]]
    unsorted = [r for r in people if r["unsorted"]]
    return {"boxes": list(per.values()), "noise": noise, "people": len(people), "unsorted": len(unsorted),
            "unsorted_messages": sum(r["count"] for r in unsorted), "unsorted_top": unsorted[:top], "note": NOTE}
