"""옛 설정(코드 폴더의 config/businesses.yaml · config/rules.yaml · .env) → 설정 v2 문서 · 견주기(설계 4-5).

옮기기 전용이다 — 이 맥을 옮긴 뒤 다음 판에서 지운다. load_legacy 는 옛 config.load 를 그대로 옮긴 것이고(뜻을 바꾸지 않는다),
to_doc 은 같은 옛 파일을 v2 문서로 바꾼다. 둘로 만든 Settings 가 같아야 옮길 수 있다(compare — 화면까지의 판정은 tools/migrate.py).
.env 는 dotenv_values 로 읽는다(환경 변수를 더럽히지 않는다)."""
from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import NamedTuple

import yaml
from dotenv import dotenv_values

from . import config, paths, settings_doc
from .config import Business, Settings, Unconfirmed
from .workhours import WorkCalendar

PERSONAL_ACCOUNTS: frozenset = frozenset()   # 옛 코드는 개인 메일 주소를 박아 두었다 — 지금은 설정의 me.personal_accounts 에 적는다(옮기기 뒤에는 그 값이 정본)
EXTRA_HOLIDAYS_FILE = Path.home() / ".config" / "wideeye-legacy" / "extra_holidays.txt"     # 옛 공용 달력 도구의 회사 휴무 파일 자리
OLD_DISPLAY_DEFAULTS = {"fill_with_waiting": False, "tab5_usb_serial": "", "old_task_days": 14, "awaiting_days": 3, "feed_bots": True, "greeting_orgs": []}   # 옛 코드의 기본값(지금 기본값과 다르다 · old_task_days · awaiting_days · feed_bots 는 새 칸 — 같은 값)


class Raw(NamedTuple):
    b: dict
    r: dict
    env: dict
    extra_holidays: list[str]


