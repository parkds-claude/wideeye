from datetime import datetime, timezone
from types import SimpleNamespace as NS
from urgent_board.collectors import telegram
from urgent_board.models import Source

AT = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)
def msg(mid, text="가능할까요?", out=False, mentioned=False, entities=None, media=None):
    return NS(id=mid, out=out, message=text, media=media, mentioned=mentioned, entities=entities or [], date=AT)

def test_incoming_dm_read_state_follows_read_max():
    kw = dict(chat_id=-100, chat_name="김과장", is_user=True, read_max=5, me_id=1, sender=NS(id=9, first_name="김과장"), reply_to_me=False)
    old = telegram.to_event(msg(5), **kw); new = telegram.to_event(msg(6), **kw)
    assert old.is_read is True and new.is_read is False
    assert new.source == Source.TELEGRAM and new.chat_id == "-100" and new.sender_id == "9" and new.sender_name == "김과장"
    assert new.is_group is False and new.is_from_me is False and new.received_at == AT

def test_my_message_is_read_and_from_me():
    e = telegram.to_event(msg(7, out=True), chat_id=-100, chat_name="방", is_user=False, read_max=0, me_id=1, sender=NS(id=1, first_name="나"), reply_to_me=False)
    assert e.is_from_me and e.is_read and e.is_group

def test_mention_by_flag_or_entity_and_media_only_text():
    kw = dict(chat_id=-5, chat_name="운영회의", is_user=False, read_max=0, me_id=1, sender=NS(id=9, title="사무국"), reply_to_me=True)
    assert telegram.to_event(msg(8, mentioned=True), **kw).mentioned_me
    assert telegram.to_event(msg(9, entities=[NS(user_id=1)]), **kw).mentioned_me
    assert not telegram.to_event(msg(10, entities=[NS(user_id=2)]), **kw).mentioned_me
    e = telegram.to_event(msg(11, text="", media=object()), **kw)
    assert e.text == "[미디어]" and e.sender_name == "사무국" and e.reply_to_me

def test_constructor_has_no_side_effects(tmp_path):
    telegram.TelegramCollector(str(tmp_path / "none.session"), 1, "x")     # 연결·파일 생성 없이 만들어진다
    assert not list(tmp_path.iterdir())

def test_service_dialogs_are_skipped_and_bots_are_read_for_the_notice_board():            # 777000 = 텔레그램 공식 알림(로그인 코드)
    assert not telegram.wanted_dialog(NS(id=777000, bot=False, deleted=False), me_id=1, is_user=True)
    assert telegram.wanted_dialog(NS(id=5, bot=True, deleted=False), me_id=1, is_user=True)          # 봇 보고서 — 탭5 알림에만(2026-10-17 D9)
    assert not telegram.wanted_dialog(NS(id=6, bot=False, deleted=True), me_id=1, is_user=True)      # 탈퇴 계정
    assert not telegram.wanted_dialog(NS(id=1, bot=False, deleted=False), me_id=1, is_user=True)      # 나에게 보낸 메모
    assert telegram.wanted_dialog(NS(id=9, bot=False, deleted=False), me_id=1, is_user=True)
    assert telegram.wanted_dialog(NS(id=-100, title="운영회의"), me_id=1, is_user=False)


def test_fetch_limit_covers_the_unread_backlog_within_one_request():
    assert telegram.fetch_limit(0) == telegram.fetch_limit(None) == 30
    assert telegram.fetch_limit(55) == 55 and telegram.fetch_limit(5000) == 100


def test_bot_messages_are_marked_for_the_notice_board_only():
    from types import SimpleNamespace as NS2
    from datetime import datetime, timezone
    m = NS2(id=3, message="오늘의 보고서", media=None, date=datetime(2026, 10, 17, tzinfo=timezone.utc), out=False, mentioned=False, entities=None)
    kw = dict(chat_id=55, chat_name="입찰봇", is_user=True, read_max=0, me_id=1, sender=NS2(id=55, first_name="입찰봇"), reply_to_me=False)
    assert telegram.to_event(m, is_bot=True, **kw).raw == {"bot": True} and telegram.to_event(m, **kw).raw == {}
