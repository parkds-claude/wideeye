"""설정: 자료 폴더의 settings.yaml(설정 v2) → 판정 코드가 쓰는 Settings. 코드에 낱말 · 문턱값을 박지 않는다(설계 2026-10-15 제품판 4).

- 판정 규칙은 urgent_board/defaults/rules.yaml 이 기본값이고, 설정의 rules: 에 적은 칸만 겹쳐 쓴다(깊은 병합 — junk.gmail_labels 는 통째로).
- Settings 의 꼴은 예전(config/*.yaml 시절)과 같다 — 판정 · 화면 코드가 그대로 받는다. 방 번호는 문서에서 글자, 여기서 정수로.
- load: 엄격(도구 · 시험). load_for_engine: 엔진용 — 깨졌으면 last-good, 그것도 없으면 빈 설정(예외를 올리지 않는다, 4-4).
- Live: 엔진의 지금 설정 한 판. 감시 고리가 5초마다 check() — 분류 · 화면 · 요약이 바뀌면 바꿔 끼우고, 연결이 바뀌면 "restart"."""
from __future__ import annotations
import copy
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import date, time
from pathlib import Path

from . import paths, settings_doc
from .settings_doc import Problem, SettingsError
from .workhours import WorkCalendar

POLL_SECONDS = {"imessage": 10, "kakao": 30, "telegram": 120, "gmail": 300, "calls": 60, "teams": 30, "whatsapp": 30}
# 이 맥의 자료를 읽는 출처(2026-10-17 알림 2단계 — 부재중 전화 · 팀즈 알림). 설정 파일에 적지 않았으면 켬(설계 D10 — 자료가 없는 맥에서는
# 빈 목록일 뿐 멈춤이 아니다). 빈 설정(EMPTY)에서는 끔
LOCAL_SOURCES = ("calls", "teams")
DISPLAY_DEFAULTS = {"old_task_days": 14,             # 열린 지 이만큼 지난 할 일은 '오래됨'으로 상자 아래에(2026-10-16 할 일 현황판)
                    "awaiting_days": 3,             # 답 기다림: 이 날 수 안에 보낸 내 질문 · 요청만(0 이면 끔 — 2026-10-16)
                    "tab5_usb_serial": "",          # 그림을 보낼 탭5 의 USB 일련번호(비우면 보내지 않는다 — tools/tab5_link.py)
                    "greeting_orgs": [],            # 알림 줄 앞의 인사말("안녕하세요 ○○님 기관이름 홍길동입니다")에서 함께 뗄 기관 이름들(없으면 기관 이름은 남는다)
                    "feed_bots": True}              # 텔레그램 봇 방의 글을 탭5 알림에 넣는다(2026-10-17 D9 — 수집은 늘 하고, 여기서 끄면 다시 시작 없이 빠진다)
                                                    # (fill_with_waiting 은 2026-10-16 부터 쓰지 않는다 — 할 일은 늘 보인다. 옛 파일에 있어도 된다)
# 이미 챙긴 것을 화면에서 빼는 장치. 적지 않으면 다른 Claude 세션의 기록을 읽지 않는다
HANDLED_DEFAULTS = {"claude_sessions": False, "lookback_days": 7, "sessions_dir": "~/.claude/projects"}
SUMMARY_DEFAULTS = {"enabled": False,                 # 켜면 화면에 뜰 메시지의 글이 Claude 로 간다 — 사용자가 직접 켠다
                    "model": "haiku", "max_chars": 60, "max_per_day": 300, "judge": True,
                    "pick_business": True,     # 규칙이 아무 데도 못 건 글은 Claude 가 사업을 고른다(같은 호출 — 2026-10-16). 사업 이름 · 낱말 · 설명이 Claude 로 간다
                    "task_max_chars": 14,      # 할 일 문구 길이(2026-10-16 — 사업 요약 card_max_chars 는 없앴다, 옛 파일에 있어도 된다)
                    "skip_when": "여러 사람에게 돌린 듯한 홍보·응원·팔로우·구독·투표 부탁, 안부·명절·축하 인사, 경조사 단체 알림, 물건·서비스·모임 권유",
                    "sources": ["gmail", "kakao", "imessage"]}     # 글을 Claude 로 보내도 되는 출처 — 텔레그램은 기본에서 뺀다(API 약관 §1.5)
