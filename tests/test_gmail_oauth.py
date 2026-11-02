import json
import os
import shutil
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from urgent_board import config, paths, settings_doc as sd
from urgent_board.connect import gmail_oauth as go
from urgent_board.store import Store
from urgent_board.web import create_app

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
H = {"Origin": "http://localhost"}
CLIENT = {"installed": {"client_id": "123.apps.googleusercontent.com", "client_secret": "CLIENT-SECRET",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}


def test_save_client_accepts_desktop_clients_only(tmp_path):
    c = go.save_client(tmp_path, json.dumps(CLIENT))
    assert c["client_id"].startswith("123") and oct((tmp_path / "google-client.json").stat().st_mode & 0o777) == "0o600"
    assert go.load_client(tmp_path)["client_secret"] == "CLIENT-SECRET"
    for bad in ("not json", json.dumps({"web": CLIENT["installed"]}), json.dumps({"installed": {"client_id": "x"}})):
        with pytest.raises(go.FlowError):
            go.save_client(tmp_path / "x", bad)
    assert go.load_client(tmp_path / "none") is None


def test_flows_are_one_time_short_lived_and_bound_to_the_browser():
    now = [1000.0]
    f = go.Flows(clock=lambda: now[0])
    url, state = f.start(CLIENT["installed"], "http://127.0.0.1:5091/connect/gmail/callback", "NONCE")
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["scope"] == [go.SCOPE] and q["state"] == [state] and q["redirect_uri"] == ["http://127.0.0.1:5091/connect/gmail/callback"]
    with pytest.raises(go.FlowError):
        f.finish(state, "OTHER")                                     # 다른 브라우저에서 온 돌아오기
    url, state = f.start(CLIENT["installed"], "http://127.0.0.1:5091/connect/gmail/callback", "NONCE")
    verifier, redirect = f.finish(state, "NONCE")
    assert len(verifier) >= 43 and redirect.endswith("/callback")
    with pytest.raises(go.FlowError):
        f.finish(state, "NONCE")                                     # 두 번은 안 된다
    _, old = f.start(CLIENT["installed"], "http://x/cb", "NONCE")
    now[0] += go.FLOW_SECONDS + 1
    with pytest.raises(go.FlowError):
        f.finish(old, "NONCE")                                       # 10분이 지났다
    with pytest.raises(go.FlowError):
        f.finish("모르는 state", "NONCE")


def test_exchange_sends_the_verifier_and_reports_errors_without_bodies():
    sent = {}
    def post(url, data):
        sent.update(url=url, data=data)
        return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
    tok = go.exchange(CLIENT["installed"], "CODE", "VERIFIER", "http://x/cb", post=post)
    assert tok["refresh_token"] == "RT" and sent["data"]["code_verifier"] == "VERIFIER" and sent["data"]["grant_type"] == "authorization_code"
    with pytest.raises(go.FlowError) as e:
        go.exchange(CLIENT["installed"], "CODE", "V", "http://x/cb", post=lambda u, d: {"error": "invalid_grant", "error_description": "Bad Request"})
    assert "invalid_grant" in str(e.value)


def test_alias_for_and_payload():
    assert go.alias_for("Me.Work@x.com", set()) == "me-work" and go.alias_for("Me.Work@x.com", {"me-work"}) == "me-work-2"
    assert go.alias_for("한글@x.com", set()) == "mail"
    p = go.token_payload(CLIENT["installed"], {"access_token": "AT", "refresh_token": "RT"})
    assert p == {"token": "AT", "refresh_token": "RT", "token_uri": CLIENT["installed"]["token_uri"], "client_id": CLIENT["installed"]["client_id"],
                 "client_secret": "CLIENT-SECRET", "scopes": [go.SCOPE]}


def app(tmp_path, post=None, get=None):
    shutil.copy(HOME / "settings.yaml", tmp_path / "settings.yaml")
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    restarts = []
    a = create_app(live, Store(tmp_path / "t.db"), restart=restarts.append)
    a.config["UB"].update(oauth_post=post or (lambda u, d: {"access_token": "AT", "refresh_token": "RT"}),
                          oauth_get=get or (lambda u, token: {"emailAddress": "New.Person@example.com"}))
    return a.test_client(), live, restarts


def start(c):
    assert c.post("/api/connect/gmail/client", json={"text": json.dumps(CLIENT)}, headers=H).get_json()["ok"] is True
    r = c.post("/api/connect/gmail/start", json={}, headers=H)
    assert "ub_oauth=" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    return parse_qs(urlparse(r.get_json()["url"]).query)["state"][0]


def test_callback_adds_the_account_with_a_file_token(tmp_path):
    c, live, restarts = app(tmp_path)
    state = start(c)
    r = c.get(f"/connect/gmail/callback?state={state}&code=CODE")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "연결했어요" in body and "RT" not in body and "CLIENT-SECRET" not in body
    doc, _ = sd.read(tmp_path)
    acc = doc["accounts"]["gmail"][-1]
    assert acc == {"alias": "new-person", "email": "New.Person@example.com", "label": "new-person", "token": "file:gmail-new-person.json"}
    f = paths.auth_dir(tmp_path) / "gmail-new-person.json"
    assert json.loads(f.read_text())["refresh_token"] == "RT" and oct(f.stat().st_mode & 0o777) == "0o600"
    end = time.monotonic() + 3
    while not restarts and time.monotonic() < end:
        time.sleep(0.05)
    assert restarts                                                   # 메일 계정이 늘었다 — 엔진이 다시 시작
    assert c.get(f"/connect/gmail/callback?state={state}&code=CODE").status_code == 400     # 같은 state 로 두 번은 안 된다


def test_bad_state_or_missing_refresh_token_adds_nothing(tmp_path):
    c, _, _ = app(tmp_path, post=lambda u, d: {"access_token": "AT"})
    before = sd.read(tmp_path)[0]["accounts"]["gmail"]
    assert c.get("/connect/gmail/callback?state=nope&code=CODE").status_code == 400
    state = start(c)
    r = c.get(f"/connect/gmail/callback?state={state}&code=CODE")
    assert r.status_code == 400 and "타사 앱" in r.get_data(as_text=True)
    assert sd.read(tmp_path)[0]["accounts"]["gmail"] == before
    assert c.get("/connect/gmail/callback?error=access_denied&state=x").status_code == 400


def test_remove_account(tmp_path):
    c, _, _ = app(tmp_path)
    state = start(c)
    c.get(f"/connect/gmail/callback?state={state}&code=CODE")
    r = c.post("/api/connect/gmail/remove", json={"alias": "new-person"}, headers=H)
    assert r.status_code == 200 and all(g["alias"] != "new-person" for g in sd.read(tmp_path)[0]["accounts"]["gmail"])
    assert not (paths.auth_dir(tmp_path) / "gmail-new-person.json").exists()
    assert c.post("/api/connect/gmail/remove", json={"alias": "workbase"}, headers=H).status_code == 200     # 키체인 참조는 설정에서만 빠진다


def test_callback_without_a_settings_file_adds_the_account_and_with_a_broken_one_stops_before_google(tmp_path):
    """설정 파일이 없어도(처음 쓰는 사람) 계정을 적고, 깨져 있으면 Google 과 코드를 바꾸기 전에 멈춘다(검토 I5 — 되돌릴 수 없는 일 앞에서)."""
    calls = []
    c, live, _ = app(tmp_path, post=lambda u, d: calls.append(u) or {"access_token": "AT", "refresh_token": "RT"})
    (tmp_path / "settings.yaml").unlink()
    state = start(c)
    r = c.get(f"/connect/gmail/callback?state={state}&code=CODE")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert sd.read(tmp_path)[0]["accounts"]["gmail"][-1]["email"] == "New.Person@example.com"
    (tmp_path / "settings.yaml").write_text("boxes: [\n", encoding="utf-8")
    calls.clear()
    state = start(c)
    r = c.get(f"/connect/gmail/callback?state={state}&code=CODE")
    assert r.status_code == 409 and "설정 파일" in r.get_data(as_text=True) and calls == []


def test_google_addresses_are_ours_not_the_client_files(tmp_path):
    """받은 클라이언트 파일이 auth_uri · token_uri 를 다른 곳으로 적어 두어도 코드 · 비밀 · 토큰은 Google 로만 간다(검토 M4)."""
    evil = {"installed": dict(CLIENT["installed"], auth_uri="https://evil.example/auth", token_uri="https://evil.example/token")}
    c = go.save_client(tmp_path, json.dumps(evil))
    url, _ = go.Flows().start(c, "http://127.0.0.1:5091/connect/gmail/callback", "N")
    assert url.startswith(go.AUTH_URI + "?")
    sent = []
    go.exchange(go.load_client(tmp_path), "CODE", "V", "http://x/cb", post=lambda u, d: sent.append(u) or {"access_token": "AT", "refresh_token": "RT"})
    assert sent == [go.TOKEN_URI]
    assert go.token_payload(evil["installed"], {"access_token": "AT", "refresh_token": "RT"})["token_uri"] == go.TOKEN_URI
