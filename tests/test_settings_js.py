"""설정 화면의 계산 모듈(static/settings/model.js) — node 로 실제 돌려 본다(ES 모듈, DOM 없음). node 가 없으면 건너뛴다."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL = (ROOT / "urgent_board" / "static" / "settings" / "model.js").as_uri()
NODE = shutil.which("node") or shutil.which("node", path="/opt/homebrew/bin")


def run(body: str):
    """body 안에서 m(모듈)을 쓰고 마지막에 out(값)을 정한다."""
    if not NODE:
        pytest.skip("node 가 없다")
    src = f"import * as m from {json.dumps(MODEL)};\nlet out;\n{body}\nconsole.log(JSON.stringify(out));"
    r = subprocess.run([NODE, "--input-type=module", "-e", src], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


DOC = {"boxes": [{"key": "a", "name": "A", "color": "green", "active": True, "keywords": ["책방", "포럼"], "kakao_rooms": [], "telegram_rooms": []},
                 {"key": "b", "name": "B", "color": "cyan", "active": True, "keywords": ["책방운영단"], "kakao_rooms": [{"id": "1"}], "telegram_rooms": []},
                 {"key": "c", "name": "C", "color": "pink", "active": False, "keywords": ["포럼"], "kakao_rooms": [], "telegram_rooms": []}],
       "unconfirmed": [{"name": "대행", "keywords": ["정책설명회", "포럼"]}], "etc_name": "개인 · 기타"}
D = f"const doc = {json.dumps(DOC, ensure_ascii=False)};"


def test_taken_colors_counts_only_other_active_boxes():
    assert sorted(run(D + "out = [...m.takenColors(doc, 0)];")) == ["cyan"]          # 쉬는 C 의 분홍은 막지 않는다
    assert run("out = m.COLORS;") == ["green", "cyan", "pink", "yellow", "violet"]


def test_keyword_clashes_same_and_contains():
    got = run(D + "out = m.clashes(doc);")
    kinds = {(x["word"], x["other"], x["kind"]) for x in got}
    assert ("책방", "책방운영단", "contains") in kinds and ("포럼", "포럼", "same") in kinds         # 포럼: A 와 확정 전 일
    assert all(x["a"] != "C" and x["b"] != "C" for x in got)                               # 쉬는 사업의 낱말은 겨루지 않는다


def test_changes_counts_boxes_and_top_level_fields():
    assert run(D + "out = m.changes(doc, structuredClone(doc));") == 0
    assert run(D + "const d = structuredClone(doc); d.boxes[0].name = 'X'; d.etc_name = '기타'; out = m.changes(doc, d);") == 2
    assert run(D + "const d = structuredClone(doc); d.boxes.push({key: 'n'}); out = m.changes(doc, d);") == 1


def test_rooms_keep_ids_as_text():
    out = run(D + """const b = structuredClone(doc.boxes[0]);
      m.addRoom(b, "kakao", {id: "100000000000001123", name: "큰 방"});
      m.addRoom(b, "kakao", {id: "100000000000001123", name: "또"});            // 같은 방은 한 번만
      const round = JSON.parse(JSON.stringify(b));
      let threw = false; try { m.addRoom(b, "kakao", {id: 100000000000001123}); } catch (e) { threw = e instanceof TypeError; }
      m.toggleWatch(round, "kakao", 0);
      out = {rooms: round.kakao_rooms, threw};""")
    assert out == {"rooms": [{"id": "100000000000001123", "name": "큰 방", "watch": True}], "threw": True}
    assert run(D + "const b = structuredClone(doc.boxes[1]); m.removeRoom(b, 'kakao', 0); out = b.kakao_rooms;") == []


def test_room_label_falls_back_to_catalog_then_number():
    cat = {"rooms": [{"source": "kakao", "chat_id": "100000000000001123", "name": "저장소에서 본 이름"}]}
    c = f"const cat = {json.dumps(cat, ensure_ascii=False)};"
    assert run(c + "out = m.roomLabel({id: '100000000000001123'}, 'kakao', cat);") == "저장소에서 본 이름"
    assert run(c + "out = m.roomLabel({id: '100000000000001123', name: '이름표'}, 'kakao', cat);") == "이름표"
    assert run(c + "out = m.roomLabel({id: '99'}, 'kakao', cat);") == "번호 99"
    assert run(c + "out = m.roomLabel({name: '이름으로만'}, 'kakao', cat);") == "이름으로만(이름으로 맞춤)"


def test_owners_new_box_move_and_problems():
    out = run(D + """const o = m.owners(doc); const d = structuredClone(doc); const nb = m.newBox(d); d.boxes.push(nb);
      m.moveBox(d, 3, -1); m.moveBox(d, 0, -1);
      out = {owner: o.get("kakao:1"), key: nb.key, color: nb.color, order: d.boxes.map(b => b.key),
             at: m.problemsAt([{path: "boxes[1].color", message: "x"}, {path: "boxes[10].key", message: "y"}], "boxes[1]").length};""")
    assert out == {"owner": "b", "key": "box-1", "color": "pink", "order": ["a", "b", "box-1", "c"], "at": 1}


def test_normalize_fills_the_shapes_the_screen_needs_so_viewing_changes_nothing():
    """화면을 보기만 했는데 '바뀐 것 1개'가 뜨면 안 된다 — 읽을 때 저장본과 고치는 본을 같은 꼴로 채운다."""
    out = run("""const raw = {version: 2, boxes: [{key: "a", name: "A"}], summary: {enabled: true}};
      const d = m.normalize(structuredClone(raw)); const again = m.normalize(structuredClone(d));
      out = {same: JSON.stringify(d) === JSON.stringify(again), box: d.boxes[0], sources: d.summary.sources, lunch: d.workhours.lunch,
             th: d.rules.thresholds, tg: d.accounts.telegram, me: d.me, un: d.unconfirmed, keep: d.summary.enabled};""")
    assert out["same"] is True and out["keep"] is True
    assert out["box"] == {"key": "a", "name": "A", "mail_accounts": [], "senders": [], "kakao_rooms": [], "telegram_rooms": [], "keywords": []}
    assert out["sources"] == ["gmail", "kakao", "imessage"] and out["lunch"] == ["12:00", "13:00"] and out["th"] == {}
    assert out["tg"] == {"enabled": False} and out["me"]["names"] == [] and out["un"] == []
    assert out["me"]["personal_senders"] == []                                             # 개인 · 기타로 둔 사람(2026-10-16)


NET = (ROOT / "urgent_board" / "static" / "settings" / "net.js").as_uri()


def run_net(body: str):
    if not NODE:
        pytest.skip("node 가 없다")
    src = f"import * as n from {json.dumps(NET)};\nlet out;\n{body}\nconsole.log(JSON.stringify(out));"
    r = subprocess.run([NODE, "--input-type=module", "-e", src], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_requests_while_the_engine_restarts_say_so_and_reading_tries_again():
    """연결 단추 · 연결 설정 저장 뒤 엔진이 다시 시작하는 몇 초 동안의 요청 — 아무 말 없이 멈추지 않는다(검토 M8)."""
    got = run_net("out = await n.call(async () => { throw new TypeError('Failed to fetch'); }, 'GET', '/api/settings');")
    assert got["status"] == 0 and got["ok"] is False and "다시 시작" in got["data"]["error"]
    got = run_net("""let k = 0;
      const fake = async () => { k++; if (k < 3) throw new TypeError('x'); return { status: 200, ok: true, json: async () => ({ v: 1 }) }; };
      const r = await n.retry(() => n.call(fake, 'GET', '/x'), { tries: 5, sleep: async () => {} });
      out = [r.status, r.data.v, k];""")
    assert got == [200, 1, 3]
    got = run_net("""let k = 0;
      const r = await n.retry(async () => { k++; return { status: 0, ok: false, data: {} }; }, { tries: 4, sleep: async () => {} });
      out = [r.status, k];""")
    assert got == [0, 4]
    got = run_net("out = await n.call(async () => ({ status: 409, ok: false, json: async () => { throw new SyntaxError('x'); } }), 'PUT', '/x', {});")
    assert got == {"status": 409, "ok": False, "data": {}}


def test_normalize_turns_whatsapp_off_unless_written():       # 왓츠앱 설계 D2 — 엔진(config.from_doc)과 같게
    out = run("""out = {off: m.normalize({version: 2}).accounts.whatsapp,
                        on: m.normalize({version: 2, accounts: {whatsapp: {enabled: true}}}).accounts.whatsapp};""")
    assert out == {"off": {"enabled": False}, "on": {"enabled": True}}


def test_every_engine_source_has_a_korean_name_in_the_boxes_page():   # 왓츠앱 검토 8 — 사용자는 영어를 읽지 못한다
    from urgent_board import settings_doc
    if not NODE:
        pytest.skip("node 가 없다")
    boxes = (ROOT / "urgent_board" / "static" / "settings" / "boxes.js").as_uri()
    src = f"import {{ SOURCE_NAME }} from {json.dumps(boxes)}; console.log(JSON.stringify(SOURCE_NAME));"
    r = subprocess.run([NODE, "--input-type=module", "-e", src], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    names = json.loads(r.stdout)
    assert all(names.get(k) for k in settings_doc.SOURCES) and names["whatsapp"] == "왓츠앱"
