import json
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from urgent_board import config
from urgent_board.connect import probes
from urgent_board.store import Store
from urgent_board.web import create_app

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
H = {"Origin": "http://localhost"}


def make_db(path, table):
    with closing(sqlite3.connect(path)) as c:
        c.execute(f"CREATE TABLE {table} (x)"); c.commit()


def test_messages_and_contacts_access(tmp_path):
    db = tmp_path / "chat.db"; make_db(db, "message")
    assert probes.messages_access(db) == {"ok": True, "why": ""}
    assert probes.messages_access(tmp_path / "none.db")["ok"] is False
    bad = tmp_path / "bad.db"; bad.write_bytes(b"not a db")
    r = probes.messages_access(bad)
    assert r["ok"] is False and "전체 디스크 접근" in r["why"]
    book = tmp_path / "AddressBook"; (book / "Sources" / "x").mkdir(parents=True)
    make_db(book / "Sources" / "x" / "AddressBook-v22.abcddb", "ZABCDRECORD")
    assert probes.contacts_access(book)["ok"] is True and probes.contacts_access(tmp_path / "nobook")["ok"] is False


def test_python_binary_is_the_real_file():
    p = probes.python_binary()
    assert Path(p).is_absolute() and Path(p).resolve() == Path(p)


def test_claude_and_kakaocli_probes(monkeypatch):
    from urgent_board import summarize
    from urgent_board.collectors import kakao
    monkeypatch.setattr(summarize, "find_claude", lambda: "/x/claude")
    monkeypatch.setattr(kakao, "find_kakaocli", lambda: None)
    ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="2.1.283 (Claude Code)\n", stderr="")
    assert probes.claude(run=ok) == {"path": "/x/claude", "found": True, "version": "2.1.283 (Claude Code)"}
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    assert probes.claude(run=missing)["found"] is False
    assert probes.kakaocli(run=ok) == {"path": "", "found": False, "version": ""}


def test_tab5_devices_lists_only_espressif_with_serial():
    ports = [SimpleNamespace(vid=0x303A, serial_number="AA:BB", device="/dev/cu.a", description="M5"),
             SimpleNamespace(vid=0x303A, serial_number=None, device="/dev/cu.b", description=""),
             SimpleNamespace(vid=0x1234, serial_number="X", device="/dev/cu.c", description="")]
    assert probes.tab5_devices(lambda: ports) == [{"device": "/dev/cu.a", "serial": "AA:BB", "description": "M5"}]


def test_source_status_states():
    s = config.load(HOME)
    sources = {"gmail:workbase": {"ok": True, "note": "74 seen", "at": "t"},
               "gmail:workmain": {"ok": False, "note": "RefreshError: ('invalid_grant: Token has been expired or revoked.')", "at": "t"},
               "kakao": {"ok": False, "note": "x" * 500, "at": "t"}, "imessage": {"ok": True, "note": "", "at": "t"}}
    st = probes.status(s, sources)
    g = {x["alias"]: x for x in st["gmail"]}
    assert g["workbase"]["state"] == "ok" and g["workmain"]["state"] == "reconnect" and g["choir"]["state"] == "waiting"
    assert g["workbase"]["token"] == "keychain" and st["kakao"]["state"] == "stopped" and len(st["kakao"]["note"]) == 120
    assert st["telegram"]["state"] == "waiting" and st["imessage"]["state"] == "ok"
    s.accounts["kakao"] = {"enabled": False}
    assert probes.status(s, sources)["kakao"]["state"] == "off"


def setup(tmp_path, **ub):
    shutil.copy(HOME / "settings.yaml", tmp_path / "settings.yaml")
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "telegram.json").write_text(json.dumps({"api_id": 1, "api_hash": "SECRET-HASH"}), encoding="utf-8")
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    app = create_app(live, Store(tmp_path / "t.db"))
    app.config["UB"].update(ub)
    return app.test_client()


def test_connect_endpoint_has_no_secrets(tmp_path):
    j = setup(tmp_path).get("/api/connect").get_json()
    assert set(j) >= {"sources", "python", "messages", "contacts", "claude", "kakaocli", "google_client", "telegram"}
    assert j["telegram"] == {"api": True, "session": False} and "SECRET-HASH" not in json.dumps(j)


