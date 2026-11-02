"""왓츠앱 수집기(설계 docs/exec-plans/2026-10-28-whatsapp-design.md) — 지어낸 DB 로. 실제 사람 · 번호 · 글을 넣지 않는다."""
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from urgent_board.collectors import whatsapp
from urgent_board.models import Source

APPLE = 978307200
SINCE = datetime.now(timezone.utc) - timedelta(days=7)
DM = "821000000000@s.whatsapp.net"
GROUP = "120363000000000000@g.us"


def mk(tmp_path, sessions, messages, members=()):
    db = tmp_path / "ChatStorage.sqlite"
    c = sqlite3.connect(db)
    c.executescript((Path(__file__).parent / "fixtures" / "whatsapp_mini.sql").read_text())
    c.executemany("INSERT INTO ZWACHATSESSION VALUES (?,?,?,?,?,?,?,?)", sessions)
    c.executemany("INSERT INTO ZWAGROUPMEMBER VALUES (?,?,?,?,?)", members)
    c.executemany("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?,?,?,?,?,?)", messages)
    c.commit()
    c.close()
    return db


def s(pk, kind, jid, name="", unread=0, hidden=0, removed=0, archived=0):
    return (pk, kind, jid, name, unread, hidden, removed, archived)


def m(pk, chat, text="견적서 확인 부탁드립니다", mine=0, kind=0, ago=60, from_jid=None, push=None, member=None):
    return (pk, chat, member, mine, kind, int(time.time() - APPLE - ago), text, from_jid, push, f"S{pk}")


def collect(db, **kw):
    return whatsapp.WhatsAppCollector(db, running=lambda: True, **kw).collect(SINCE)


def test_only_dm_and_groups_are_read(tmp_path):            # D3
    archived = "821000000009@s.whatsapp.net"
    sessions = [s(1, 0, DM), s(2, 1, GROUP, "현장팀"), s(3, 2, "1700000000@broadcast"), s(4, 5, "120000000@newsletter"),
                s(5, 0, "status@broadcast"), s(6, 0, "821000000007@s.whatsapp.net", hidden=1),
                s(7, 0, "821000000008@s.whatsapp.net", removed=1), s(8, 0, archived, archived=1)]
    jids = {1: DM, 3: "1700000000@broadcast", 4: "120000000@newsletter", 5: "status@broadcast", 6: "821000000007@s.whatsapp.net",
            7: "821000000008@s.whatsapp.net", 8: archived}
    msgs = [m(i, i, from_jid=jids.get(i)) for i in range(1, 9)]
    msgs[1] = m(2, 2, member=1, from_jid=GROUP)
    evs = collect(mk(tmp_path, sessions, msgs, [(1, 2, "821000000002@s.whatsapp.net", "", "")]))
    assert {e.chat_id for e in evs} == {DM, GROUP, archived}
    assert all(e.source == Source.WHATSAPP for e in evs)


def test_system_and_deleted_are_dropped_media_is_attachment(tmp_path):   # D4
    msgs = [m(1, 1, "견적서 확인 부탁드립니다", ago=70), m(2, 1, "https://example.com 자료", kind=7, ago=60),
            m(3, 1, "수정한 글", kind=42, ago=50), m(4, 1, None, kind=1, ago=40), m(5, 1, "암호화 안내", kind=10, ago=30),
            m(6, 1, None, kind=14, ago=20), m(7, 1, None, kind=59, ago=10)]
    evs = collect(mk(tmp_path, [s(1, 0, DM)], [x[:7] + (DM,) + x[8:] for x in msgs]))
    assert [(e.raw["type"], e.text) for e in evs] == [(0, "견적서 확인 부탁드립니다"), (7, "https://example.com 자료"), (42, "수정한 글"), (1, "[첨부]")]


def test_senders_and_me(tmp_path):                          # D6
    lid = "123456789@lid"
    sessions = [s(1, 0, DM), s(2, 0, lid), s(3, 1, GROUP, "현장팀")]
    msgs = [m(1, 1, from_jid=DM, ago=40), m(2, 2, from_jid=lid, ago=30), m(3, 3, from_jid=GROUP, member=1, ago=20), m(4, 1, "보냈습니다", mine=1, ago=10)]
    evs = {e.message_id: e for e in collect(mk(tmp_path, sessions, msgs, [(1, 3, "821000000002@s.whatsapp.net", "", "")]))}
    assert evs["S1"].sender_id == "+821000000000" and evs["S2"].sender_id == lid and evs["S3"].sender_id == "+821000000002"
    assert evs["S4"].sender_id == "me" and evs["S4"].is_from_me and evs["S4"].is_read
    assert evs["S3"].is_group and not evs["S1"].is_group