def _extra_holidays(path: Path) -> list[str]:
    """옛 회사 휴무 파일(줄마다 'YYYY-MM-DD  # 메모') → 날짜 글자 목록. 없거나 틀린 줄은 건너뛴다(옛 코드와 같다)."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        head = line.split("#", 1)[0].strip()
        if not head:
            continue
        try:
            out.append(date.fromisoformat(head).isoformat())
        except ValueError:
            continue
    return out


def read_raw(root: Path, extra_holidays_file: Path = EXTRA_HOLIDAYS_FILE) -> Raw:
    root = Path(root)
    b = yaml.safe_load((root / "config" / "businesses.yaml").read_text(encoding="utf-8")) or {}
    r = yaml.safe_load((root / "config" / "rules.yaml").read_text(encoding="utf-8")) or {}
    env = {k: (v or "") for k, v in dotenv_values(root / ".env").items()} if (root / ".env").exists() else {}
    return Raw(b, r, env, _extra_holidays(extra_holidays_file))


def _gmail_accounts(raw: Raw) -> list[dict]:
    """GMAIL_ACCOUNTS(별칭:주소, …)를 mail_labels 차례로 — 화면 꼬리표의 우선순위가 그 차례다. 꼴이 틀린 항목은 옛 수집기처럼 건너뛴다."""
    labels = {str(k).lower(): str(v) for k, v in (raw.b.get("mail_labels") or {}).items()}
    prefix = raw.env.get("GMAIL_KEYCHAIN_PREFIX") or "WideEyeMail"
    accs = []
    for pair in (raw.env.get("GMAIL_ACCOUNTS") or "").split(","):
        alias, sep, email = pair.strip().partition(":")
        email = email.strip()
        if alias and sep and email:
            acc = {"alias": alias, "email": email, "token": f"keychain:{prefix}.{alias}/oauth2_token"}
            if email.lower() in labels:
                acc["label"] = labels[email.lower()]
            accs.append(acc)
    order = list(labels)
    return sorted(accs, key=lambda a: order.index(a["email"].lower()) if a["email"].lower() in order else len(order))


def _accounts(raw: Raw) -> dict:
    tg = bool(raw.env.get("TELEGRAM_API_ID") and raw.env.get("TELEGRAM_API_HASH"))
    return {"gmail": _gmail_accounts(raw), "telegram": {"enabled": tg}, "kakao": {"enabled": True}, "imessage": {"enabled": True}}


LEGACY_SOURCES = ("gmail", "kakao", "telegram", "imessage")     # 옛 설정이 알던 출처 — 왓츠앱(2026-10-28)은 옮기기로 켜지 않는다


def _summary(r: dict) -> dict:
    """옛 요약 설정 — 옛 코드는 켜짐이 기본이었고 모든 출처를 보냈다."""
    return {**config.SUMMARY_DEFAULTS, "enabled": True, "sources": list(LEGACY_SOURCES), **(r.get("summary") or {})}


def load_legacy(root: Path, extra_holidays_file: Path = EXTRA_HOLIDAYS_FILE) -> Settings:
    """옛 config.load 의 뜻 그대로 — 새 Settings 꼴로."""
    raw = read_raw(root, extra_holidays_file)
    b, r = raw.b, raw.r
    businesses = [Business(**x) for x in b.get("businesses", [])]
    config.assign_colors(businesses)
    cal = WorkCalendar(extra_workdays=config.parse_dates(r.get("extra_workdays")), extra_holidays=config.parse_dates(raw.extra_holidays))
    rules = config.build_rules(config.waiting_minutes({k: r[k] for k in settings_doc.RULE_KEYS if k in r}), cal)
    contacts = {str(i).strip().lower(): c["name"] for c in (b.get("contacts") or []) for i in c.get("ids", [])}
    return Settings(
        root=paths.CODE_ROOT, port=int(raw.env.get("BOARD_PORT") or paths.DEFAULT_PORT), db_path=Path(root) / "state" / "urgent.db",
        poll_seconds=dict(config.POLL_SECONDS),
        my_aliases=b.get("my_aliases", []), my_addresses={a.lower() for a in b.get("my_addresses", [])},
        my_phone_tails=b.get("my_phone_tails", []), businesses=businesses, rules=rules, contacts=contacts,
        mail_labels={str(k).lower(): str(v) for k, v in (b.get("mail_labels") or {}).items()},
        display={**OLD_DISPLAY_DEFAULTS, **(r.get("display") or {})},
        summary=_summary(r),
        handled={**config.HANDLED_DEFAULTS, **(r.get("handled") or {})},
        unconfirmed=[Unconfirmed(name=str(x["name"]), keywords=list(x.get("keywords") or [])) for x in (b.get("unconfirmed") or [])],
        watch_rooms=config.parse_rooms(r.get("watch_rooms")),
        accounts=_accounts(raw), personal_accounts=PERSONAL_ACCOUNTS, etc_name=config.ETC_NAME, service={"engine": "external"},
    )


def to_doc(raw: Raw, engine: str = "external", names_by_id: dict | None = None) -> dict:
    """옛 파일 → v2 문서. names_by_id 는 {(출처, 번호 글자): 최근 방 이름} — 번호 항목의 이름표(판정에 안 쓴다)."""
    b, r, names_by_id = raw.b, raw.r, names_by_id or {}
    watch = config.parse_rooms(r.get("watch_rooms"))
    placed: set = set()

    def rooms(src: str, ids, names) -> list[dict]:
        out = []
        for i in ids or []:
            key = (src, str(int(i)))
            room = {"id": key[1]}
            if names_by_id.get(key):
                room["name"] = names_by_id[key]
            if key in watch:
                room["watch"] = True
                placed.add(key)
            out.append(room)
        out.extend({"name": str(n)} for n in names or [])                 # 이름 목록은 번호와 짝짓지 않고 따로(옛 뜻: 번호 OR 이름)
        return out

    boxes = [{
        "key": x["key"], "name": x["name"], "color": x.get("color", ""), "active": bool(x.get("active", True)), "memo": "",
        "mail_accounts": list(x.get("mail_accounts") or []), "senders": list(x.get("senders") or []),
        "kakao_rooms": rooms("kakao", x.get("kakao_chat_ids"), x.get("kakao_chat_names")),
        "telegram_rooms": rooms("telegram", x.get("telegram_chat_ids"), []),
        "keywords": list(x.get("keywords") or []),
    } for x in b.get("businesses", [])]
    defaults = settings_doc.default_rules()
    rr = config.waiting_minutes({k: r[k] for k in settings_doc.RULE_KEYS if k in r})      # 옛 문턱(시간)은 분으로 견준다
    over = {k: rr[k] for k in settings_doc.RULE_KEYS if k in rr and not _same(rr[k], defaults.get(k))}
    hand = {k: v for k, v in {**config.HANDLED_DEFAULTS, **(r.get("handled") or {})}.items() if k != "sessions_dir"}
    return {
        "version": settings_doc.VERSION,
        "me": {"names": list(b.get("my_aliases") or []), "addresses": list(b.get("my_addresses") or []),
               "phone_tails": list(b.get("my_phone_tails") or []), "personal_accounts": sorted(PERSONAL_ACCOUNTS)},
        "accounts": _accounts(raw),
        "boxes": boxes,
        "etc_name": config.ETC_NAME,
        "unconfirmed": [{"name": str(u["name"]), "keywords": list(u.get("keywords") or []), "memo": ""} for u in b.get("unconfirmed") or []],
        "contacts": list(b.get("contacts") or []),
        "watch_rooms": [{"source": s, "chat_id": c} for s, c in sorted(watch - placed)],
        "display": {**OLD_DISPLAY_DEFAULTS, **(r.get("display") or {})},
        "summary": _summary(r),
        "handled": hand,
        "workhours": {"start": "09:00", "end": "18:00", "lunch": ["12:00", "13:00"], "holidays": "KR",
                      "extra_holidays": list(raw.extra_holidays),
                      "extra_workdays": [d.isoformat() for d in sorted(config.parse_dates(r.get("extra_workdays")))]},
        "server": {"port": int(raw.env.get("BOARD_PORT") or paths.DEFAULT_PORT)},
        "service": {"engine": engine},
        "rules": over,
    }


PATTERNS = ("urgent_ko", "urgent_en", "soft_ko", "deadline_suffix", "deadline_inner", "q_end_ko", "q_end_short_ko", "q_wh_ko", "req_ko",
            "q_en", "req_en", "courtesy", "resolved_ko", "noise_only", "auto_sender", "auto_subject", "quote_cut", "q_indirect_ko",
            "junk_ad_text", "junk_auto_text")


def _same(old, new) -> bool:
    """옛 규칙 값이 지금 기본값과 같은가 — 옛 파일에 없던 안쪽 칸(나중에 기본값에 더한 것, 예: 2026-10-17 junk.auto_domain_re)은 견주지 않는다.
    옛 코드는 그 칸을 몰랐으니 새 기본값을 따르는 것이 맞다(덮어쓰기로 옛 판을 통째로 적지 않게)."""
    if isinstance(old, dict) and isinstance(new, dict):
        return all(k in new and _same(v, new[k]) for k, v in old.items())
    return old == new


def _rules_print(r) -> dict:
    out = {k: (getattr(r, k).pattern, getattr(r, k).flags) for k in PATTERNS}
    return {**out, "thresholds": dict(r.thresholds), "client_titles": list(r.client_titles), "junk_labels": dict(r.junk_labels),
            "calendar": r.calendar}


def fingerprint(s: Settings) -> dict:
    """판정 · 화면에 쓰이는 것 전부(차례까지). 두 Settings 를 견줄 때 쓴다."""
    return {
        "businesses": [(b.key, b.name, b.color, b.active, list(b.kakao_chat_ids), list(b.kakao_chat_names), list(b.telegram_chat_ids),
                        list(b.mail_accounts), list(b.senders), list(b.keywords)) for b in s.businesses],
        "names_colors": ({b.key: b.name for b in s.businesses}, {b.key: b.color for b in s.businesses}),
        "mail_labels": list(s.mail_labels.items()),
        "me": (list(s.my_aliases), sorted(s.my_addresses), list(s.my_phone_tails), sorted(s.personal_accounts)),
        "contacts": dict(s.contacts),
        "unconfirmed": [(u.name, list(u.keywords)) for u in s.unconfirmed],
        "watch_rooms": sorted(s.watch_rooms),
        "rules": _rules_print(s.rules),
        "display": dict(s.display),
        "summary": dict(s.summary),
        "handled": dict(s.handled),
        "port": s.port, "etc_name": s.etc_name, "poll_seconds": dict(s.poll_seconds),
        "accounts": (tuple((g.get("alias"), g.get("email"), g.get("token")) for g in s.accounts.get("gmail") or []),
                     tuple(bool((s.accounts.get(k) or {}).get("enabled")) for k in ("telegram", "kakao", "imessage"))),
    }


def compare(a: Settings, b: Settings) -> list[str]:
    """다른 칸 목록("businesses: 다름" · "summary: skip_when"). 비어 있으면 같다."""
    fa, fb = fingerprint(a), fingerprint(b)
    out = []
    for k in fa:
        if fa[k] == fb[k]:
            continue
        if isinstance(fa[k], dict) and isinstance(fb[k], dict):
            diff = sorted(str(x) for x in set(fa[k]) | set(fb[k]) if fa[k].get(x) != fb[k].get(x))
            out.append(f"{k}: {', '.join(diff)}")
        else:
            out.append(f"{k}: 다름")
    return out
