"""수집기 공통: 추상 Collector 와 주기 실행 루프. 루프는 run_board.main 에서만 띄운다(import 시점 금지)."""
from __future__ import annotations
import logging
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from ..models import Event, Source
from ..store import Store

log = logging.getLogger("collector")


class Collector(ABC):
    name: str = "base"
    poll_seconds: int = 60
    lookback_days: int = 7
    source: Source | None = None          # read_marks 를 주는 수집기만 적는다

    @abstractmethod
    def collect(self, since: datetime) -> list[Event]: ...

    def read_marks(self) -> dict[str, int]:
        """마지막 collect 에서 본 방마다 '여기까지 읽음' 메시지 번호(chat_id → 번호). 번호가 방 안에서 늘어나는 출처만 준다 —
        이번에 다시 가져오지 않은 옛 글의 읽음을 맞추는 데 쓴다(Store.mark_read_upto). 기본은 없음."""
        return {}

    def present_ids(self) -> set[str] | None:
        """마지막 collect 에서 아직 '살아 있는' 글의 message_id 전부(알림 센터에 남은 알림 — 팀즈). 주면 run_loop 가 그 밖의 이 출처의 글을
        읽음으로 맞춘다(Store.mark_read_missing). None = 모른다(맞추지 않는다) — 기본."""
        return None


def run_loop(collector: Collector, store: Store, stop: threading.Event) -> None:
    """주기 실행. 실패해도 죽지 않고 상태 표에 적는다 — 수집기 하나의 실패가 전체를 멈추지 않게."""
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            since = datetime.now(timezone.utc) - timedelta(days=collector.lookback_days)
            evs = collector.collect(since)
            n = store.upsert_events(evs)
            marks = collector.read_marks()
            if marks and collector.source:
                store.mark_read_upto(collector.source, marks)
            present = collector.present_ids()
            if present is not None and collector.source:
                store.mark_read_missing(collector.source, present)
            store.set_source_status(collector.name, True, f"{len(evs)} seen, {n} new", datetime.now(timezone.utc))
        except Exception as e:                                   # noqa: BLE001
            log.exception("%s collect failed", collector.name)
            store.set_source_status(collector.name, False, f"{type(e).__name__}: {e}", datetime.now(timezone.utc))
        stop.wait(max(1.0, collector.poll_seconds - (time.monotonic() - t0)))
