"""맥 메시지 앱 DB(읽기 전용 URI). 조사 A §1: 탭백·시스템 행 제외, text 없으면 attributedBody, ns/s 자동."""
from __future__ import annotations
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ..models import PLACEHOLDER_ATTACHMENT, Event, Source
from .base import Collector

APPLE_EPOCH = 978307200


def apple_to_dt(v):
    if not v or v <= 0:
        return None
    secs = v / 1e9 if v >= 1_000_000_000_000 else v
    return datetime.fromtimestamp(secs + APPLE_EPOCH, tz=timezone.utc)


def _unarchive(blob: bytes):
    try:
        from Foundation import NSData, NSUnarchiver                 # pyobjc
        obj = NSUnarchiver.unarchiveObjectWithData_(NSData.dataWithBytes_length_(blob, len(blob)))
        if obj is None:
            return None
        s = obj.string() if hasattr(obj, "string") else str(obj)
        return str(s).replace("￼", "").strip() or None
    except Exception:
        return None


def _scan(blob: bytes):
    """typedstream 을 직접 훑는다: 'NSString' 뒤 '+' 다음에 길이(1/2/4바이트)와 UTF-8 본문."""
    i = 0
    while True:
        i = blob.find(b"NSString", i)
        if i < 0:
            return None
        j = i + 8
        k = blob.find(b"+", j, j + 8)
        if k >= 0:
            p = k + 1
            try:
                n = blob[p]
                if n == 0x81:
                    n = int.from_bytes(blob[p + 1:p + 3], "little"); p += 3
                elif n == 0x82:
                    n = int.from_bytes(blob[p + 1:p + 5], "little"); p += 5
                else:
                    p += 1
                s = blob[p:p + n].decode("utf-8")
                if s and "�" not in s:
                    return s
            except Exception:
                pass
        i = j


OBJ = "\ufffc"          # 첨부 자리 표시 문자 — 본문이 아니다


def body(row) -> str:
    """본문 한 줄. text 가 없으면 attributedBody 를 푼다. 첨부만 있으면 "[첨부]"."""
    t = (row["text"] or "").replace(OBJ, "").strip()
    if t:
        return t
    b = row["attributedBody"]
    if b:
        t = (_unarchive(bytes(b)) or _scan(bytes(b)) or "").replace(OBJ, "").strip()
        if t:
            return t
    return PLACEHOLDER_ATTACHMENT if row["cache_has_attachments"] else ""


SQL = """
SELECT m.ROWID AS msg_id, m.guid, m.date, m.is_from_me, m.is_read, m.text, m.attributedBody, m.cache_has_attachments,
       h.id AS sender, c.ROWID AS chat_id, c.guid AS chat_guid, c.display_name, c.chat_identifier,
       (SELECT COUNT(*) FROM chat_handle_join j WHERE j.chat_id = c.ROWID) AS members
FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID JOIN chat c ON c.ROWID = cmj.chat_id
LEFT JOIN handle h ON h.ROWID = m.handle_id
WHERE m.item_type = 0 AND m.associated_message_type = 0 AND c.is_filtered = 0
  AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL OR m.cache_has_attachments = 1) AND m.date >= :since
ORDER BY m.date
"""


class IMessageCollector(Collector):
    name = "imessage"
    poll_seconds = 10

    def __init__(self, db_path: Path, my_phone_tails: list[str], names: Callable[[str], str] | None = None):
        """names: 번호·주소 → 연락처 이름(없으면 ""). 메시지 앱처럼 이름으로 보이고, 이름이 사업 낱말에도 걸린다(contacts.AddressBook)."""
        self.db_path = Path(db_path)
        self.my_phone_tails = my_phone_tails
        self.names = names or (lambda handle: "")

    def _connect(self):
        c = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        c.execute("PRAGMA busy_timeout = 5000")
        c.row_factory = sqlite3.Row
        return c

    def collect(self, since: datetime) -> list[Event]:
        since_ns = int((since.timestamp() - APPLE_EPOCH) * 1e9)
        out: list[Event] = []
        with closing(self._connect()) as c:                  # `with 연결` 은 닫지 않는다(store.py 머리말)
            for r in c.execute(SQL, {"since": since_ns}):
                at = apple_to_dt(r["date"])
                if not at:
                    continue
                name = r["display_name"] or self.names(r["chat_identifier"]) or r["chat_identifier"] or str(r["chat_id"])
                sender = r["sender"] or ""
                mine = bool(r["is_from_me"])
                out.append(Event(source=Source.IMESSAGE, chat_id=str(r["chat_id"]), message_id=str(r["msg_id"]), chat_name=name,
                                 sender_id=("me" if mine and not sender else sender), sender_name=(self.names(sender) or sender) or name,
                                 text=body(r), received_at=at, is_from_me=mine, is_read=bool(r["is_read"]) or mine,
                                 is_group=(r["members"] or 1) > 1, link=f"imessage://{r['chat_identifier'] or ''}"))
        return out
