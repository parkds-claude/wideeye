"""맥 왓츠앱 앱의 DB(ChatStorage.sqlite, 암호 없음)를 읽기 전용으로 — 설계 docs/exec-plans/2026-10-28-whatsapp-design.md.

- D1: 문자 수집기와 같은 꼴로 연다(`mode=ro` · busy_timeout · closing). 쓰지 않는다. 엔진의 전체 디스크 접근으로 열린다.
- D3: 1:1(유형 0) · 단톡(1)만. 방송 목록(2) · 채널(5) · 상태(`status@broadcast`) · 지운 대화 · 숨긴(잠근) 대화는 뺀다.
- D4: 시스템 글 · 지운 글은 빼고, 글이 없는 미디어는 "[첨부]".
- D5 · D18: 안 읽음 = 대화의 안 읽은 수 N → 마지막 받은 글 N건(음수 = 직접 '안 읽음' 표시 → 1건).
- D11: 앱 본체가 꺼져 있으면 '수집 중단' — 꺼진 동안 새 글이 DB 에 들어오는지 확인하지 못했다(없음으로 보이지 않게).
열 이름 · 유형 번호는 2026-10-28 맥북 실측. 생성자에서는 아무것도 읽지 않는다.
"""
from __future__ import annotations
import os
import re
import sqlite3
import subprocess
from collections import defaultdict
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path

from ..models import PLACEHOLDER_ATTACHMENT, Event, Source
from .base import Collector
from .imessage import APPLE_EPOCH, apple_to_dt

DB = Path.home() / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite"
SYSTEM_TYPES = frozenset({6, 10, 14})                 # 단톡 들고남 · 암호화 안내 같은 시스템 글, 14 = 지운 글
MEDIA_TYPES = frozenset({1, 2, 3, 4, 5, 8, 11, 15})   # 사진 · 영상 · 음성 · 연락처 · 위치 · 문서 · GIF · 스티커
PHONE_JID = re.compile(r"^(\d+)@s\.whatsapp\.net$")
BIDI = re.compile("[‎‏‪-‮]")
MENTION = re.compile(r"@(\d{7,})")

SQL = """
SELECT m.Z_PK AS pk, m.ZSTANZAID AS stanza, m.ZMESSAGEDATE AS date, m.ZISFROMME AS mine, m.ZMESSAGETYPE AS type, m.ZTEXT AS text,
       m.ZFROMJID AS from_jid, m.ZPUSHNAME AS push, s.ZCONTACTJID AS chat_jid, s.ZPARTNERNAME AS chat_name, s.ZSESSIONTYPE AS kind,
       g.ZMEMBERJID AS member_jid, g.ZCONTACTNAME AS member_name, g.ZFIRSTNAME AS member_first
FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER
WHERE s.ZSESSIONTYPE IN (0, 1) AND COALESCE(s.ZHIDDEN, 0) = 0 AND COALESCE(s.ZREMOVED, 0) = 0
  AND s.ZCONTACTJID NOT LIKE '%@broadcast' AND m.ZMESSAGEDATE >= :since
ORDER BY m.ZMESSAGEDATE, m.Z_PK
"""
SQL_UNREAD = ("SELECT ZCONTACTJID, ZUNREADCOUNT FROM ZWACHATSESSION WHERE ZSESSIONTYPE IN (0, 1)"
              " AND COALESCE(ZHIDDEN, 0) = 0 AND COALESCE(ZREMOVED, 0) = 0")      # 글 SQL 과 같은 대화만 — 지운 대화의 0 이 산 대화를 덮지 않게(검토 4)
# 번호를 숨긴 꼴(`…@lid`) ↔ 번호 — 같은 폴더의 왓츠앱 연락처. 2026-10-28 실측: 지난 7일 받은 글의 보낸 사람이 모두 lid 였고(5명),
# 다섯 모두 여기에 번호 짝이 있었다(lid 하나에 번호 하나). 이어야 문자 · 전화와 같은 사람으로 묶이고 맥 연락처 이름이 걸린다(설계 D6 · D7)
CONTACTS = "ContactsV2.sqlite"
SQL_LID = "SELECT ZLID, ZPHONENUMBER FROM ZWAADDRESSBOOKCONTACT WHERE ZLID IS NOT NULL AND ZPHONENUMBER IS NOT NULL"
PHONE = re.compile(r"^\+\d{7,}$")


def sender_of(jid: str | None) -> str:
    """번호 JID → 문자 · 전화와 같은 꼴 "+<번호>"(같은 사람으로 묶인다 — D7). 번호를 숨긴 상대(`…@lid`) 등은 JID 그대로."""
    if not jid:
        return ""
    p = PHONE_JID.match(jid)
    return "+" + p.group(1) if p else jid


def clean_name(s: str | None) -> str:
    """대화 이름의 방향 표시 문자(번호 이름은 U+202A … U+202C 로 감싸여 있다)를 뗀다."""
    return BIDI.sub("", s or "").strip()


def mentions_me(text: str, phone_tails: list[str]) -> bool:
    """`@<번호>` 멘션이 내 번호 끝자리로 끝나는가(D9). 번호를 숨긴 꼴로 부른 멘션은 잡지 못한다."""
    tails = [re.sub(r"\D", "", str(t)) for t in phone_tails or []]
    return any(n.endswith(t) for n in MENTION.findall(text or "") for t in tails if t)


