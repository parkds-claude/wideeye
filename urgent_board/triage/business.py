"""사업 꼬리표: 등록된 방·계정·발신자 1순위, 키워드 2순위. 개인 메일 계정(설정의 me.personal_accounts)은 personal, 그 밖은 other.
아직 확정되지 않은 일(settings.unconfirmed)의 낱말도 키워드와 함께 겨루고, 이기면 사업이 아니다(personal·other — 개인·기타 상자)."""
from __future__ import annotations

from ..models import Event, Source

MAIL = (Source.GMAIL, Source.NAVER)
UNCONFIRMED = object()           # 확정 전 일이 이겼다는 표식 — 사업 key 와 겹치지 않는다


def tag(ev: Event, settings) -> str:
    return match(ev, settings)[0]


NOTHING = "어디에도 안 걸림"          # 규칙이 아무 데도 못 건 까닭 — 이것만 Claude 가 사업을 고를 수 있다(2026-10-16 분류 키우기)


def explain(ev: Event, settings) -> tuple[str, bool, str]:
    """(사업 key, 등록된 방·계정·발신자로 걸렸는가, 까닭). 설정 화면의 분류 시험이 같은 길을 쓴다(두 벌이 되지 않게).
    등록이 먼저(위쪽 상자가 이긴다), 다음은 낱말(가장 긴 것 → 많이 걸린 것 → 설정 차례, 확정 전 일과 함께 겨룸)."""
    text = f"{ev.chat_name} {ev.text}".lower()
    active = [b for b in settings.businesses if b.active]
    for b in active:
        if ev.source == Source.KAKAO and (ev.chat_name in b.kakao_chat_names or _int(ev.chat_id) in b.kakao_chat_ids):
            return b.key, True, "등록한 카톡 방"
        if ev.source == Source.TELEGRAM and _int(ev.chat_id) in b.telegram_chat_ids:
            return b.key, True, "등록한 텔레그램 방"
        if ev.source in MAIL and ev.account.lower() in [a.lower() for a in b.mail_accounts]:
            return b.key, True, "등록한 메일 계정"
        if ev.sender_id and ev.sender_id.lower() in [s.lower() for s in b.senders]:
            return b.key, True, SURE_SENDER
    # 개인 · 기타로 둔 사람(사용자 2026-10-16 — 회사 단톡에서 특정인의 개인 글이 사업 낱말 때문에 사업 상자로 갔다) — 사업 낱말이 들어 있어도. 사업에 등록한 방 · 계정 ·
    # 보낸 사람이 먼저다(위). 'personal' 은 물려받기를 하지도 주지도 않는다(pipeline.NO_BUSINESS) — 같은 방의 다른 글을 끌고 가지 않는다
    if ev.sender_id and ev.sender_id.strip().lower() in getattr(settings, "personal_senders", frozenset()):
        return "personal", False, "개인 · 기타로 둔 사람"
    # 키워드: 가장 긴(구체적인) 낱말이 걸린 쪽, 같으면 걸린 낱말이 많은 쪽, 그래도 같으면 설정 순서(사업이 먼저, 확정 전 일이 나중)
    contenders = [(b.key, b.keywords, "") for b in active] + [(UNCONFIRMED, u.keywords, u.name) for u in settings.unconfirmed]
    best, best_score, best_word, best_name = None, (0, 0), "", ""
    for key, words, name in contenders:
        hits = [k for k in words if k.lower() in text]
        score = (max((len(k) for k in hits), default=0), len(hits))
        if score > best_score:
            best, best_score, best_word, best_name = key, score, max(hits, key=len), name
    if best is not None and best is not UNCONFIRMED:
        return best, False, f"낱말 '{best_word}'({len(best_word)}자)"
    personal = ev.source in MAIL and ev.account.lower() in getattr(settings, "personal_accounts", frozenset())
    if best is UNCONFIRMED:
        return ("personal" if personal else "other"), False, f"확정 전 일 '{best_name}'의 낱말 '{best_word}'"
    if personal:
        return "personal", False, "개인 메일 계정"
    return "other", False, NOTHING


SURE_SENDER = "등록한 보낸 사람"


def sure(ev: Event, settings) -> tuple[str, bool] | None:
    """(사업 key, 보낸 사람 등록으로 걸렸는가) — 보낸 사람 등록이나 사업 낱말로 걸렸을 때만. 자동 알림을 상자에 띄울 때(2026-10-16).
    메일 계정 · 방 등록 · 물려받기는 치지 않는다(그 계정 · 방의 모든 알림이 뜬다 — 사업 계정에 온 보안 알림 같은 것).
    낱말로만 걸린 것은 부르는 쪽이 Claude 의 동의를 한 번 더 본다(board.screen — '항공' 낱말에 걸린 홍보 메일)."""
    key, _, why = explain(ev, settings)
    if why == SURE_SENDER:
        return key, True
    return (key, False) if why.startswith("낱말") else None


def free(ev: Event, settings) -> bool:
    """규칙이 아무 데도 못 걸었는가(Claude 가 사업을 골라도 되는가)."""
    return explain(ev, settings)[2] == NOTHING


def match(ev: Event, settings) -> tuple[str, bool]:
    """(사업 key, 등록된 방·계정·발신자로 걸렸는가). 키워드로만 걸린 것은 꼬리표일 뿐 '사업 방'이 아니다(스펙 §5-3 ⑥)."""
    return explain(ev, settings)[:2]


def _int(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None
