"""설정 파일(settings.yaml) 읽기 · 꼴 검사 · 저장 — 판정 코드가 쓰는 Settings 를 만드는 일은 config.py 가 한다(설계 4-2).

- 판(version): 파일 내용의 해시 앞 16자. 화면이 읽은 판과 저장할 때의 판이 다르면 거절한다(손으로 고친 것과 부딪히지 않게).
- 저장: 프로세스 안 잠금 + 자료 폴더의 파일 잠금(엔진 · 설정 명령 · 옮기기가 따로 프로세스다 — 2026-10-16) → 판 확인 → 앞 판을 backups/ 에(20개까지)
  → 임시 파일에 쓰고 fsync → os.replace(원자적) → 0600. 되돌리기(undo)는 가장 새 백업을 다시 쓴다.
  꼴 검사(validate)에 error 가 하나라도 있으면 쓰지 않는다. 실제로 Settings 를 만들어 보는 검사는 config.problems 가 더한다.
- last-good: 엔진이 이 판으로 60초 넘게 잘 돌았을 때만 올린다(boot_ok). 시작 시도 기록(state/boot.json)과 함께
  "같은 판으로 세 번 시작하고도 못 버텼으면 last-good 으로"를 가능하게 한다(설계 4-4).
- 방 번호는 글자로 적는다(카톡은 17자리까지 — 브라우저의 JSON 이 정수로 다루면 끝자리가 바뀐다)."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
import re
import shutil
import threading
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import yaml

from . import paths

VERSION = 2
BACKUPS_KEPT = 20
BUSINESS_COLORS = ("green", "cyan", "pink", "yellow", "violet")   # 서로 확실히 다른 다섯(사용자 2026-10-13) — 연두 · 주황은 사업에 주지 않는다
RESERVED_KEYS = frozenset({"personal", "other", "etc"})           # 판정(개인 · 기타)과 화면(여섯째 상자)이 쓰는 이름
SOURCES = ("gmail", "kakao", "telegram", "imessage", "whatsapp")
ENGINES = ("launchd", "external")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
ALIAS_RE = re.compile(r"^[a-z0-9-]{1,24}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ROOM_ID_RE = re.compile(r"^-?\d{1,20}$")
FILE_TOKEN_RE = re.compile(r"^file:[A-Za-z0-9._-]+$")
KEYCHAIN_TOKEN_RE = re.compile(r"^keychain:[^/\s]+/[^/\s]+$")
TOP_KEYS = ("version", "me", "accounts", "boxes", "etc_name", "unconfirmed", "contacts", "watch_rooms", "display", "summary",
            "handled", "workhours", "server", "service", "rules")
RULE_KEYS = ("thresholds", "junk", "client_titles", "urgent_words_ko", "urgent_words_en", "deadline_suffix_ko", "deadline_inner_ko",
             "soft_words_ko", "question_ends_ko", "question_ends_short_ko", "question_indirect_ko", "question_wh_ko", "request_ko",
             "question_en", "request_en", "courtesy_ko", "resolved_ko", "noise_only", "auto_sender_re", "auto_subject_re", "quote_cut_re",
             "foreign")           # 한국어 · 영어 규칙이 못 읽는 말의 기준(2026-10-17)
REGEX_RULES = tuple(k for k in RULE_KEYS if k not in ("thresholds", "junk", "client_titles", "foreign"))
HEADER = ("# 긴급현황판 설정 — 설정 화면(/settings)이 쓴다. 손으로 고쳐도 된다(5초 안에 반영 · 틀리면 이전 설정으로 계속 돈다).\n"
          "# 화면으로 저장하면 이 파일의 주석은 지워진다 — 남길 말은 사업의 memo 칸에.\n")
DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
BOOT_FILE = "boot.json"

_save_lock = threading.Lock()


class SettingsError(Exception):
    """설정 파일을 읽을 수 없다(YAML 꼴이 아님 · 맨 위가 사전이 아님) — 또는 그 내용으로 Settings 를 만들 수 없다."""


class Conflict(Exception):
    """화면이 읽은 뒤 파일이 바뀌었다 — 다시 읽고 고쳐야 한다."""


class Invalid(Exception):
    def __init__(self, problems: list["Problem"]):
        super().__init__(f"설정에 문제 {len(problems)}개: {problems[0].path} — {problems[0].message}" if problems else "설정에 문제")
        self.problems = problems


class Problem(NamedTuple):
    path: str            # "boxes[2].color"
    message: str
    level: str = "error" # error(저장 안 함) · warn(알림만)


def parse(text: str | bytes) -> dict:
    """글(또는 바이트) → 문서. UTF-8 이 아님 · YAML 꼴이 아님 · 너무 깊게 겹침 · 맨 위가 사전이 아님은 모두 SettingsError(검토 I2)."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise SettingsError("UTF-8 글자가 아니다") from None
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SettingsError(f"YAML 꼴이 아니다: {str(e).splitlines()[0]}") from None
    except RecursionError:
        raise SettingsError("너무 깊게 겹쳐 있다") from None
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise SettingsError("맨 위가 '이름: 값' 꼴(사전)이 아니다")
    return d


