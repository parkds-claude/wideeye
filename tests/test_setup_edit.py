"""설정 문서 고치기(urgent_board/setup_edit.py) — 설정 명령이 부르는 순수 함수. 고친 문서는 늘 꼴 검사를 지나야 한다."""
import pytest

from urgent_board import config, settings_doc as sd, setup_edit as se


def fresh():
    return sd.example()


def ok(doc):
    bad = [p for p in config.problems(doc) if p.level == "error"]
    assert bad == [], bad
    return doc


def test_first_box_replaces_the_untouched_example_and_gets_a_free_color():
    d = fresh()
    key, notes = se.add_box(d, "○○ 박람회")
    assert [b["name"] for b in d["boxes"]] == ["○○ 박람회"] and notes == ["손대지 않은 예시 상자를 지웠다", "상자 '○○ 박람회' 을 더했다(열쇠 box-1 · 색 green)"]
    assert key == "box-1" and d["boxes"][0]["color"] == "green" and d["boxes"][0]["active"] is True
    k2, notes2 = se.add_box(d, "Seoul Forum")
    assert k2 == "seoul-forum" and d["boxes"][1]["color"] == "cyan" and notes2 == ["상자 'Seoul Forum' 을 더했다(열쇠 seoul-forum · 색 cyan)"]
    again = se.add_box(d, "seoul  forum", color="pink")                          # 다시 불러도 같은 결과 — 색은 바꾸지 않는다고 알린다
    assert again[0] == "seoul-forum" and "이미 있다" in again[1][0] and "box set" in again[1][1] and len(d["boxes"]) == 2
    ok(d)


def test_a_touched_example_is_kept():
    d = fresh()
    d["boxes"][0]["keywords"].append("박람회")
    se.add_box(d, "새 일")
    assert [b["name"] for b in d["boxes"]] == ["예시 사업", "새 일"]


def test_five_active_boxes_at_most_and_names_are_unique():
    d = fresh()
    for n in "가나다라마":
        se.add_box(d, f"{n} 사업")
    with pytest.raises(se.EditError, match="다섯|5"):
        se.add_box(d, "바 사업")
    with pytest.raises(se.EditError, match="이미 있다"):
        se.set_box(d, "가 사업", name="나 사업")
    se.set_box(d, "가 사업", active_=False)
    se.add_box(d, "바 사업")
    with pytest.raises(se.EditError, match="다섯|5"):
        se.set_box(d, "가 사업", active_=True)
    with pytest.raises(se.EditError, match="개인"):
        se.add_box(d, "개인 · 기타")
    ok(d)


def test_boxes_are_found_by_key_or_name_and_personal_words():
    d = fresh()
    key, _ = se.add_box(d, "○○ 박람회")
    assert se.find_box(d, key) == key and se.find_box(d, " ○○  박람회 ") == key
    assert se.find_box(d, "개인") == se.PERSONAL and se.find_box(d, "개인 · 기타") == se.PERSONAL and se.find_box(d, "personal") == se.PERSONAL
    with pytest.raises(se.EditError, match="있는 상자: ○○ 박람회 · 개인 · 기타"):
        se.find_box(d, "없는 상자")


def test_colors_do_not_clash():
    d = fresh()
    se.add_box(d, "가", color="pink")
    with pytest.raises(se.EditError, match="이미 그 색"):
        se.add_box(d, "나", color="pink")
    with pytest.raises(se.EditError, match="가운데 하나"):
        se.set_box(d, "가", color="orange")
    ok(d)


def test_rules_words_senders_accounts_rooms():
    d = fresh()
    se.add_box(d, "가")
    se.add_box(d, "나")
    notes = se.add_rule(d, "가", "word", ["박람회", "박람회", "A"])
    assert d["boxes"][0]["keywords"] == ["박람회", "A"] and any("짧아" in n for n in notes)
    assert any("'가' 상자에도" in n for n in se.add_rule(d, "나", "word", ["박람회"]))
    se.add_rule(d, "가", "sender", ["kim@a.org", "010-1234-5678"])
    se.add_rule(d, "나", "sender", ["KIM@a.org"])                                # 한 사람은 한 상자에만 — 가에서 빠진다
    assert d["boxes"][0]["senders"] == ["+821012345678"] and d["boxes"][1]["senders"] == ["KIM@a.org"]
    se.add_rule(d, "개인", "sender", ["+821012345678"])                          # 개인 · 기타로 둘 사람
    assert d["boxes"][0]["senders"] == [] and d["me"]["personal_senders"] == ["+821012345678"]
    se.add_rule(d, "가", "account", ["Work@Expo.kr"])
    se.add_rule(d, "나", "account", ["work@expo.kr"])
    assert d["boxes"][0]["mail_accounts"] == [] and d["boxes"][1]["mail_accounts"] == ["work@expo.kr"]
    se.add_rule(d, "가", "room", ["100000000000001"], source="kakao", watch=True, room_name="박람회 단톡")
    assert d["boxes"][0]["kakao_rooms"] == [{"id": "100000000000001", "name": "박람회 단톡", "watch": True}]
    se.add_rule(d, "나", "room", ["100000000000001"], source="kakao")           # 옮기면 지켜보기도 따라간다
    assert d["boxes"][0]["kakao_rooms"] == [] and d["boxes"][1]["kakao_rooms"][0]["watch"] is True
    se.add_rule(d, "나", "room", ["100000000000001"], source="kakao", watch=False)    # 지켜보기만 끈다(방은 그대로)
    assert d["boxes"][1]["kakao_rooms"] == [{"id": "100000000000001", "name": "박람회 단톡"}]
    assert se.add_rule(d, "나", "sender", ["kim@a.org"]) == ["나: 이미 있는 보낸 사람"]
    with pytest.raises(se.EditError, match="source"):
        se.add_rule(d, "가", "room", ["1"])
    with pytest.raises(se.EditError, match="숫자"):
        se.add_rule(d, "가", "room", ["단톡"], source="telegram")
    with pytest.raises(se.EditError, match="메일 주소"):
        se.add_rule(d, "가", "account", ["not-mail"])
    with pytest.raises(se.EditError, match="낱말"):
        se.add_rule(d, "개인", "word", ["x"])
    ok(d)


