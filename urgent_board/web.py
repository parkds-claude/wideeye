"""웹: 화면(GET /) · 항목 JSON(GET /api/items) · 처리됨(POST /api/dismiss) · 헬스(GET /healthz). 127.0.0.1 전용."""
from __future__ import annotations
import hashlib
import hmac
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request

from .models import Grade
from .store import Store
from . import board, paths, summarize, tasks as taskbook, web_settings, web_setup
from .board import MAIL, SOURCE_LABEL, SOURCE_ORDER      # 출처 이름은 board 가 정한다(화면에 낼 것 · 상자는 board.screen)
from .workhours import KST

DISMISS_DAYS = 14
STALE_MIN_SECONDS = 600           # 수집 기록이 이보다(그리고 주기의 3배보다) 오래되면 멈춘 것으로 본다
LOOKBACK_DAYS = 7
PACKAGE_DIR = Path(__file__).resolve().parent


def waited_text(business_min: int, wall_min: int) -> str:
    """기다린 시간(업무시간 기준). 업무시간이 아직 흐르지 않았으면 방금/업무시간 밖 수신."""
    if business_min >= 60:
        h, m = divmod(business_min, 60)
        return f"기다린 {h}시간 {m}분" if m else f"기다린 {h}시간"
    if business_min >= 1:
        return f"기다린 {business_min}분"
    return "방금" if wall_min < 2 else "업무시간 밖 수신"


def deadline_text(deadline: datetime | None, now: datetime) -> str:
    if deadline is None:
        return ""
    d, n = deadline.astimezone(KST), now.astimezone(KST)
    days = (d.date() - n.date()).days
    day = "오늘" if days == 0 else "내일" if days == 1 else d.strftime("%m/%d")
    return f"{day} {d.strftime('%H:%M')}"


PICTOGRAPH = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


ACCOUNT_MAX = 12


def source_text(source: str, accounts: list[str], labels: dict[str, str]) -> str:
    """출처 꼬리표. 메일은 어느 계정인지 함께("메일 업무"), 같은 메일이 여러 계정에 왔으면 "+N"."""
    base = SOURCE_LABEL.get(source, source)
    if source not in MAIL or not accounts:
        return base
    first = accounts[0]
    name = labels.get(first) or first.split("@", 1)[0][:ACCOUNT_MAX]
    return f"{base} {name}" + (f" +{len(accounts) - 1}" if len(accounts) > 1 else "")


def where_text(chat_name: str, sender_name: str, source: str) -> str:
    """어느 방인지. 1:1(방 이름 = 상대 이름)이나 메일(제목이 요약에 이미 있음)은 비운다. 그림 문자는 뺀다(흑백 화면)."""
    if source in MAIL or not chat_name or chat_name == sender_name:
        return ""
    return re.sub(r"\s+", " ", PICTOGRAPH.sub("", chat_name)).strip()


SENDER_MAX = 16
BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|（[^）]*）")
KR_MOBILE = re.compile(r"^\+82(1\d)(\d{3,4})(\d{4})$")


def sender_text(name: str) -> str:
    """화면에 낼 상대 이름. 소속 괄호를 떼고, 주소는 @ 앞만, 번호는 010-0000-0000 꼴로, 길면 줄인다(요약이 보일 자리를 남긴다)."""
    t = (name or "").strip()
    m = KR_MOBILE.match(t.replace("-", "").replace(" ", ""))
    if m:
        return f"0{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if "@" in t and " " not in t:
        t = t.split("@", 1)[0]
    t = re.sub(r"\s+", " ", BRACKETS.sub(" ", t)).strip() or re.sub(r"[()\[\]（）]", "", t).strip()
    if not t:
        return "(이름 없음)"
    return t if len(t) <= SENDER_MAX else t[:SENDER_MAX] + "…"


ASSETS = ("static/board.js", "static/board.css", "templates/board.html")


