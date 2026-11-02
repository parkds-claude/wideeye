import copy
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path

import pytest

from urgent_board import board, config, paths, settings_doc as sd
from urgent_board.models import Event, Grade, Item, Source

ROOT = Path(__file__).resolve().parents[1]


def boxes(*specs):
    return [dict({"key": k, "name": k.upper(), "color": c, "active": True}, **extra) for k, c, extra in specs]


def write(home, doc):
    return sd.save(home, doc, None)


def test_from_doc_maps_rooms_ids_and_names_separately(tmp_path):
    d = sd.example()
    d["boxes"] = boxes(("a", "green", {"kakao_rooms": [{"id": "100000000000001123", "name": "번호 방(이름표)"}, {"name": "이름 방"}],
                                       "telegram_rooms": [{"id": "-100000000001", "name": "단톡", "watch": True}],
                                       "senders": ["han@venue.example"], "keywords": ["포럼"], "memo": "메모"}))
    d["watch_rooms"] = [{"source": "telegram", "chat_id": "-100", "name": "상자 밖 방"}]
    s = config.from_doc(d, tmp_path)
    b = s.businesses[0]
    assert b.kakao_chat_ids == [100000000000001123] and b.kakao_chat_names == ["이름 방"]       # 번호 항목의 이름은 이름표(맞추지 않는다)
    assert b.telegram_chat_ids == [-100000000001] and b.memo == "메모"
    assert s.watch_rooms == frozenset({("telegram", "-100000000001"), ("telegram", "-100")})
    assert s.home == tmp_path and s.root == paths.CODE_ROOT and s.db_path == paths.db_path(tmp_path)


def test_defaults_for_new_users(tmp_path):
    s = config.from_doc(sd.example(), tmp_path)
    assert s.summary["enabled"] is False and s.summary["sources"] == ["gmail", "kakao", "imessage"]   # 텔레그램 요약은 기본에서 빠진다(약관)
    assert s.display["old_task_days"] == 14 and s.etc_name == "개인 · 기타" and s.port == 5091
    assert s.rules.calendar == config.WorkCalendar()
    assert config.SUMMARY_DEFAULTS["enabled"] is False and config.SUMMARY_DEFAULTS["task_max_chars"] == 14


def test_rules_override_deep_merge(tmp_path):
    d = sd.example()
    d["rules"] = {"thresholds": {"unanswered_dm_minutes": 30}, "junk": {"gmail_labels": {"SPAM": "ad"}}}
    s = config.from_doc(d, tmp_path)
    assert s.rules.thresholds["unanswered_dm_minutes"] == 30 and s.rules.thresholds["unanswered_group_minutes"] == 240     # 적은 칸만
    assert s.rules.junk_labels == {"SPAM": "ad"}                                          # gmail_labels 는 통째로
    assert s.rules.junk_ad_text.search("(광고) 특가")                                      # 나머지 junk 는 기본값 그대로
    base = sd.default_rules()
    assert config.deep_merge(base, {})["thresholds"] == base["thresholds"]


def test_workhours_from_doc(tmp_path):
    d = sd.example()
    d["workhours"] = {"start": "10:00", "end": "19:00", "lunch": ["12:30", "13:30"], "holidays": "", "extra_holidays": ["2026-12-31"], "extra_workdays": ["2026-10-31"]}
    cal = config.from_doc(d, tmp_path).rules.calendar
    assert (cal.start, cal.end, cal.lunch_start, cal.lunch_end, cal.country) == (dtime(10), dtime(19), dtime(12, 30), dtime(13, 30), "")
    assert cal.is_workday(date(2026, 10, 31)) and not cal.is_workday(date(2026, 12, 31))


def test_colors_only_five_and_only_active(tmp_path):
    d = sd.example()
    d["boxes"] = [{"key": f"b{i}", "name": f"B{i}"} for i in range(7)] + [{"key": "off", "name": "OFF", "active": False, "color": "green"}]
    s = config.from_doc(d, tmp_path)
    got = [b.color for b in s.businesses]
    assert got[:5] == list(sd.BUSINESS_COLORS) and got[5:7] == ["", ""]            # 여섯째부터는 색 없음(연두 · 주황을 주지 않는다)
    assert "lime" not in got and "orange" not in got


def test_problems_include_build_errors(tmp_path):
    d = sd.example()
    assert config.problems(d) == []
    d["workhours"] = {"start": "18:00", "end": "09:00"}
    assert any(p.path == "workhours" for p in config.problems(d))                    # 끝이 시작보다 앞 — 만들어 보면 드러난다


def test_load_is_strict(tmp_path):
    with pytest.raises(FileNotFoundError):
        config.load(tmp_path)
    write(tmp_path, sd.example())
    assert config.load(tmp_path).businesses[0].key == "example"
    paths.settings_file(tmp_path).write_text("version: 2\nboxes: [{key: Bad}]\n", encoding="utf-8")
    with pytest.raises(sd.SettingsError):
        config.load(tmp_path)