# 화면의 형광색 이름(board.css 의 --neon-*). 사업에는 앞의 다섯만 준다(서로 확실히 다른 색 — 사용자 2026-10-13).
# 연두는 노랑 · 초록과 헷갈리고 주황은 긴급 색이라 사업에 주지 않는다(PALETTE 에 남는 것은 화면 토큰과 맞추려는 것)
PALETTE = ("green", "cyan", "pink", "yellow", "violet", "lime", "orange")
BUSINESS_COLORS = settings_doc.BUSINESS_COLORS
ETC_NAME = "개인 · 기타"
BOOT_LIMIT = 3                    # 같은 판으로 이만큼 시작하고도 60초를 못 버텼으면 last-good 으로(4-4)
REPLACE = frozenset({("junk", "gmail_labels")})      # 깊은 병합에서 통째로 바꾸는 칸


@dataclass
class Business:
    key: str
    name: str
    active: bool = True
    kakao_chat_ids: list[int] = field(default_factory=list)
    kakao_chat_names: list[str] = field(default_factory=list)
    telegram_chat_ids: list[int] = field(default_factory=list)
    mail_accounts: list[str] = field(default_factory=list)
    senders: list[str] = field(default_factory=list)      # 메일 주소 · 전화번호(+82 꼴) · 카톡/텔레그램 사용자 번호
    keywords: list[str] = field(default_factory=list)
    color: str = ""                                       # BUSINESS_COLORS 의 이름. 비우면 남는 색을 준다(다섯까지)
    memo: str = ""                                        # 사람이 남기는 메모(판정에 쓰지 않는다)
    about: str = ""                                       # Claude 에게 주는 설명(발주처 · 담당자 · 행사 날짜) — 규칙이 못 건 글의 사업을 고를 때(2026-10-16)


@dataclass
class Unconfirmed:
    """아직 확정되지 않은 일(입찰 중 등). 낱말은 사업의 낱말과 함께 겨루고, 이기면 사업 상자가 아니라 '개인 · 기타'로 간다.
    상자를 차지하지 않는다. 따내면 사업으로 옮긴다."""
    name: str
    keywords: list[str] = field(default_factory=list)
    memo: str = ""


@dataclass
class Rules:
    thresholds: dict
    client_titles: list[str]
    urgent_ko: re.Pattern
    urgent_en: re.Pattern
    soft_ko: re.Pattern
    deadline_suffix: re.Pattern
    deadline_inner: re.Pattern
    q_end_ko: re.Pattern
    q_end_short_ko: re.Pattern
    q_wh_ko: re.Pattern
    req_ko: re.Pattern
    q_en: re.Pattern
    req_en: re.Pattern
    courtesy: re.Pattern
    resolved_ko: re.Pattern
    noise_only: re.Pattern
    auto_sender: re.Pattern
    auto_subject: re.Pattern
    quote_cut: re.Pattern
    calendar: WorkCalendar = field(default_factory=WorkCalendar)   # 업무시간 달력(설정의 workhours)
    junk_labels: dict = field(default_factory=dict)   # Gmail 분류 → 빼는 까닭(ad·bulk·notice)
    q_indirect_ko: re.Pattern = re.compile(r"(?!)")   # 물음표 없는 간접 질문(…궁금합니다 · 여쭙니다 · 문의드립니다)
    junk_ad_text: re.Pattern = re.compile(r"(?!)")   # 광고 표식
    junk_auto_text: re.Pattern = re.compile(r"(?!)") # 자동 발송 표식(문자의 [Web발신] 등)
    junk_auto_domain: re.Pattern = re.compile(r"(?!)")   # 알림 · 홍보용 하위 주소(notice. · email. …) — 메일 도메인에 견준다(2026-10-17)
    junk_sms_person: re.Pattern = re.compile(r"(?!)")    # 사람이 쓰는 번호(휴대폰 · 070) — 아니고 연락처에도 없으면 문자 자동 발송(2026-10-17)
    foreign: dict = field(default_factory=dict)       # 한국어 · 영어 규칙이 못 읽는 말의 기준(rules.yaml foreign — 2026-10-17)
    foreign_en_words: frozenset = frozenset()          # 라틴 글자 글이 영어인지 가르는 낱말


