from pathlib import Path
from urgent_board.config import load
from urgent_board.models import Source, Grade, Event, Item

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"

def test_load_businesses_and_rules():
    s = load(HOME)
    keys = [b.key for b in s.businesses if b.active]
    assert "bazaar2" in keys and "bookshop-team" in keys
    it = next(b for b in s.businesses if b.key == "bazaar2")
    assert -100000000001 in it.telegram_chat_ids
    assert "office@example.org" in it.mail_accounts
    assert s.rules.thresholds["unanswered_dm_minutes"] == 120
    assert s.port == 5091
    assert "대장님" in s.my_aliases

def test_enums_and_dataclasses():
    assert Source("kakao").value == "kakao"
    assert Grade.URGENT.value == "urgent"
    assert Event.__dataclass_fields__ and Item.__dataclass_fields__

def test_extra_workdays_default_empty_and_parsed():
    from datetime import date
    from urgent_board import config
    assert load(HOME).rules.calendar.extra_workdays == frozenset()
    assert config.parse_dates(["2026-10-17", date(2026, 10, 18)]) == frozenset({date(2026, 10, 17), date(2026, 10, 18)})

def test_every_business_has_a_palette_color():
    from urgent_board import config
    s = load(HOME)
    colors = {b.key: b.color for b in s.businesses}
    assert all(c in config.PALETTE for c in colors.values())
    # 사용자(2026-10-13): "사업별로 색상이 비슷해서 잘 구분이 안됨" — 노랑·연두·초록이 나란히 쓰이면 안 된다. 연두는 사업에 주지 않는다
    assert colors["bazaar2"] == "yellow" and colors["bookshop-team"] == "cyan" and "lime" not in colors.values()
    assert config.PALETTE.index("lime") > config.PALETTE.index("violet") and config.PALETTE[-1] == "orange"      # 남는 색을 줄 때도 헷갈리는 색은 뒤로
    assert len(set(colors.values())) == len(colors)                     # 사업끼리 색이 겹치지 않는다
    bs = [config.Business(key="a", name="A"), config.Business(key="b", name="B", color="green"), config.Business(key="c", name="C", color="없는색")]
    config.assign_colors(bs)
    assert bs[1].color == "green" and bs[0].color in config.PALETTE and bs[2].color in config.PALETTE and len({b.color for b in bs}) == 3

def test_mail_labels_are_loaded_in_order():
    s = load(HOME)
    assert list(s.mail_labels)[:2] == ["minji@example.com", "main@example.com"] and s.mail_labels["office@example.org"] == "사무국"

def test_display_options_and_junk_rules():
    s = load(HOME)
    from urgent_board import config as cfg
    assert s.display == {"fill_with_waiting": True, "tab5_usb_serial": "AA:BB:CC:00:00:02", "old_task_days": 14, "awaiting_days": 3, "feed_bots": True, "greeting_orgs": []}     # 옛 칸(채움)은 읽기만 한다 · 봇 알림은 기본 켬(2026-10-17)
    assert cfg.DISPLAY_DEFAULTS == {"old_task_days": 14, "awaiting_days": 3, "tab5_usb_serial": "", "feed_bots": True, "greeting_orgs": []}       # 답 기다림 3일(2026-10-16) · 봇 알림 켬(2026-10-17)                     # 일련번호를 모르면 그림을 보내지 않는다(2026-10-15) · 할 일은 늘 보인다(2026-10-16)
    assert cfg.SUMMARY_DEFAULTS["task_max_chars"] == 14 and "card_max_chars" not in cfg.SUMMARY_DEFAULTS      # 사업 요약은 없앴다(2026-10-16)
    assert "urgent_after_hours" not in s.rules.thresholds         # 긴급 = 안 읽은 것 — 오래 기다린 것을 긴급으로 올리는 규칙은 없앴다
    assert s.rules.junk_labels["CATEGORY_PROMOTIONS"] == "ad" and s.rules.junk_labels["CATEGORY_UPDATES"] == "notice"
    assert s.rules.junk_ad_text.search("(광고) 특가") and s.rules.junk_auto_text.search("[Web발신] 인증번호")

def test_summary_settings():
    s = load(HOME)
    assert s.summary["enabled"] is True and s.summary["model"] == "haiku" and s.summary["max_chars"] == 60 and s.summary["max_per_day"] > 0
    assert s.summary["judge"] is True and "팔로우" in s.summary["skip_when"]            # 대기 목록을 내용에 따라 고른다(사용자 2026-10-13)


def test_handled_settings():                                           # 사용자 2026-10-13: "너와 대책을 세웠던 것은 빼줘"
    from urgent_board import config as cfg
    s = load(HOME)
    assert s.handled["claude_sessions"] is True and s.handled["lookback_days"] >= 1 and s.handled["sessions_dir"].endswith(".claude/projects")
    assert cfg.HANDLED_DEFAULTS["claude_sessions"] is False            # 설정에 적지 않으면 세션 기록을 읽지 않는다