def test_names_order_and_bidi_marks(tmp_path):              # Review Focus 2
    other = "821000000001@s.whatsapp.net"
    sessions = [s(1, 0, DM, "‪+82 10-0000-0000‬"), s(2, 0, other, "‪+82 10-0000-0001‬"), s(3, 1, GROUP, "‪현장팀‬")]
    msgs = [m(1, 1, from_jid=DM, push="푸시이름"), m(2, 2, from_jid=other, push="푸시이름"), m(3, 3, from_jid=GROUP, member=1, push="푸시이름")]
    names = {"+821000000000": "가나다 과장"}.get
    evs = {e.message_id: e for e in collect(mk(tmp_path, sessions, msgs, [(1, 3, "821000000002@s.whatsapp.net", "라마바", "")]),
                                            names=lambda h: names(h, ""))}
    assert evs["S1"].sender_name == "가나다 과장" and evs["S1"].chat_name == "가나다 과장"
    assert evs["S2"].sender_name == "+82 10-0000-0001" and evs["S2"].chat_name == "+82 10-0000-0001"
    assert evs["S3"].sender_name == "라마바" and evs["S3"].chat_name == "현장팀"


def test_dm_without_partner_name_uses_push_name(tmp_path):    # D6 — 대화 이름이 비면 글의 푸시 이름, 그것도 없으면 번호
    lid = "123456789@lid"
    evs = {e.message_id: e for e in collect(mk(tmp_path, [s(1, 0, lid, ""), s(2, 0, DM, "")],
                                                [m(1, 1, from_jid=lid, push="푸시이름"), m(2, 2, from_jid=DM)]))}
    assert evs["S1"].sender_name == "푸시이름" and evs["S1"].chat_name == "푸시이름"
    assert evs["S2"].sender_name == "+821000000000"


def test_unread_count_edges(tmp_path):                      # D5 · Review Focus 3
    counts = {1: 0, 2: 2, 3: -1, 4: 9}
    jid = {k: f"82100000000{k}@s.whatsapp.net" for k in counts}
    sessions = [s(k, 0, jid[k], unread=n) for k, n in counts.items()]
    msgs = []
    for k in counts:
        msgs.append(m(k * 10, k, "보냈습니다", mine=1, ago=400))
        msgs += [m(k * 10 + i, k, from_jid=jid[k], ago=400 - 100 * i) for i in (1, 2, 3)]
    evs = collect(mk(tmp_path, sessions, msgs))
    got = {n: [e.is_read for e in evs if e.chat_id == jid[k] and not e.is_from_me] for k, n in counts.items()}
    assert got[0] == [True, True, True]
    assert got[2] == [True, False, False]
    assert got[-1] == [True, True, False]
    assert got[9] == [False, False, False]
    assert all(e.is_read for e in evs if e.is_from_me)


def test_mention_by_my_number_only(tmp_path):               # D9
    assert whatsapp.mentions_me("@821012345678 확인 부탁", ["12345678"])
    assert whatsapp.mentions_me("@821012345678 확인 부탁", ["5678"])
    assert not whatsapp.mentions_me("@821099999999 확인 부탁", ["12345678"])
    assert not whatsapp.mentions_me("@123456789 확인", ["12345678"])        # lid 꼴은 못 잡는다(알려진 한계)
    assert not whatsapp.mentions_me("@821012345678", [])
    text = "@821012345678 확인 부탁드립니다"
    sessions = [s(1, 0, DM), s(2, 1, GROUP, "현장팀")]
    msgs = [m(1, 1, text, from_jid=DM), m(2, 2, text, from_jid=GROUP, member=1)]
    evs = {e.message_id: e for e in collect(mk(tmp_path, sessions, msgs, [(1, 2, "821000000002@s.whatsapp.net", "", "")]),
                                            my_phone_tails=["5678"])}
    assert evs["S2"].mentioned_me and not evs["S1"].mentioned_me


def test_since_and_link(tmp_path):
    sessions = [s(1, 0, DM), s(2, 1, GROUP, "현장팀"), s(3, 0, "123456789@lid")]
    msgs = [m(1, 1, from_jid=DM, ago=8 * 86400), m(2, 1, from_jid=DM), m(3, 2, from_jid=GROUP, member=1), m(4, 3, from_jid="123456789@lid")]
    evs = {e.message_id: e for e in collect(mk(tmp_path, sessions, msgs, [(1, 2, "821000000002@s.whatsapp.net", "", "")]))}
    assert set(evs) == {"S2", "S3", "S4"}                    # 8일 전 글은 빠진다
    assert evs["S2"].link == "whatsapp://send?phone=821000000000"
    assert evs["S3"].link == "whatsapp://" and evs["S4"].link == "whatsapp://"


def test_missing_db_raises(tmp_path):
    with pytest.raises(RuntimeError, match="자료가 없다"):
        whatsapp.WhatsAppCollector(tmp_path / "none.sqlite", running=lambda: True).collect(SINCE)


