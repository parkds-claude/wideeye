import copy
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from urgent_board import config, settings_doc as sd
from urgent_board.models import Event, Source
from urgent_board.store import Store
from urgent_board.web import create_app

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
H = {"Origin": "http://localhost"}


def setup(tmp_path):
    shutil.copy(HOME / "settings.yaml", tmp_path / "settings.yaml")
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    restarts = []
    st = Store(tmp_path / "t.db")
    c = create_app(live, st, restart=restarts.append).test_client()
    return c, live, st, restarts


def wait_for(cond, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not cond():
        time.sleep(0.05)
    return cond()


def test_settings_page_is_served(tmp_path):
    c, _, _, _ = setup(tmp_path)
    r = c.get("/settings")
    assert r.status_code == 200 and "설정" in r.get_data(as_text=True)


def test_get_settings_has_doc_version_and_text_ids(tmp_path):
    c, live, _, _ = setup(tmp_path)
    j = c.get("/api/settings").get_json()
    assert j["version"] == live.get().version and j["doc"]["boxes"][0]["key"] == "spring-festival" and j["problems"] == []
    assert all(isinstance(r["id"], str) for b in j["doc"]["boxes"] for r in b["kakao_rooms"] + b["telegram_rooms"] if "id" in r)
    assert j["colors"] == list(sd.BUSINESS_COLORS)


def test_put_saves_and_applies_without_restart(tmp_path):
    c, live, _, restarts = setup(tmp_path)
    j = c.get("/api/settings").get_json()
    doc = j["doc"]; doc["boxes"][0]["name"] = "이름 바꿈"; doc["boxes"][0]["senders"] = ["010-1234-5678"]
    r = c.put("/api/settings", json={"doc": doc, "version": j["version"]}, headers=H)
    assert r.status_code == 200 and r.get_json()["applied"] == "swapped" and restarts == []
    assert live.get().businesses[0].name == "이름 바꿈" and live.get().businesses[0].senders == ["+821012345678"]


def test_put_rejects_problems_and_stale_versions(tmp_path):
    c, _, _, _ = setup(tmp_path)
    j = c.get("/api/settings").get_json()
    bad = copy.deepcopy(j["doc"]); bad["boxes"][1]["color"] = bad["boxes"][0]["color"]
    r = c.put("/api/settings", json={"doc": bad, "version": j["version"]}, headers=H)
    assert r.status_code == 422 and any(p["path"] == "boxes[1].color" for p in r.get_json()["problems"])
    ok = copy.deepcopy(j["doc"]); ok["etc_name"] = "기타 일"
    assert c.put("/api/settings", json={"doc": ok, "version": j["version"]}, headers=H).status_code == 200
    assert c.put("/api/settings", json={"doc": ok, "version": j["version"]}, headers=H).status_code == 409     # 그 사이 바뀌었다


def test_put_connection_change_schedules_restart(tmp_path):
    c, _, _, restarts = setup(tmp_path)
    j = c.get("/api/settings").get_json()
    doc = j["doc"]; doc["accounts"]["kakao"]["enabled"] = False
    r = c.put("/api/settings", json={"doc": doc, "version": j["version"]}, headers=H)
    assert r.get_json()["applied"] == "restart" and wait_for(lambda: bool(restarts))


def test_new_keychain_tokens_cannot_be_added_from_the_screen(tmp_path):
    c, _, _, _ = setup(tmp_path)
    j = c.get("/api/settings").get_json()
    doc = j["doc"]; doc["accounts"]["gmail"].append({"alias": "x", "email": "x@y.z", "token": "keychain:Other.app/secret"})
    assert c.put("/api/settings", json={"doc": doc, "version": j["version"]}, headers=H).status_code == 422


def test_preview_shows_moves_without_saving(tmp_path):
    c, live, st, _ = setup(tmp_path)
    now = datetime.now(timezone.utc)
    st.upsert_events([Event(source=Source.GMAIL, chat_id="t", message_id="1", chat_name="확장개점 기념 학술대회", sender_id="x@y.z",
                            sender_name="담당", text="확장개점 기념 학술대회 — 일정 확인 부탁드립니다", received_at=now - timedelta(hours=1),
                            is_from_me=False, is_read=False, is_group=False, account="minji@example.com")])
    j0 = c.get("/api/settings").get_json()
    doc = j0["doc"]
    next(b for b in doc["boxes"] if b["key"] == "bookshop-team")["keywords"].remove("확장개점")
    j = c.post("/api/settings/preview", json={"doc": doc}, headers=H).get_json()
    assert j["moved_total"] == 1 and j["moved"][0]["from"] == "책방운영단" and j["moved"][0]["to"] == "개인 · 기타"
    assert "summary" in j["moved"][0] and j["resummarize"] is True        # 낱말은 Claude 에게 주는 사업 목록에도 들어간다(2026-10-16 사업 고르기) — 바꾸면 다시 만든다
    assert [b["urgent"] for b in j["before"]] != [b["urgent"] for b in j["after"]]
    assert c.get("/api/settings").get_json()["version"] == j0["version"]                  # 미리 보기는 쓰지 않는다


def test_preview_reports_resummarize_and_problems(tmp_path):
    c, _, _, _ = setup(tmp_path)
    doc = c.get("/api/settings").get_json()["doc"]
    d2 = copy.deepcopy(doc); d2["summary"]["max_chars"] = 50
    assert c.post("/api/settings/preview", json={"doc": d2}, headers=H).get_json()["resummarize"] is True
    d3 = copy.deepcopy(doc); d3["boxes"][0]["key"] = "etc"
    assert c.post("/api/settings/preview", json={"doc": d3}, headers=H).status_code == 422


def test_classify_with_current_or_draft(tmp_path):
    c, _, _, _ = setup(tmp_path)
    q = {"source": "kakao", "chat_id": "100000000000003", "chat_name": "방", "sender_id": "7", "sender_name": "여행사", "account": "", "text": "발권"}
    j = c.post("/api/classify", json=q, headers=H).get_json()
    assert (j["key"], j["why"], j["box"]) == ("bookshop-team", "등록한 카톡 방", "책방운영단") and "③" in j["note"]
    doc = c.get("/api/settings").get_json()["doc"]
    team = next(b for b in doc["boxes"] if b["key"] == "bookshop-team"); team["kakao_rooms"] = []
    j2 = c.post("/api/classify", json={**q, "doc": doc}, headers=H).get_json()
    assert j2["key"] == "bookshop-team" and j2["why"].startswith("낱말 '발권'")              # 고치는 중인 설정으로 시험한다


def test_catalog_and_status(tmp_path):
    c, _, _, _ = setup(tmp_path)
    cat = c.get("/api/catalog").get_json()
    assert set(cat) >= {"rooms", "people", "accounts", "tab5", "owners"} and cat["owners"]["kakao:100000000000003"] == "bookshop-team"
    assert cat["accounts"][0] == {"alias": "workbase", "email": "minji@example.com", "label": "업무"}
    s = c.get("/api/status").get_json()
    assert set(s) >= {"totals", "blind", "down", "cards", "settings_error", "version", "tab5"} and len(s["cards"]) == 6


def test_restart_endpoint(tmp_path):
    c, _, _, restarts = setup(tmp_path)
    assert c.post("/api/restart", json={}, headers=H).status_code == 202
    assert wait_for(lambda: bool(restarts))


def test_settings_carry_rule_defaults_and_preview_carries_warnings(tmp_path):
    c, _, _, _ = setup(tmp_path)
    j = c.get("/api/settings").get_json()
    assert j["rule_defaults"]["thresholds"]["unanswered_dm_minutes"] == 120                  # 업무시간 칸이 비어 있을 때 보일 값
    doc = j["doc"]; doc["boxes"][1]["senders"] = list(doc["boxes"][0]["senders"]) + ["dup@example.com"]; doc["boxes"][0]["senders"].append("dup@example.com")
    p = c.post("/api/settings/preview", json={"doc": doc}, headers=H).get_json()
    assert any("위쪽 상자가 이긴다" in w["message"] for w in p["warnings"])


def test_a_round_trip_through_the_browser_keeps_big_ids_and_dates(tmp_path):
    """손으로 따옴표 없이 적은 큰 방 번호 · 날짜도 화면에서 다른 칸만 고쳐 저장했을 때 그대로(검토 I3).
    브라우저의 JSON.parse 는 2^53 넘는 정수를 잃는다 — 시험은 그것을 흉내 낸다(정수를 float 으로 읽음)."""
    import json
    c, live, st, _ = setup(tmp_path)
    text = (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    text += "watch_rooms:\n  - {source: kakao, chat_id: 100000000000001123}\n"
    text = text.replace("  extra_holidays: []\n", "  extra_holidays: [2026-10-17]\n", 1)
    (tmp_path / "settings.yaml").write_text(text, encoding="utf-8")
    assert sd.read(tmp_path)[0]["workhours"]["extra_holidays"]          # 날짜로 읽혔다
    r = c.get("/api/settings")
    got = json.loads(r.get_data(as_text=True), parse_int=lambda s: int(float(s)))    # 브라우저처럼
    doc = got["doc"]
    doc["etc_name"] = "그 밖의 일"
    r = c.put("/api/settings", json={"doc": doc, "version": got["version"]}, headers=H)
    assert r.status_code == 200, r.get_json()
    saved = sd.read(tmp_path)[0]
    assert saved["watch_rooms"][-1]["chat_id"] == "100000000000001123"
    assert [str(d) for d in saved["workhours"]["extra_holidays"]] == ["2026-10-17"]
