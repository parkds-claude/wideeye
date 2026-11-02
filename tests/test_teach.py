"""한 번 알려 주면 그대로(2026-10-16 분류 키우기 — docs/exec-plans/2026-10-16-classify-teach-and-pick.md).
전체 목록에서 할 일의 사업을 고르면 그 보낸 사람(방 묶음이면 방)을 고른 상자에 등록하고, 장부의 같은 사람의 할 일도 곧바로 옮긴다."""
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from urgent_board import config, settings_doc as sd
from urgent_board.models import Event, Source
from urgent_board.store import Store
from urgent_board.web import create_app

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
H = {"Origin": "http://localhost"}
NOW = datetime.now(timezone.utc)


def setup(tmp_path):
    shutil.copy(HOME / "settings.yaml", tmp_path / "settings.yaml")
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    live.get().handled = {**live.get().handled, "claude_sessions": False}
    st = Store(tmp_path / "t.db")
    return create_app(live, st, clock=lambda: NOW).test_client(), live, st


def mail(mid, sender="kim@client.example", subject="견적 문의", minutes=30):
    return Event(source=Source.GMAIL, chat_id="t" + mid, message_id=mid, chat_name=subject, sender_id=sender, sender_name="김과장",
                 text=subject + " — 견적서를 이번 주 안에 보내 주실 수 있을까요 회신 부탁드립니다", received_at=NOW - timedelta(minutes=minutes),
                 is_from_me=False, is_read=False, is_group=False, thread_id="t" + mid, account="main@example.com",
                 raw={"subject": subject, "headers": {}, "to": ["main@example.com"], "labels": []})


def boxes_of(c):
    return {t["key"]: t["box"] for t in c.get("/api/status").get_json()["tasks"]}


def test_teaching_a_sender_registers_them_and_moves_their_tasks(tmp_path):
    c, live, st = setup(tmp_path)
    st.upsert_events([mail("m1"), mail("m2", subject="다른 건", minutes=60), mail("m3", sender="lee@x.example", subject="세 번째")])
    c.get("/api/items")                                                             # 장부에 연다
    r = c.post("/api/teach", json={"key": "gmail:tm1:m1", "box": "bazaar2"}, headers=H)
    assert r.status_code == 200 and r.get_json()["ok"] and r.get_json()["what"] == "보낸 사람"
    assert "kim@client.example" in next(b for b in live.get().businesses if b.key == "bazaar2").senders
    assert "kim@client.example" in sd.read(tmp_path)[0]["boxes"][1]["senders"]                       # 설정 파일에 남는다
    got = {r["key"]: r["business"] for r in st.tasks("open")}
    assert got["gmail:tm1:m1"] == got["gmail:tm2:m2"] == "bazaar2" and got["gmail:tm3:m3"] != "bazaar2"   # 같은 사람의 다른 할 일도


def test_teaching_moves_a_sender_between_boxes_and_to_personal(tmp_path):
    c, live, st = setup(tmp_path)
    st.upsert_events([mail("m1")])
    c.get("/api/items")
    c.post("/api/teach", json={"key": "gmail:tm1:m1", "box": "bazaar2"}, headers=H)
    c.post("/api/teach", json={"key": "gmail:tm1:m1", "box": "calligraphy-club"}, headers=H)
    by = {b.key: b.senders for b in live.get().businesses}
    assert "kim@client.example" in by["calligraphy-club"] and "kim@client.example" not in by["bazaar2"]      # 한 상자에만
    r = c.post("/api/teach", json={"key": "gmail:tm1:m1", "box": "personal"}, headers=H)
    assert r.status_code == 200 and "kim@client.example" in live.get().personal_senders
    assert all("kim@client.example" not in b.senders for b in live.get().businesses)