def test_remove_rules():
    d = fresh()
    se.add_box(d, "가")
    se.add_rule(d, "가", "word", ["박람회", "전시"])
    se.add_rule(d, "가", "sender", ["010-1234-5678"])
    se.add_rule(d, "가", "room", ["-100123"], source="telegram")
    assert se.remove_rule(d, "가", "word", ["전시", "없음"]) == ["-1: 전시", "없던 것: 없음"]
    se.remove_rule(d, "가", "sender", ["010-1234-5678"])                          # 적을 때와 같은 꼴로 바꿔 찾는다
    assert d["boxes"][0]["senders"] == []
    assert se.remove_rule(d, "가", "room", ["-100123"], source="telegram") == ["방 -1"]
    ok(d)


def test_remove_box_keeps_watching_its_rooms_and_order():
    d = fresh()
    se.add_box(d, "가")
    se.add_box(d, "나")
    se.add_box(d, "다")
    se.add_rule(d, "나", "room", ["77"], source="telegram", watch=True)
    assert se.remove_box(d, "나") == "나"
    assert d["watch_rooms"] == [{"source": "telegram", "chat_id": "77"}]
    assert se.order_boxes(d, ["다"]) == ["다", "가"]
    with pytest.raises(se.EditError):
        se.remove_box(d, "개인")
    ok(d)


def test_me_and_missing_addresses():
    d = fresh()
    d["accounts"]["gmail"] = [{"alias": "work", "email": "Me@Work.kr", "label": "work", "token": "file:gmail-work.json"}]
    assert se.missing_addresses(d) == ["me@work.kr"]
    se.set_me(d, names=["홍길동", "팀장님", "홍길동"], emails=["me@work.kr"])
    assert d["me"]["names"] == ["홍길동", "팀장님"] and se.missing_addresses(d) == []
    with pytest.raises(se.EditError):
        se.set_me(d, emails=["nope"])
    se.set_me(d, names=["새 이름"], clear=True)
    assert d["me"]["names"] == ["새 이름"] and d["me"]["addresses"] == []
    ok(d)


def test_sources_summary_hours_tab5_unconfirmed():
    d = fresh()
    se.set_source(d, "kakao", True)
    assert d["accounts"]["kakao"] == {"enabled": True}
    with pytest.raises(se.EditError):
        se.set_source(d, "gmail", True)
    se.set_summary(d, True)
    se.set_hours(d, "08:30", "17:30")
    with pytest.raises(se.EditError):
        se.set_hours(d, "18:00", "09:00")
    se.set_tab5(d, " AA:BB ")
    assert d["display"]["tab5_usb_serial"] == "AA:BB"
    se.add_unconfirmed(d, "○○ 입찰", ["정책설명회 대행"])
    se.add_unconfirmed(d, "○○ 입찰", ["정책설명회 대행", "가나다 입찰"])
    assert d["unconfirmed"] == [{"name": "○○ 입찰", "keywords": ["정책설명회 대행", "가나다 입찰"]}]
    se.remove_unconfirmed(d, "○○ 입찰")
    with pytest.raises(se.EditError):
        se.remove_unconfirmed(d, "○○ 입찰")
    ok(d)


def test_etc_box_can_be_renamed_only():
    d = fresh()
    se.add_box(d, "가")
    assert se.set_box(d, "개인", name="그 밖의 일") == ["여섯째 상자 이름: 그 밖의 일"]
    assert se.find_box(d, "그 밖의 일") == se.PERSONAL
    with pytest.raises(se.EditError):
        se.set_box(d, "개인", about="x")
    ok(d)


def test_set_source_whatsapp():                             # 왓츠앱 설계 D2
    d = fresh()
    se.set_source(d, "whatsapp", True)
    assert d["accounts"]["whatsapp"] == {"enabled": True}