@dataclass
class Settings:
    root: Path                                        # 코드 폴더(Claude 세션 처리의 "현황판을 고치는 세션" 판별이 쓴다)
    port: int
    db_path: Path
    poll_seconds: dict
    my_aliases: list[str]
    my_addresses: set[str]
    my_phone_tails: list[str]
    businesses: list[Business]
    rules: Rules
    contacts: dict = field(default_factory=dict)      # 별칭(소문자) → 사람 이름
    mail_labels: dict = field(default_factory=dict)   # 메일 계정(소문자) → 화면에 낼 이름. 순서가 우선순위
    display: dict = field(default_factory=dict)       # 화면 동작(긴급이 없는 상자를 대기로 채울지 · 탭5 일련번호)
    summary: dict = field(default_factory=dict)       # 메시지 요약(Claude Haiku) 켬/끔 · 모델 · 길이 · 하루 상한 · 허용 출처
    handled: dict = field(default_factory=lambda: dict(HANDLED_DEFAULTS))     # 다른 Claude 세션이 다룬 메일을 뺄지
    unconfirmed: list[Unconfirmed] = field(default_factory=list)   # 아직 확정되지 않은 일 — 낱말이 이기면 개인·기타
    watch_rooms: frozenset = frozenset()              # 통째로 지켜보는 방 — (출처, chat_id 글자) 묶음
    home: Path = field(default_factory=paths.home)    # 자료 폴더
    accounts: dict = field(default_factory=dict)      # {"gmail": [...], "telegram": {...}, "kakao": {...}, "imessage": {...}}
    personal_accounts: frozenset = frozenset()        # 개인 메일 계정(소문자)
    personal_senders: frozenset = frozenset()         # 개인 · 기타로 둔 사람 — 보낸 사람 번호 · 주소(소문자, 2026-10-16)
    etc_name: str = ETC_NAME
    service: dict = field(default_factory=lambda: {"engine": "launchd"})
    settings_error: str = ""                          # 설정 파일 오류(이전 설정으로 동작 중일 때)
    version: str = ""                                 # 이 Settings 를 만든 설정 파일의 판("" = 파일 없음 · 빈 설정)
    legacy_config: bool = False                       # 자료 폴더에 설정이 없고 코드 폴더에 옛 config/ 가 있다 — 옮기기 안내
    rejected_version: str = ""                        # 시작할 때 거절한 settings.yaml 의 판(물러서 돈다) — 감시가 그 판을 다시 읽지 않게(검토 C1)


def assign_colors(businesses: list[Business]) -> None:
    """진행 중인 사업마다 색 하나. 적힌 색(다섯 가운데)은 그대로, 없거나 모르는 이름이면 아직 안 쓴 색을 차례로 — 다섯을 넘으면 색 없음
    ("그 밖" 상자에 들어간다). 쉬는 사업의 색은 그대로 두되 다른 사업이 쓰는 것을 막지 않는다."""
    for b in businesses:
        if b.color not in BUSINESS_COLORS:
            b.color = ""
    active = [b for b in businesses if b.active]
    used = {b.color for b in active if b.color}
    spare = [c for c in BUSINESS_COLORS if c not in used]
    for b in (x for x in active if not x.color):
        b.color = spare.pop(0) if spare else ""


def parse_dates(values) -> frozenset:
    """["2026-10-17", …] → 날짜 집합. yaml 이 날짜로 읽어 준 값도 받는다."""
    return frozenset(v if isinstance(v, date) else date.fromisoformat(str(v)) for v in (values or []))


def parse_rooms(values) -> frozenset:
    """[{source: telegram, chat_id: "-123"}, …] → {("telegram", "-123"), …}. 꼴이 틀린 줄은 버린다(없는 방을 지켜보는 것과 같다)."""
    out = set()
    for v in values or []:
        if isinstance(v, dict) and v.get("source") and v.get("chat_id") not in (None, ""):
            out.add((str(v["source"]).strip().lower(), str(v["chat_id"]).strip()))
    return frozenset(out)


def _rx(s: str, flags=re.I) -> re.Pattern:
    return re.compile(s, flags)


