"""설정 화면의 연결 칸(static/settings/connect.js)을 node 로 실제 그려 본다 — 브라우저 없이, 화면 객체는 빈 흉내.

2026-10-17: 해요체로 바꾸며 넣은 '멈춘 계정 안내'를 계정 줄 밖(map 의 둘째 인자 자리)에 두어, 메일 계정이 하나라도 있으면
연결 칸 전체가 그리다 오류로 멈췄다(설정 화면이 '읽는 중…'에 머묾). 계산만 보던 시험(test_settings_js)으로는 못 잡았다."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONNECT = ROOT / "urgent_board" / "static" / "settings" / "connect.js"
NODE = shutil.which("node") or shutil.which("node", path="/opt/homebrew/bin")

HARNESS = r"""
class Node {}
class El extends Node {
  constructor(tag) { super(); this.tag = tag; this.kids = []; this.className = ""; this.attrs = {}; this.style = {}; this.textContent = ""; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  append(...k) { this.kids.push(...k); }
  get text() { return this.kids.map(k => k instanceof El ? k.text : k.data).join(" "); }
}
globalThis.Node = Node;
globalThis.document = { createElement: t => new El(t), createTextNode: t => ({ data: String(t) }), body: new El("body") };
const [file, raw] = process.argv.slice(-2);           // node -e 는 스크립트 경로 칸이 없다
const m = await import(file);
const ctx = JSON.parse(raw);
ctx.api = async () => ({ ok: true, data: {} }); ctx.say = () => {}; ctx.touch = () => {}; ctx.render = () => {};
ctx.reloadAll = async () => {}; ctx.loadConnect = () => {};
const out = {};
for (const [name, fn] of Object.entries(m)) if (name.startsWith("render") && typeof fn === "function") out[name] = fn(ctx).text;
console.log(JSON.stringify(out));
"""


def render_all(connect: dict | None) -> dict:
    if not NODE:
        pytest.skip("node 가 없다")
    ctx = {"state": {"doc": {"accounts": {}}, "connect": connect, "problems": []}}
    r = subprocess.run([NODE, "--input-type=module", "-e", HARNESS, "--", CONNECT.as_uri(), json.dumps(ctx)],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


CONNECTED = {
    "google_client": True, "telegram": {"api": True, "session": True},
    "kakaocli": {"found": True, "path": "/opt/homebrew/bin/kakaocli", "version": "0.5.0"},
    "messages": {"ok": True}, "contacts": {"ok": True}, "python": "/usr/bin/python3", "claude": {"found": True, "path": "claude", "version": "2"},
    "sources": {
        "gmail": [{"alias": "a", "email": "a@example.com", "label": "a", "token": "file", "state": "ok", "at": "", "note": ""},
                  {"alias": "b", "email": "b@example.com", "label": "b", "token": "keychain", "state": "stopped", "at": "", "note": "토큰 없음"}],
        "telegram": {"state": "ok", "at": "", "note": ""}, "kakao": {"state": "ok", "at": "", "note": ""}, "imessage": {"state": "ok", "at": "", "note": ""}},
}


def test_connect_pages_draw_with_accounts_and_while_still_checking():
    for connect in (CONNECTED, None):                        # 계정이 있을 때 · 연결 점검이 아직 오지 않았을 때
        out = render_all(connect)
        assert out and all(isinstance(v, str) for v in out.values())
    text = " ".join(render_all(CONNECTED).values())
    assert "a@example.com" in text and "b@example.com" in text
    assert "다시 로그인하면 고쳐져요" in text                  # 멈춘 계정(b)에만 안내
    assert text.count("다시 로그인하면 고쳐져요") == 1


def test_sms_page_has_missed_calls_and_teams_and_the_telegram_page_has_bots():
    # 2026-10-17 알림 2단계 — 부재중 전화 · 팀즈 알림(문자와 같은 전체 디스크 접근 권한)은 문자 칸에, 봇 보고서는 텔레그램 칸에
    sms = render_all({**CONNECTED, "calls": {"ok": True}, "teams": {"ok": False, "why": "자료가 없어요(이 맥에서 그 앱을 쓴 적이 없을 수 있어요)."},
                      "sources": {**CONNECTED["sources"], "calls": {"state": "ok", "at": "", "note": "9 seen, 0 new"}}})
    assert "부재중 전화 모으기" in sms["renderSms"] and "통화 기록을 읽을 수 있어요." in sms["renderSms"]
    assert "팀즈 알림 모으기" in sms["renderSms"] and "자료가 없어요" in sms["renderSms"]
    assert "봇 보고서도 탭5 알림에 보이기" in sms["renderTelegram"]
    assert "부재중 전화 모으기" in render_all(None)["renderSms"]                         # 연결 점검이 오기 전에도 그려진다
