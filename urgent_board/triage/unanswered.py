"""답 안 함: 뒤에 내 답이 없고, 상대의 해결 표현도 없고, 단톡방이면 N분 안에 남이 답하지도 않았다."""
from __future__ import annotations
import re
from datetime import timedelta

from ..models import Event, Source
from .needs_reply import needs_reply

NOT_LETTERS = re.compile(r"[\W_]+")


def contact_key(ev: Event) -> str:
    """같은 상대를 채널 가로질러 묶는 키. 메일은 주소, 문자 · 전화 · 왓츠앱은 번호(왓츠앱의 번호를 숨긴 상대는 JID), 카톡·텔레그램은 출처+사용자 id."""
    if ev.source in (Source.GMAIL, Source.NAVER, Source.IMESSAGE, Source.CALLS, Source.WHATSAPP):   # 전화 · 왓츠앱은 문자와 같은 번호 꼴(+82…) — 같은 사람으로 묶인다
        return (ev.sender_id or "").lower()
    return f"{ev.source.value}:{ev.sender_id}"


MAIL = (Source.GMAIL, Source.NAVER)


def identities(ev: Event, contacts: dict) -> set[str]:
    """이 메시지를 보낸 사람을 가리키는 키들: 채널 키 + (설정 contacts 에 등록돼 있으면) 사람 이름."""
    key = contact_key(ev)
    out = {key}
    for alias in (key, f"{ev.source.value}:{(ev.sender_name or '').lower()}"):
        name = contacts.get(alias)
        if name:
            out.add("person:" + name)
    return out


def addressed(my_ev: Event, chat_events: list[Event], contacts: dict) -> set[str]:
    """내가 보낸 메시지가 향한 상대들. 메일은 받는 사람, 1:1 대화는 그 방의 상대. 단톡방 발언은 특정 상대를 향하지 않는다."""
    if my_ev.source in MAIL:
        addrs = {str(a).lower() for a in (my_ev.raw or {}).get("to", [])}
        return addrs | {"person:" + contacts[a] for a in addrs if a in contacts}
    if my_ev.is_group:
        return set()
    out: set[str] = set()
    for x in chat_events:
        if not x.is_from_me:
            out |= identities(x, contacts)
    return out


def is_resolution(text: str, rules) -> bool:
    """상대가 자기 요청을 거둔 말인가("해결됐습니다", "취소합니다", "감사합니다").
    짧은 메시지 전체가 그럴 때만 — 정보를 덧붙인 후속이 "감사합니다"로 끝난다고 앞선 질문이 풀린 것은 아니다."""
    t = (text or "").strip()
    if not rules.resolved_ko.search(t):
        return False
    if len(NOT_LETTERS.sub("", t)) > rules.thresholds.get("resolved_max_chars", 12):
        return False
    return not needs_reply(t, rules)


def is_unanswered(ev: Event, later: list[Event], rules, same_contact: bool = False) -> bool:
    """ev 뒤에 온 메시지들(later) 가운데 내 답(같은 방/스레드, 또는 same_contact 면 어느 채널이든)이 없으면 True."""
    for e in later:
        if e.received_at <= ev.received_at:
            continue
        same_place = e.source == ev.source and e.chat_id == ev.chat_id and (not ev.thread_id or e.thread_id == ev.thread_id)
        if e.is_from_me and (same_place or same_contact):
            return False
        if same_place and not e.is_from_me:
            if e.sender_id == ev.sender_id and is_resolution(e.text, rules):
                return False
            if ev.is_group and e.sender_id != ev.sender_id and \
                    (e.received_at - ev.received_at) <= timedelta(minutes=rules.thresholds["group_other_reply_min"]):
                return False
    return True