def deep_merge(base: dict, over: dict, _path: tuple = ()) -> dict:
    """사전은 칸마다 겹쳐 쓴다. REPLACE 의 칸과 사전이 아닌 값은 통째로 바꾼다. base 는 건드리지 않는다."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        here = _path + (k,)
        if isinstance(v, dict) and isinstance(out.get(k), dict) and here not in REPLACE:
            out[k] = deep_merge(out[k], v, here)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _time(v, default: time) -> time:
    if not v:
        return default
    h, m = str(v).split(":")
    return time(int(h), int(m))


def calendar_from(wh: dict) -> WorkCalendar:
    wh = wh or {}
    d = WorkCalendar()
    lunch = wh.get("lunch") or [None, None]
    cal = WorkCalendar(start=_time(wh.get("start"), d.start), end=_time(wh.get("end"), d.end),
                       lunch_start=_time(lunch[0], d.lunch_start), lunch_end=_time(lunch[1], d.lunch_end),
                       country=str(wh.get("holidays", d.country) or ""),
                       extra_holidays=parse_dates(wh.get("extra_holidays")), extra_workdays=parse_dates(wh.get("extra_workdays")))
    if cal.end <= cal.start:
        raise SettingsError("workhours: 끝이 시작보다 늦어야 한다")
    return cal


def waiting_minutes(rules: dict) -> dict:
    """대기 문턱을 분으로(2026-10-16 — 시간으로는 10분을 못 적는다). 예전처럼 시간으로 적은 칸(unanswered_dm_hours ·
    unanswered_group_hours)은 분으로 바꾼다 — 같은 칸을 분으로도 적었으면 분이 이긴다. 기본값과 겹치기 전의 사용자 덮어쓰기 ·
    옛 config/rules.yaml 에 쓴다. 고친 사본을 돌려준다."""
    th = (rules or {}).get("thresholds")
    if not isinstance(th, dict):
        return rules
    th = dict(th)
    for kind in ("dm", "group"):
        hours = th.pop(f"unanswered_{kind}_hours", None)
        if hours is not None:
            th.setdefault(f"unanswered_{kind}_minutes", hours * 60)
    return {**rules, "thresholds": th}


def build_rules(r: dict, cal: WorkCalendar) -> Rules:
    junk = r.get("junk") or {}
    return Rules(
        thresholds=r["thresholds"], client_titles=r["client_titles"],
        urgent_ko=_rx(r["urgent_words_ko"]), urgent_en=_rx(r["urgent_words_en"]), soft_ko=_rx(r["soft_words_ko"]),
        deadline_suffix=_rx(r["deadline_suffix_ko"], 0), deadline_inner=_rx(r["deadline_inner_ko"], 0),
        q_end_ko=_rx(r["question_ends_ko"], 0), q_end_short_ko=_rx(r["question_ends_short_ko"], 0),
        q_wh_ko=_rx(r["question_wh_ko"], 0), req_ko=_rx(r["request_ko"], 0), q_en=_rx(r["question_en"]),
        q_indirect_ko=_rx(r.get("question_indirect_ko", "(?!)"), 0),
        req_en=_rx(r["request_en"]), courtesy=_rx(r["courtesy_ko"], 0), resolved_ko=_rx(r["resolved_ko"], 0), noise_only=_rx(r["noise_only"], 0),
        auto_sender=_rx(r["auto_sender_re"]), auto_subject=_rx(r["auto_subject_re"]), quote_cut=_rx(r["quote_cut_re"], 0),
        calendar=cal,
        junk_labels=dict(junk.get("gmail_labels") or {}),
        junk_ad_text=_rx(junk.get("ad_text_re") or "(?!)", 0),
        junk_auto_text=_rx(junk.get("auto_text_re") or "(?!)", 0),
        junk_auto_domain=_rx(junk.get("auto_domain_re") or "(?!)"),
        junk_sms_person=_rx(junk.get("sms_person_re") or "(?!)", 0),
        foreign=dict(r.get("foreign") or {}),
        foreign_en_words=frozenset(w for w in str((r.get("foreign") or {}).get("english_words") or "").lower().split("|") if w),
    )


def _ids(rooms) -> list[int]:
    return [int(r["id"]) for r in rooms or [] if isinstance(r, dict) and r.get("id") not in (None, "")]


def _names(rooms) -> list[str]:
    """번호 없는 방 항목의 이름 — 이름으로 맞춘다. 번호가 있는 항목의 이름은 이름표일 뿐이다(설계 4-1)."""
    return [str(r["name"]) for r in rooms or [] if isinstance(r, dict) and r.get("id") in (None, "") and r.get("name")]


def _business(b: dict) -> Business:
    return Business(key=str(b["key"]), name=str(b["name"]), active=bool(b.get("active", True)),
                    kakao_chat_ids=_ids(b.get("kakao_rooms")), kakao_chat_names=_names(b.get("kakao_rooms")),
                    telegram_chat_ids=_ids(b.get("telegram_rooms")),
                    mail_accounts=list(b.get("mail_accounts") or []), senders=list(b.get("senders") or []),
                    keywords=list(b.get("keywords") or []), color=str(b.get("color") or ""), memo=str(b.get("memo") or ""),
                    about=str(b.get("about") or ""))


def _watched(doc: dict) -> frozenset:
    """지켜보는 방 = 상자의 방 항목 가운데 watch: true(쉬는 사업이어도) + 상자에 없는 방(watch_rooms)."""
    out = set(parse_rooms(doc.get("watch_rooms")))
    for b in doc.get("boxes") or []:
        for src, f in (("kakao", "kakao_rooms"), ("telegram", "telegram_rooms")):
            for r in b.get(f) or []:
                if isinstance(r, dict) and r.get("watch") and r.get("id") not in (None, ""):
                    out.add((src, str(int(r["id"]))))
    return frozenset(out)


def from_doc(doc: dict, home: Path, root: Path = paths.CODE_ROOT) -> Settings:
    """v2 문서 → Settings. 꼴 검사는 하지 않는다(problems 가 한다) — 만들 수 없으면 예외."""
    me, acc = doc.get("me") or {}, doc.get("accounts") or {}
    rules = build_rules(deep_merge(settings_doc.default_rules(), waiting_minutes(doc.get("rules") or {})), calendar_from(doc.get("workhours") or {}))
    businesses = [_business(b) for b in doc.get("boxes") or []]
    assign_colors(businesses)
    gmail = [dict(g) for g in acc.get("gmail") or []]
    return Settings(
        root=Path(root), port=int((doc.get("server") or {}).get("port") or paths.DEFAULT_PORT), db_path=paths.db_path(home),
        poll_seconds=dict(POLL_SECONDS),
        my_aliases=list(me.get("names") or []), my_addresses={str(a).lower() for a in me.get("addresses") or []},
        my_phone_tails=list(me.get("phone_tails") or []), businesses=businesses, rules=rules,
        contacts={str(i).strip().lower(): c["name"] for c in doc.get("contacts") or [] for i in c.get("ids") or []},
        mail_labels={str(g["email"]).lower(): str(g["label"]) for g in gmail if g.get("label")},
        display={**DISPLAY_DEFAULTS, **(doc.get("display") or {})},
        summary={**SUMMARY_DEFAULTS, **(doc.get("summary") or {})},
        handled={**HANDLED_DEFAULTS, **(doc.get("handled") or {})},
        unconfirmed=[Unconfirmed(name=str(u["name"]), keywords=list(u.get("keywords") or []), memo=str(u.get("memo") or ""))
                     for u in doc.get("unconfirmed") or []],
        watch_rooms=_watched(doc), home=Path(home),
        accounts={"gmail": gmail, **{k: dict(acc.get(k) or {"enabled": False}) for k in ("telegram", "kakao", "imessage", "whatsapp")},
                  **{k: {"enabled": True, **dict(acc.get(k) or {})} for k in LOCAL_SOURCES}},
        personal_accounts=frozenset(str(a).lower() for a in me.get("personal_accounts") or []),
        personal_senders=frozenset(str(a).strip().lower() for a in me.get("personal_senders") or []),
        etc_name=str(doc.get("etc_name") or ETC_NAME), service={"engine": "launchd", **(doc.get("service") or {})},
    )


def problems(doc: dict, saved: dict | None = None) -> list[Problem]:
    """꼴 검사 + 그 문서로 실제로 Settings 를 만들어 본다(정규식 · 날짜 · 시각이 실제로 읽히는지 — 두 벌 검사를 쓰지 않는다)."""
    out = settings_doc.validate(doc, saved)
    if any(p.level == "error" for p in out):
        return out
    try:
        from_doc(doc, paths.home())
    except SettingsError as e:
        out.append(Problem(str(e).split(":", 1)[0], str(e)))
    except (KeyError, TypeError, ValueError, re.error) as e:
        out.append(Problem("", f"설정으로 만들 수 없다: {type(e).__name__}: {e}"))
    return out


def load(home: Path | None = None) -> Settings:
    """엄격하게 읽는다(도구 · 시험). 파일이 없으면 FileNotFoundError, 문제가 있으면 SettingsError."""
    home = Path(home) if home is not None else paths.home()
    doc, ver = settings_doc.read(home)
    bad = [p for p in problems(doc) if p.level == "error"]
    if bad:
        raise SettingsError(f"{bad[0].path}: {bad[0].message}")
    return replace(from_doc(doc, home), version=ver)


EMPTY = {"version": settings_doc.VERSION, "accounts": {"gmail": [], "telegram": {"enabled": False},
                                                       "kakao": {"enabled": False}, "imessage": {"enabled": False}, "whatsapp": {"enabled": False},
                                                       "calls": {"enabled": False}, "teams": {"enabled": False}}}


def load_for_engine(home: Path) -> Settings:
    """엔진이 시작할 때(4-4): settings.yaml → (안 되면) last-good → (그것도 안 되면) 빈 설정. 예외를 올리지 않는다.
    물러설 때는 거절한 판(rejected_version)을 적어 둔다 — Live 가 그 판을 다시 읽어 나쁜 설정으로 돌아가지 않게, 엔진이 그 판의
    시작 횟수를 지우지 않게(검토 C1). 사람이 파일을 고쳐 판이 바뀌면 그때 다시 읽는다."""
    home = Path(home)
    why, rejected = "", ""
    p = paths.settings_file(home)
    if p.exists():
        try:
            s = load(home)
            if settings_doc.boot_attempts(home, s.version) >= BOOT_LIMIT:
                rejected = s.version
                raise SettingsError(f"이 설정으로 세 번 시작하고도 60초를 못 버텼다(판 {s.version})")
            return s
        except Exception as e:                                   # noqa: BLE001 — 엔진이 설정 하나 때문에 죽지 않게(UTF-8 아님 · 권한 · 깊은 YAML …)
            why = str(e) or type(e).__name__
            rejected = rejected or settings_doc.version(p) or "unreadable"
    lg = paths.last_good_file(home)
    if lg.exists():
        try:
            data = lg.read_bytes()
            doc = settings_doc.parse(data)
            if not [x for x in problems(doc) if x.level == "error"]:
                return replace(from_doc(doc, home), version=settings_doc.version_of(data), rejected_version=rejected,
                               settings_error=f"설정 파일 오류 — 이전 설정으로 동작 중: {why}" if why else "")
        except Exception:                                        # noqa: BLE001
            pass
    legacy = not p.exists() and (paths.CODE_ROOT / "config" / "businesses.yaml").exists()
    return replace(from_doc(EMPTY, home), legacy_config=legacy, rejected_version=rejected,
                   settings_error=f"설정 파일 오류 — 빈 설정으로 동작 중: {why}" if why else "")


def connection_key(s: Settings) -> tuple:
    """수집기가 생성자에서 받는 값 — 이것이 바뀌면 엔진이 다시 시작한다(그 밖은 바꿔 끼운다)."""
    a = s.accounts or {}
    gmail = tuple((g.get("alias"), str(g.get("email", "")).lower(), g.get("token")) for g in a.get("gmail") or [])
    flags = tuple(bool((a.get(k) or {}).get("enabled")) for k in ("telegram", "kakao", "imessage", "whatsapp", *LOCAL_SOURCES))
    return (s.port, gmail, flags, tuple(sorted(s.my_addresses)), tuple(s.my_phone_tails))


class Live:
    """엔진의 지금 설정 한 판. 읽는 쪽은 get() 한 번으로 받은 판을 그 일 동안 쓴다(반쯤 바뀐 설정을 읽지 않는다)."""

    def __init__(self, home: Path, settings: Settings):
        self.home, self._s = Path(home), settings
        self._seen = settings.rejected_version or settings.version   # 마지막으로 살펴본 파일의 판(깨진 판 · 거절한 판을 거듭 읽지 않게)
        self._lock = threading.Lock()

    def get(self) -> Settings:
        return self._s

    @property
    def seen(self) -> str:
        """마지막으로 살펴본 파일의 판 — 이 판이 지금 도는 판(get().version)과 다르면 엔진이 그 판을 거절한 것이다(설정 명령이 본다)."""
        return self._seen

    def check(self) -> str:
        """"same" · "swapped"(분류 · 화면 · 요약이 바뀜) · "restart"(연결이 바뀜 — 부르는 쪽이 다시 시작) · "error"(새 판이 깨짐)."""
        with self._lock:
            v = settings_doc.version(paths.settings_file(self.home))
            if not v or v == self._seen:
                return "same"
            self._seen = v
            try:
                new = load(self.home)
            except Exception as e:                               # noqa: BLE001 — 어떤 오류든 지금 설정으로 계속 돈다
                self._s = replace(self._s, settings_error=f"설정 파일 오류 — 이전 설정으로 동작 중: {str(e) or type(e).__name__}")
                return "error"
            restart = connection_key(new) != connection_key(self._s)
            self._s = new
            return "restart" if restart else "swapped"
