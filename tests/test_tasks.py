"""할 일 장부(urgent_board/tasks.py) — 설계 2026-10-16 4. 저장소는 임시 파일, 항목은 손으로 만든다."""
from datetime import date, datetime, timedelta, timezone
from urgent_board import config, tasks
from urgent_board.models import Grade, Item, Source, Event
from urgent_board.store import Store
from pathlib import Path

HOME = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "home"
S = config.load(HOME)
NOW = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)          # 15:00 KST 금요일


def item(key, conv, grade=Grade.URGENT, at=NOW - timedelta(minutes=30), biz="bazaar2", src=Source.TELEGRAM, chat="c1"):
    return Item(grade=grade, business=biz, source=src, chat_name="방", sender_name="가나다", summary="원문 앞부분",
                received_at=at, waited_minutes=30, deadline=None, reasons=[], key=key, conv=conv, chat_id=chat, since=at)


def sync(st, shown, now=NOW, dropped=None, dismissed=frozenset(), lines=None, write=True, pending=frozenset()):
    return tasks.sync(st, shown, dropped or {}, set(dismissed), lines or {}, now, write=write, pending=set(pending))


def test_open_update_and_unread(tmp_path):
    st = Store(tmp_path / "t.db")
    got = sync(st, [item("k1", "c|1")], lines={"k1": ("연락처 회신", date(2026, 10, 23))})
    assert [(t.conv, t.unread, t.what, t.due) for t in got] == [("c|1", True, "연락처 회신", date(2026, 10, 23))]
    got = sync(st, [item("k2", "c|1", grade=Grade.WAITING, at=NOW - timedelta(minutes=5))])   # 같은 대화에 새 글 — 같은 할 일을 고친다
    assert len(got) == 1 and got[0].key == "k2" and got[0].unread is False and got[0].opened_at == NOW - timedelta(minutes=30)


def test_closes_on_reply_dismiss_and_not_needed(tmp_path):
    st = Store(tmp_path / "t.db")
    sync(st, [item("k1", "c|1"), item("k2", "c|2", chat="c2"), item("k3", "c|3", chat="c3")])
    st.upsert_events([Event(source=Source.TELEGRAM, chat_id="c1", message_id="m", chat_name="방", sender_id="me", sender_name="나", text="네",
                       received_at=NOW - timedelta(minutes=1), is_from_me=True, is_read=True, is_group=False)])
    got = sync(st, [], dropped={"c|3": "not_needed"}, dismissed={"k2"})          # 뺀 것은 대화(conv)로 — 그사이 새 글이 와서 key 가 바뀌어도
    assert got == []
    closed = {r["conv"]: r["closed_why"] for r in tasks.recently_closed(st, NOW)}
    assert closed == {"c|1": "reply", "c|2": "dismiss", "c|3": "not_needed"}


def test_read_request_inside_the_threshold_stays_as_read(tmp_path):
    """읽은 요청은 문턱(업무시간) 안에서는 판정에 안 나온다 — 판정이 '문턱 전'이라고 알려 준 대화(pending)는 닫지 않고 ○(읽음)으로 남긴다."""
    st = Store(tmp_path / "t.db")
    sync(st, [item("k1", "c|1", at=NOW - timedelta(minutes=3))])
    got = sync(st, [], pending={"c|1"})
    assert [(t.conv, t.unread) for t in got] == [("c|1", False)]                       # 읽었다 — ● 로 남지 않는다
    assert st.tasks()[0]["unread"] == 0                                                 # 장부에도 읽음으로
    assert sync(st, [], now=NOW + timedelta(minutes=5)) == []                           # 문턱 전도 아니고 판정에도 없다 = 읽었고 할 일이 아니다
    assert tasks.recently_closed(st, NOW + timedelta(minutes=5))[0]["closed_why"] == "read"


def test_read_plain_message_closes_at_once_even_after_hours(tmp_path):
    """검토 C1: 저녁 · 주말에 읽은 일반 글이 다음 업무일 아침까지 '● 안 읽음'으로 남았다(문턱을 업무시간으로 셌다)."""
    st = Store(tmp_path / "t.db")
    fri_evening = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)                  # 18:30 KST 금요일 — 업무시간 밖
    sync(st, [item("k1", "c|1", at=fri_evening)], now=fri_evening + timedelta(minutes=1))
    later = fri_evening + timedelta(minutes=10)
    assert sync(st, [], now=later) == [] and tasks.recently_closed(st, later)[0]["closed_why"] == "read"