def test_personal_accounts_come_from_settings(tmp_path):
    from urgent_board.triage import business
    d = sd.example()
    d["me"]["personal_accounts"] = ["Me.Private@gmail.com"]
    s = config.from_doc(d, tmp_path)
    ev = Event(source=Source.GMAIL, chat_id="t", message_id="m", chat_name="안부", sender_id="x@y.z", sender_name="x", text="안부",
               received_at=datetime.now(timezone.utc), is_from_me=False, is_read=False, is_group=False, account="me.private@gmail.com")
    assert business.tag(ev, s) == "personal"
    assert business.tag(ev, config.from_doc(sd.example(), tmp_path)) == "other"


def test_etc_name_and_summary_sources_on_cards():
    now = datetime.now(timezone.utc)
    def it(k, src):
        return Item(grade=Grade.URGENT, business="other", source=src, chat_name="", sender_name="s", summary="x", received_at=now,
                    waited_minutes=0, deadline=None, reasons=[], key=k, text="글" * 40)
    cards = board.build_task_cards([], [], "기타 일", now, config.calendar_from({}))
    assert cards[-1].name == "기타 일"
    assert board.summary_allowed(it("1", Source.TELEGRAM), None) and not board.summary_allowed(it("1", Source.TELEGRAM), ["gmail"])


def test_connection_key_changes_only_for_connections(tmp_path):
    d = sd.example()
    s1 = config.from_doc(d, tmp_path)
    d2 = copy.deepcopy(d); d2["boxes"][0]["keywords"] = ["다른 낱말"]; d2["summary"]["enabled"] = True; d2["etc_name"] = "기타"
    assert config.connection_key(config.from_doc(d2, tmp_path)) == config.connection_key(s1)
    for change in (lambda x: x["accounts"]["kakao"].update(enabled=True), lambda x: x["server"].update(port=5191),
                   lambda x: x["me"]["addresses"].append("me@example.com"),
                   lambda x: x["accounts"]["gmail"].append({"alias": "w", "email": "w@example.com", "token": "file:gmail-w.json"})):
        d3 = copy.deepcopy(d); change(d3)
        assert config.connection_key(config.from_doc(d3, tmp_path)) != config.connection_key(s1)


def test_live_swaps_restarts_and_survives_broken_file(tmp_path):
    write(tmp_path, sd.example())
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    assert live.check() == "same"
    d, v = sd.read(tmp_path); d["boxes"][0]["keywords"] = ["새 낱말"]; sd.save(tmp_path, d, v)
    assert live.check() == "swapped" and live.get().businesses[0].keywords == ["새 낱말"]
    paths.settings_file(tmp_path).write_text("boxes: [\n", encoding="utf-8")          # 손으로 깨뜨림
    assert live.check() == "error" and live.get().businesses[0].keywords == ["새 낱말"] and "설정 파일 오류" in live.get().settings_error
    assert live.check() == "same"                                                      # 같은 깨진 판을 5초마다 다시 알리지 않는다
    d["accounts"]["kakao"]["enabled"] = True; sd.save(tmp_path, d, None)
    assert live.check() == "restart"


def test_load_for_engine_falls_back(tmp_path):
    s = config.load_for_engine(tmp_path)                                               # 아무것도 없다 → 빈 설정(수집기 없음)
    assert s.businesses == [] and s.accounts.get("imessage", {}).get("enabled") is not True and s.version == ""
    v = write(tmp_path, sd.example()); sd.boot_ok(tmp_path, v)                        # 잘 돈 판
    paths.settings_file(tmp_path).write_text("- 깨짐\n", encoding="utf-8")
    s = config.load_for_engine(tmp_path)
    assert s.businesses[0].key == "example" and "이전 설정" in s.settings_error and s.version == sd.version(paths.last_good_file(tmp_path))
    v2 = write(tmp_path, dict(sd.example(), etc_name="새 판"))
    for _ in range(config.BOOT_LIMIT):
        sd.boot_begin(tmp_path, v2)                                                    # 이 판으로 세 번 시작했지만 60초를 못 버텼다
    s = config.load_for_engine(tmp_path)
    assert s.etc_name == "개인 · 기타" and "세 번" in s.settings_error


def test_legacy_config_is_noticed_not_migrated(tmp_path):
    s = config.load_for_engine(tmp_path)
    assert s.legacy_config is (paths.CODE_ROOT / "config" / "businesses.yaml").exists()
    assert not paths.settings_file(tmp_path).exists()                                  # 저절로 옮기지 않는다(설계 4-4)


