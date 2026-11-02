import threading
from datetime import datetime, timezone
from urgent_board.collectors.base import Collector, run_loop
from urgent_board.models import Event, Source
from urgent_board.store import Store

class Boom(Collector):
    name = "boom"; poll_seconds = 1
    def collect(self, since):
        raise RuntimeError("db locked")

class One(Collector):
    name = "one"; poll_seconds = 1
    def collect(self, since):
        return [Event(source=Source.KAKAO, chat_id="1", message_id="1", chat_name="방", sender_id="u", sender_name="홍",
                      text="안녕", received_at=datetime.now(timezone.utc), is_from_me=False, is_read=False, is_group=False)]

def _run_once(col, store):
    stop = threading.Event()
    orig = store.set_source_status
    def once(*a, **k):
        orig(*a, **k); stop.set()          # 상태를 한 번 적으면 루프를 멈춘다
    store.set_source_status = once
    run_loop(col, store, stop)

def test_run_loop_records_failure_and_keeps_going(tmp_path):       # Review Focus 5
    st = Store(tmp_path / "t.db"); _run_once(Boom(), st)
    s = st.source_status()["boom"]; assert s["ok"] is False and "db locked" in s["note"]

def test_run_loop_records_success(tmp_path):
    st = Store(tmp_path / "t.db"); _run_once(One(), st)
    assert st.source_status()["one"]["ok"] is True and len(st.recent_events(1)) == 1


class Marks(Collector):
    name = "marks"; poll_seconds = 1; source = Source.TELEGRAM
    def collect(self, since):
        return []
    def read_marks(self):
        return {"-5": 10}

def test_run_loop_applies_read_marks(tmp_path):
    st = Store(tmp_path / "t.db")
    st.upsert_events([Event(source=Source.TELEGRAM, chat_id="-5", message_id="9", chat_name="방", sender_id="u", sender_name="홍",
                            text="안녕", received_at=datetime.now(timezone.utc), is_from_me=False, is_read=False, is_group=True)])
    _run_once(Marks(), st)
    assert st.recent_events(1)[0].is_read is True and st.source_status()["marks"]["ok"] is True
