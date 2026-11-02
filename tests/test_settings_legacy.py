import copy
import shutil
import sys
from pathlib import Path

import pytest

from urgent_board import config, settings_doc as sd, settings_legacy as sl

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "legacy"
LEGACY = FIXTURE                    # 시험마다 아래 고정 장치가 임시 사본으로 바꾼다
NO_FILE = Path("/nonexistent/extra_holidays.txt")


@pytest.fixture(autouse=True)
def _legacy_copy(tmp_path, monkeypatch):
    """옛 설정 자료의 사본 — 저장소에는 .env 대신 dotenv.txt 로 둔다(공개판에 .env 이름의 파일을 싣지 않는다 — 공개판 작업 9)."""
    d = tmp_path / "legacy"
    shutil.copytree(FIXTURE, d)
    (d / "dotenv.txt").rename(d / ".env")
    monkeypatch.setattr(sys.modules[__name__], "LEGACY", d)


def raw():
    return sl.read_raw(LEGACY, NO_FILE)


def test_legacy_and_v2_settings_are_identical(tmp_path):
    old = sl.load_legacy(LEGACY, NO_FILE)
    doc = sl.to_doc(raw())
    assert [p for p in config.problems(doc) if p.level == "error"] == []
    new = config.from_doc(doc, tmp_path)
    assert sl.compare(old, new) == []


def test_doc_keeps_room_ids_as_text_and_names_apart():
    doc = sl.to_doc(raw())
    team = next(b for b in doc["boxes"] if b["key"] == "bookshop-team")
    assert [r["id"] for r in team["kakao_rooms"] if "id" in r] == ["100000000000003"]
    assert [r["name"] for r in team["kakao_rooms"] if "id" not in r] == ["🍎책마을-해오름 항공권 발권방", "책마을-해오름 항공권 발권방"]
    choir = next(b for b in doc["boxes"] if b["key"] == "bazaar2")
    watched = [r for r in choir["telegram_rooms"] if r.get("watch")]
    assert [r["id"] for r in watched] == ["-100000000001"] and doc["watch_rooms"] == []    # 지켜보는 방은 그 방 항목에 붙는다


def test_doc_accounts_follow_label_order_and_point_to_keychain():
    doc = sl.to_doc(raw())
    g = doc["accounts"]["gmail"]
    assert [x["email"] for x in g][:2] == ["minji@example.com", "main@example.com"] and g[2]["label"] == "사무국"
    assert g[0]["alias"] == "workbase" and g[0]["token"] == "keychain:MailHelperApp.workbase/oauth2_token"
    assert doc["accounts"]["telegram"] == {"enabled": True} and doc["service"] == {"engine": "external"}
    assert "api_hash" not in sd.dump(doc) and "0123456789abcdef" not in sd.dump(doc)          # 비밀 값은 설정 문서에 넣지 않는다


def test_rules_overrides_only_what_differs():
    doc = sl.to_doc(raw())
    assert doc["rules"] == {}                                     # 낱말 · 문턱값은 기본값 파일과 같다(개인 칸은 다른 곳으로)
    assert doc["display"]["tab5_usb_serial"] == "AA:BB:CC:00:00:02" and doc["summary"]["enabled"] is True
    assert doc["summary"]["sources"] == ["gmail", "kakao", "telegram", "imessage"]           # 이 맥은 쓰던 대로 넷 다
    assert doc["handled"]["claude_sessions"] is True and doc["me"]["personal_accounts"] == []   # 옛 설정 파일에는 개인 메일 칸이 없다 — 옮기기는 빈 칸을 낸다(실제 값은 설정의 me.personal_accounts 가 정본)


def test_compare_finds_differences(tmp_path):
    old = sl.load_legacy(LEGACY, NO_FILE)
    doc = sl.to_doc(raw())
    choir = next(i for i, b in enumerate(doc["boxes"]) if b["key"] == "bazaar2")
    for change, field in ((lambda d: d["boxes"][0]["keywords"].pop(), "businesses"),
                          (lambda d: d["accounts"]["gmail"].reverse(), "mail_labels"),
                          (lambda d: d["boxes"][choir]["telegram_rooms"][0].update(watch=False), "watch_rooms"),
                          (lambda d: d["summary"].update(skip_when="다른 기준"), "summary"),
                          (lambda d: d["rules"].update(thresholds={"unanswered_dm_hours": 9}), "rules")):
        d = copy.deepcopy(doc); change(d)
        assert any(x.startswith(field) for x in sl.compare(old, config.from_doc(d, tmp_path))), field


def test_name_labels_from_db_do_not_change_matching(tmp_path):
    doc = sl.to_doc(raw(), names_by_id={("kakao", "100000000000003"): "🍎책마을-해오름 항공권 발권방"})
    team = next(b for b in doc["boxes"] if b["key"] == "bookshop-team")
    assert {"id": "100000000000003", "name": "🍎책마을-해오름 항공권 발권방"} in team["kakao_rooms"]
    assert sl.compare(sl.load_legacy(LEGACY, NO_FILE), config.from_doc(doc, tmp_path)) == []


def test_extra_holidays_file_is_read(tmp_path):
    f = tmp_path / "extra.txt"
    f.write_text("2026-12-31  # 종무식\n잘못된 줄\n\n2027-01-02\n", encoding="utf-8")
    assert sl.read_raw(LEGACY, f).extra_holidays == ["2026-12-31", "2027-01-02"]
    assert sl.to_doc(sl.read_raw(LEGACY, f))["workhours"]["extra_holidays"] == ["2026-12-31", "2027-01-02"]
    assert sl.compare(sl.load_legacy(LEGACY, f), config.from_doc(sl.to_doc(sl.read_raw(LEGACY, f)), tmp_path)) == []