def test_after_falling_back_the_rejected_file_is_not_read_again(tmp_path):
    """검토 C1: 세 번 못 버틴 판으로 last-good 에 물러선 뒤, 감시가 5초 만에 그 판을 다시 읽어 바꿔 끼우거나 다시 시작하면 안 된다."""
    v = write(tmp_path, sd.example()); sd.boot_ok(tmp_path, v)
    bad = write(tmp_path, dict(sd.example(), etc_name="못 버티는 판"))
    for _ in range(config.BOOT_LIMIT):
        sd.boot_begin(tmp_path, bad)
    s = config.load_for_engine(tmp_path)
    assert s.rejected_version == bad and s.etc_name == "개인 · 기타"
    live = config.Live(tmp_path, s)
    assert live.check() == "same" and live.get().etc_name == "개인 · 기타"
    assert sd.boot_attempts(tmp_path, bad) == config.BOOT_LIMIT                       # 시작 횟수가 지워지지 않았다
    d, v2 = sd.read(tmp_path); d["etc_name"] = "고친 판"; sd.save(tmp_path, d, v2)  # 사람이 고치면 그때 읽는다
    assert live.check() == "swapped" and live.get().etc_name == "고친 판"


def test_without_last_good_a_rejected_file_does_not_loop(tmp_path):
    bad = write(tmp_path, sd.example())
    for _ in range(config.BOOT_LIMIT):
        sd.boot_begin(tmp_path, bad)
    s = config.load_for_engine(tmp_path)
    assert s.businesses == [] and s.rejected_version == bad
    assert config.Live(tmp_path, s).check() == "same"                                 # 빈 설정 ↔ 나쁜 판을 오가며 exec 를 되풀이하지 않는다


def test_non_utf8_and_unreadable_files_do_not_kill_the_engine(tmp_path):
    """검토 I2: UTF-8 이 아닌 파일 · 못 읽는 파일 · 깊게 겹친 YAML 이 예외로 빠져나가 엔진이 죽기를 되풀이하면 안 된다."""
    import os
    v = write(tmp_path, sd.example()); sd.boot_ok(tmp_path, v)
    paths.settings_file(tmp_path).write_bytes(b"version: 2\netc_name: \xff\xfe\n")
    s = config.load_for_engine(tmp_path)
    assert s.businesses[0].key == "example" and "이전 설정" in s.settings_error and s.rejected_version
    live = config.Live(tmp_path, s)
    assert live.check() == "same"
    paths.settings_file(tmp_path).write_text("a: " + "[" * 5000 + "]" * 5000 + "\n", encoding="utf-8")
    assert live.check() in ("error", "same") and live.get().businesses[0].key == "example"
    os.chmod(paths.settings_file(tmp_path), 0)
    try:
        assert sd.version(paths.settings_file(tmp_path)) == "" and live.check() == "same"
    finally:
        os.chmod(paths.settings_file(tmp_path), 0o600)


def test_waiting_thresholds_written_in_hours_still_count(tmp_path):
    """대기 문턱은 2026-10-16 부터 분으로 적는다(사용자 "읽어도 10분 후에는 표시가 되게" — 시간으로는 10분을 못 적는다).
    예전처럼 시간으로 적은 덮어쓰기(unanswered_dm_hours · unanswered_group_hours)도 분으로 바꿔 쓴다."""
    d = sd.example()
    d["rules"] = {"thresholds": {"unanswered_dm_hours": 3, "unanswered_group_hours": 0.5}}
    t = config.from_doc(d, tmp_path).rules.thresholds
    assert t["unanswered_dm_minutes"] == 180 and t["unanswered_group_minutes"] == 30 and t["unanswered_sms_minutes"] == 20
    d["rules"] = {"thresholds": {"unanswered_dm_hours": 3, "unanswered_dm_minutes": 10}}
    assert config.from_doc(d, tmp_path).rules.thresholds["unanswered_dm_minutes"] == 10              # 분으로 적은 것이 이긴다


def test_personal_senders_come_from_settings(tmp_path):
    d = sd.example()
    d["me"]["personal_senders"] = ["123456789", "Kim@Client.com"]
    assert config.from_doc(d, tmp_path).personal_senders == frozenset({"123456789", "kim@client.com"})
    assert config.from_doc(sd.example(), tmp_path).personal_senders == frozenset()


def test_whatsapp_is_off_unless_written_and_changes_the_connection_key(tmp_path):     # 왓츠앱 설계 D2 · D15
    off = config.from_doc({"version": sd.VERSION}, tmp_path)
    assert off.accounts["whatsapp"] == {"enabled": False}
    on = config.from_doc({"version": sd.VERSION, "accounts": {"whatsapp": {"enabled": True}}}, tmp_path)
    assert config.connection_key(on) != config.connection_key(off)
    assert not [p for p in sd.validate({"version": sd.VERSION, "accounts": {"whatsapp": {"enabled": True}},
                                         "summary": {"sources": ["gmail", "whatsapp"]}}) if p.level == "error"]
    assert config.EMPTY["accounts"]["whatsapp"] == {"enabled": False}
