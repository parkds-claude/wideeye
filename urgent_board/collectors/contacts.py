"""맥 연락처(주소록) — 번호·메일 주소 → 저장해 둔 이름. 읽기 전용. 문자 수집기가 번호 대신 이름을 보이게 쓴다(2026-10-14).

주소록은 `~/Library/Application Support/AddressBook` 아래 DB 여럿이다(맨 위 하나 + 계정마다 `Sources/<id>/`).
읽을 때마다 읽기 전용으로 열고 그 자리에서 닫는다. 읽지 못하면(권한 · 파일 없음) 빈 사전 — 번호가 보일 뿐 수집은 멈추지 않는다.
생성자에서는 아무것도 읽지 않는다(처음 물을 때 읽고, REFRESH_SECONDS 마다 다시 읽는다)."""
from __future__ import annotations
import logging
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

log = logging.getLogger("contacts")

ROOT = Path.home() / "Library" / "Application Support" / "AddressBook"
DB_NAME = "AddressBook-v22.abcddb"
REFRESH_SECONDS = 600            # 연락처를 고치면 길어도 10분 뒤에 화면에 반영된다
PHONES = "SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, r.ZNICKNAME, p.ZFULLNUMBER FROM ZABCDPHONENUMBER p JOIN ZABCDRECORD r ON r.Z_PK = p.ZOWNER"
EMAILS = "SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, r.ZNICKNAME, e.ZADDRESS FROM ZABCDEMAILADDRESS e JOIN ZABCDRECORD r ON r.Z_PK = e.ZOWNER"
HANGUL = re.compile(r"[가-힣]")


def phone_key(s: str | None) -> str:
    """번호를 견줄 꼴로: 숫자만, 한국 국가번호(82)는 0 으로. "+82 10-1234-5678" · "010-1234-5678" → "01012345678".
    숫자가 7자리 미만이거나 메일 주소면 ""(번호가 아니다)."""
    if not s or "@" in s:
        return ""
    d = re.sub(r"\D", "", s)
    if d.startswith("82") and (s.strip().startswith("+") or len(d) >= 11):
        d = "0" + d[2:]
    return d if len(d) >= 7 else ""


def display_name(first, last, org, nick) -> str:
    """연락처 앱이 보이는 이름: 한글 이름은 성+이름 붙여서, 그 밖은 이름 성. 이름이 없으면 별명 · 회사."""
    first, last = (first or "").strip(), (last or "").strip()
    if first and last:
        return last + first if HANGUL.search(last + first) else f"{first} {last}"
    return first or last or (nick or "").strip() or (org or "").strip()


class AddressBook:
    """`name_for(번호나 주소)` → 연락처 이름(없으면 ""). 문자 수집기의 `names` 로 넘긴다."""

    def __init__(self, root: Path = ROOT, clock=time.monotonic):
        self.root, self.clock = Path(root), clock
        self._names: dict[str, str] = {}
        self._loaded_at: float | None = None

    def name_for(self, handle: str | None) -> str:
        if not handle:
            return ""
        now = self.clock()
        if self._loaded_at is None or now - self._loaded_at >= REFRESH_SECONDS:
            self._names, self._loaded_at = self._load(), now
        key = handle.strip().lower() if "@" in handle else phone_key(handle)
        return self._names.get(key, "") if key else ""

    def _load(self) -> dict[str, str]:
        out: dict[str, str] = {}
        paths = [self.root / DB_NAME, *sorted(self.root.glob(f"Sources/*/{DB_NAME}"))]
        for p in paths:
            if not p.exists():
                continue
            try:
                with closing(sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True)) as c:
                    for *who, number in c.execute(PHONES):
                        k, name = phone_key(number), display_name(*who)
                        if k and name:
                            out.setdefault(k, name)            # 같은 번호가 둘이면 먼저 읽은 쪽
                    for *who, addr in c.execute(EMAILS):
                        name = display_name(*who)
                        if addr and name:
                            out.setdefault(addr.strip().lower(), name)
            except sqlite3.Error as e:                         # 권한 없음 · 꼴이 다른 DB — 이름 없이 간다
                log.warning("주소록을 읽지 못함 %s: %s", p.parent.name, e)
        return out
