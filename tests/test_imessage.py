import sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
from urgent_board.collectors import imessage

APPLE = 978307200
def mk(tmp_path):
    db = tmp_path / "chat.db"; c = sqlite3.connect(db)
    c.executescript((Path(__file__).parent / "fixtures" / "chat_mini.sql").read_text())
    now_ns = int((time.time() - APPLE) * 1e9)
    c.execute("INSERT INTO message VALUES (1,'m1','발권 진행할까요?',NULL,1,?,0,0,0,0,0,'SMS')", (now_ns - 60_000_000_000,))
    blob = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+\x0d\xec\xb2\xa8\xeb\xb6\x80 \xed\x99\x95\xec\x9d\xb8\x86"
    c.execute("INSERT INTO message VALUES (2,'m2',NULL,?,1,?,0,0,0,0,0,'SMS')", (blob, now_ns - 30_000_000_000))
    c.execute("INSERT INTO message VALUES (3,'m3','탭백',NULL,1,?,0,0,0,2001,0,'SMS')", (now_ns,))   # 탭백 — 제외
    c.executemany("INSERT INTO chat_message_join VALUES (1,?)", [(1,), (2,), (3,)]); c.commit(); c.close()
    return db

def test_collect_excludes_tapback_and_decodes_body(tmp_path):      # Review Focus 1
    col = imessage.IMessageCollector(mk(tmp_path), my_phone_tails=[])
    evs = col.collect(since=datetime.now(timezone.utc).replace(year=2000))
    assert [e.message_id for e in evs] == ["1", "2"]
    assert evs[0].text == "발권 진행할까요?" and evs[1].text == "첨부 확인"
    assert evs[0].chat_name == "+821000000000" and not evs[0].is_group

def test_apple_to_dt_handles_ns_and_s():
    assert imessage.apple_to_dt(0) is None
    a = imessage.apple_to_dt(700_000_000); b = imessage.apple_to_dt(700_000_000 * 10**9)
    assert a == b

def test_body_attachment_only_blob_becomes_label():
    blob = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+\x03\xef\xbf\xbc\x86"
    row = {"text": None, "attributedBody": blob, "cache_has_attachments": 1}
    assert imessage.body(row) == "[첨부]"
    row2 = {"text": None, "attributedBody": blob, "cache_has_attachments": 0}
    assert imessage.body(row2) == ""


def test_collect_shows_contact_names_instead_of_numbers(tmp_path):
    # 메시지 앱은 연락처 이름("협의회 가나다 연구관")을 보이는데 현황판은 번호만 보였다(2026-10-14) — 이름이 사업 낱말에도 걸린다
    names = {"+821000000000": "협의회 가나다 연구관"}.get
    evs = imessage.IMessageCollector(mk(tmp_path), my_phone_tails=[], names=names).collect(since=datetime.now(timezone.utc).replace(year=2000))
    assert evs[0].chat_name == "협의회 가나다 연구관" and evs[0].sender_name == "협의회 가나다 연구관"
    assert evs[0].sender_id == "+821000000000" and evs[0].link == "imessage://+821000000000"     # 견주기 · 열기는 번호 그대로
