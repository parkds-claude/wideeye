"""설정 단계 · 쉬운 말 요약 · 받은 곳(urgent_board/setup_steps.py) — 순수 함수."""
from datetime import datetime, timedelta, timezone

from urgent_board import config, settings_doc as sd, setup_edit as se, setup_steps as ss
from urgent_board.models import Event, Source

NOW = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)


def ids(r):
    return {s["id"]: s for s in r["steps"]}


def ready_doc():
    d = sd.example()
    se.set_source(d, "imessage", True)
    se.set_me(d, names=["홍길동"])
    se.add_box(d, "○○ 박람회")
    se.add_rule(d, "○○ 박람회", "word", ["박람회"])
    return d


SOURCES_OK = {"gmail": [], "imessage": {"enabled": True, "state": "ok", "note": ""}, "telegram": {"state": "off"}, "kakao": {"state": "off"}}


def test_fresh_install_starts_at_connect():
    r = ss.steps(sd.example(), SOURCES_OK)
    s = ids(r)
    assert r["next"] == "connect" and r["left"] == 4                              # 연결 · 나 · 상자 · 기준 — 예시는 출처를 모두 끈다
    assert s["boxes"]["done"] is False and "box add" in s["boxes"]["todo"]          # 손대지 않은 예시 상자는 치지 않는다
    assert s["connect"]["line"] == "켠 출처가 없다"
    assert [x["id"] for x in r["steps"]] == list(ss.ORDER)


def test_connect_waits_for_the_first_real_collection_but_the_menubar_does_not():
    d = ready_doc()
    started = {**SOURCES_OK, "imessage": {"enabled": True, "state": "ok", "note": "시작"}}      # 엔진이 뜰 때 적는 표시(run_board.announce)
    s = ids(ss.steps(d, started, cmd="/x/bin/urgentboard"))
    assert s["connect"]["done"] is False and s["connect"]["line"] == "문자 수집 전" and "첫 수집" in s["connect"]["todo"]
    assert "/x/bin/urgentboard setup status" in s["connect"]["todo"]
    stopped = {**SOURCES_OK, "imessage": {"enabled": True, "state": "stopped", "note": "권한"}}
    assert ss.left_fast(d, started) == 0 and ss.left_fast(d, stopped) == 0     # 메뉴 막대는 설정만 본다 — 멈춤은 '수집 중단' 줄의 몫
    assert ss.steps(d, stopped)["left"] == 1


def test_ready_doc_is_done_and_optional_steps_never_count():
    r = ss.steps(ready_doc(), SOURCES_OK)
    assert r["left"] == 0 and r["next"] is None
    assert ids(r)["criteria"]["notes"] and "설명이 빈 상자" in ids(r)["criteria"]["notes"][0]


def test_engine_down_and_unknown_states():
    r = ss.steps(ready_doc(), None, engine_ok=False)
    s = ids(r)
    assert r["next"] == "engine" and s["engine"]["todo"].startswith("urgentboard start")
    assert s["connect"]["line"] == "문자 상태 모름" and s["connect"]["done"] is True             # 모를 때는 엔진 단계가 막는다


def test_connection_problems_and_missing_addresses():
    d = ready_doc()
    d["accounts"]["gmail"] = [{"alias": "work", "email": "me@work.kr", "label": "work", "token": "file:gmail-work.json"}]
    src = {**SOURCES_OK, "gmail": [{"alias": "work", "state": "reconnect", "note": "invalid_grant"}]}
    s = ids(ss.steps(d, src))
    assert s["connect"]["done"] is False and s["connect"]["todo"].startswith("메일 me@work.kr: 다시 연결 필요")
    assert s["me"]["done"] is False and "--email me@work.kr" in s["me"]["todo"]
    d["accounts"]["imessage"]["enabled"] = False
    d["accounts"]["gmail"] = []
    assert ids(ss.steps(d, SOURCES_OK))["connect"]["line"] == "켠 출처가 없다"
    assert ss.left_fast(d, SOURCES_OK) == 1


def test_box_without_rules_and_summary_without_claude():
    d = ready_doc()
    se.add_box(d, "빈 상자")
    se.set_summary(d, True)
    r = ss.steps(d, SOURCES_OK, claude_found=False)
    s = ids(r)
    assert s["criteria"]["line"] == "기준이 없는 상자: 빈 상자" and r["left"] == 1
    assert s["summary"]["done"] is False and s["summary"]["required"] is False
    assert ss.steps(d, SOURCES_OK)["steps"][5]["done"] is True                    # claude 를 묻지 않으면 보지 않는다(메뉴 막대)


def test_describe_lists_six_slots():
    d = ready_doc()
    se.add_rule(d, "○○ 박람회", "room", ["123"], source="telegram", watch=True, room_name="방")
    se.add_rule(d, "개인", "sender", ["a@b.kr"])
    se.add_rule(d, "○○ 박람회", "sender", ["1000000001"])
    out = ss.describe(d, SOURCES_OK, {"1000000001": "정○○", "a@b.kr": "에이"})
    assert out["boxes"][0]["name"] == "○○ 박람회" and out["boxes"][0]["rooms"] == [{"source": "telegram", "id": "123", "name": "방", "watch": True}]
    assert out["boxes"][0]["senders_named"] == [{"id": "1000000001", "name": "정○○"}]          # 번호만으로는 누군지 모른다
    assert out["etc"] == {"name": "개인 · 기타", "senders": ["a@b.kr"], "senders_named": [{"id": "a@b.kr", "name": "에이"}], "accounts": [], "watch_rooms": []}
    assert out["sources"][0]["state_text"] == "정상"


