"""팀즈 알림(2026-10-17 알림 2단계 — 설계 docs/exec-plans/2026-10-17-notice-feed-design.md D6 · D8 · D10).

맥 알림 센터의 DB 에서 팀즈가 띄운 알림 하나가 Event 하나다. 읽기 전용으로 열고 그 자리에서 닫는다.
- 탭5 알림에만 간다(D6 — pipeline.feed_only).
- ● 안 읽음 = 알림 센터에 남아 있고 받은 지 3시간 안(D8 — 고침). 처음에는 '남아 있는 동안'으로 했는데, 실측(2026-10-17 맥북)에서
  팀즈 알림 31건이 10/15 것부터 모두 알림 센터에 남아 있었다(26건은 화면에 떴었다) — 사람이 알림 센터를 손으로 비우지 않는 한 사라지지
  않으니 '봤는가'를 알 수 없다. 그래서 시간으로 흰 ● 가 된다(줄은 그대로 보인다). 사라진 알림은 곧바로 읽음 — 이번에 본 알림의 id 를
  present_ids 로 넘기면 run_loop 가 Store.mark_read_missing 으로 나머지를 읽음으로 맞춘다.
- 맥에 뜬 알림만 잡힌다 — 이 맥이 잠들었거나 팀즈가 꺼져 있으면 빠진다(화면 오류가 아니다).
- 알림 하나(bplist): req.titl(제목 — 보낸 사람이나 대화 이름) · req.subt(부제, 없을 수 있다) · req.body(글). 실측 2026-10-17(macOS 27, 열쇠만 봄).
- 파일이 없는 맥은 빈 목록 — 멈춤이 아니다(D10). 읽을 권한이 없으면 예외 → '수집 중단'.
생성자에서는 아무것도 읽지 않는다."""
from __future__ import annotations
import plistlib
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import Event, Source
from .base import Collector

DB = Path.home() / "Library" / "Group Containers" / "group.com.apple.usernoted" / "db2" / "db"
APPS = ("com.microsoft.teams2",)           # 지금은 팀즈 하나(D8) — 다른 앱을 더하면 출처 이름도 함께 정한다
APPLE_EPOCH = 978307200
FRESH_HOURS = 3                            # 이보다 오래된 팀즈 알림은 알림 센터에 남아 있어도 ○(D8)
SQL = """SELECT r.uuid AS uuid, r.data AS data, r.delivered_date AS delivered FROM record r JOIN app a ON a.app_id = r.app_id
         WHERE a.identifier IN ({marks})"""


def parse(data: bytes) -> dict:
    """알림 하나의 bplist → {title, subtitle, body}. 꼴이 다르면 빈 사전(그 알림만 건너뛴다)."""
    try:
        p = plistlib.loads(data)
    except Exception:                                            # noqa: BLE001 — 알림 센터의 꼴은 macOS 판마다 바뀔 수 있다
        return {}
    req = p.get("req") if isinstance(p, dict) else None
    if not isinstance(req, dict):
        return {}
    return {"title": str(req.get("titl") or "").strip(), "subtitle": str(req.get("subt") or "").strip(), "body": str(req.get("body") or "").strip()}


class TeamsCollector(Collector):
    name = "teams"
    poll_seconds = 30
    source = Source.TEAMS

    def __init__(self, db_path: Path = DB, apps: tuple[str, ...] = APPS, clock=None):
        self.db_path, self.apps = Path(db_path), tuple(apps)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._present: set[str] | None = None

    def present_ids(self) -> set[str] | None:
        return None if self._present is None else set(self._present)

    def collect(self, since: datetime) -> list[Event]:
        self._present = None
        if not self.db_path.exists():
            return []
        with closing(sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)) as c:
            c.execute("PRAGMA busy_timeout = 5000")
            c.row_factory = sqlite3.Row
            rows = c.execute(SQL.format(marks=",".join("?" * len(self.apps))), self.apps).fetchall()
        out: list[Event] = []
        present: set[str] = set()
        now = self.clock()
        for r in rows:
            uid = bytes(r["uuid"]).hex() if isinstance(r["uuid"], (bytes, bytearray)) else str(r["uuid"] or "")
            if not uid or r["delivered"] is None:
                continue
            present.add(uid)
            at = datetime.fromtimestamp(float(r["delivered"]) + APPLE_EPOCH, timezone.utc)
            n = parse(r["data"] or b"")
            if at < since or not (n.get("title") or n.get("body")):
                continue
            title = n["title"] or "팀즈"
            text = " — ".join(x for x in (n["subtitle"], n["body"]) if x) or title
            out.append(Event(source=Source.TEAMS, chat_id=title, message_id=uid, chat_name=title, sender_id=title, sender_name=title,
                             text=text, received_at=at, is_from_me=False, is_read=now - at >= timedelta(hours=FRESH_HOURS), is_group=False))
        self._present = present
        return out
