import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from urgent_board import paths, settings_doc as sd
from urgent_board.models import Event, Source
from urgent_board.store import Store

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "tests" / "fixtures" / "legacy"
NO_FILE = Path("/nonexistent/extra_holidays.txt")


def legacy_copy(tmp_path):
    src = tmp_path / "old"
    shutil.copytree(LEGACY, src)
    (src / "dotenv.txt").rename(src / ".env")          # 저장소에는 .env 대신 이 이름(공개판에 .env 이름의 파일을 싣지 않는다 — 공개판 작업 9)
    (src / "state").mkdir()
    st = Store(src / "state" / "urgent.db")
    now = datetime.now(timezone.utc)
    st.upsert_events([
        Event(source=Source.TELEGRAM, chat_id="-100000000001", message_id="1", chat_name="합창 연습 일정방", sender_id="9", sender_name="참가자",
              text="내일 합창 행사 일정 공유드립니다", received_at=now - timedelta(minutes=30), is_from_me=False, is_read=False, is_group=True),
        Event(source=Source.GMAIL, chat_id="t1", message_id="m1", chat_name="숙박 명단", sender_id="a@b.c", sender_name="담당자",
              text="숙박 명단 — 확인 부탁드립니다", received_at=now - timedelta(hours=3), is_from_me=False, is_read=False, is_group=False,
              account="office@example.org", raw={"subject": "숙박 명단", "headers": {"date": "Wed, 14 Oct 2026 10:00:00 +0900"}}),
        Event(source=Source.KAKAO, chat_id="100000000000003", message_id="5", chat_name="🍎책마을-해오름 항공권 발권방", sender_id="7", sender_name="여행사",
              text="발권 확인 부탁드립니다", received_at=now - timedelta(hours=1), is_from_me=False, is_read=True, is_group=True),
    ])
    (src / "auth").mkdir()
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(src / "auth" / "telethon.session")) as c:      # 텔레그램 세션도 SQLite 다
        c.execute("CREATE TABLE sessions (dc_id INTEGER, auth_key BLOB)"); c.execute("INSERT INTO sessions VALUES (5, x'00')"); c.commit()
    return src


def test_dry_run_reports_no_differences_and_writes_nothing(tmp_path):
    from tools import migrate
    src, home = legacy_copy(tmp_path), tmp_path / "home"
    rep = migrate.run(src, home, extra_holidays_file=NO_FILE)
    assert rep.diffs == [] and rep.scan_diffs == [] and rep.wrote is False and not home.exists()


def test_write_copies_settings_db_session_and_api_values(tmp_path):
    from tools import migrate
    src, home = legacy_copy(tmp_path), tmp_path / "home"
    rep = migrate.run(src, home, write=True, extra_holidays_file=NO_FILE)
    assert rep.wrote is True
    doc, _ = sd.read(home)
    team = next(b for b in doc["boxes"] if b["key"] == "bookshop-team")
    assert {"id": "100000000000003", "name": "🍎책마을-해오름 항공권 발권방"} in team["kakao_rooms"]      # 이름표를 저장소에서 채움
    assert Store(paths.db_path(home)).recent_events(days=7) and (home / "backups" / "legacy-config" / "businesses.yaml").exists()
    assert not (home / "backups" / "legacy-config" / ".env").exists()                                     # 비밀 값이 든 .env 는 옮기지 않는다
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(home / "auth" / "telethon.session")) as c:      # 백업 API 로 복사한 온전한 사본(검토 I4)
        assert c.execute("SELECT dc_id FROM sessions").fetchall() == [(5,)]
    tg = json.loads((home / "auth" / "telegram.json").read_text())
    assert tg == {"api_id": 12345, "api_hash": "0123456789abcdef0123456789abcdef"} and oct(os.stat(home / "auth" / "telegram.json").st_mode & 0o777) == "0o600"
    assert paths.last_good_file(home).exists()
    again = migrate.run(src, home, write=True, extra_holidays_file=NO_FILE)
    assert again.wrote is False and "이미" in again.note                                                    # 덮어쓰지 않는다


def test_any_difference_blocks_writing(tmp_path, monkeypatch):
    from tools import migrate
    from urgent_board import settings_legacy as sl
    src, home = legacy_copy(tmp_path), tmp_path / "home"
    real = sl.to_doc
    def broken(raw, **kw):
        d = real(raw, **kw); d["boxes"][0]["keywords"] = d["boxes"][0]["keywords"][:-1]; return d
    monkeypatch.setattr(sl, "to_doc", broken)
    rep = migrate.run(src, home, write=True, extra_holidays_file=NO_FILE)
    assert rep.diffs and rep.wrote is False and not paths.settings_file(home).exists()


def test_scan_print_has_no_message_text(tmp_path):
    from tools import migrate
    from urgent_board.config import load
    src = legacy_copy(tmp_path)
    out = migrate.scan_print(Store(src / "state" / "urgent.db"), load(ROOT / "tests" / "fixtures" / "home"), datetime.now(timezone.utc))
    text = json.dumps(out, ensure_ascii=False, default=str)
    assert "숙박 명단" not in text and "부탁드립니다" not in text and out["items"]          # 견주기 파일에 본문이 남지 않는다(키 · 등급 · 사업만)


def test_a_failed_step_writes_no_settings_so_it_can_run_again(tmp_path):
    """검토 M5: 설정 파일을 먼저 쓰고 뒤 단계가 실패하면, 다시 돌려도 "이미 설정이 있다"로 막혀 반쯤 옮긴 채 남는다 — 설정은 맨 마지막에."""
    import pytest
    from tools import migrate
    src, home = legacy_copy(tmp_path), tmp_path / "home"
    (src / "auth" / "telethon.session").write_bytes(b"not a database")
    with pytest.raises(Exception):
        migrate.run(src, home, write=True, extra_holidays_file=NO_FILE)
    assert not paths.settings_file(home).exists() and not paths.last_good_file(home).exists()
    assert not (paths.auth_dir(home) / "telethon.session").exists()                     # 실패한 복사가 빈 파일을 남기지 않는다(다시 돌릴 때 건너뛰지 않게)
    (src / "auth" / "telethon.session").unlink()
    assert migrate.run(src, home, write=True, extra_holidays_file=NO_FILE).wrote is True