def test_teaching_a_room_bundle_moves_the_room(tmp_path):
    c, live, st = setup(tmp_path)
    doc, ver = sd.read(tmp_path)
    doc["watch_rooms"] = [{"source": "telegram", "chat_id": "-777"}]
    sd.save(tmp_path, doc, ver); live.check()
    st.upsert_events([Event(source=Source.TELEGRAM, chat_id="-777", message_id=str(i), chat_name="지켜보는 방", sender_id=f"u{i}", sender_name="가",
                            text=f"오늘 공지 {i}", received_at=NOW - timedelta(minutes=10 * i), is_from_me=False, is_read=False, is_group=True)
                      for i in (1, 2)])
    rows = c.get("/api/status").get_json()["tasks"]
    assert rows, "방 묶음이 할 일로 떠야 한다"
    r = c.post("/api/teach", json={"key": rows[0]["key"], "box": "calligraphy-club"}, headers=H)
    assert r.status_code == 200 and r.get_json()["what"] == "방"
    room = [x for x in sd.read(tmp_path)[0]["boxes"][4]["telegram_rooms"] if str(x.get("id")) == "-777"]
    assert room and room[0].get("watch") is True                                   # 지켜보기는 따라간다
    assert boxes_of(c)[rows[0]["key"]] == "동네 서예 동호회" and sd.read(tmp_path)[0]["watch_rooms"] == []      # 상자에 있는 방이 지켜보기를 맡는다


def test_teach_refuses_unknown_tasks_boxes_and_cross_site_posts(tmp_path):
    c, _, st = setup(tmp_path)
    st.upsert_events([mail("m1")])
    c.get("/api/items")
    assert c.post("/api/teach", json={"key": "gmail:tm1:m1", "box": "없는상자"}, headers=H).status_code == 400
    assert c.post("/api/teach", json={"key": "없는:열쇠", "box": "bazaar2"}, headers=H).status_code == 404
    assert c.post("/api/teach", data="key=gmail:tm1:m1&box=bazaar2", headers={**H, "Content-Type": "application/x-www-form-urlencoded"}).status_code in (400, 403, 415)


def test_list_offers_the_box_choice_and_marks_claude_guesses(tmp_path):
    c, _, st = setup(tmp_path)
    st.upsert_events([mail("m1")])
    html = c.get("/list").get_data(as_text=True)
    assert 'class="teach"' in html and 'data-key="gmail:tm1:m1"' in html and "동네 합창 모임" in html and "개인 · 기타" in html


def test_teaching_an_awaiting_line_registers_the_person_written_to(tmp_path):
    """답 기다림(내가 보낸 요청)에서 고르면 받는 사람을 등록한다 — 그 뒤 그 사람에게 보낸 것 · 받은 것이 그 사업으로."""
    c, live, st = setup(tmp_path)
    mine = Event(source=Source.GMAIL, chat_id="tw1", message_id="w1", chat_name="배차 재확인 요청", sender_id="minji@example.com", sender_name="나",
                 text="배차 재확인 요청 — 확인 후 회신 부탁드립니다", received_at=NOW - timedelta(hours=1), is_from_me=True, is_read=True, is_group=False,
                 thread_id="tw1", account="minji@example.com", raw={"subject": "배차 재확인 요청", "headers": {}, "to": ["bus@vendor.example"], "labels": ["SENT"]})
    st.upsert_events([mine])
    html = c.get("/list").get_data(as_text=True)
    assert 'class="teach" data-key="gmail:tw1:w1"' in html
    r = c.post("/api/teach", json={"key": "gmail:tw1:w1", "box": "bazaar2"}, headers=H)
    assert r.status_code == 200 and r.get_json()["what"] == "보낸 사람"
    assert "bus@vendor.example" in next(b for b in live.get().businesses if b.key == "bazaar2").senders
    j = c.get("/api/items").get_json()
    assert all(not t["awaiting"] for x in j["cards"] for t in x["tasks"])                 # 답 기다림은 탭5 상자에 없다(2026-10-17 — 목록에만)
    assert 'data-key="gmail:tw1:w1"' in c.get("/list").get_data(as_text=True)              # 목록에는 남는다
