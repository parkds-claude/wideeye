"""나에게 향한 메시지인가. 1:1 은 항상. 단톡방·CC 는 멘션·답장·호칭·내 발언 직후 중 하나."""
from __future__ import annotations
from datetime import datetime

from ..models import Event


def is_directed(ev: Event, settings, my_last_in_chat: datetime | None) -> bool:
    if ev.is_from_me:
        return False
    if not ev.is_group:
        return True
    if ev.mentioned_me or ev.reply_to_me:
        return True
    t = ev.text or ""
    if any(a in t for a in settings.my_aliases):
        return True
    if my_last_in_chat is not None:
        gap = (ev.received_at - my_last_in_chat).total_seconds() / 60
        if 0 <= gap <= settings.rules.thresholds["after_my_message_min"]:
            return True
    return False
