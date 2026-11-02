"""설정 한눈에(/setup)와 설정 명령이 묻는 API(urgent_board/web_setup.py) — 읽기만 한다. 접속 열쇠 안쪽이다."""
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from urgent_board import config, paths, settings_doc as sd, setup_edit as se
from urgent_board.models import Event, Source
from urgent_board.store import Store
from urgent_board.web import create_app

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)


def app(tmp_path, doc=None, token=None):
    if doc is None:
        shutil.copy(ROOT / "tests" / "fixtures" / "home" / "settings.yaml", tmp_path / "settings.yaml")
    else:
        sd.save(tmp_path, doc, None)
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    st = Store(tmp_path / "t.db")
    return create_app(live, st, clock=lambda: NOW, token=token).test_client(), st


def fresh():
    return sd.example()


def test_api_setup_on_a_fresh_install(tmp_path):
    c, _ = app(tmp_path, fresh())
    j = c.get("/api/setup").get_json()
    assert j["steps"]["next"] == "connect" and j["steps"]["left"] == 4           # 연결 · 나 · 상자 · 기준
    assert j["prompt"].startswith("길 명령의 출력만 따라 주세요.") and f"{paths.CODE_ROOT}/bin/wideeye guide next" in j["prompt"]   # 마지막 검토 I2
    assert j["describe"]["boxes"][0]["example"] is True


def test_status_carries_setup_left(tmp_path):
    c, _ = app(tmp_path, fresh())
    assert c.get("/api/status").get_json()["setup_left"] == 4
    d = fresh()
    se.set_me(d, names=["홍길동"])
    se.set_source(d, "imessage", True)
    se.add_box(d, "○○ 박람회")
    se.add_rule(d, "○○ 박람회", "word", ["박람회"])
    sd.save(tmp_path, d, None)
    c2, _ = app(tmp_path, d)
    assert c2.get("/api/status").get_json()["setup_left"] == 0


def test_setup_page_shows_steps_prompt_and_six_slots(tmp_path):
    d = fresh()
    se.add_box(d, "○○ 박람회")
    se.add_rule(d, "○○ 박람회", "word", ["박람회", "부스"])
    se.set_box(d, "○○ 박람회", about="가을 박람회 운영 대행")
    c, _ = app(tmp_path, d)
    html = c.get("/setup").get_data(as_text=True)
    assert "설정 한눈에" in html and "AI 와 설정하기" in html and "가을 박람회 운영 대행" in html and "박람회, 부스" in html
    assert html.count('class="card empty"') == 4 and "개인 · 기타" in html
    assert "다음: <code>" in html and "자세한 설정 화면" in html


def test_setup_page_and_api_need_the_token(tmp_path):
    c, _ = app(tmp_path, fresh(), token="T" * 40)
    assert c.get("/api/setup").status_code == 401 and c.get("/api/setup/places").status_code == 401
    assert c.get("/setup").status_code == 401
    assert c.get("/api/setup", headers={"X-UB-Token": "T" * 40}).status_code == 200


def test_places_lists_who_and_where(tmp_path):
    d = fresh()
    se.add_box(d, "○○ 박람회")
    se.add_rule(d, "○○ 박람회", "word", ["박람회"])
    c, st = app(tmp_path, d)
    st.upsert_events([Event(source=Source.GMAIL, chat_id="t1", message_id="m1", chat_name="", sender_id="kim@a.org", sender_name="김",
                            text="박람회 부스 배치 — 확인 부탁", received_at=datetime.now(timezone.utc) - timedelta(hours=2), is_from_me=False,
                            is_read=False, is_group=False, account="me@x.kr", raw={"subject": "박람회 부스 배치"})])
    j = c.get("/api/setup/places").get_json()
    assert j["rows"][0]["id"] == "kim@a.org" and j["rows"][0]["box"] == "○○ 박람회" and "sample" not in j["rows"][0]
    assert j["sorting"]["boxes"][0] == {"box": "○○ 박람회", "places": 1, "messages": 1, "tasks": 1}
    assert j["rows"][0]["tasks"] == 1                                                     # 안 읽은 것 = 할 일(화면과 같은 길)
    assert c.get("/api/setup/version").get_json()["version"]
    assert c.get("/api/setup/places?detail=1").get_json()["rows"][0]["sample"] == "박람회 부스 배치"


def test_broken_settings_file_is_reported_not_raised(tmp_path):
    c, _ = app(tmp_path, fresh())
    (tmp_path / "settings.yaml").write_text("boxes: [", encoding="utf-8")
    j = c.get("/api/setup").get_json()
    assert "YAML" in j["settings_error"]
    assert c.get("/api/status").get_json()["setup_left"] is None
