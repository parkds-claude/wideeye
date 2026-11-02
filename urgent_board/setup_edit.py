"""설정 문서 고치기 — 설정 명령(`urgentboard setup …`, tools/setup_cli.py)이 부른다(설계 2026-10-16 AI 와 함께 설정 2-2).

순수 함수다: 문서(dict)를 받아 그 자리에서 고치고, 사람(AI)에게 알릴 말을 돌려준다. 꼴 검사와 저장은 부르는 쪽이
(`config.problems` → `settings_doc.save` — 판 확인 · 백업). 보낸 사람 · 방은 전체 목록의 가르치기와 같은 함수
(`settings_doc.teach_sender` · `teach_room`)로 적는다 — 한 사람(방)은 한 상자에만 있다.

상자는 열쇠(key) 또는 이름으로 가리킨다. `개인` · `개인 · 기타` · `personal` · 설정의 `etc_name` 은 여섯째 상자(개인 · 기타)다.
진행 중인 상자는 다섯까지만 만든다 — 화면은 다섯 + 개인 · 기타의 여섯 칸이다(여섯째를 넘기는 길은 자세한 설정 화면에만 남긴다)."""
from __future__ import annotations
import re
import unicodedata
from datetime import time

from . import settings_doc

PERSONAL = settings_doc.PERSONAL
OWN_MAX = 5                                  # 진행 중인 상자 — 화면의 사업 칸 수(board.OWN_SLOTS 와 같다)
PERSONAL_WORDS = ("개인", "개인 · 기타", "개인·기타", "개인 기타", "기타", "personal")
KINDS = ("word", "sender", "account", "room")
ROOM_SOURCES = tuple(settings_doc.ROOM_FIELD)
EXAMPLE_KEY = "example"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SHORT_WORD = 2                               # 이 길이 이하의 낱말은 넓게 걸린다 — 알리기만


class EditError(Exception):
    """고칠 수 없다 — 사람이 읽을 까닭 한 줄."""


