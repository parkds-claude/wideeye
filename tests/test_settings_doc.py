import json
import os
from pathlib import Path

import pytest
import yaml

from urgent_board import paths, settings_doc as sd

ROOT = Path(__file__).resolve().parents[1]


def doc(**over):
    d = sd.example()
    d.update(over)
    return d


def errors(d, saved=None):
    return [(p.path, p.message) for p in sd.validate(d, saved) if p.level == "error"]


def test_defaults_rules_have_only_rule_keys_and_match_old_file():
    r = sd.default_rules()
    assert set(r) == set(sd.RULE_KEYS)
    old = yaml.safe_load((ROOT / "tests" / "fixtures" / "legacy" / "config" / "rules.yaml").read_text(encoding="utf-8"))   # 옛 rules.yaml(옮기기 전 운영 판)
    from urgent_board import config
    old = config.waiting_minutes(old)                              # 대기 문턱은 2026-10-16 부터 분으로(2시간 = 120분)
    added = {"foreign"}                                            # 옮긴 뒤에 새로 생긴 칸(2026-10-17 외국어 문의) — 옛 파일에는 없다
    assert set(sd.RULE_KEYS) - set(old) == added
    new_inside = {"junk": {"auto_domain_re", "sms_person_re"}}      # 옮긴 뒤 안쪽에 새로 생긴 칸(2026-10-17 홍보 메일 · 자동 문자 거르기)
    drop = lambda k, v: {x: y for x, y in v.items() if x not in new_inside.get(k, ())} if isinstance(v, dict) else v     # noqa: E731
    assert all(drop(k, r[k]) == old[k] for k in sd.RULE_KEYS if k not in added)     # 그 밖의 칸은 지금 쓰는 값과 한 글자도 다르지 않다(이 맥의 판정 그대로)
    assert not {"summary", "display", "handled", "watch_rooms", "extra_workdays"} & set(r)


def test_example_is_valid():
    assert errors(sd.example()) == []


@pytest.mark.parametrize("path,value,where", [
    ("version", 1, "version"),
    ("boxes", [{"key": "Bad Key", "name": "x"}], "boxes[0].key"),
    ("boxes", [{"key": "etc", "name": "x"}], "boxes[0].key"),                       # 예약어
    ("boxes", [{"key": "a", "name": ""}], "boxes[0].name"),
    ("boxes", [{"key": "a", "name": "A", "color": "orange"}], "boxes[0].color"),   # 주황은 긴급 색
    ("boxes", [{"key": "a", "name": "A", "color": "green"}, {"key": "b", "name": "B", "color": "green"}], "boxes[1].color"),
    ("boxes", [{"key": "a", "name": "A"}, {"key": "a", "name": "B"}], "boxes[1].key"),
    ("boxes", [{"key": "a", "name": "A", "kakao_rooms": [{"id": 1000000000000011}]}], "boxes[0].kakao_rooms[0].id"),   # 숫자가 아니라 글자로
    ("boxes", [{"key": "a", "name": "A", "kakao_rooms": [{}]}], "boxes[0].kakao_rooms[0]"),
    ("boxes", [{"key": "a", "name": "A", "telegram_rooms": [{"name": "번호 없음"}]}], "boxes[0].telegram_rooms[0].id"),
    ("boxes", [{"key": "a", "name": "A", "keywords": ["", "x"]}], "boxes[0].keywords"),
    ("server", {"port": 80}, "server.port"),
    ("workhours", {"start": "9시"}, "workhours.start"),
    ("workhours", {"extra_workdays": ["2026-13-01"]}, "workhours.extra_workdays"),
    ("summary", {"sources": ["gmail", "fax"]}, "summary.sources"),
    ("service", {"engine": "cron"}, "service.engine"),
    ("rules", {"urgent_words_ko": "(긴급"}, "rules.urgent_words_ko"),
    ("rules", {"thresholds": {"unanswered_dm_hours": "두 시간"}}, "rules.thresholds.unanswered_dm_hours"),
    ("me", {"personal_senders": "123456789"}, "me.personal_senders"),            # 목록이어야 한다
    ("me", {"personal_senders": [123456789]}, "me.personal_senders"),            # 번호는 글자로
])
def test_validate_catches(path, value, where):
    d = doc(**{path: value})
    assert any(p == where for p, _ in errors(d)), errors(d)