def ev(i, src=Source.GMAIL, sender="kim@a.org", name="김", text="박람회 일정 문의", group=False, chat="c1", chat_name="", mine=False,
       read=False, ago=1, raw=None):
    return Event(source=src, chat_id=chat, message_id=str(i), chat_name=chat_name, sender_id=sender, sender_name=name, text=text,
                 received_at=NOW - timedelta(hours=ago), is_from_me=mine, is_read=read, is_group=group, account="me@work.kr",
                 raw=raw or {"subject": text})


def test_places_group_by_sender_and_room_and_flag_noise():
    d = ready_doc()
    s = config.from_doc(d, "/tmp")
    events = [ev(1), ev(2, text="견적", read=True, ago=3), ev(3, sender="news@shop.kr", name="쇼핑", text="할인", raw={"subject": "할인", "labels": ["CATEGORY_PROMOTIONS"]}),
              ev(4, src=Source.KAKAO, sender="99", name="박", text="안녕", group=True, chat="555", chat_name="동창 단톡"),
              ev(5, src=Source.KAKAO, sender="98", name="최", text="박람회 부스", group=True, chat="555", chat_name="동창 단톡", ago=2),
              ev(6, mine=True), ev(7, ago=24 * 9)]
    rows = ss.places(events, s, NOW, detail=True, task_keys=frozenset({"gmail:c1:1"}))
    by = {(r["kind"], r["id"]): r for r in rows}
    kim = by[("sender", "kim@a.org")]
    assert kim["count"] == 2 and kim["unread"] == 1 and kim["box"] == "○○ 박람회" and kim["mixed"] == {}   # "견적"은 같은 사람에게서 물려받는다(판정과 같은 길)
    assert kim["sample"] == "박람회 일정 문의" and kim["tasks"] == 1 and rows[0] is kim            # 할 일이 된 곳이 위
    room = by[("room", "555")]
    assert room["name"] == "동창 단톡" and room["count"] == 2 and room["source_name"] == "카톡"
    assert by[("sender", "news@shop.kr")]["noise"] is True and not kim["noise"]
    assert "sample" not in ss.places(events, s, NOW)[0]                          # 제목은 --detail 일 때만
    sort = ss.sorting(rows, s)
    assert sort["noise"] == {"places": 1, "messages": 1} and sort["people"] == 2
    assert [b["box"] for b in sort["boxes"]] == ["○○ 박람회", "개인 · 기타"]
    assert ss.sorting(rows, s, box_tasks={"○○ 박람회": 3})["boxes"][0]["tasks"] == 3


def test_places_inherit_like_the_board_watch_rooms_are_not_noise_and_telegram_has_no_sample():
    d = ready_doc()
    se.add_rule(d, "○○ 박람회", "room", ["777"], source="telegram", watch=True)
    s = config.from_doc(d, "/tmp")
    events = [ev(1, text="박람회 일정 문의", ago=3), ev(2, text="네 감사합니다", ago=1),
              ev(3, src=Source.TELEGRAM, sender="5", name="정", text="많은 관심과 참여 부탁드립니다", group=True, chat="777", chat_name="행사방")]
    by = {r["id"]: r for r in ss.places(events, s, NOW, detail=True)}
    assert by["kim@a.org"]["box"] == "○○ 박람회" and by["kim@a.org"]["mixed"] == {}             # 짧은 답장은 같은 사람의 사업을 물려받는다
    assert by["777"]["noise"] is False and by["777"]["watched"] is True and by["777"]["sample"] == ""


def test_places_unsorted_are_people_no_rule_caught():
    d = ready_doc()
    s = config.from_doc(d, "/tmp")
    rows = ss.places([ev(1, text="안녕하세요", sender="lee@b.org", name="이")], s, NOW)
    assert rows[0]["unsorted"] is True and rows[0]["why"] == "어디에도 안 걸림" and rows[0]["box"] == "개인 · 기타" and rows[0]["tasks"] == 0
    assert ss.sorting(rows, s)["unsorted_top"][0]["id"] == "lee@b.org"


def test_unnamed_kakao_rooms_are_named_by_who_spoke():
    s = config.from_doc(ready_doc(), "/tmp")
    es = [ev(i, src=Source.KAKAO, sender=str(i), name=n, text="안녕", group=True, chat="9", chat_name="(unknown)", ago=10 - i)
          for i, n in enumerate(["가", "나", "다", "라"])]
    assert ss.places(es, s, NOW)[0]["name"] == "이름 없는 방: 라 · 다 외 2명"


def test_clean_keeps_names_and_emoji_but_not_line_breaks():
    assert ss.clean("홍길동 (주)해오름") == "홍길동 (주)해오름"
    assert ss.clean("👨\u200d👩\u200d👧 김철수") == "👨\u200d👩\u200d👧 김철수"                      # 이모지를 잇는 글자는 둔다
    assert ss.clean("김\r\n다음 줄\u202e") == "김 다음 줄" and ss.clean("가" * 50).endswith("…")


def test_whatsapp_counts_as_a_connected_source():           # 왓츠앱 설계 D2 — '연결했다' 단계에 친다
    rows = ss._src_rows({"accounts": {"whatsapp": {"enabled": True}}}, None)
    assert [(r["source"], r["name"]) for r in rows] == [("whatsapp", "왓츠앱")]


def test_local_sources_are_listed_only_when_turned_on_by_hand():     # web.has_sources 와 같은 규칙(코덱스 검수 2026-11-02)
    from urgent_board import setup_steps as s2
    assert s2._src_rows({"accounts": {}}, None) == []
    assert [r["source"] for r in s2._src_rows({"accounts": {"teams": {"enabled": True}}}, None)] == ["teams"]
    assert s2._src_rows({"accounts": {"calls": {"enabled": False}}}, None) == []