def _norm(s: str) -> str:
    """이름 견주기 — NFC(맥의 파일 이름 · 붙여 넣은 글은 NFD 일 수 있다) · 빈칸 접기 · 대소문자 무시."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(s or ""))).strip().casefold()


def boxes(doc: dict) -> list[dict]:
    return [b for b in doc.get("boxes") or [] if isinstance(b, dict)]


def active(doc: dict) -> list[dict]:
    return [b for b in boxes(doc) if b.get("active", True) is True]


def etc_name(doc: dict) -> str:
    return str(doc.get("etc_name") or "개인 · 기타")


def box_names(doc: dict) -> list[str]:
    """가리킬 수 있는 이름(오류 글에 보인다)."""
    return [str(b.get("name") or b.get("key")) for b in boxes(doc)] + [etc_name(doc)]


def find_box(doc: dict, ref: str) -> str:
    """상자 열쇠 또는 이름 → 열쇠(개인 · 기타는 PERSONAL). 못 찾으면 EditError(있는 이름을 함께)."""
    want = _norm(ref)
    if not want:
        raise EditError("상자를 적지 않았다")
    for b in boxes(doc):
        if str(b.get("key")) == str(ref).strip():
            return str(b["key"])
    for b in boxes(doc):
        if _norm(b.get("name")) == want:
            return str(b["key"])
    if want in {_norm(x) for x in (*PERSONAL_WORDS, etc_name(doc))}:
        return PERSONAL
    raise EditError(f"'{ref}' 상자가 없다 — 있는 상자: " + " · ".join(box_names(doc)))


def _box(doc: dict, key: str) -> dict:
    return next(b for b in boxes(doc) if b.get("key") == key)


def _add_unique(xs: list, values, fold=str.casefold) -> list[str]:
    """목록에 없는 것만 더한다(대소문자 무시). 더한 것."""
    have = {fold(str(x)) for x in xs}
    out = []
    for v in values:
        v = str(v).strip()
        if v and fold(v) not in have:
            xs.append(v)
            have.add(fold(v))
            out.append(v)
    return out


# ---- 나 -------------------------------------------------------------------------------------------------------------

def set_me(doc: dict, names=(), emails=(), clear: bool = False) -> list[str]:
    """나를 부르는 말 · 내 메일 주소를 더한다(clear 면 둘 다 비우고). 알릴 말."""
    me = doc.setdefault("me", {})
    for f in ("names", "addresses"):
        if clear or not isinstance(me.get(f), list):
            me[f] = []
    bad = [e for e in emails if not EMAIL_RE.match(str(e).strip())]
    if bad:
        raise EditError(f"메일 주소가 아니다: {', '.join(bad)}")
    notes = []
    got = _add_unique(me["names"], names)
    if got:
        notes.append("나를 부르는 말: " + ", ".join(got))
    got = _add_unique(me["addresses"], [str(e).strip().lower() for e in emails])
    if got:
        notes.append("내 메일 주소: " + ", ".join(got))
    return notes


def connected_emails(doc: dict) -> list[str]:
    return [str(g.get("email") or "").lower() for g in ((doc.get("accounts") or {}).get("gmail") or []) if isinstance(g, dict) and g.get("email")]


def missing_addresses(doc: dict) -> list[str]:
    """연결한 메일 계정 가운데 '내 메일 주소'에 없는 것 — 내가 보낸 메일 · 나에게 온 메일을 가르는 데 쓴다."""
    mine = {str(a).lower() for a in (doc.get("me") or {}).get("addresses") or []}
    return [e for e in connected_emails(doc) if e not in mine]


# ---- 출처 · 선택 단계 ----------------------------------------------------------------------------------------------------

def set_source(doc: dict, kind: str, on: bool) -> None:
    if kind not in ("imessage", "telegram", "kakao", "whatsapp", "calls", "teams"):
        raise EditError("켜고 끌 수 있는 출처: imessage · telegram · kakao · whatsapp(왓츠앱) · calls(부재중 전화) · teams(팀즈 알림)(메일은 계정을 연결하면 켜진다)")
    acc = doc.setdefault("accounts", {})
    acc[kind] = {**(acc.get(kind) if isinstance(acc.get(kind), dict) else {}), "enabled": bool(on)}


def set_summary(doc: dict, on: bool) -> None:
    s = doc.setdefault("summary", {})
    s["enabled"] = bool(on)


def set_hours(doc: dict, start: str, end: str) -> None:
    for t in (start, end):
        if not settings_doc.TIME_RE.match(str(t)):
            raise EditError(f"시각은 HH:MM 꼴(예: 09:00): {t}")
    if time.fromisoformat(start) >= time.fromisoformat(end):
        raise EditError("끝나는 시각이 시작보다 늦어야 한다")
    doc.setdefault("workhours", {}).update(start=str(start), end=str(end))


def set_tab5(doc: dict, serial: str) -> None:
    doc.setdefault("display", {})["tab5_usb_serial"] = str(serial or "").strip()


# ---- 상자 -------------------------------------------------------------------------------------------------------------

def _rules_of(b: dict) -> int:
    return sum(len(b.get(f) or []) for f in ("mail_accounts", "senders", "kakao_rooms", "telegram_rooms", "keywords"))


def is_untouched_example(b: dict) -> bool:
    """처음 깔 때 들어가는 예시 상자를 손대지 않은 채인가(settings.example.yaml — 2026-10-16 까지의 예시는 낱말 '예시' 하나를 가졌다)."""
    return (b.get("key") == EXAMPLE_KEY and b.get("name") == "예시 사업" and list(b.get("keywords") or []) in ([], ["예시"])
            and _rules_of(b) == len(b.get("keywords") or []) and not (b.get("about") or "").strip())


def _free_color(doc: dict, skip: str = "") -> str:
    used = {b.get("color") for b in active(doc) if b.get("key") != skip}
    return next((c for c in settings_doc.BUSINESS_COLORS if c not in used), "")


def _check_color(doc: dict, color: str, skip: str = "") -> None:
    if color not in settings_doc.BUSINESS_COLORS:
        raise EditError("색은 " + " · ".join(settings_doc.BUSINESS_COLORS) + " 가운데 하나")
    clash = next((b for b in active(doc) if b.get("key") != skip and b.get("color") == color), None)
    if clash:
        raise EditError(f"'{clash.get('name')}' 상자가 이미 그 색을 쓴다 — 남은 색: " +
                        " · ".join(c for c in settings_doc.BUSINESS_COLORS if c not in {b.get('color') for b in active(doc) if b.get('key') != skip}))


def _new_key(doc: dict, name: str) -> str:
    """열쇠 — 이름이 영문이면 그것으로, 아니면 box-1 · box-2 …(화면에는 보이지 않는다)."""
    taken = {str(b.get("key")) for b in boxes(doc)} | settings_doc.RESERVED_KEYS
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    if slug and settings_doc.KEY_RE.match(slug) and slug not in taken:
        return slug
    n = 1
    while f"box-{n}" in taken:
        n += 1
    return f"box-{n}"


def add_box(doc: dict, name: str, color: str = "") -> tuple[str, list[str]]:
    """진행 중인 상자를 하나 더한다 — (열쇠, 알릴 말). 손대지 않은 예시 상자는 이때 지운다."""
    name = str(name or "").strip()
    if not name:
        raise EditError("상자 이름이 비었다")
    if _norm(name) in {_norm(x) for x in (*PERSONAL_WORDS, etc_name(doc))}:
        raise EditError(f"'{name}' 은 여섯째 상자(개인 · 기타)의 이름이다 — 다른 이름으로")
    notes = []
    doc["boxes"] = boxes(doc)
    same = next((b for b in doc["boxes"] if _norm(b.get("name")) == _norm(name)), None)
    if same:                                                     # 같은 명령을 다시 불러도 같은 결과(AI 가 다시 시도한다)
        notes = [f"'{name}' 상자는 이미 있다(열쇠 {same['key']} · 색 {same.get('color') or '없음'}) — 바꾸지 않았다"]
        if color and color != same.get("color"):
            notes.append(f"색은 바꾸지 않았다 — 바꾸려면 box set \"{name}\" --color {color}")
        return str(same["key"]), notes
    ex = next((b for b in doc["boxes"] if is_untouched_example(b)), None)
    if ex:
        doc["boxes"].remove(ex)
        notes.append("손대지 않은 예시 상자를 지웠다")
    if len(active(doc)) >= OWN_MAX:
        raise EditError(f"진행 중인 상자는 {OWN_MAX}개까지다(여섯째 칸은 개인 · 기타) — 끝난 일을 먼저 쉬게 한다: box set <상자> --active no")
    if color:
        _check_color(doc, color)
    else:
        color = _free_color(doc)
    key = _new_key(doc, name)
    doc["boxes"].append({"key": key, "name": name, "color": color, "active": True, "about": "", "mail_accounts": [], "senders": [],
                         "kakao_rooms": [], "telegram_rooms": [], "keywords": []})
    return key, notes + [f"상자 '{name}' 을 더했다(열쇠 {key} · 색 {color or '없음'})"]


def set_box(doc: dict, ref: str, name: str | None = None, color: str | None = None, about: str | None = None,
            active_: bool | None = None) -> list[str]:
    key = find_box(doc, ref)
    if key == PERSONAL:
        if name and not (color or about is not None or active_ is not None):
            if any(_norm(b.get("name")) == _norm(name) for b in boxes(doc)):
                raise EditError(f"'{name}' 상자가 이미 있다")
            doc["etc_name"] = str(name).strip()
            return [f"여섯째 상자 이름: {doc['etc_name']}"]
        raise EditError("여섯째 상자(개인 · 기타)는 이름만 바꿀 수 있다")
    b = _box(doc, key)
    notes = []
    if name is not None:
        name = str(name).strip()
        if not name:
            raise EditError("상자 이름이 비었다")
        if _norm(name) in {_norm(x) for x in (*PERSONAL_WORDS, etc_name(doc))}:
            raise EditError(f"'{name}' 은 여섯째 상자(개인 · 기타)의 이름이다 — 다른 이름으로")
        if any(_norm(x.get("name")) == _norm(name) for x in boxes(doc) if x.get("key") != key):
            raise EditError(f"'{name}' 상자가 이미 있다")
        b["name"] = name
        notes.append(f"이름: {name}")
    if active_ is not None:
        if active_ and b.get("active", True) is not True and len(active(doc)) >= OWN_MAX:
            raise EditError(f"진행 중인 상자는 {OWN_MAX}개까지다 — 다른 상자를 먼저 쉬게 한다")
        b["active"] = bool(active_)
        notes.append("진행 중" if active_ else "쉼(화면에서 빠지고 그 메시지는 개인 · 기타로)")
        if active_ and b.get("color") and any(x.get("color") == b["color"] for x in active(doc) if x.get("key") != key):
            b["color"] = _free_color(doc, skip=key)
            notes.append(f"색이 겹쳐 바꿨다: {b['color'] or '없음'}")
    if color is not None:
        _check_color(doc, color, skip=key)
        b["color"] = color
        notes.append(f"색: {color}")
    if about is not None:
        b["about"] = str(about).strip()
        notes.append("설명을 적었다" if b["about"] else "설명을 비웠다")
    return notes


def remove_box(doc: dict, ref: str) -> str:
    key = find_box(doc, ref)
    if key == PERSONAL:
        raise EditError("여섯째 상자(개인 · 기타)는 지울 수 없다")
    b = _box(doc, key)
    doc["boxes"] = [x for x in boxes(doc) if x.get("key") != key]
    watched = [{"source": src, "chat_id": str(r["id"])} for src, f in settings_doc.ROOM_FIELD.items()
               for r in b.get(f) or [] if isinstance(r, dict) and r.get("watch") and r.get("id") not in (None, "")]
    have = {(str(w.get("source")), str(w.get("chat_id"))) for w in doc.get("watch_rooms") or [] if isinstance(w, dict)}
    watched = [w for w in watched if (w["source"], w["chat_id"]) not in have]
    if watched:                                                  # 지켜보던 방은 계속 지켜본다(개인 · 기타 상자에서)
        doc["watch_rooms"] = list(doc.get("watch_rooms") or []) + watched
    return str(b.get("name") or key)


def order_boxes(doc: dict, refs: list[str]) -> list[str]:
    """적은 상자를 그 차례로 맨 앞에, 나머지는 원래 차례대로 뒤에. 화면의 상자 차례가 이것이다."""
    keys = []
    for r in refs:
        k = find_box(doc, r)
        if k == PERSONAL:
            raise EditError("여섯째 상자(개인 · 기타)는 늘 마지막이다")
        if k not in keys:
            keys.append(k)
    rest = [b for b in boxes(doc) if b.get("key") not in keys]
    doc["boxes"] = [_box(doc, k) for k in keys] + rest
    return [str(b.get("name")) for b in doc["boxes"]]


# ---- 기준 -------------------------------------------------------------------------------------------------------------

def _word_owner(doc: dict, word: str, skip: str) -> str:
    for b in boxes(doc):
        if b.get("key") != skip and any(_norm(w) == _norm(word) for w in b.get("keywords") or []):
            return str(b.get("name"))
    return ""


def add_rule(doc: dict, ref: str, kind: str, values: list[str], source: str = "", watch: bool | None = None, room_name: str = "") -> list[str]:
    """기준 더하기 — 알릴 말. 보낸 사람 · 메일 계정 · 방은 한 상자에만(다른 곳에서 뺀다). 방의 watch: True 지켜봄 · False 끔 · None 그대로."""
    if kind not in KINDS:
        raise EditError("기준의 종류: word(낱말) · sender(보낸 사람) · account(메일 계정) · room(방)")
    values = [str(v).strip() for v in values if str(v).strip()]
    if not values:
        raise EditError("더할 값을 적지 않았다")
    key = find_box(doc, ref)
    where = etc_name(doc) if key == PERSONAL else str(_box(doc, key).get("name"))
    notes: list[str] = []
    if kind == "word":
        if key == PERSONAL:
            raise EditError("개인 · 기타에는 낱말을 두지 않는다 — 다른 일과 헷갈리는 낱말이면 `unconfirmed add` 로 확정 전 일에")
        got = _add_unique(_box(doc, key).setdefault("keywords", []), values)
        for w in got:
            if len(w) <= SHORT_WORD:
                notes.append(f"낱말 '{w}' 은 짧아 넓게 걸린다 — 더 구체적인 말이 낫다")
            other = _word_owner(doc, w, key)
            if other:
                notes.append(f"낱말 '{w}' 이 '{other}' 상자에도 있다 — 둘 다 걸리면 더 긴 낱말이 이긴다")
        notes.insert(0, f"{where} 낱말 +{len(got)}: {', '.join(got)}" if got else f"{where}: 이미 있는 낱말")
    elif kind == "sender":
        mine = (doc.get("me") or {}).get("personal_senders") if key == PERSONAL else _box(doc, key).get("senders")
        had = {str(x).strip().lower() for x in mine or []}
        new = [v for v in values if settings_doc.normalize_phone(v).lower() not in had and v.lower() not in had]
        for v in new:                                            # 이미 그 상자에 있는 사람은 건드리지 않는다(순서가 바뀌면 '바뀐 것 없음'이 아니게 된다)
            settings_doc.teach_sender(doc, v, key)
        notes.append(f"{where} 보낸 사람 +{len(new)}: {', '.join(new)}(다른 상자에 있었다면 거기서는 뺐다)" if new else f"{where}: 이미 있는 보낸 사람")
    elif kind == "account":
        bad = [v for v in values if not EMAIL_RE.match(v)]
        if bad:
            raise EditError(f"메일 주소가 아니다: {', '.join(bad)}")
        for v in (x.lower() for x in values):
            here = (doc.get("me") or {}).get("personal_accounts") if key == PERSONAL else _box(doc, key).get("mail_accounts")
            if v in {str(x).lower() for x in here or []}:
                continue                                         # 이미 여기 있다 — 건드리지 않는다
            _drop_account(doc, v)
            if key == PERSONAL:
                doc.setdefault("me", {}).setdefault("personal_accounts", []).append(v)
            else:
                _box(doc, key).setdefault("mail_accounts", []).append(v)
        if not set(x.lower() for x in values) <= set(connected_emails(doc)):
            notes.append("아직 연결하지 않은 메일 계정이다 — 연결하면 그 계정으로 온 메일이 이 상자로 간다")
        notes.insert(0, f"{where} 메일 계정 +{len(values)}: {', '.join(values)}(그 계정으로 온 메일은 모두 이 상자)")
    else:
        if source not in ROOM_SOURCES:
            raise EditError("방은 --source kakao 또는 telegram 을 함께 적는다")
        field = settings_doc.ROOM_FIELD[source]
        for v in values:
            if not settings_doc.ROOM_ID_RE.match(v):
                raise EditError(f"방 번호는 숫자다(who 가 보이는 번호): {v}")
            here = ([str(w.get("chat_id")) for w in doc.get("watch_rooms") or [] if isinstance(w, dict) and w.get("source") == source]
                    if key == PERSONAL else [str(r.get("id")) for r in _box(doc, key).get(field) or [] if isinstance(r, dict)])
            if v not in here:                                    # 이미 그 상자의 방이면 옮기지 않는다(지켜보기만 아래에서)
                settings_doc.teach_room(doc, source, v, room_name, key)
            if watch is not None:
                _set_watch(doc, source, v, key, watch)
        notes.append(f"{where} {'카톡' if source == 'kakao' else '텔레그램'} 방 {len(values)}개"
                     + {True: " · 지켜봄(나를 부르지 않은 글도 한 줄로)", False: " · 지켜보지 않음", None: ""}[watch])
    return notes


def _drop_account(doc: dict, email: str) -> None:
    for b in boxes(doc):
        b["mail_accounts"] = [x for x in b.get("mail_accounts") or [] if str(x).lower() != email]
    me = doc.setdefault("me", {})
    me["personal_accounts"] = [x for x in me.get("personal_accounts") or [] if str(x).lower() != email]


def _set_watch(doc: dict, source: str, chat_id: str, key: str, on: bool = True) -> None:
    if key == PERSONAL:
        rest = [w for w in doc.get("watch_rooms") or [] if not (isinstance(w, dict) and w.get("source") == source and str(w.get("chat_id")) == chat_id)]
        doc["watch_rooms"] = rest + ([{"source": source, "chat_id": chat_id}] if on else [])
        return
    for r in _box(doc, key).get(settings_doc.ROOM_FIELD[source]) or []:
        if isinstance(r, dict) and str(r.get("id")) == chat_id:
            if on:
                r["watch"] = True
            else:
                r.pop("watch", None)


def remove_rule(doc: dict, ref: str, kind: str, values: list[str], source: str = "") -> list[str]:
    if kind not in KINDS:
        raise EditError("기준의 종류: word · sender · account · room")
    key = find_box(doc, ref)
    values = [str(v).strip() for v in values if str(v).strip()]
    me = doc.get("me") if isinstance(doc.get("me"), dict) else {}
    if kind == "word":
        if key == PERSONAL:
            raise EditError("개인 · 기타에는 낱말이 없다")
        field, target = "keywords", _box(doc, key)
    elif kind == "sender":
        field, target = ("personal_senders", me) if key == PERSONAL else ("senders", _box(doc, key))
        values = [settings_doc.normalize_phone(v) for v in values]
    elif kind == "account":
        field, target = ("personal_accounts", me) if key == PERSONAL else ("mail_accounts", _box(doc, key))
    else:
        if source not in ROOM_SOURCES:
            raise EditError("방은 --source kakao 또는 telegram 을 함께 적는다")
        gone = []
        if key == PERSONAL:
            before = list(doc.get("watch_rooms") or [])
            doc["watch_rooms"] = [w for w in before if not (isinstance(w, dict) and w.get("source") == source and str(w.get("chat_id")) in values)]
            gone = [str(w.get("chat_id")) for w in before if w not in doc["watch_rooms"]]
        else:
            f = settings_doc.ROOM_FIELD[source]
            b = _box(doc, key)
            keep = [r for r in b.get(f) or [] if not (isinstance(r, dict) and str(r.get("id") or "") in values)]
            gone = [str(r.get("id")) for r in b.get(f) or [] if r not in keep]
            b[f] = keep
        missing = [v for v in values if v not in gone]
        return [f"방 -{len(gone)}"] + ([f"없던 방: {', '.join(missing)}"] if missing else [])
    xs = target.get(field) or []
    low = {v.casefold() for v in values}
    kept = [x for x in xs if str(x).casefold() not in low]
    gone = [x for x in xs if x not in kept]
    if gone:                                                     # 뺀 것이 있을 때만 쓴다 — 없던 칸 · 빈 '나'를 만들지 않는다
        target[field] = kept
    missing = [v for v in values if v.casefold() not in {str(g).casefold() for g in gone}]
    return [f"-{len(gone)}: {', '.join(map(str, gone))}" if gone else "뺀 것 없음"] + ([f"없던 것: {', '.join(missing)}"] if missing else [])


# ---- 확정 전 일 -----------------------------------------------------------------------------------------------------------

def add_unconfirmed(doc: dict, name: str, words: list[str]) -> list[str]:
    """아직 따내지 않은 일 — 그 낱말이 사업 낱말보다 길게 걸리면 개인 · 기타로 간다(상자를 차지하지 않는다)."""
    name = str(name or "").strip()
    words = [str(w).strip() for w in words if str(w).strip()]
    if not name or not words:
        raise EditError("이름과 낱말 하나 이상")
    us = doc.setdefault("unconfirmed", [])
    u = next((x for x in us if isinstance(x, dict) and _norm(x.get("name")) == _norm(name)), None)
    if u is None:
        u = {"name": name, "keywords": []}
        us.append(u)
    got = _add_unique(u.setdefault("keywords", []), words)
    return [f"확정 전 일 '{name}' 낱말 +{len(got)}: {', '.join(got)}" if got else f"확정 전 일 '{name}': 이미 있는 낱말"]


def remove_unconfirmed(doc: dict, name: str) -> None:
    us = doc.get("unconfirmed") or []
    keep = [x for x in us if not (isinstance(x, dict) and _norm(x.get("name")) == _norm(name))]
    if len(keep) == len(us):
        raise EditError(f"'{name}' 확정 전 일이 없다")
    doc["unconfirmed"] = keep