def test_kakao_check_and_summary_test(tmp_path, monkeypatch):
    from urgent_board.collectors import kakao
    monkeypatch.setattr(kakao, "find_kakaocli", lambda: "/bin/kakaocli")
    monkeypatch.setattr(kakao, "load_auth", lambda cache=None, helper=None, **k: None)
    monkeypatch.setattr(kakao, "query", lambda sql, auth, binary: [[18]])
    fake = SimpleNamespace(summarize=lambda sender, where, text: SimpleNamespace(summary="회의 자료 공유 요청", needed=True))
    c = setup(tmp_path, summarizer=fake)
    assert c.post("/api/connect/kakao/check", json={}, headers=H).get_json() == {"ok": True, "rooms": 18}
    monkeypatch.setattr(kakao, "query", lambda sql, auth, binary: (_ for _ in ()).throw(kakao.KakaocliError("kakaocli query rc=1: file is not a database")))
    j = c.post("/api/connect/kakao/check", json={}, headers=H).get_json()
    assert j["ok"] is False and "file is not a database" in j["error"]
    assert c.post("/api/connect/summary/test", json={}, headers=H).get_json() == {"ok": True, "summary": "회의 자료 공유 요청"}


def test_whatsapp_access_states(tmp_path):                  # 왓츠앱 설계 — 엔진의 권한으로 DB 를 열어 보고 앱이 켜져 있는지 함께 준다
    r = probes.whatsapp_access(tmp_path / "none.sqlite", running=lambda: True)
    assert r["ok"] is False and r["why"].startswith("자료가 없어요") and r["running"] is True
    db = tmp_path / "ChatStorage.sqlite"; make_db(db, "ZWAMESSAGE")
    assert probes.whatsapp_access(db, running=lambda: False) == {"ok": True, "why": "", "running": False}
    bad = tmp_path / "bad.sqlite"; bad.write_bytes(b"not a db")
    assert "전체 디스크 접근" in probes.whatsapp_access(bad, running=lambda: True)["why"]


def test_status_and_connect_endpoint_have_whatsapp(tmp_path, monkeypatch):
    s = config.load(HOME)
    s.accounts["whatsapp"] = {"enabled": True}
    assert probes.status(s, {})["whatsapp"]["state"] == "waiting"
    s.accounts["whatsapp"] = {"enabled": False}
    assert probes.status(s, {})["whatsapp"]["state"] == "off"
    monkeypatch.setattr(probes, "whatsapp_access", lambda: {"ok": True, "why": "", "running": True})
    assert setup(tmp_path).get("/api/connect").get_json()["whatsapp"] == {"ok": True, "why": "", "running": True}


def test_probe_says_permission_when_the_file_cannot_even_be_looked_at(tmp_path, monkeypatch):   # 왓츠앱 검토 I2
    db = tmp_path / "ChatStorage.sqlite"; make_db(db, "ZWAMESSAGE")
    real = probes.os.stat
    monkeypatch.setattr(probes.os, "stat", lambda p, *a, **k: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted")) if str(p) == str(db) else real(p, *a, **k))
    assert probes.whatsapp_access(db, running=lambda: True)["why"] == probes.NO_ACCESS


def test_kakao_check_uses_the_configured_helper_dir(tmp_path, monkeypatch):
    import yaml
    from urgent_board.collectors import kakao
    shutil.copy(HOME / "settings.yaml", tmp_path / "settings.yaml")
    d = yaml.safe_load((tmp_path / "settings.yaml").read_text(encoding="utf-8"))
    d["accounts"]["kakao"]["helper_dir"] = str(tmp_path / "hd")
    (tmp_path / "settings.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    (tmp_path / "auth").mkdir()
    seen = []
    monkeypatch.setattr(kakao, "find_kakaocli", lambda: "/bin/kakaocli")
    monkeypatch.setattr(kakao, "load_auth", lambda cache=None, helper=None, **k: seen.append(helper) or None)
    monkeypatch.setattr(kakao, "query", lambda sql, auth, binary: [[1]])
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    c = create_app(live, Store(tmp_path / "t.db")).test_client()
    assert c.post("/api/connect/kakao/check", json={}, headers=H).get_json()["ok"] is True
    assert seen == [tmp_path / "hd" / "scripts" / "kakaotalk_mac.py"]