def asset_version(package_dir: Path) -> str:
    """탭5 화면 코드의 판 — 파일의 크기·바뀐 때로 만든다. 화면은 이 값이 달라지면 스스로 새로 읽는다(창을 다시 띄우지 않아도 반영된다)."""
    parts = []
    for rel in ASSETS:
        try:
            st = (Path(package_dir) / rel).stat()
            parts.append(f"{rel}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{rel}:-")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


AUX_SOURCES = ("calls", "teams")           # 곁가지(부재중 전화 · 팀즈 알림 — 처음부터 켜져 있다). 멈춰도 탭5 · 메뉴 막대의 '멈춤'으로 치지 않는다


def _main(sources: dict[str, dict]) -> dict[str, dict]:
    """곁가지를 뺀 수집기들(독립 검토 2026-10-17: 권한이 막힌 맥에서 켠 적도 없는 곁가지 때문에 본 화면이 '일부 멈춤'이 됐다).
    곁가지의 멈춤은 설정 화면의 연결 칸 · doctor 가 보인다."""
    return {k: v for k, v in sources.items() if k.split(":")[0] not in AUX_SOURCES}


def blindness(sources: dict[str, dict]) -> str:
    """수집기가 얼마나 멈췄나: "all"(전부) · "some"(일부) · ""(없음, 또는 아직 기록이 하나도 없음).
    전부 멈췄는데 "긴급 없음"이라고 보이는 것이 가장 나쁜 고장이라 화면이 이 값으로 말을 바꾼다. 곁가지(AUX_SOURCES)는 세지 않는다."""
    sources = _main(sources)
    if not sources:
        return ""
    down = sum(1 for s in sources.values() if not s.get("ok") or s.get("stale"))
    return "all" if down == len(sources) else "some" if down else ""


NOTHING_WIRED = "모든 출처"                 # 화면이 뒤에 " 수집 중단"을 붙인다 — "모든 출처 수집 중단"


def has_sources(settings) -> bool:
    """수집할 곳이 하나라도 켜져 있는가(메일 계정 · 텔레그램 · 카톡 · 문자 · 왓츠앱). 부재중 전화 · 팀즈 알림은 적지 않았으면 켜지는 곁가지라
    치지 않는다 — 메시지를 하나도 잇지 않았는데 초록 "없음"이 뜨면 안 된다(2026-10-17). 다만 설정에 enabled: true 를 직접 적었으면(길의 잇기 ·
    setup source … on) 친다 — 팀즈만 이은 사람의 화면이 "모든 출처 수집 중단"으로 뜨던 것(코덱스 검수 2026-11-02)."""
    a = getattr(settings, "accounts", None) or {}
    return (bool(a.get("gmail")) or any((a.get(k) or {}).get("enabled") for k in ("telegram", "kakao", "imessage", "whatsapp"))
            or any((a.get(k) or {}).get("enabled") is True for k in ("calls", "teams")))


def down_labels(sources: dict[str, dict]) -> list[str]:
    """멈춘 수집기의 화면 표시 이름. 메일은 계정 수로 묶는다. 곁가지(AUX_SOURCES)는 적지 않는다."""
    n = Counter(name.split(":")[0] for name, s in _main(sources).items() if not s.get("ok") or s.get("stale"))
    out = []
    for kind in SOURCE_ORDER:
        if n.get(kind):
            out.append(f"메일 {n[kind]}계정" if kind == "gmail" else SOURCE_LABEL[kind])
    out.extend(sorted(k for k in n if k not in SOURCE_ORDER))
    return out


SKIP_LABEL = {"ad": "광고", "bulk": "대량 발송", "notice": "자동 알림", "auto": "자동 발송", "cc": "참조·단체 메일", "group": "단톡방 일반 글",
              "noise": "인사·스티커", "answered": "내가 그 뒤에 답함",
              "read_elsewhere": "다른 메일함에서 읽음"}