def dump(doc: dict) -> str:
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=None, width=140)


def version_of(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha1(data).hexdigest()[:16]


def version(path: Path) -> str:
    """파일 내용의 판. 없거나 읽을 수 없으면 ""(권한 · 디스크 오류 — 부르는 쪽은 '바뀌지 않음'으로 다룬다)."""
    try:
        return version_of(Path(path).read_bytes())
    except OSError:
        return ""


def read(home: Path) -> tuple[dict, str]:
    """(문서, 판). 파일이 없으면 FileNotFoundError, 꼴이 틀리면 SettingsError."""
    data = paths.settings_file(home).read_bytes()
    return parse(data), version_of(data)


@lru_cache(maxsize=1)
def _default_rules_text() -> str:
    return (DEFAULTS_DIR / "rules.yaml").read_text(encoding="utf-8")


def default_rules() -> dict:
    """판정 규칙 기본값 — 부를 때마다 새 사전(겹쳐 쓰기가 기본값을 더럽히지 않게)."""
    return yaml.safe_load(_default_rules_text())


def example() -> dict:
    return yaml.safe_load((DEFAULTS_DIR / "settings.example.yaml").read_text(encoding="utf-8"))


PHONE_CHARS = re.compile(r"^[+\d][\d\s()-]{6,}$")


def normalize_phone(s: str) -> str:
    """전화번호처럼 보이면 +82 꼴로(메시지 앱의 보낸 사람 id 꼴), 아니면 그대로. "010-1234-5678" → "+821012345678"."""
    t = (s or "").strip()
    if "@" in t or not PHONE_CHARS.match(t):
        return t
    d = re.sub(r"\D", "", t)
    if t.startswith("+"):
        return "+" + d
    if d.startswith("0") and len(d) >= 9:
        return "+82" + d[1:]
    return t


# ---- 꼴 검사 ------------------------------------------------------------------------------------------------------------

def _is_date(v) -> bool:
    if isinstance(v, date):
        return True
    try:
        date.fromisoformat(str(v))
        return True
    except ValueError:
        return False


def validate(doc: dict, saved: dict | None = None) -> list[Problem]:
    """꼴 검사. saved 는 지금 저장돼 있는 문서(화면에서 저장할 때) — 그 문서에 없던 keychain 토큰은 막는다(검토 17)."""
    out: list[Problem] = []

    def err(path: str, msg: str) -> None:
        out.append(Problem(path, msg))

    def warn(path: str, msg: str) -> None:
        out.append(Problem(path, msg, "warn"))

    def as_dict(v, path: str) -> dict:
        if v is None:
            return {}
        if not isinstance(v, dict):
            err(path, "'이름: 값' 꼴이어야 한다")
            return {}
        return v

    def as_list(v, path: str) -> list:
        if v is None:
            return []
        if not isinstance(v, list):
            err(path, "목록이어야 한다")
            return []
        return v

    def strings(v, path: str, nonempty: bool = False) -> list[str]:
        vs = as_list(v, path)
        if any(not isinstance(x, str) or (nonempty and not x.strip()) for x in vs):
            err(path, "빈 글이 아닌 글자 목록이어야 한다" if nonempty else "글자 목록이어야 한다")
            return []
        return vs

    def boolean(v, path: str) -> None:
        if not isinstance(v, bool):
            err(path, "true 또는 false")

    if not isinstance(doc, dict):
        return [Problem("", "맨 위가 '이름: 값' 꼴이어야 한다")]
    if doc.get("version") != VERSION:
        err("version", f"version 은 {VERSION} 이어야 한다")
    for k in doc:
        if k not in TOP_KEYS:
            warn(str(k), "모르는 칸 — 오타가 아닌지 본다(쓰지 않는다)")

    me = as_dict(doc.get("me"), "me")
    for f in ("names", "addresses", "phone_tails", "personal_accounts", "personal_senders"):
        strings(me.get(f), f"me.{f}", nonempty=True)

    acc = as_dict(doc.get("accounts"), "accounts")
    saved_tokens = {str(g.get("token")) for g in ((saved or {}).get("accounts") or {}).get("gmail") or [] if isinstance(g, dict)}
    aliases: set[str] = set()
    for i, g in enumerate(as_list(acc.get("gmail"), "accounts.gmail")):
        p = f"accounts.gmail[{i}]"
        if not isinstance(g, dict):
            err(p, "'이름: 값' 꼴이어야 한다")
            continue
        a = str(g.get("alias") or "")
        if not ALIAS_RE.match(a):
            err(p + ".alias", "영문 소문자 · 숫자 · - 로 1~24자")
        elif a in aliases:
            err(p + ".alias", "다른 메일 계정과 겹친다")
        aliases.add(a)
        if "@" not in str(g.get("email") or ""):
            err(p + ".email", "메일 주소가 아니다")
        if g.get("label") is not None and not isinstance(g.get("label"), str):
            err(p + ".label", "글자여야 한다")
        t = str(g.get("token") or "")
        if KEYCHAIN_TOKEN_RE.match(t):
            if saved is not None and t not in saved_tokens:
                err(p + ".token", "키체인 토큰은 화면에서 새로 적을 수 없다(옮기기가 적은 것만 읽는다)")
        elif not FILE_TOKEN_RE.match(t):
            err(p + ".token", "file:<auth 폴더 안의 이름> 꼴이어야 한다")
    for k in ("telegram", "kakao", "imessage", "whatsapp", "calls", "teams"):
        v = as_dict(acc.get(k), f"accounts.{k}")
        if "enabled" in v:
            boolean(v["enabled"], f"accounts.{k}.enabled")
        if k == "kakao" and "helper_dir" in v and not isinstance(v["helper_dir"], str):
            err("accounts.kakao.helper_dir", "글자여야 한다(k-skill 도우미가 든 폴더 — 비우면 기본 자리)")

    keys: set[str] = set()
    colors: dict[str, str] = {}
    owner: dict[tuple, str] = {}
    word_owner: dict[str, str] = {}
    boxes = as_list(doc.get("boxes"), "boxes")
    for i, b in enumerate(boxes):
        p = f"boxes[{i}]"
        if not isinstance(b, dict):
            err(p, "'이름: 값' 꼴이어야 한다")
            continue
        k = str(b.get("key") or "")
        if not KEY_RE.match(k):
            err(p + ".key", "영문 소문자 · 숫자 · - 로 1~40자(첫 글자는 영문 · 숫자)")
        elif k in RESERVED_KEYS:
            err(p + ".key", f"'{k}' 는 쓸 수 없다(개인 · 기타 상자가 쓰는 이름)")
        elif k in keys:
            err(p + ".key", "다른 사업과 겹친다")
        keys.add(k)
        name = b.get("name")
        if not isinstance(name, str) or not name.strip():
            err(p + ".name", "이름이 비었다")
        active = b.get("active", True)
        boolean(active, p + ".active")
        c = b.get("color", "") or ""
        if c not in ("", *BUSINESS_COLORS):
            err(p + ".color", "green · cyan · pink · yellow · violet 가운데 하나")
        elif c and active is True:
            if c in colors:
                err(p + ".color", f"'{colors[c]}' 와 색이 같다 — 진행 중인 사업끼리는 다른 색")
            colors.setdefault(c, str(name or k))
        for f in ("memo", "about"):
            if b.get(f) is not None and not isinstance(b.get(f), str):
                err(f"{p}.{f}", "글자여야 한다")
        for f in ("mail_accounts", "senders"):
            for x in strings(b.get(f), f"{p}.{f}", nonempty=True):
                key = (f, x.strip().lower())
                if key in owner and owner[key] != k:
                    warn(f"{p}.{f}", f"'{x}' 는 '{owner[key]}' 에도 등록돼 있다 — 위쪽 상자가 이긴다")
                owner.setdefault(key, k)
        for w in strings(b.get("keywords"), f"{p}.keywords", nonempty=True):
            if w in word_owner and word_owner[w] != k:
                warn(f"{p}.keywords", f"낱말 '{w}' 가 '{word_owner[w]}' 에도 있다")
            word_owner.setdefault(w, k)
        for f, src in (("kakao_rooms", "kakao"), ("telegram_rooms", "telegram")):
            for j, r in enumerate(as_list(b.get(f), f"{p}.{f}")):
                rp = f"{p}.{f}[{j}]"
                if not isinstance(r, dict):
                    err(rp, "{id, name} 꼴이어야 한다")
                    continue
                rid, rname = r.get("id"), r.get("name")
                if rid in (None, ""):
                    if src == "telegram":
                        err(rp + ".id", "텔레그램 방은 번호가 있어야 한다")
                    elif not isinstance(rname, str) or not rname.strip():
                        err(rp, "번호(id)나 이름(name) 가운데 하나는 있어야 한다")
                    elif r.get("watch"):
                        warn(rp + ".watch", "번호 없는 방은 지켜볼 수 없다(이름만으로는 방을 정하지 못한다)")
                elif not isinstance(rid, str) or not ROOM_ID_RE.match(rid):
                    err(rp + ".id", "방 번호는 따옴표 친 숫자 글자(\"100000000000001\")로 적는다")
                else:
                    key = (src, rid)
                    if key in owner and owner[key] != k:
                        warn(rp, f"이 방은 '{owner[key]}' 에도 등록돼 있다 — 위쪽 상자가 이긴다")
                    owner.setdefault(key, k)
                if rname is not None and not isinstance(rname, str):
                    err(rp + ".name", "글자여야 한다")
                if "watch" in r:
                    boolean(r["watch"], rp + ".watch")
    if sum(1 for b in boxes if isinstance(b, dict) and b.get("active", True) is True) > 5:
        warn("boxes", "진행 중인 사업이 다섯을 넘는다 — 여섯째부터는 '그 밖' 상자에 함께 뜬다")

    if doc.get("etc_name") is not None and (not isinstance(doc.get("etc_name"), str) or not doc["etc_name"].strip()):
        err("etc_name", "빈 글이 아니어야 한다")
    for i, u in enumerate(as_list(doc.get("unconfirmed"), "unconfirmed")):
        if not isinstance(u, dict) or not str(u.get("name") or "").strip():
            err(f"unconfirmed[{i}]", "{name, keywords} 꼴 — 이름이 있어야 한다")
            continue
        strings(u.get("keywords"), f"unconfirmed[{i}].keywords", nonempty=True)
    for i, c in enumerate(as_list(doc.get("contacts"), "contacts")):
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            err(f"contacts[{i}]", "{name, ids} 꼴 — 이름이 있어야 한다")
            continue
        strings(c.get("ids"), f"contacts[{i}].ids", nonempty=True)
    for i, w in enumerate(as_list(doc.get("watch_rooms"), "watch_rooms")):
        if not isinstance(w, dict) or str(w.get("source")) not in ("kakao", "telegram") or not ROOM_ID_RE.match(str(w.get("chat_id", ""))):
            err(f"watch_rooms[{i}]", "{source: kakao|telegram, chat_id: \"번호\"} 꼴")

    disp = as_dict(doc.get("display"), "display")
    if "fill_with_waiting" in disp:
        boolean(disp["fill_with_waiting"], "display.fill_with_waiting")
    aw = disp.get("awaiting_days")
    if "awaiting_days" in disp and (not isinstance(aw, int) or isinstance(aw, bool) or not 0 <= aw <= 14):
        err("display.awaiting_days", "0~14 사이의 정수(0 이면 답 기다림을 끈다)")
    od = disp.get("old_task_days")
    if "old_task_days" in disp and (not isinstance(od, int) or isinstance(od, bool) or not 1 <= od <= 365):
        err("display.old_task_days", "1~365 사이의 정수")
    go = disp.get("greeting_orgs")
    if go is not None and not (isinstance(go, list) and all(isinstance(x, str) and x.strip() for x in go)):
        err("display.greeting_orgs", "기관 이름(글자)의 목록이어야 한다")
    if "tab5_usb_serial" in disp and not isinstance(disp["tab5_usb_serial"], str):
        err("display.tab5_usb_serial", "글자여야 한다")

    summ = as_dict(doc.get("summary"), "summary")
    for f in ("enabled", "judge", "pick_business"):
        if f in summ:
            boolean(summ[f], f"summary.{f}")
    for f, lo, hi in (("max_chars", 10, 200), ("card_max_chars", 0, 120), ("task_max_chars", 6, 40), ("max_per_day", 0, 5000)):
        if f in summ and (not isinstance(summ[f], int) or isinstance(summ[f], bool) or not lo <= summ[f] <= hi):
            err(f"summary.{f}", f"{lo}~{hi} 사이의 정수")
    if "sources" in summ and any(x not in SOURCES for x in strings(summ["sources"], "summary.sources")):
        err("summary.sources", "gmail · kakao · telegram · imessage · whatsapp 가운데서")
    for f in ("model", "skip_when"):
        if f in summ and not isinstance(summ[f], str):
            err(f"summary.{f}", "글자여야 한다")

    hd = as_dict(doc.get("handled"), "handled")
    if "claude_sessions" in hd:
        boolean(hd["claude_sessions"], "handled.claude_sessions")
    if "lookback_days" in hd and (not isinstance(hd["lookback_days"], int) or not 1 <= hd["lookback_days"] <= 30):
        err("handled.lookback_days", "1~30 사이의 정수")

    wh = as_dict(doc.get("workhours"), "workhours")
    for f in ("start", "end"):
        if f in wh and not (isinstance(wh[f], str) and TIME_RE.match(wh[f])):
            err(f"workhours.{f}", "HH:MM(예: 09:00)")
    if "lunch" in wh and not (isinstance(wh["lunch"], list) and len(wh["lunch"]) == 2
                              and all(isinstance(x, str) and TIME_RE.match(x) for x in wh["lunch"])):
        err("workhours.lunch", "[\"12:00\", \"13:00\"] 꼴")
    if "holidays" in wh and not isinstance(wh["holidays"], str):
        err("workhours.holidays", "나라 이름(KR) 또는 빈 글")
    for f in ("extra_holidays", "extra_workdays"):
        if any(not _is_date(x) for x in as_list(wh.get(f), f"workhours.{f}")):
            err(f"workhours.{f}", "날짜(YYYY-MM-DD) 목록")

    srv = as_dict(doc.get("server"), "server")
    if "port" in srv and (not isinstance(srv["port"], int) or isinstance(srv["port"], bool) or not 1024 <= srv["port"] <= 65535):
        err("server.port", "1024~65535 사이의 정수")
    svc = as_dict(doc.get("service"), "service")
    if "engine" in svc and svc["engine"] not in ENGINES:
        err("service.engine", "launchd 또는 external")

    rules = as_dict(doc.get("rules"), "rules")
    for k, v in rules.items():
        if k not in RULE_KEYS:
            warn(f"rules.{k}", "모르는 규칙 칸(쓰지 않는다)")
        elif k in REGEX_RULES:
            try:
                re.compile(str(v))
            except re.error as e:
                err(f"rules.{k}", f"정규식 오류: {e}")
        elif k == "thresholds":
            for tk, tv in as_dict(v, "rules.thresholds").items():
                if not isinstance(tv, (int, float)) or isinstance(tv, bool) or tv < 0:
                    err(f"rules.thresholds.{tk}", "0 이상의 숫자")
        elif k == "client_titles":
            strings(v, "rules.client_titles", nonempty=True)
        elif k == "junk":
            j = as_dict(v, "rules.junk")
            for rk in ("ad_text_re", "auto_text_re"):
                if rk in j:
                    try:
                        re.compile(str(j[rk]))
                    except re.error as e:
                        err(f"rules.junk.{rk}", f"정규식 오류: {e}")
            if "gmail_labels" in j and not isinstance(j["gmail_labels"], dict):
                err("rules.junk.gmail_labels", "{분류: 까닭} 꼴")
    return out


# ---- 저장 --------------------------------------------------------------------------------------------------------------

PERSONAL = "personal"                 # 가르치기에서 '개인 · 기타'


def teach_sender(doc: dict, sender: str, box: str) -> None:
    """보낸 사람 하나를 상자 box 의 senders 에 적고(box == PERSONAL 이면 me.personal_senders 에) 다른 곳에서는 뺀다 — 한 곳에만 있게
    (2026-10-16 분류 키우기: 전체 목록에서 사업을 고름). 전화번호는 +82 꼴로."""
    value = normalize_phone(sender.strip()) if sender.strip().startswith(("+", "0")) and any(c.isdigit() for c in sender) else sender.strip()
    low = value.lower()
    for b in doc.get("boxes") or []:
        if isinstance(b, dict):
            b["senders"] = [x for x in (b.get("senders") or []) if str(x).strip().lower() != low]
    me = doc.setdefault("me", {})
    me["personal_senders"] = [x for x in (me.get("personal_senders") or []) if str(x).strip().lower() != low]
    if box == PERSONAL:
        me["personal_senders"].append(value)
        return
    target = next(b for b in doc["boxes"] if isinstance(b, dict) and b.get("key") == box)
    target["senders"].append(value)


ROOM_FIELD = {"kakao": "kakao_rooms", "telegram": "telegram_rooms"}


def teach_room(doc: dict, source: str, chat_id: str, chat_name: str, box: str) -> None:
    """방 하나를 상자 box 로 옮긴다(지켜보기 표시는 따라간다). box == PERSONAL 이면 어느 상자에도 두지 않고, 지켜보던 방이면 watch_rooms 에 남긴다."""
    field = ROOM_FIELD[source]
    entry: dict = {"id": str(chat_id)}
    watched = False
    for b in doc.get("boxes") or []:
        if not isinstance(b, dict):
            continue
        keep = []
        for r in b.get(field) or []:
            same = isinstance(r, dict) and (str(r.get("id") or "") == str(chat_id) or (not r.get("id") and chat_name and r.get("name") == chat_name))
            if same:
                watched = watched or bool(r.get("watch"))
                if r.get("name"):
                    entry["name"] = r["name"]
            else:
                keep.append(r)
        b[field] = keep
    rest = []
    for w in doc.get("watch_rooms") or []:
        if isinstance(w, dict) and str(w.get("source")) == source and str(w.get("chat_id")) == str(chat_id):
            watched = True
        else:
            rest.append(w)
    doc["watch_rooms"] = rest
    if box == PERSONAL:
        if watched:
            doc["watch_rooms"].append({"source": source, "chat_id": str(chat_id)})
        return
    if chat_name and "name" not in entry:
        entry["name"] = chat_name
    if watched:
        entry["watch"] = True
    target = next(b for b in doc["boxes"] if isinstance(b, dict) and b.get("key") == box)
    target.setdefault(field, []).append(entry)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _backup(home: Path, current: Path) -> None:
    d = paths.backups_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(current, d / f"settings-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.yaml")
    old = sorted(d.glob("settings-*.yaml"))
    for p in old[:-BACKUPS_KEPT]:
        p.unlink(missing_ok=True)


LOCK_FILE = ".settings.lock"


@contextmanager
def _locked(home: Path):
    """한 번에 한 곳만 설정 파일을 쓴다 — 프로세스 안(스레드)과 프로세스 사이(엔진 · 설정 명령) 둘 다. 판 확인과 바꿔 넣기 사이에 남이 끼지 않게."""
    with _save_lock:
        paths.ensure(home)
        fd = os.open(Path(home) / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def save(home: Path, doc: dict, expected: str | None) -> str:
    """꼴 검사 → 판 확인 → 백업 → 원자적 쓰기. expected 가 None 이면 판을 견주지 않는다(옮기기 · 처음 만들기). 새 판을 돌려준다."""
    bad = [p for p in validate(doc) if p.level == "error"]
    if bad:
        raise Invalid(bad)
    text = HEADER + dump(doc)
    with _locked(home):
        p = paths.settings_file(home)
        if expected is not None and version(p) != expected:
            raise Conflict("설정 파일이 그 사이 바뀌었다 — 다시 읽고 고친다")
        if p.exists():
            _backup(home, p)
        _write_atomic(p, text)
    return version_of(text)


UNDONE = "undone-latest.yaml"


class NothingToUndo(Exception):
    """되돌릴 앞 판이 없다."""


def undo(home: Path) -> tuple[str, str]:
    """마지막 저장 하나를 되돌린다(어디서 저장했든) — 가장 새 백업을 설정 파일로 다시 쓰고 그 백업을 지운다. 그래서 거듭 부르면 더 앞으로 간다.
    되돌리기 전의 파일은 backups/undone-latest.yaml 에 하나만 남긴다(되돌린 것을 되돌릴 때 사람이 꺼낸다). (새 판, 쓴 백업 이름)."""
    with _locked(home):
        d = paths.backups_dir(home)
        backups = sorted(d.glob("settings-*.yaml")) if d.exists() else []
        if not backups:
            raise NothingToUndo("되돌릴 앞 판이 없다")
        latest = backups[-1]
        data = latest.read_bytes()
        parse(data)                                                 # 깨진 백업은 쓰지 않는다
        p = paths.settings_file(home)
        if p.exists():
            _write_atomic(d / UNDONE, p.read_text(encoding="utf-8"))
        _write_atomic(p, data.decode("utf-8"))
        latest.unlink()
    return version_of(data), latest.name


# ---- 시작 시도 · last-good ------------------------------------------------------------------------------------------------

def _boot(home: Path) -> dict:
    try:
        d = json.loads((paths.state_dir(home) / BOOT_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def boot_attempts(home: Path, ver: str) -> int:
    d = _boot(home)
    return int(d.get("attempts", 0)) if d.get("version") == ver else 0


def boot_begin(home: Path, ver: str) -> int:
    """이 판으로 시작한다고 적는다 — 몇 번째인지 돌려준다."""
    n = boot_attempts(home, ver) + 1
    paths.state_dir(home).mkdir(parents=True, exist_ok=True)
    _write_atomic(paths.state_dir(home) / BOOT_FILE, json.dumps({"version": ver, "attempts": n}))
    return n


def boot_ok(home: Path, ver: str) -> bool:
    """이 판으로 잘 돌았다 — 지금 파일이 아직 그 판이면 last-good 으로 올리고 시도 기록을 지운다."""
    p = paths.settings_file(home)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if version_of(text) != ver:
        return False
    _write_atomic(paths.last_good_file(home), text)
    (paths.state_dir(home) / BOOT_FILE).unlink(missing_ok=True)
    return True


def mark_good(home: Path) -> None:
    """지금 파일을 그대로 last-good 으로(옮기기가 처음 쓸 때)."""
    p = paths.settings_file(home)
    _write_atomic(paths.last_good_file(home), p.read_text(encoding="utf-8"))