def test_gmail_account_rules():
    good = {"alias": "work", "email": "me@example.com", "label": "work", "token": "file:gmail-work.json"}
    assert errors(doc(accounts={**sd.example()["accounts"], "gmail": [good]})) == []
    for bad, where in ((dict(good, alias="Work!"), "alias"), (dict(good, email="x"), "email"),
                       (dict(good, token="file:../../.ssh/id_rsa"), "token"), (dict(good, token="http://x"), "token")):
        e = errors(doc(accounts={**sd.example()["accounts"], "gmail": [bad]}))
        assert any(p.endswith(where) for p, _ in e), (bad, e)
    two = [good, dict(good, email="b@example.com")]
    assert any(p == "accounts.gmail[1].alias" for p, _ in errors(doc(accounts={**sd.example()["accounts"], "gmail": two})))


def test_new_keychain_token_is_refused_but_saved_one_is_kept():
    kc = {"alias": "work", "email": "me@example.com", "token": "keychain:MailHelperApp.work/oauth2_token"}
    d = doc(accounts={**sd.example()["accounts"], "gmail": [kc]})
    assert any(p == "accounts.gmail[0].token" for p, _ in errors(d, saved=sd.example()))        # 화면에서 새로 만들 수 없다
    assert errors(d, saved=d) == []                                                              # 옮기기가 적어 둔 것은 그대로 둔다
    assert errors(d) == []                                                                       # 손으로 고친 파일을 읽을 때(견줄 판 없음)는 막지 않는다


def test_warnings_do_not_block():
    d = doc(boxes=[{"key": "a", "name": "A", "color": "green", "senders": ["x@y.z"]},
                   {"key": "b", "name": "B", "color": "cyan", "senders": ["x@y.z"]}], typo_field=1)
    ps = sd.validate(d)
    assert not [p for p in ps if p.level == "error"]
    assert {p.path for p in ps if p.level == "warn"} >= {"typo_field", "boxes[1].senders"}


def test_big_room_id_survives_yaml_and_json():
    d = doc(boxes=[{"key": "a", "name": "A", "kakao_rooms": [{"id": "100000000000001123", "name": "방"}]}])
    back = sd.parse(sd.dump(json.loads(json.dumps(d))))
    assert back["boxes"][0]["kakao_rooms"][0]["id"] == "100000000000001123"


def test_save_is_atomic_backed_up_and_checks_version(tmp_path):
    v1 = sd.save(tmp_path, sd.example(), None)
    assert paths.settings_file(tmp_path).exists() and oct(os.stat(paths.settings_file(tmp_path)).st_mode & 0o777) == "0o600"
    d, v = sd.read(tmp_path)
    assert v == v1 and d["version"] == 2
    d["etc_name"] = "기타"
    v2 = sd.save(tmp_path, d, v1)
    assert v2 != v1 and len(list(paths.backups_dir(tmp_path).glob("settings-*.yaml"))) == 1
    with pytest.raises(sd.Conflict):
        sd.save(tmp_path, d, v1)                                    # 그 사이 바뀌었다
    with pytest.raises(sd.Invalid):
        sd.save(tmp_path, dict(d, version=1), v2)
    for i in range(25):
        v2 = sd.save(tmp_path, dict(d, etc_name=f"기타{i}"), v2)
    assert len(list(paths.backups_dir(tmp_path).glob("settings-*.yaml"))) == sd.BACKUPS_KEPT
    assert not list(tmp_path.glob("*.tmp"))


def test_version_is_content_hash(tmp_path):
    assert sd.version(tmp_path / "none.yaml") == ""
    assert sd.version_of("a: 1\n") == sd.version_of("a: 1\n") != sd.version_of("a: 2\n")


def test_parse_rejects_non_mapping():
    with pytest.raises(sd.SettingsError):
        sd.parse("- a\n- b\n")
    with pytest.raises(sd.SettingsError):
        sd.parse("a: [\n")
    assert sd.parse("") == {}