SKIP_ORDER = ("answered", "read_elsewhere", "cc", "group", "noise", "notice", "auto", "bulk", "ad")
LOCAL_HOSTS = ("127.0.0.1", "localhost")
OPEN_PATHS = ("/healthz", "/static/", "/auth", "/connect/gmail/callback")     # 접속 열쇠 없이 열리는 길(감시 · 글꼴 · 열쇠 받기 · Google 이 돌아오는 길)
COOKIE = "ub"
ONE_YEAR = 365 * 24 * 3600
KEY_MAX = 200
SAFE_LINK = ("https://mail.google.com/", "tg://", "imessage://", "sms://", "whatsapp://")
SKIPPED_MAX = 200


def safe_link(link: str) -> str:
    """원문을 여는 주소. 우리 수집기가 만든 꼴(Gmail · 텔레그램 · 메시지 앱)만 내보낸다."""
    return link if link and link.startswith(SAFE_LINK) else ""


def received_text(at: datetime, now: datetime) -> str:
    d, n = at.astimezone(KST), now.astimezone(KST)
    days = (n.date() - d.date()).days
    day = "오늘" if days == 0 else "어제" if days == 1 else d.strftime("%m/%d")
    return f"{day} {d.strftime('%H:%M')}"


CARD_SENDER_MAX = 10


def card_sender(name: str) -> str:
    """상자에 적는 보낸 사람 — 짧게(요약이 보일 자리를 남긴다). 이름 뒤에 소속·직함을 붙인 긴 표시 이름("홍길동 ○○협회 사무국장")은
    이름만 남기고, 그 밖의 긴 이름은 낱말 사이에서 자른다("Dr. Tanaka Hiroshi" → "Dr. Tanaka" — 낱말 가운데서 끊지 않는다).
    전화번호는 줄이지 않는다(끝이 잘리면 누구인지 모른다)."""
    t = sender_text(name)
    if len(t) <= CARD_SENDER_MAX or KR_MOBILE.match((name or "").strip().replace("-", "").replace(" ", "")):
        return t
    words = t.split(" ")
    if 2 <= len(words[0]) <= 4 and all("가" <= ch <= "힣" for ch in words[0]):
        return words[0]
    if len(words[0]) > CARD_SENDER_MAX:
        return words[0][:CARD_SENDER_MAX] + "…"
    out = words[0]
    for word in words[1:]:
        if len(out) + 1 + len(word) > CARD_SENDER_MAX:
            break
        out += " " + word
    return out


CLOSED_LABEL = {"reply": "내가 답함", "dismiss": "처리됨", "read": "읽음 · 할 일 아님", "not_needed": "할 일 없다고 판단", "handled": "Claude 와 다룸"}
TASKS_ON_CARD = 2                  # 할 일 상자(cards — 메뉴 막대 · 2026-10-17 전의 탭5) 하나에 싣는 할 일 줄
FEED_ON_CARD = 3                   # 탭5 알림 상자 하나에 보이는 줄(D14 — 사용자 2026-10-18 "각 셀(사업)의 본문을 3개 알림이 들어가도록". 그 전 2, board.css 의 줄 높이와 함께 바꾼다)
TASKS_ON_STATUS = 10               # 메뉴 막대 차림표에 보이는 할 일


def due_label(due, now: datetime) -> str:
    """할 일의 기한 글 — "오늘까지" · "내일까지" · "10/23까지", 지났으면 "10/23 지남"."""
    if not due:
        return ""
    today = now.astimezone(KST).date()
    md = f"{due.month}/{due.day}"
    if due < today:
        return f"{md} 지남"
    return "오늘까지" if due == today else "내일까지" if (due - today).days == 1 else f"{md}까지"


def task_who(t) -> str:
    """할 일 둘째 줄의 앞 — 어디로 · 누가("메일 탁새봄", 방 묶음은 "텔레그램 3건")."""
    who = f"{t.count}건" if t.count else card_sender(t.sender_name)               # 방 묶음은 짧게 — "글 8건 · 오늘 10:17" 도 상자 한 줄을 넘겨 건수가 잘렸다(실제 자료 2026-10-16)
    return f"{SOURCE_LABEL.get(t.source.value, t.source.value)} {who}".strip()