def test_old_task_stays_and_closes_on_a_late_reply(tmp_path):
    st = Store(tmp_path / "t.db")
    sync(st, [item("k1", "c|1", grade=Grade.WAITING, at=NOW - timedelta(days=1))])
    later = NOW + timedelta(days=10)                                                    # 판정(7일)에 안 나온다
    assert [t.conv for t in sync(st, [], now=later)] == ["c|1"]
    st.upsert_events([Event(source=Source.TELEGRAM, chat_id="c1", message_id="m2", chat_name="방", sender_id="me", sender_name="나", text="늦었지만 보냅니다",
                       received_at=later - timedelta(hours=1), is_from_me=True, is_read=True, is_group=False)])
    assert sync(st, [], now=later) == []


def test_dismissed_stays_done_after_the_hide_expires(tmp_path):
    st = Store(tmp_path / "t.db")
    sync(st, [item("k1", "c|1")])
    sync(st, [], dismissed={"k1"})
    assert sync(st, [], now=NOW + timedelta(days=20)) == []                            # dismissed 의 14일이 지나도
    got = sync(st, [item("k9", "c|1", at=NOW + timedelta(days=20))], now=NOW + timedelta(days=20))
    assert [t.key for t in got] == ["k9"]                                              # 새 글이면 새로 연다


def test_write_false_leaves_the_book_alone(tmp_path):
    st = Store(tmp_path / "t.db")
    got = sync(st, [item("k1", "c|1")], write=False)
    assert [t.conv for t in got] == ["c|1"] and st.tasks() == []


def test_starts_empty_and_takes_only_what_the_scan_shows(tmp_path):
    st = Store(tmp_path / "t.db")
    assert sync(st, []) == [] and st.tasks() == []


def _mine(chat, at, subject="Re: 견적 문의", to=("kim@client.com",), src=Source.GMAIL, mid="r1"):
    return Event(source=src, chat_id=chat, message_id=mid, chat_name=subject, sender_id="me@x.com", sender_name="나", text=subject,
                 received_at=at, is_from_me=True, is_read=True, is_group=False, raw={"subject": subject, "to": list(to)})


def test_old_mail_task_closes_on_a_reply_from_another_mailbox(tmp_path):
    """검토 I5: 업무 주소(minji@)에 온 메일의 사본이 workbase@ 에도 있어, 내 답장이 다른 계정의 스레드(다른 chat_id)에 있을 수 있다.
    메일은 보낸 사람 주소 + 정리한 제목으로 답장을 찾는다(판정의 _answers · addressed 와 같은 셈)."""
    st = Store(tmp_path / "t.db")
    sync(st, [item("k1", "gmail|kim@client.com|견적 문의", grade=Grade.WAITING, at=NOW - timedelta(days=1), src=Source.GMAIL, chat="t-minji")])
    later = NOW + timedelta(days=10)
    st.upsert_events([_mine("t-other", later - timedelta(hours=2), subject="Re: 다른 건", mid="x")])        # 다른 제목 — 이 할 일의 답이 아니다
    st.upsert_events([_mine("t-other", later - timedelta(hours=1), to=("lee@client.com",), mid="y")])     # 다른 사람에게 — 답이 아니다
    assert [t.conv for t in sync(st, [], now=later)] == ["gmail|kim@client.com|견적 문의"]
    st.upsert_events([_mine("t-workbase", later - timedelta(minutes=30), mid="z")])
    assert sync(st, [], now=later) == [] and tasks.recently_closed(st, later)[0]["closed_why"] == "reply"


def test_old_room_bundle_closes_when_the_room_is_read(tmp_path):
    """작은 것 ⑤: 7일 밖의 방 묶음은 그 방에 안 읽은 글이 남았는지(저장소)로 닫는다 — 텔레그램 읽음은 '여기까지 읽음'으로 맞춰진다."""
    st = Store(tmp_path / "t.db")
    at = NOW - timedelta(days=1)
    room = item("k1", "room|telegram|g1", at=at, chat="g1")
    sync(st, [room])
    msg = Event(source=Source.TELEGRAM, chat_id="g1", message_id="1", chat_name="방", sender_id="u1", sender_name="가", text="공지",
                received_at=at, is_from_me=False, is_read=False, is_group=True)
    st.upsert_events([msg])
    later = NOW + timedelta(days=10)
    assert [t.conv for t in sync(st, [], now=later)] == ["room|telegram|g1"]
    from dataclasses import replace
    st.upsert_events([replace(msg, is_read=True)])
    assert sync(st, [], now=later) == [] and tasks.recently_closed(st, later)[0]["closed_why"] == "read"