def test_app_not_running_raises(tmp_path):                  # D11 · Review Focus 1
    with pytest.raises(RuntimeError, match="꺼져 있어"):
        whatsapp.WhatsAppCollector(mk(tmp_path, [s(1, 0, DM)], [m(1, 1, from_jid=DM)]), running=lambda: False).collect(SINCE)


def test_constructor_touches_nothing(tmp_path, monkeypatch):    # import · 생성자에서는 파일 · 하위 프로세스를 일으키지 않는다
    monkeypatch.setattr(whatsapp.subprocess, "run", lambda *a, **k: pytest.fail("생성자가 프로세스를 띄웠다"))
    whatsapp.WhatsAppCollector(tmp_path / "none.sqlite")


def test_lid_resolves_to_phone_through_whatsapp_contacts(tmp_path):    # D6 고침(2026-10-28 실측: 지난 7일 받은 글의 보낸 사람이 모두 lid 였다)
    lid, unknown = "123456789@lid", "987654321@lid"
    db = mk(tmp_path, [s(1, 0, lid, "‪+82 10-0000-0005‬"), s(2, 0, unknown, "모르는 분")],
            [m(1, 1, from_jid=lid), m(2, 2, from_jid=unknown)])
    c = sqlite3.connect(tmp_path / "ContactsV2.sqlite")
    c.execute("CREATE TABLE ZWAADDRESSBOOKCONTACT (Z_PK INTEGER PRIMARY KEY, ZLID VARCHAR, ZPHONENUMBER VARCHAR)")
    c.executemany("INSERT INTO ZWAADDRESSBOOKCONTACT (ZLID, ZPHONENUMBER) VALUES (?, ?)", [(lid, "+821000000005"), (None, "+821000000006")])
    c.commit(); c.close()
    names = {"+821000000005": "가나다 과장"}
    evs = {e.message_id: e for e in collect(db, names=lambda h: names.get(h, ""))}
    assert evs["S1"].sender_id == "+821000000005" and evs["S1"].sender_name == "가나다 과장" and evs["S1"].chat_name == "가나다 과장"
    assert evs["S1"].link == "whatsapp://send?phone=821000000005" and evs["S1"].chat_id == lid      # 대화 번호는 그대로(저장 열쇠)
    assert evs["S2"].sender_id == unknown and evs["S2"].sender_name == "모르는 분" and evs["S2"].link == "whatsapp://"


def test_permission_denied_is_not_reported_as_missing_app(tmp_path, monkeypatch):    # 검토 I2 — Path.exists() 는 권한 오류도 False
    db = mk(tmp_path, [s(1, 0, DM)], [m(1, 1, from_jid=DM)])
    real = whatsapp.os.stat
    monkeypatch.setattr(whatsapp.os, "stat", lambda p, *a, **k: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted")) if str(p) == str(db) else real(p, *a, **k))
    with pytest.raises(RuntimeError, match="전체 디스크 접근"):
        whatsapp.WhatsAppCollector(db, running=lambda: True).collect(SINCE)


def test_unread_count_ignores_removed_and_hidden_sessions_with_the_same_jid(tmp_path):   # 검토 4 — 안 읽은 글이 읽은 것으로 보이면 안 된다
    evs = collect(mk(tmp_path, [s(1, 0, DM, unread=1), s(2, 0, DM, unread=0, removed=1), s(3, 0, DM, unread=0, hidden=1)],
                     [m(1, 1, from_jid=DM)]))
    assert [e.is_read for e in evs] == [False]


def test_lid_with_two_phones_is_not_guessed(tmp_path):      # 검토 5 — 짝이 둘이면 고르지 않고 lid 그대로(엉뚱한 사람에게 붙이지 않는다)
    lid = "123456789@lid"
    db = mk(tmp_path, [s(1, 0, lid, "가나다")], [m(1, 1, from_jid=lid)])
    c = sqlite3.connect(tmp_path / "ContactsV2.sqlite")
    c.execute("CREATE TABLE ZWAADDRESSBOOKCONTACT (Z_PK INTEGER PRIMARY KEY, ZLID VARCHAR, ZPHONENUMBER VARCHAR)")
    c.executemany("INSERT INTO ZWAADDRESSBOOKCONTACT (ZLID, ZPHONENUMBER) VALUES (?, ?)", [(lid, "+821000000005"), (lid, "+821000000006")])
    c.commit(); c.close()
    assert [e.sender_id for e in collect(db)] == [lid]


def test_incoming_dm_without_from_jid_is_from_the_partner(tmp_path):   # 검토 6 — 1:1 에서 받은 글의 보낸 사람은 상대다(빈칸이면 남과 묶인다)
    evs = collect(mk(tmp_path, [s(1, 0, DM, "가나다")], [m(1, 1, from_jid=None)]))
    assert evs[0].sender_id == "+821000000000" and evs[0].sender_name == "가나다"