def task_when(t, now: datetime) -> str:
    """할 일 둘째 줄의 뒤 — 기한("10/23까지" · "10/1 지남"), 없으면 받은 때."""
    return due_label(t.due, now) or received_text(t.last_at, now)


def feed_src(line) -> str:
    """알림 둘째 줄 맨 앞의 출처 꼬리표(두 글자 — 메일 · 문자 · 카톡 · 텔레 · 전화 · 팀즈). 화면이 본문과 다른 색의 꼬리표로 그린다
    (사용자 2026-10-17 "'메일/팀즈/문자/텔레그램'을 본문 텍스트와 색상 다르게 할 것. 헤갈림")."""
    return board.SHORT_LABEL.get(line.source.value, line.source.value)


def feed_who(line) -> str:
    """알림 둘째 줄의 누구 — 보낸 사람("탁새봄"), 안 읽은 글이 여럿이면 그 수("탁새봄 3건"), 지켜보는 방 묶음은 글 수("3건"). 출처는 feed_src 가 따로."""
    if line.room:
        return f"{line.count}건"
    return card_sender(line.sender_name) + (f" {line.unread}건" if line.unread > 1 else "")


def feed_view(sc, now: datetime, orgs: tuple = ()) -> dict:
    """알림(탭5 — 2026-10-17): 상자 여섯(꼴은 할 일 상자와 같다 — 줄 세 개(FEED_ON_CARD), n = 안 읽은 줄 수)과 머리줄의 수(안 읽은 줄 · 오늘 받은 줄)."""
    today = now.astimezone(KST).date()
    cards = [{"key": c.key, "name": c.name, "color": c.color, "state": c.state, "n": c.urgent, "urgent": c.urgent, "waiting": c.waiting,
              "tasks": [{"what": board.feed_what(line, orgs), "src": feed_src(line), "who": feed_who(line), "when": received_text(line.at, now), "unread": line.unread > 0}
                        for line in c.tasks[:FEED_ON_CARD]],
              "more": max(0, len(c.tasks) - FEED_ON_CARD)} for c in sc.feed_cards]
    return {"cards": cards, "totals": {"unread": sum(1 for line in sc.feed if line.unread),
                                       "today": sum(1 for line in sc.feed if line.at.astimezone(KST).date() == today)}}


def task_meta(t, now: datetime) -> str:
    """할 일의 둘째 줄 한 글(메뉴 막대 · 전체 목록)."""
    return f"{task_who(t)} · {task_when(t, now)}"


class _StaticLive:
    """설정 하나를 Live 처럼(시험 · 미리 보기 도구) — 바뀌지 않는다."""

    def __init__(self, settings):
        self._s = settings

    def get(self):
        return self._s

    def check(self) -> str:
        return "same"


