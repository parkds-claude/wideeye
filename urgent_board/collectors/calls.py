"""부재중 전화(2026-10-17 알림 2단계 — 설계 docs/exec-plans/2026-10-17-notice-feed-design.md D6 · D7 · D10).

맥의 통화 기록(아이폰 통화가 넘어온 것)에서 받지 않은 전화 한 통이 Event 하나다. 읽기 전용으로 열고 그 자리에서 닫는다.
- 탭5 알림에만 간다(D6 — pipeline.feed_only). 할 일 장부 · 요약 · 메뉴 막대 · 전체 목록에는 넣지 않는다.
- ● 안 읽음 = 그 번호와 나중에 통화한 적(내가 받음 · 내가 걺)이 없고 24시간 안(D7). 통화 기록의 읽음 칸(ZREAD)은 쓸 수 없다 —
  실측 2026-10-17: 지난 7일 받지 않은 9통이 모두 '읽음'이었다. 읽음은 매번 다시 계산해 넘긴다(Store.upsert_events 가 갱신한다).
- 번호는 문자 수집기와 같은 꼴(+8210…)로 맞춘다 — 사업에 등록한 보낸 사람 · 개인 · 기타로 둔 사람이 문자와 전화에 함께 걸린다.
- 스팸으로 걸러진 전화(ZFILTERED_OUT_REASON · ZJUNKCONFIDENCE)는 뺀다.
- 파일이 없는 맥(아이폰과 통화를 잇지 않은 맥)은 빈 목록 — 멈춤이 아니다(D10). 읽을 권한이 없으면 예외 → '수집 중단'.
생성자에서는 아무것도 읽지 않는다."""
from __future__ import annotations
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import Event, Source
from .base import Collector

DB = Path.home() / "Library" / "Application Support" / "CallHistoryDB" / "CallHistory.storedata"
APPLE_EPOCH = 978307200                    # 2001-01-01 UTC (Core Data 시각의 0)
FRESH_HOURS = 24                           # 이보다 오래된 부재중 전화는 다시 걸지 않았어도 ○(D7)
TEXT = "부재중 전화"
NO_NUMBER = "발신 번호 없음"
SQL = """SELECT Z_PK AS pk, ZUNIQUE_ID AS uid, ZDATE AS date, ZADDRESS AS address, ZANSWERED AS answered, ZORIGINATED AS originated,
                ZSERVICE_PROVIDER AS service, COALESCE(ZFILTERED_OUT_REASON, 0) AS filtered, COALESCE(ZJUNKCONFIDENCE, 0) AS junk
         FROM ZCALLRECORD WHERE ZDATE >= :since ORDER BY ZDATE"""


def handle(address) -> str:
    """통화 기록의 번호 → 문자 수집기의 보낸 사람 꼴. 한국 번호(0 으로 시작)는 "+82…", 82 로 시작하면 "+" 를 붙인다
    ("+82 010…"처럼 0 을 남긴 국제 표기는 0 을 뗀다). 국제 전화 접두(00…)는 숫자 그대로 둔다(나라 번호를 가르지 않는다). 숫자가 없으면 ""."""
    raw = str(address or "").strip()
    d = re.sub(r"\D", "", raw)
    if not d:
        return ""
    if d.startswith("00"):
        return d
    if d.startswith("820"):
        return "+82" + d[3:]
    if d.startswith("0"):
        return "+82" + d[1:]
    if d.startswith("82") or raw.startswith("+"):
        return "+" + d
    return d


class CallsCollector(Collector):
    name = "calls"
    poll_seconds = 60

    def __init__(self, db_path: Path = DB, names=None, clock=None):
        self.db_path = Path(db_path)
        self.names = names or (lambda h: "")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def collect(self, since: datetime) -> list[Event]:
        if not self.db_path.exists():
            return []
        now = self.clock()
        with closing(sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)) as c:      # 경로에 빈칸("Application Support") — as_uri 가 감싼다
            c.execute("PRAGMA busy_timeout = 5000")
            c.row_factory = sqlite3.Row
            rows = c.execute(SQL, {"since": since.timestamp() - APPLE_EPOCH}).fetchall()
        talked: dict[str, float] = {}                         # 번호 → 그 번호와 마지막으로 통화한 때(받음 · 걺)
        for r in rows:
            if r["answered"] or r["originated"]:
                h = handle(r["address"])
                talked[h] = max(talked.get(h, 0.0), float(r["date"] or 0))
        out: list[Event] = []
        for r in rows:
            if r["answered"] or r["originated"] or r["filtered"] or r["junk"] or r["date"] is None:
                continue
            h = handle(r["address"])
            at = datetime.fromtimestamp(float(r["date"]) + APPLE_EPOCH, timezone.utc)
            called = bool(h) and talked.get(h, 0.0) > float(r["date"])
            name = (self.names(h) if h else "") or ""
            who = name or h or NO_NUMBER
            facetime = "facetime" in str(r["service"] or "").lower()
            out.append(Event(source=Source.CALLS, chat_id=h or NO_NUMBER, message_id=str(r["uid"] or r["pk"]), chat_name=who, sender_id=h,
                             sender_name=who, text=TEXT + (" (FaceTime)" if facetime else ""), received_at=at, is_from_me=False,
                             is_read=called or now - at >= timedelta(hours=FRESH_HOURS), is_group=False))
        return out