def test_the_first_due_is_kept_and_rule_due_comes_from_the_item(tmp_path):
    st = Store(tmp_path / "t.db")
    first = item("k1", "c|1")
    first.deadline = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
    assert sync(st, [first])[0].due == date(2026, 10, 19)                              # 규칙이 잡은 기한(받은 때 기준 — pipeline)
    again = item("k2", "c|1", at=NOW - timedelta(minutes=5))                            # 같은 대화의 새 글 — 기한 말이 없다
    assert sync(st, [again])[0].due == date(2026, 10, 19)                              # 처음 잡힌 기한을 잃지 않는다


def test_unchanged_tasks_are_not_rewritten(tmp_path, monkeypatch):
    """작은 것 ②: 화면이 10초마다 당긴다 — 바뀐 줄만 쓴다."""
    st = Store(tmp_path / "t.db")
    sync(st, [item("k1", "c|1")])
    writes = []
    real = st.put_task
    monkeypatch.setattr(st, "put_task", lambda row: (writes.append(row["conv"]), real(row)))
    sync(st, [item("k1", "c|1")])
    assert writes == []
    sync(st, [item("k2", "c|1", at=NOW - timedelta(minutes=1))])
    assert writes == ["c|1"]


def test_times_are_kept_in_utc(tmp_path):
    """작은 것 ⑪: 장부의 시각은 저장소의 다른 시각처럼 UTC 글자 — 글자로 줄 세우는 ORDER BY last_at 이 어긋나지 않게."""
    from urgent_board.workhours import KST
    st = Store(tmp_path / "t.db")
    sync(st, [item("k1", "c|1", at=(NOW - timedelta(minutes=30)).astimezone(KST))])
    r = st.tasks()[0]
    assert r["opened_at"].endswith("+00:00") and r["last_at"].endswith("+00:00")


def test_old_mail_task_also_closes_on_a_reply_in_the_same_thread(tmp_path):
    """검토(고침 뒤): 별칭 주소로 답했거나 제목을 바꿔 답해도 같은 스레드의 내 글이면 답이다(예전 길도 함께 본다)."""
    st = Store(tmp_path / "t.db")
    sync(st, [item("k1", "gmail|kim@client.com|견적 문의", grade=Grade.WAITING, at=NOW - timedelta(days=1), src=Source.GMAIL, chat="t-minji")])
    later = NOW + timedelta(days=10)
    st.upsert_events([_mine("t-minji", later - timedelta(hours=1), subject="수정본 보냅니다", to=("kim.alias@client.com",))])
    assert sync(st, [], now=later) == [] and tasks.recently_closed(st, later)[0]["closed_why"] == "reply"


def test_a_read_closure_reopens_when_the_scan_now_calls_it_a_pending_request(tmp_path):
    """판정 규칙이 넓어지면(2026-10-17 외국어 문의) '읽음 — 할 일 아님'으로 닫았던 대화가 '읽은 요청(문턱 전)'이 된다 — 장부도 다시 연다(○).
    고치기 전에 닫힌 사무국 문의가 업무시간 문턱(월요일 아침)까지 안 보였다. 답함 · 처리됨 · 할 일 아님으로 닫은 것은 다시 열지 않는다."""
    st = Store(tmp_path / "t.db")
    sync(st, [item("k1", "c|1"), item("k2", "c|2", chat="c2"), item("k3", "c|3", chat="c3")])
    sync(st, [], dropped={"c|3": "not_needed"}, dismissed={"k2"})                  # c|1 은 읽음으로 · c|2 처리됨 · c|3 할 일 아님
    assert {r["conv"]: r["closed_why"] for r in tasks.recently_closed(st, NOW)} == {"c|1": "read", "c|2": "dismiss", "c|3": "not_needed"}
    got = sync(st, [], pending={"c|1", "c|2", "c|3"}, dismissed={"k2"})
    assert [(t.conv, t.unread) for t in got] == [("c|1", False)]                     # 읽음으로 닫은 것만 ○ 로 다시
    assert {r["conv"] for r in st.tasks()} == {"c|1"}
    assert sync(st, [], pending={"c|1"}, write=False) and {r["conv"] for r in st.tasks()} == {"c|1"}     # 미리 보기는 그대로