def create_app(settings_or_live, store: Store, clock=None, token: str | None = None, restart=None) -> Flask:
    """settings_or_live: Settings 하나(시험) 또는 Live(엔진 — 요청마다 지금 설정 한 판을 받는다, 설계 4-3).
    token: 접속 열쇠(설계 9 — None 이면 예전처럼 열려 있다: 시험 · 미리 보기만. 빈 글자는 받지 않는다 — 검토 I1). restart: 엔진을 다시 시작하는 함수(설정 화면이 쓴다)."""
    if token is not None and not token:
        raise ValueError("빈 접속 열쇠 — 문이 열린 채로 뜨지 않는다")
    app = Flask(__name__, template_folder="templates", static_folder="static")
    clock = clock or (lambda: datetime.now(timezone.utc))
    live = settings_or_live if hasattr(settings_or_live, "check") else _StaticLive(settings_or_live)
    current = live.get
    app.config["UB"] = {"live": live, "store": store, "home": current().home, "restart": restart, "clock": clock}
    app.register_blueprint(web_settings.bp)
    app.register_blueprint(web_setup.bp)                   # 설정 한눈에(/setup) · 설정 명령이 묻는 API

    def _sources(settings, now: datetime) -> dict[str, dict]:
        out = store.source_status()
        for name, s in out.items():
            kind = name.split(":")[0]
            limit = max(3 * settings.poll_seconds.get(kind, 60), STALE_MIN_SECONDS)
            s["stale"] = (now - datetime.fromisoformat(s["at"])).total_seconds() > limit
            s["label"] = SOURCE_LABEL.get(kind, kind)
        return out

    def _task_rows(settings, sc, ai: dict[str, str], now: datetime) -> dict[str, dict]:
        """할 일 → 화면 줄(탭5 · 전체 목록 · 메뉴 막대가 함께 쓴다). 키는 대화(conv)."""
        names = {b.key: b.name for b in settings.businesses} | {"personal": "개인", "other": "기타"}
        colors = {b.key: b.color for b in settings.businesses}
        active = {b.key for b in settings.businesses if b.active}
        cal = settings.rules.calendar
        old_days = int(settings.display.get("old_task_days") or board.OLD_TASK_DAYS)
        return {t.conv: {
            "key": t.key, "conv": t.conv, "business": t.business, "business_name": names.get(t.business, t.business),
            "business_known": t.business not in ("personal", "other"), "business_color": colors.get(t.business, ""),
            "source": SOURCE_LABEL.get(t.source.value, t.source.value),
            "sender": f"안 읽은 글 {t.count}건" if t.count else sender_text(t.sender_name),
            "where": where_text(t.chat_name, t.sender_name, t.source.value),
            "what": t.what, "summary": ai.get(t.key, t.summary), "meta": task_meta(t, now), "who": task_who(t), "when": task_when(t, now),
            "unread": t.unread, "due_soon": board.due_soon(t, now, cal), "old": board.is_old(t, now, old_days),
            "due_text": due_label(t.due, now), "received_text": received_text(t.last_at, now), "opened_text": received_text(t.opened_at, now),
            "guessed": t.guessed, "box_key": t.business if t.business in colors and t.business in active else "personal",
            "awaiting": t.kind == "awaiting", "link": safe_link(t.link)} for t in [*sc.tasks, *sc.awaiting]}

    def _cards(sc, rows: dict[str, dict]) -> list[dict]:
        """사업 상자(탭5) — 할 일 앞의 두 줄과 나머지 수. urgent = 안 읽음 · 기한 임박 수(주황), waiting = 나머지."""
        short = ("what", "who", "when", "unread", "due_soon", "old", "awaiting")
        out = []
        for c in sc.cards:
            # 탭5 · 메뉴 막대의 상자에는 받은 할 일만 — 내가 보낸 것(답 기다림)은 맥의 전체 목록에만(사용자 2026-10-17 "내가 보낸 메일, 문자, 카톡도 …
            # 이럴 필요가 있는가? 내가 보낸건데" → "탭5에서 빼고 목록에만"). 답 기다림은 주황 수에 들지 않으므로 urgent 는 그대로다
            ts = [t for t in c.tasks if t.kind != "awaiting"]
            state = "urgent" if c.urgent else "waiting" if ts else "quiet"
            out.append({"key": c.key, "name": c.name, "color": c.color, "state": state, "n": len(ts), "urgent": c.urgent, "waiting": len(ts) - c.urgent,
                        "tasks": [{k: rows[t.conv][k] for k in short} for t in ts[:TASKS_ON_CARD]],
                        "more": max(0, len(ts) - TASKS_ON_CARD)})
        return out

    def _view(settings, now: datetime) -> dict:
        """화면(탭5)과 전체 목록(맥)이 함께 쓰는 자료. 한 요청 동안 설정 한 판(settings)만 쓴다."""
        names = {b.key: b.name for b in settings.businesses} | {"personal": "개인", "other": "기타"}
        colors = {b.key: b.color for b in settings.businesses}      # 사업 → 형광색 이름(개인·기타는 색 없음)
        # 판정 → 화면에 낼 것(대기에서 뺀 것 · Claude 와 다룬 것을 뺀다) → 상자 — 미리 보기 · 옮기기와 같은 한 길(board.screen).
        # 뺀 것은 전체 목록 아래에 까닭 · 다룬 때와 함께 남긴다
        sc = board.screen(store, settings, now, LOOKBACK_DAYS, write=True)       # 장부는 엔진(웹)만 쓴다
        its, skipped, recipe, seen = sc.items, sc.skipped, sc.recipe, sc.seen
        ai = summarize.lookup(store, its, recipe)               # 저장된 요약(글이나 요약 방식이 바뀌면 쓰지 않는다)
        drop, done = seen.unneeded, seen.handled
        everything, its = its, seen.items
        rows = {i.key: {
            "grade": i.grade.value, "business": i.business, "business_name": names.get(i.business, i.business),
            "business_known": i.business not in ("personal", "other"), "business_color": colors.get(i.business, ""),
            "source": source_text(i.source.value, i.accounts, settings.mail_labels), "accounts": i.accounts,
            "chat_name": i.chat_name, "sender_name": i.sender_name, "count": i.count,
            "sender": f"안 읽은 글 {i.count}건" if i.count else sender_text(i.sender_name),      # 방 묶음은 보낸 사람 자리에 건수
            "where": where_text(i.chat_name, i.sender_name, i.source.value),
            "summary": ai.get(i.key, i.summary), "raw_summary": i.summary, "ai": i.key in ai,
            "waited_text": waited_text(i.waited_minutes, int((now - i.received_at).total_seconds() // 60)),
            "received_text": received_text(i.received_at, now),
            "deadline_text": deadline_text(i.deadline, now), "key": i.key, "link": safe_link(i.link),
        } for i in everything}
        payload = [rows[i.key] for i in its]
        left_out = [{
            "reason": x.reason, "reason_label": SKIP_LABEL.get(x.reason, x.reason),
            "source": source_text(x.source.value, [x.account.lower()] if x.account else [], settings.mail_labels),
            "sender": sender_text(x.sender_name), "summary": x.summary, "received_text": received_text(x.received_at, now),
            "key": x.key, "link": safe_link(x.link),
        } for x in skipped]
        sources = _sources(settings, now)
        wired = has_sources(settings)                           # 수집할 곳이 하나도 없으면 "전부 확인했고 없다"가 아니다(검토 I6)
        counts = {"urgent": sum(1 for i in its if i.grade == Grade.URGENT), "waiting": sum(1 for i in its if i.grade == Grade.WAITING)}
        task_rows = _task_rows(settings, sc, ai, now)
        ordered = [{**task_rows[t.conv], "box": c.name} for c in sc.cards for t in c.tasks if t.kind == "todo"]   # 상자 차례 · 상자 안 차례(box = 그 할 일이 든 상자)
        waits = [{**task_rows[t.conv], "box": c.name} for c in sc.cards for t in c.tasks if t.kind == "awaiting"]   # 답 기다림(전체 목록 — 처리할 일과 따로)
        ordered.sort(key=lambda r: (r["old"], not r["unread"], not r["due_soon"]))      # 전체 목록 · 메뉴 막대: 오래됨은 아래, 안 읽음 · 임박 먼저
        totals = {"tasks": len(sc.tasks), "unread": sum(1 for t in sc.tasks if t.unread), "awaiting": len(sc.awaiting)}
        down = down_labels(sources) if wired else [NOTHING_WIRED]
        blind = blindness(sources) if wired else "all"
        if sc.book_error and not blind:
            blind = "some"                                      # 장부를 못 읽어 7일 안의 할 일만 보인다 — 초록 "없음"이 되지 않게(검토 I3). 문구는 화면이 "할 일 장부 오류"
        # 다뤘다고 뺀 것 가운데 머리줄의 "N건"에 셌을 것(안 읽은 것) — 읽은 것은 건수에 들지 않으니 세지 않는다
        gone = [i for i in everything if i.key in done and i.grade == Grade.URGENT]
        return {
            "checked_at": now.astimezone(KST).strftime("%H:%M"), "items": payload, "sources": sources,
            "down": down, "blind": blind, "book_error": sc.book_error,
            "v": asset_version(PACKAGE_DIR),
            # 머리줄의 "처리할 일 N건" · "안 읽음 M"(totals)과 사업 상자 여섯(할 일). counts 는 판정 항목의 건수(긴급 · 대기 — 전체 목록 아래 표)
            "totals": totals, "counts": counts, "tasks": ordered, "awaiting": waits,
            "closed": [{**r, "why": CLOSED_LABEL.get(r["closed_why"], r["closed_why"]),
                        "closed_text": received_text(datetime.fromisoformat(r["closed_at"]), now),
                        "source_text": SOURCE_LABEL.get(r["source"], r["source"]), "sender": sender_text(r["sender_name"] or "")}
                       for r in taskbook.recently_closed(store, now)],
            "cards": _cards(sc, task_rows),
            "feed": feed_view(sc, now, tuple(str(o) for o in settings.display.get("greeting_orgs") or [])),                         # 탭5 는 이것을 그린다(알림 — 2026-10-17). cards 는 메뉴 막대 · 상태 API 의 할 일 상자
            # 전체 목록의 사업 고르기(가르치기 — /api/teach): 진행 사업 + 개인 · 기타
            "box_choices": [(b.key, b.name) for b in settings.businesses if b.active] + [("personal", settings.etc_name)],
            "skipped": left_out, "unneeded": [rows[i.key] for i in everything if i.key in drop],
            "handled": [{**rows[i.key], "handled_text": received_text(done[i.key], now)} for i in everything if i.key in done],
            # 그 수와 보낸 사람 — 화면이 이것을 적어, 잘못 빠져도 조용히 사라지지 않는다(건수만으로는 무엇이 빠졌는지 모른다)
            "handled_shown": len(gone),
            "handled_who": list(dict.fromkeys(rows[i.key]["sender"] for i in gone if rows[i.key]["sender"])),
        }

    @app.before_request
    def local_names_only():
        """127.0.0.1·localhost 라는 이름으로 온 요청만 받는다. 남의 웹페이지가 제 이름을 127.0.0.1 로 돌려(DNS 리바인딩)
        브라우저를 통해 목록을 읽거나 처리됨을 누르는 것을 막는다."""
        host = (request.host or "").rsplit(":", 1)[0] if ":" in (request.host or "") else (request.host or "")
        if host not in LOCAL_HOSTS:
            return jsonify(error="local only"), 403

    @app.before_request
    def same_site_writes():
        """GET 이 아닌 요청: 우리 페이지에서 온 JSON 만. 남의 웹페이지는 127.0.0.1 로도 POST 를 보낼 수 있다(이름 검사를 지난다 — 설계 9).
        Origin 이 있으면 우리 주소와 같아야 하고, Sec-Fetch-Site 가 있으면 같은 곳이어야 하고, 본문은 JSON(꼴 없는 form 은 사전 확인 없이 온다)."""
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            return jsonify(error="origin"), 403
        if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            return jsonify(error="cross-site"), 403
        if not request.is_json:
            return jsonify(error="json only"), 415
        return None

    @app.before_request
    def need_token():
        """접속 열쇠 — 쿠키(브라우저 · 탭5 창) 또는 머리말(메뉴 막대 · 도구). 없으면 API 는 401 JSON, 화면은 여는 법을 적은 쪽."""
        if not token or request.path.startswith(OPEN_PATHS):
            return None
        given = request.cookies.get(COOKIE) or request.headers.get("X-UB-Token") or ""
        if hmac.compare_digest(given.encode(), token.encode()):
            return None
        if request.path.startswith("/api/"):
            return jsonify(error="token"), 401
        return render_template("locked.html", next=request.path), 401

    @app.get("/auth")
    def auth():
        """열쇠가 맞으면 쿠키를 심고 그 길로(메뉴 막대 · 관리 명령 · 탭5 창이 auth/api-token 을 읽어 연다). 다른 곳으로는 보내지 않는다."""
        nxt = request.args.get("next") or "/"
        if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
            nxt = "/"
        if not token or not hmac.compare_digest((request.args.get("t") or "").encode(), token.encode()):
            return render_template("locked.html", next=nxt), 401
        r = redirect(nxt, code=302)
        r.set_cookie(COOKIE, token, max_age=ONE_YEAR, httponly=True, samesite="Strict", path="/")   # 남의 사이트가 연 길로는 안 간다(검토 I2)
        return r

    @app.get("/")
    def index():
        return render_template("board.html", v=asset_version(PACKAGE_DIR))

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True)

    @app.get("/api/items")
    def items():
        """탭5 화면이 10초마다 당기는 자료. 화면은 feed(알림 상자 · 수)와 머리줄 자료만 쓴다 — items 는 할 일 판정 항목 전부다(한 건씩은 /list)."""
        v = _view(current(), clock())
        v["skipped_total"] = len(v.pop("skipped"))              # 탭5 화면에는 건수만(목록은 /list)
        v["unneeded_total"] = len(v.pop("unneeded"))
        v["handled_total"] = len(v.pop("handled"))
        v.pop("closed"); v.pop("tasks"); v.pop("box_choices"); v.pop("awaiting")                         # 맥에서만 보는 목록(최근 끝난 것 · 할 일 전체 줄) — 탭5 WebView 에 쌓지 않는다
        return jsonify(v)

    @app.get("/api/status")
    def status():
        """메뉴 막대용 작은 상태(10초마다) — 건수 · 고장 · 상자 · 설정 오류 · 탭5."""
        s = current()
        v = _view(s, clock())
        tab5 = None
        try:
            tab5 = json.loads((paths.state_dir(s.home) / "tab5-link.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return jsonify(totals=v["totals"], blind=v["blind"], down=v["down"], book_error=v["book_error"], checked_at=v["checked_at"],
                       cards=[{k: c[k] for k in ("key", "name", "color", "n", "urgent", "waiting")} for c in v["cards"]],
                       tasks=[{"key": r["key"], "box": r["box"], "what": r["what"],
                               "meta": r["meta"], "unread": r["unread"], "due_soon": r["due_soon"], "old": r["old"], "link": r["link"]}
                              for r in v["tasks"][:TASKS_ON_STATUS]],
                       settings_error=s.settings_error, legacy_config=s.legacy_config, tab5=tab5, version=s.version,
                       setup_left=web_setup.left())       # 남은 필수 설정 단계(메뉴 막대 — 이 칸이 있으면 /setup 이 있는 엔진이다)

    @app.get("/list")
    def full_list():
        """맥에서 여는 전체 목록 — 탭5 는 사업마다 요약만 보이고 터치가 안 되므로, 여기서 한 건씩 보고 원문을 열고 지운다."""
        v = _view(current(), clock())
        n = Counter(x["reason"] for x in v["skipped"])
        return render_template(
            "list.html", v=v,
            skipped=v["skipped"][:SKIPPED_MAX], skipped_total=len(v["skipped"]), skipped_max=SKIPPED_MAX,
            skipped_counts=[(SKIP_LABEL[r], n[r]) for r in SKIP_ORDER if n.get(r)])

    @app.post("/api/dismiss")
    def dismiss():
        body = request.get_json(silent=True)
        key = body.get("key") if isinstance(body, dict) else None
        if not isinstance(key, str) or not key or len(key) > KEY_MAX:
            return jsonify(error="key"), 400
        store.dismiss(key, clock() + timedelta(days=DISMISS_DAYS))
        return "", 204

    return app
