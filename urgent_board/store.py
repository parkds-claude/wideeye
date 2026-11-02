"""SQLite 하나. 수집기가 넣고(upsert) 판정·웹이 읽는다. 부를 때마다 연결을 새로 열고 **그 자리에서 닫는다**(check_same_thread 회피).

`with sqlite3.connect(...)` 는 커밋만 하고 닫지 않는다 — 닫히는 것은 파이썬의 쓰레기 수거에 달려 있어, 요청이 몰리면 열린 파일이 쌓인다
(실측 2026-10-13: 요청 300번에 70 → 226개. launchd 가 띄운 프로세스의 한도는 256 이고, 닿으면 살아 있는데 답을 못 한다). 그래서 `_conn` 이 닫는다."""
from __future__ import annotations
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import Event, Source

SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        """연결 하나를 열어 주고, 블록이 끝나면 커밋(예외면 되돌림)한 뒤 닫는다."""
        c = sqlite3.connect(self.path, timeout=10)
        try:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            with c:
                yield c
        finally:
            c.close()

    def upsert_events(self, events: list[Event]) -> int:
        """새로 들어간 수를 돌려준다. 같은 키면 읽음 상태와 이름(방 · 보낸 사람)만 갱신한다 — 읽음은 바뀌고, 문자는 번호로 들어왔다가
        연락처 이름을 알게 된다. 본문은 고치지 않는다."""
        n = 0
        with self._lock, self._conn() as c:
            for e in events:
                cur = c.execute(
                    "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e.key, e.source.value, e.chat_id, e.message_id, e.chat_name, e.sender_id, e.sender_name, e.text,
                     _iso(e.received_at), int(e.is_from_me), int(e.is_read), int(e.is_group),
                     int(e.mentioned_me), int(e.reply_to_me), e.thread_id, e.link, e.account,
                     json.dumps(e.raw, ensure_ascii=False, default=str)))
                n += cur.rowcount
                c.execute("UPDATE events SET is_read=? WHERE key=? AND is_read<>?", (int(e.is_read), e.key, int(e.is_read)))
                c.execute("UPDATE events SET chat_name=?, sender_name=? WHERE key=? AND (chat_name IS NOT ? OR sender_name IS NOT ?)",
                          (e.chat_name, e.sender_name, e.key, e.chat_name, e.sender_name))
        return n

    def mark_read_upto(self, source: Source, marks: dict[str, int]) -> int:
        """방마다 '여기까지 읽음' 번호 이하의 글을 읽음으로 맞추고 바꾼 수를 돌려준다. 수집기가 이번에 다시 보지 못한 옛 글을 위한 것이다
        (텔레그램은 방마다 최근 글만 다시 본다 — 그 앞의 글은 읽어도 '안 읽음'으로 남았다). 메시지 번호가 방 안에서 늘어나는 출처에만 쓴다.
        읽음으로만 바꾼다 — 안 읽음으로 되돌리는 것은 upsert_events 의 몫."""
        n = 0
        with self._lock, self._conn() as c:
            for chat_id, upto in marks.items():
                n += c.execute("UPDATE events SET is_read=1 WHERE source=? AND chat_id=? AND is_read=0 AND CAST(message_id AS INTEGER) <= ?",
                               (source.value, str(chat_id), int(upto))).rowcount
        return n

    def mark_read_missing(self, source: Source, present: set[str]) -> int:
        """이 출처의 안 읽은 글 가운데 present(지금도 살아 있는 글의 message_id)에 없는 것을 읽음으로 — 알림 센터에서 사라진 팀즈 알림
        (2026-10-17 알림 2단계 D8). 읽음으로만 바꾼다. 바꾼 수."""
        with self._lock, self._conn() as c:
            rows = [r["key"] for r in c.execute("SELECT key, message_id FROM events WHERE source=? AND is_read=0", (source.value,))
                    if r["message_id"] not in present]
            for k in rows:
                c.execute("UPDATE events SET is_read=1 WHERE key=?", (k,))
        return len(rows)

    BOT_RAW = '%"bot": true%'                # 텔레그램 봇 방의 글(collectors/telegram.to_event 가 raw 에 적는다 — json.dumps 꼴)

    def recent_events(self, days: int = 7) -> list[Event]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events WHERE received_at >= ? ORDER BY received_at", (_iso(since),)).fetchall()
        return [_row_to_event(r) for r in rows]

    def catalog(self, days: int = 7, limit: int = 300) -> dict:
        """본 적 있는 방 · 사람(최근 순) — 설정 화면의 "목록에서 고르기". 방 이름은 그 방의 가장 최근 글의 것(따로 고른다 — SQLite 의
        'MAX 와 함께 쓴 맨 칸' 규칙은 MAX 가 하나일 때만 맞는다). 사람 이름은 MAX 가 하나뿐이라 그 규칙으로. 번호는 글자로(카톡 방 번호는 17자리까지)."""
        since = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        with self._conn() as c:
            rooms = c.execute("""SELECT source, chat_id, MAX(received_at) AS last, COUNT(*) AS n, MAX(is_group) AS g,
                                        (SELECT x.chat_name FROM events x WHERE x.source = events.source AND x.chat_id = events.chat_id
                                         ORDER BY x.received_at DESC LIMIT 1) AS chat_name,
                                        SUM(CASE WHEN is_read = 0 AND is_from_me = 0 THEN 1 ELSE 0 END) AS unread
                                 FROM events WHERE received_at >= ? AND source IN ('kakao', 'telegram', 'imessage') AND raw_json NOT LIKE ?
                                 GROUP BY source, chat_id ORDER BY last DESC LIMIT ?""", (since, self.BOT_RAW, limit)).fetchall()
            # 알림에만 들어가는 글(부재중 전화 · 팀즈 · 텔레그램 봇 — pipeline.feed_only)은 고를 곳이 아니다(독립 검토 2026-10-17)
            people = c.execute("""SELECT source, sender_id, sender_name, MAX(received_at) AS last, COUNT(*) AS n FROM events
                                  WHERE received_at >= ? AND is_from_me = 0 AND sender_id <> '' AND source NOT IN ('calls', 'teams') AND raw_json NOT LIKE ?
                                  GROUP BY source, sender_id ORDER BY last DESC LIMIT ?""", (since, self.BOT_RAW, limit)).fetchall()
        return {"rooms": [{"source": r["source"], "chat_id": str(r["chat_id"]), "name": r["chat_name"] or "", "group": bool(r["g"]),
                           "count": r["n"], "unread": r["unread"], "last": r["last"]} for r in rooms],
                "people": [{"source": p["source"], "id": str(p["sender_id"]), "name": p["sender_name"] or "", "count": p["n"],
                            "last": p["last"]} for p in people]}

    def dismiss(self, key: str, until: datetime) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO dismissed VALUES (?,?)", (key, _iso(until)))

    def dismissed_keys(self, now: datetime) -> set[str]:
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT key FROM dismissed WHERE until > ?", (_iso(now),))}

    def put_summary(self, key: str, text_hash: str, summary: str, at: datetime) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?)", (key, text_hash, summary, _iso(at)))

    def get_summaries(self, keys: list[str]) -> dict[str, tuple[str, str]]:
        """key → (글의 해시, 요약)."""
        if not keys:
            return {}
        marks = ",".join("?" * len(keys))
        with self._conn() as c:
            return {r["key"]: (r["text_hash"], r["summary"]) for r in c.execute(f"SELECT * FROM summaries WHERE key IN ({marks})", keys)}

    def put_verdict(self, key: str, text_hash: str, needed: bool, at: datetime) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO verdicts VALUES (?,?,?,?)", (key, text_hash, int(needed), _iso(at)))

    def get_verdicts(self, keys: list[str]) -> dict[str, tuple[str, bool]]:
        """key → (글의 해시, 볼 필요가 있는가)."""
        if not keys:
            return {}
        marks = ",".join("?" * len(keys))
        with self._conn() as c:
            return {r["key"]: (r["text_hash"], bool(r["needed"])) for r in c.execute(f"SELECT * FROM verdicts WHERE key IN ({marks})", keys)}

    def put_task_line(self, key: str, text_hash: str, what: str, due: date | None, at: datetime) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO task_lines VALUES (?,?,?,?,?)", (key, text_hash, what, due.isoformat() if due else "", _iso(at)))

    def get_task_lines(self, keys: list[str]) -> dict[str, tuple[str, str, date | None]]:
        """key → (글의 해시, 할 일 문구, 기한)."""
        if not keys:
            return {}
        marks = ",".join("?" * len(keys))
        with self._conn() as c:
            return {r["key"]: (r["text_hash"], r["what"], date.fromisoformat(r["due"]) if r["due"] else None)
                    for r in c.execute(f"SELECT * FROM task_lines WHERE key IN ({marks})", keys)}

    def put_pick(self, key: str, text_hash: str, business: str, at: datetime) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO picks VALUES (?,?,?,?)", (key, text_hash, business, _iso(at)))

    def get_picks(self, keys: list[str]) -> dict[str, tuple[str, str]]:
        """key → (글의 해시, Claude 가 고른 사업 key — 빈 글이면 '없음')."""
        if not keys:
            return {}
        marks = ",".join("?" * len(keys))
        with self._conn() as c:
            return {r["key"]: (r["text_hash"], r["business"]) for r in c.execute(f"SELECT * FROM picks WHERE key IN ({marks})", keys)}

    def retag_tasks(self, convs: list[str], business: str) -> int:
        """열린 할 일의 사업을 바꾼다(전체 목록에서 사업을 골랐을 때 — 7일 밖의 할 일까지 곧바로)."""
        if not convs:
            return 0
        marks = ",".join("?" * len(convs))
        with self._lock, self._conn() as c:
            return c.execute(f"UPDATE tasks SET business = ? WHERE state = 'open' AND conv IN ({marks})", [business, *convs]).rowcount

    def event(self, key: str) -> Event | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM events WHERE key = ?", (key,)).fetchone()
        return _row_to_event(r) if r else None

    # ---- 할 일 장부(urgent_board/tasks.py) ----
    def tasks(self, state: str = "open") -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE state = ? ORDER BY last_at DESC", (state,))]

    def put_task(self, row: dict) -> None:
        cols = ",".join(row)
        with self._lock, self._conn() as c:
            c.execute(f"INSERT OR REPLACE INTO tasks ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))

    def close_task(self, conv: str, why: str, at: datetime) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE tasks SET state = 'done', closed_at = ?, closed_why = ? WHERE conv = ? AND state = 'open'", (_iso(at), why, conv))

    def closed_since(self, since: datetime) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE state = 'done' AND closed_at >= ? ORDER BY closed_at DESC", (_iso(since),))]

    def replied_after(self, source: str, chat_id: str, after: datetime) -> bool:
        """그 방 · 스레드에 내 글이 after 뒤에 있는가(7일 밖의 할 일을 답장으로 끝낼 때)."""
        with self._conn() as c:
            return c.execute("SELECT 1 FROM events WHERE source = ? AND chat_id = ? AND is_from_me = 1 AND received_at > ? LIMIT 1",
                             (source, chat_id, _iso(after))).fetchone() is not None

    def my_mail_after(self, after: datetime) -> list[Event]:
        """after 뒤에 내가 보낸 메일 전부(어느 계정 · 어느 스레드든) — 7일 밖의 메일 할 일을 답장으로 끝낼 때(사본이 다른 계정에 있다)."""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events WHERE source IN ('gmail','naver') AND is_from_me = 1 AND received_at > ? ORDER BY received_at",
                             (_iso(after),)).fetchall()
        return [_row_to_event(r) for r in rows]

    def unread_in(self, source: str, chat_id: str) -> bool:
        """그 방에 안 읽은 남의 글이 남았는가(7일 밖의 방 묶음을 읽음으로 끝낼 때)."""
        with self._conn() as c:
            return c.execute("SELECT 1 FROM events WHERE source = ? AND chat_id = ? AND is_from_me = 0 AND is_read = 0 LIMIT 1",
                             (source, chat_id)).fetchone() is not None

    def dismissed_keys_all(self) -> set[str]:
        """처리됨으로 누른 모든 key(숨김 기한과 상관없이) — 장부가 끝남을 기억한다."""
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT key FROM dismissed")}

    def touch_many(self, seen: dict[str, datetime]) -> None:
        """id → 다룬 때. 이미 있는 id 는 더 늦은 때를 남긴다."""
        if not seen:
            return
        with self._lock, self._conn() as c:
            c.executemany("INSERT INTO touched VALUES (?,?) ON CONFLICT(ref) DO UPDATE SET at = MAX(at, excluded.at)",
                          [(ref, _iso(at)) for ref, at in seen.items()])

    def touched_at(self, refs: list[str]) -> dict[str, datetime]:
        if not refs:
            return {}
        out: dict[str, datetime] = {}
        with self._conn() as c:
            for i in range(0, len(refs), 500):
                chunk = refs[i:i + 500]
                marks = ",".join("?" * len(chunk))
                for r in c.execute(f"SELECT ref, at FROM touched WHERE ref IN ({marks})", chunk):
                    out[r["ref"]] = datetime.fromisoformat(r["at"])
        return out

    def reset_touched(self) -> None:
        """다룬 것의 기록과 읽은 자리를 모두 지운다(치는 기준이 바뀌었을 때)."""
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM touched")
            c.execute("DELETE FROM scan_pos")

    def prune_touched(self, before: datetime) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM touched WHERE at < ?", (_iso(before),))

    def get_scan_pos(self, paths: list[str]) -> dict[str, int]:
        if not paths:
            return {}
        with self._conn() as c:
            known = {r["path"]: r["pos"] for r in c.execute("SELECT path, pos FROM scan_pos")}
        return {p: known[p] for p in paths if p in known}

    def set_scan_pos(self, pos: dict[str, int]) -> None:
        if not pos:
            return
        with self._lock, self._conn() as c:
            c.executemany("INSERT OR REPLACE INTO scan_pos VALUES (?,?)", list(pos.items()))

    def count_summaries_since(self, since: datetime) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM summaries WHERE at >= ?", (_iso(since),)).fetchone()[0]

    def set_source_status(self, source: str, ok: bool, note: str, at: datetime) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?)", (source, int(ok), (note or "")[:200], _iso(at)))

    def forget_sources(self, keep: set[str]) -> None:
        """지금 도는 수집기가 아닌 상태 행을 지운다(뺀 계정이 '수집 중단'으로 남지 않게)."""
        with self._lock, self._conn() as c:
            for (name,) in c.execute("SELECT source FROM sources").fetchall():
                if name not in keep:
                    c.execute("DELETE FROM sources WHERE source=?", (name,))

    def source_status(self) -> dict[str, dict]:
        with self._conn() as c:
            return {r["source"]: {"ok": bool(r["ok"]), "note": r["note"], "at": r["at"]} for r in c.execute("SELECT * FROM sources")}


def _row_to_event(r: sqlite3.Row) -> Event:
    return Event(source=Source(r["source"]), chat_id=r["chat_id"], message_id=r["message_id"], chat_name=r["chat_name"] or "",
                 sender_id=r["sender_id"] or "", sender_name=r["sender_name"] or "", text=r["text"] or "",
                 received_at=datetime.fromisoformat(r["received_at"]), is_from_me=bool(r["is_from_me"]), is_read=bool(r["is_read"]),
                 is_group=bool(r["is_group"]), mentioned_me=bool(r["mentioned_me"]), reply_to_me=bool(r["reply_to_me"]),
                 thread_id=r["thread_id"] or "", link=r["link"] or "", account=r["account"] or "", raw=json.loads(r["raw_json"] or "{}"))