def test_boot_record_and_last_good(tmp_path):
    v = sd.save(tmp_path, sd.example(), None)
    assert sd.boot_attempts(tmp_path, v) == 0
    assert sd.boot_begin(tmp_path, v) == 1 and sd.boot_begin(tmp_path, v) == 2
    assert sd.boot_attempts(tmp_path, v) == 2 and sd.boot_attempts(tmp_path, "other") == 0
    assert sd.boot_ok(tmp_path, v) is True
    assert sd.boot_attempts(tmp_path, v) == 0 and paths.last_good_file(tmp_path).read_text(encoding="utf-8") == paths.settings_file(tmp_path).read_text(encoding="utf-8")
    assert sd.boot_ok(tmp_path, "stale-version") is False          # 그 사이 파일이 바뀌었으면 올리지 않는다


def test_normalize_phone():
    assert sd.normalize_phone("010-1234-5678") == "+821012345678"
    assert sd.normalize_phone("+82 10 1234 5678") == "+821012345678"
    assert sd.normalize_phone("me@example.com") == "me@example.com" and sd.normalize_phone("12345") == "12345"


def test_box_about_and_pick_business_are_checked():                  # 2026-10-16 분류 키우기
    from urgent_board import settings_doc as _sd
    import copy as _copy
    doc = _sd.read(Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "home")[0]
    ok = _copy.deepcopy(doc); ok["boxes"][0]["about"] = "동네 장터 준비 — 꽃시장 · 5/20"; ok["summary"]["pick_business"] = False
    assert not [p for p in _sd.validate(ok) if p.level == "error"]
    bad = _copy.deepcopy(doc); bad["boxes"][0]["about"] = 3; bad["summary"]["pick_business"] = "네"
    paths = {p.path for p in _sd.validate(bad) if p.level == "error"}
    assert "boxes[0].about" in paths and "summary.pick_business" in paths


def test_save_takes_a_file_lock_shared_with_other_processes(tmp_path):
    """엔진과 설정 명령은 따로 프로세스다 — 판 확인과 바꿔 넣기 사이에 남이 끼지 않게 자료 폴더의 파일 잠금을 쥔다(2026-10-16)."""
    import fcntl
    import os
    import threading
    sd.save(tmp_path, sd.example(), None)
    lock = tmp_path / sd.LOCK_FILE
    assert lock.exists() and (lock.stat().st_mode & 0o777) == 0o600
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)                         # 다른 프로세스가 쥐고 있는 것처럼
    done = threading.Event()
    t = threading.Thread(target=lambda: (sd.save(tmp_path, sd.example(), None), done.set()), daemon=True)
    t.start()
    assert not done.wait(0.3)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    assert done.wait(5)


def test_undo_goes_back_one_save_at_a_time(tmp_path):
    import pytest as _pytest
    d = sd.example()
    v1 = sd.save(tmp_path, d, None)
    d["etc_name"] = "둘"
    sd.save(tmp_path, d, v1)
    d["etc_name"] = "셋"
    sd.save(tmp_path, d, None)
    ver, name = sd.undo(tmp_path)
    assert sd.read(tmp_path)[0]["etc_name"] == "둘" and sd.read(tmp_path)[1] == ver and name.startswith("settings-")
    assert "셋" in (sd_backups := tmp_path / "backups" / sd.UNDONE).read_text(encoding="utf-8")
    sd.undo(tmp_path)
    assert sd.read(tmp_path)[0].get("etc_name") == sd.example().get("etc_name")
    with _pytest.raises(sd.NothingToUndo):
        sd.undo(tmp_path)
    assert sd_backups.exists()


def test_kakao_helper_dir_must_be_text():
    bad = errors(doc(accounts={**sd.example()["accounts"], "kakao": {"enabled": True, "helper_dir": 5}}))
    assert any(p == "accounts.kakao.helper_dir" for p, _ in bad)
    ok = errors(doc(accounts={**sd.example()["accounts"], "kakao": {"enabled": True, "helper_dir": "~/somewhere"}}))
    assert not any(p == "accounts.kakao.helper_dir" for p, _ in ok)