def lid_phones(path: Path) -> dict[str, str]:
    """`<id>@lid` → "+<번호>"(짝이 있는 것만). 파일이 없거나 못 읽으면 빈 사전 — 수집은 멈추지 않고 lid 그대로 보인다(이름은 대화 이름)."""
    if not Path(path).exists():
        return {}
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as c:
            c.execute("PRAGMA busy_timeout = 5000")
            pairs = defaultdict(set)
            for lid, pn in c.execute(SQL_LID):
                if PHONE.match(str(pn)):
                    pairs[str(lid)].add(str(pn))
        return {lid: next(iter(pns)) for lid, pns in pairs.items() if len(pns) == 1}     # 짝이 둘 이상이면 고르지 않는다(검토 5)
    except sqlite3.Error:
        return {}


def link_for(chat_jid: str) -> str:
    p = PHONE_JID.match(chat_jid or "")
    return f"whatsapp://send?phone={p.group(1)}" if p else "whatsapp://"


def mark_read(events: list[Event], unread_by_chat: dict[str, int]) -> None:
    """대화의 안 읽은 수 N → 마지막 받은 글 N건만 안 읽음. N < 0(직접 '안 읽음'으로 표시)은 1건. 내 글은 읽음.
    `kakao.mark_read` 와 같은 뜻 — 왓츠앱 글 번호는 숫자가 아니라 받은 때 · Z_PK 로 줄 세운다(D18)."""
    incoming: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        if e.is_from_me:
            e.is_read = True
        else:
            incoming[e.chat_id].append(e)
    for chat_id, evs in incoming.items():
        n = unread_by_chat.get(chat_id) or 0
        n = 1 if n < 0 else n
        evs.sort(key=lambda e: (e.received_at, e.raw["pk"]))
        for i, e in enumerate(evs):
            e.is_read = i < len(evs) - n


def app_running() -> bool:
    return subprocess.run(["pgrep", "-x", "WhatsApp"], capture_output=True).returncode == 0


def _body(r) -> str | None:
    """넣을 글 — None 이면 뺀다(D4)."""
    if r["type"] in SYSTEM_TYPES:
        return None
    t = (r["text"] or "").strip()
    if t:
        return t
    return PLACEHOLDER_ATTACHMENT if r["type"] in MEDIA_TYPES else None


class WhatsAppCollector(Collector):
    name = "whatsapp"
    poll_seconds = 30

    def __init__(self, db_path: Path = DB, my_phone_tails: list[str] | None = None, names: Callable[[str], str] | None = None,
                 running: Callable[[], bool] = app_running):
        """names: 번호 → 맥 연락처 이름(없으면 ""). running: 앱 본체가 켜져 있는가(시험에서 끼워 넣는다)."""
        self.db_path = Path(db_path)
        self.my_phone_tails = list(my_phone_tails or [])
        self.names = names or (lambda handle: "")
        self.running = running

    def _connect(self):
        c = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        c.execute("PRAGMA busy_timeout = 5000")
        c.row_factory = sqlite3.Row
        return c

    def collect(self, since: datetime) -> list[Event]:
        try:
            os.stat(self.db_path)          # Path.exists() 는 권한 오류도 False — 권한 문제를 '앱 없음'으로 안내하지 않게(검토 I2)
        except FileNotFoundError:
            raise RuntimeError("왓츠앱 맥 앱의 자료가 없다 — 앱을 깔고 로그인") from None
        except OSError:
            raise RuntimeError("왓츠앱 자료를 열 권한이 없다 — 엔진 파이썬에 전체 디스크 접근 권한") from None
        if not self.running():
            raise RuntimeError("왓츠앱 앱이 꺼져 있어 새 글을 볼 수 없다")
        out: list[Event] = []
        with closing(self._connect()) as c:
            rows = c.execute(SQL, {"since": since.timestamp() - APPLE_EPOCH}).fetchall()
            unread = {str(j): int(n or 0) for j, n in c.execute(SQL_UNREAD)}
        phones = lid_phones(self.db_path.parent / CONTACTS)

        def resolve(jid):
            s = sender_of(jid)
            return phones.get(s, s)
        for r in rows:
            text, at = _body(r), apple_to_dt(r["date"])
            if text is None or at is None:
                continue
            group, mine = r["kind"] == 1, bool(r["mine"])
            sid = "me" if mine else resolve(r["member_jid"] if group else (r["from_jid"] or r["chat_jid"]))   # 1:1 받은 글은 상대가 보냈다(검토 6)
            link = link_for(r["chat_jid"])
            if group:
                who = (self.names(sid) if not mine else "") or clean_name(r["member_name"]) or clean_name(r["member_first"]) \
                    or clean_name(r["push"]) or sid
                chat = clean_name(r["chat_name"]) or r["chat_jid"]
            else:
                partner = resolve(r["chat_jid"])
                if partner.startswith("+"):
                    link = f"whatsapp://send?phone={partner[1:]}"
                chat = self.names(partner) or clean_name(r["chat_name"]) or (clean_name(r["push"]) if not mine else "") or partner
                who = chat if not mine else "me"
            out.append(Event(source=Source.WHATSAPP, chat_id=r["chat_jid"], message_id=r["stanza"] or f"pk{r['pk']}", chat_name=chat,
                             sender_id=sid, sender_name=who, text=text, received_at=at, is_from_me=mine, is_read=mine, is_group=group,
                             mentioned_me=group and not mine and mentions_me(text, self.my_phone_tails), link=link,
                             raw={"pk": r["pk"], "type": r["type"]}))
        mark_read(out, unread)
        return out
