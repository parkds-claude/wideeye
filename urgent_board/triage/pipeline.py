"""Event 목록 → Item 목록.

순서: 잡음·자동 → 향함 → 답 필요 → 답 안 함 → 등급 → 사업. 결과는 긴급 → 대기, 같은 등급은 기다린 시간 내림차순.
같은 방의 같은 상대는 항목 하나로 묶는다(등급은 가장 높은 것, 기다린 시간은 가장 긴 것, 처리됨 키는 가장 최근 메시지).
지켜보는 방(settings.watch_rooms)은 나를 부르지 않은 안 읽은 글도 올린다 — 방마다 항목 하나로 묶어서(방 묶음).
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from typing import NamedTuple

from ..models import Event, FeedLine, Grade, Item, Skipped, Source
from ..workhours import business_minutes
from . import business, directed, grade as grading, needs_reply, preprocess, unanswered

SUMMARY_LEN = 80
ROOM_SENDER = "단톡방의 여러 사람"       # 방 묶음의 '보낸 사람' 자리(요약을 만들 때 건네는 머리말 — 화면에는 "안 읽은 글 N건"이 나온다)
ORDER = {Grade.URGENT: 0, Grade.WAITING: 1}


def _who(e: Event) -> tuple:
    """항목을 묶는 단위: 같은 방의 같은 상대. 메일은 계정마다 스레드 id 가 달라 보낸 사람 + 제목으로 묶는다(여러 계정에 온 같은 메일 = 하나)."""
    if e.source in unanswered.MAIL:
        return (e.source, (e.sender_id or "").lower(), _subject(e))
    return (e.source, e.chat_id, e.sender_id)


def conv_id(e: Event) -> str:
    """할 일 장부의 대화 열쇠 — 항목을 묶는 단위(_who)를 글자로."""
    return "|".join(str(getattr(x, "value", x)) for x in _who(e))


def _subject(e: Event) -> str:
    return preprocess.norm_subject((e.raw or {}).get("subject") or e.chat_name)


NO_BUSINESS = ("other", "personal")


def _business_tags(events: list[Event], settings, replied: set[str] = frozenset()) -> dict[str, tuple[str, bool, bool]]:
    """메시지마다 (사업 key, 등록된 방·계정·발신자인가). 스스로는 어느 사업인지 모르는('other') 메시지는
    같은 방 → 같은 상대 순으로, 그쪽의 다른 메시지가 **한 사업에만** 걸려 있으면 그 사업을 물려받는다.
    둘 이상에 걸려 있으면 짐작하지 않는다. 물려받은 것은 '등록'이 아니다(안 읽음 30분 규칙에 쓰지 않는다).
    광고·홍보(ad)는 물려주지 않는다 — 협회 공지의 장소 이름이 그 방의 다음 글을 한 사업으로 끌고 가지 않게.
    자동 알림·대량 발송(notice·bulk)은 물려준다: Gmail 이 거래처 메일을 '업데이트'로 잘못 분류하는 일이 있다.
    셋째 값(free)은 규칙이 아무 데도 못 걸었고 물려받지도 못했다는 것 — Claude 가 사업을 골라도 된다(2026-10-16 분류 키우기)."""
    own = {e.key: business.explain(e, settings) for e in events if not e.is_from_me}
    by_chat: dict[tuple, set[str]] = defaultdict(set)
    by_sender: dict[str, set[str]] = defaultdict(set)
    for e in events:
        if e.is_from_me or preprocess.junk_reason(e, settings.rules, replied) == "ad":
            continue
        key = own[e.key][0]
        if key not in NO_BUSINESS:
            by_chat[(e.source, e.chat_id)].add(key)
            by_sender[unanswered.contact_key(e)].add(key)
    out: dict[str, tuple[str, bool]] = {}
    for e in events:
        if e.is_from_me:
            continue
        key, registered, why = own[e.key]
        if key == "other":
            for known in (by_chat[(e.source, e.chat_id)], by_sender[unanswered.contact_key(e)]):
                if len(known) == 1:
                    key = next(iter(known))
                    break
        out[e.key] = (key, registered, key == "other" and why == business.NOTHING)
    return out


INHERITED = "같은 방 · 사람의 다른 글에서 물려받음"


def _mine(events: list[Event], by_chat: dict, contacts: dict) -> tuple[list, set[str]]:
    """내가 보낸 메시지마다 '누구에게 보냈나'와 내가 메일을 보낸 적 있는 주소 — 다른 계정 · 채널로 답한 것도 답으로 치기 위해.
    judge 와 business_map 이 함께 쓴다(두 벌이 어긋나지 않게)."""
    mine = [(m, unanswered.addressed(m, by_chat[(m.source, m.chat_id)], contacts)) for m in events if m.is_from_me]
    # 둘째 값: 내가 글을 보낸 적 있는 상대 — 메일 주소와 문자 번호(2026-10-17: 문자 자동 발송 판정이 내가 보낸 적 있는 번호를 빼게). 자동 발송 판정에만 쓴다
    return mine, {a for m, addr in mine if m.source in (*unanswered.MAIL, Source.IMESSAGE) for a in addr}


def business_map(events: list[Event], settings) -> tuple[dict[str, tuple[str, str]], set[str]]:
    """메시지마다 (사업 key, 까닭) 와 내가 메일을 보낸 적 있는 주소 — 판정(judge)과 같은 길: 규칙(business.explain) + 같은 방 · 사람에게서
    물려받기(_business_tags), 광고는 물려주지 않는다. Claude 가 고르는 사업은 들어 있지 않다. 설정 명령의 받은 곳 목록(setup_steps.places)이
    쓴다 — '지금 어느 상자'가 화면과 어긋나지 않게(2026-10-16 AI 와 함께 설정, 검토 6)."""
    events = sorted(events, key=lambda e: e.received_at)
    contacts = getattr(settings, "contacts", None) or {}
    by_chat: dict[tuple, list[Event]] = defaultdict(list)
    for e in events:
        by_chat[(e.source, e.chat_id)].append(e)
    _, replied = _mine(events, by_chat, contacts)
    tags = _business_tags(events, settings, replied)
    out = {}
    for e in events:
        if e.is_from_me:
            continue
        own, _, why = business.explain(e, settings)
        key = tags[e.key][0]
        out[e.key] = (key, why if key == own else INHERITED)
    return out, replied


def _accounts(pairs, settings) -> list[str]:
    """묶인 메일 사본들이 온 내 계정들 — 설정(mail_labels)에 적힌 순서, 나머지는 가나다순."""
    order = list(getattr(settings, "mail_labels", None) or {})
    found = {a for _, it in pairs for a in it.accounts}
    return sorted(found, key=lambda a: (order.index(a) if a in order else len(order), a))


def _summary(e: Event, rules) -> str:
    """화면에 낼 한 줄. 메일은 제목 앞의 Re:/Fw: 를 뗀다."""
    t = preprocess.clean_text(e.text, rules)
    if e.source in unanswered.MAIL:
        t = preprocess.SUBJECT_PREFIX.sub("", t)
    return t[:SUMMARY_LEN]


def _answers(mine: Event, e: Event) -> bool:
    """그 상대에게 보낸 내 메시지가 e 의 답인가. 메일끼리는 같은 제목일 때만(다른 건 답장이 이 요청을 지우면 안 된다),
    그 밖(다른 채널로 답함·1:1 대화)은 그 뒤에 보낸 것이면 답으로 본다."""
    if mine.source in unanswered.MAIL and e.source in unanswered.MAIL:
        return _subject(mine) == _subject(e)
    return True


def _dismissed_until(events: list[Event], dismissed: set[str]) -> dict[tuple, datetime]:
    """처리됨으로 누른 메시지 시각까지는 그 상대의 메시지를 숨긴다(그 뒤에 새로 보내면 다시 뜬다)."""
    out: dict[tuple, datetime] = {}
    for e in events:
        if e.key in dismissed:
            k = _who(e)
            out[k] = max(out.get(k, e.received_at), e.received_at)
    return out


def _room_dismissed_until(events: list[Event], dismissed: set[str], watch) -> dict[tuple, datetime]:
    """지켜보는 방에서 처리됨으로 누른 글의 시각까지는 그 방의 일반 글을 묶음에 넣지 않는다(누가 썼든 — 묶음은 방 단위다)."""
    out: dict[tuple, datetime] = {}
    for e in events:
        room = _room_of(e)
        if e.key in dismissed and room in watch:
            out[room] = max(out.get(room, e.received_at), e.received_at)
    return out


def _room_of(e: Event) -> tuple[str, str]:
    return (e.source.value, str(e.chat_id))


def _said(e: Event, rules) -> str:
    """누가 무엇을 — 방 묶음의 글 한 줄."""
    return f"{(e.sender_name or '').strip()}: {preprocess.clean_text(e.text, rules)}"


def _room_text(evs: list[Event], rules) -> str:
    """방 묶음의 글 전체(시간순, "누가: 무엇"). 길면 최근 것부터 담는다 — 요약은 지금 오가는 이야기를 말해야 한다."""
    kept: list[str] = []
    size = 0
    for e in reversed(evs):
        line = _said(e, rules)
        if kept and size + len(line) + 3 > TEXT_LEN:
            break
        kept.append(line); size += len(line) + 3
    return " / ".join(reversed(kept))[:TEXT_LEN]


def _room_item(evs: list[Event], tags: dict, now: datetime, rules) -> Item:
    """지켜보는 방의 안 읽은 일반 글들 → 항목 하나. 대표는 가장 최근 글(처리됨 키 · 여는 주소 · 요약이 없을 때 보일 글),
    기다린 시간은 가장 오래된 안 읽은 글부터."""
    evs = sorted(evs, key=lambda e: e.received_at)
    first, last = evs[0], evs[-1]
    biz = next((tags[e.key][0] for e in reversed(evs) if tags[e.key][0] not in NO_BUSINESS), tags[last.key][0])
    free = biz == "other" and all(tags[e.key][2] for e in evs)
    return Item(grade=Grade.URGENT, business=biz, source=last.source, chat_name=last.chat_name, sender_name=ROOM_SENDER,
                summary=_said(last, rules)[:SUMMARY_LEN], received_at=last.received_at,
                waited_minutes=business_minutes(first.received_at, now, rules.calendar), deadline=None,
                reasons=["watched_room"], key=last.key, link=last.link, text=_room_text(evs, rules), count=len(evs),
                since=first.received_at, conv=f"room|{last.source.value}|{last.chat_id}", chat_id=str(last.chat_id), business_free=free)


def _is_nudge(x: Event, settings) -> bool:
    """재촉으로 셀 만한 메시지인가: 나에게 향했고(단톡방이면 멘션·호칭·답장), 질문·요청이거나 긴급 낱말이 있다."""
    rules = settings.rules
    if preprocess.is_noise(x, rules) or not directed.is_directed(x, settings, None):
        return False
    text = x.text or ""
    return bool(needs_reply.needs_reply(text, rules) or rules.urgent_ko.search(text) or rules.urgent_en.search(text))


def _nudges(e: Event, later_same: list[Event], settings) -> int:
    """이 메시지 + 뒤따른 재촉 수. 몇 분 안에 이어 보낸 것(나눠 쓴 메시지), 정보만 덧붙인 것, 남에게 한 말은 재촉이 아니다."""
    gap = settings.rules.thresholds.get("nudge_gap_min", 10) * 60
    n, prev = 1, e.received_at
    for x in later_same:
        if x.is_from_me or x.sender_id != e.sender_id:
            continue
        if (x.received_at - prev).total_seconds() >= gap and _is_nudge(x, settings):
            n += 1
        prev = x.received_at
    return n


TEXT_LEN = 2000
COPY_WINDOW_SEC = 600          # 여러 계정에 온 같은 메일의 도착 시각 차이로 보는 한도


def _copy_id(e: Event) -> tuple | None:
    """여러 내 메일함에 온 같은 메일을 알아보는 열쇠: 보낸 주소 · 제목 · Date 머리글(사본은 셋이 똑같다). Date 가 없으면 None — 사본으로 치지 않는다."""
    if e.source not in unanswered.MAIL or e.is_from_me:
        return None
    raw = e.raw or {}
    date = ((raw.get("headers") or {}).get("date") or "").strip()
    if not date:
        return None
    return (e.sender_id.strip().lower(), (raw.get("subject") or e.chat_name or "").strip(), date)


def _read_elsewhere(events: list[Event]) -> tuple[list[Event], set[str]]:
    """한 메일함에서 읽은 메일은 다른 메일함의 사본도 읽은 것으로 본다(사용자 2026-10-15 "그 사업 메일은 다 읽었는데.. 왜?" —
    한 메일함 앞으로 온 메일이 다른 메일함에도 사본으로 들어와, 사용자가 보지 않는 그 사본 때문에 늘 긴급으로 남았다).
    돌려주는 것: 읽음으로 바꾼 사건들, 바꾼 것의 열쇠(까닭을 남기려고)."""
    read = {k for e in events if e.is_read and (k := _copy_id(e)) is not None}
    if not read:
        return events, set()
    changed: set[str] = set()
    out = []
    for e in events:
        if not e.is_read and _copy_id(e) in read:
            changed.add(e.key)
            e = replace(e, is_read=True)
        out.append(e)
    return out, changed


def build_items(events: list[Event], settings, now: datetime, dismissed: set[str]) -> list[Item]:
    """화면에 낼 항목. 긴급(안 읽은 것, 최근 것부터) → 대기(읽었지만 답 안 한 요청, 오래 기다린 것부터)."""
    return scan(events, settings, now, dismissed)[0]


def skipped_unread(events: list[Event], settings, now: datetime, dismissed: set[str]) -> list[Skipped]:
    """안 읽었지만 긴급에서 뺀 것과 그 까닭(광고·자동 알림·참조 메일·단톡방 일반 글) — 최근 것부터."""
    return scan(events, settings, now, dismissed)[1]


def scan(events: list[Event], settings, now: datetime, dismissed: set[str]) -> tuple[list[Item], list[Skipped]]:
    """한 번 훑어 (화면에 낼 항목, 안 읽었지만 뺀 것)을 함께 돌려준다."""
    j = judge(events, settings, now, dismissed)
    return j.items, j.skipped


class Judged(NamedTuple):
    items: list[Item]              # 화면에 낼 항목
    skipped: list[Skipped]         # 안 읽었지만 뺀 것
    pending: set[str]              # 읽었고 질문 · 요청인데 문턱 전이라 아직 등급이 없는 대화(conv) — 할 일 장부가 닫지 않고 ○ 로 남긴다
    awaiting: list[Item] = []      # 답 기다림 — 내가 보낸 질문 · 요청에 상대 답이 아직 없는 것(kind="awaiting", 최근 것부터)


FEED_ONLY = (Source.CALLS, Source.TEAMS)


def feed_only(e: Event) -> bool:
    """탭5 알림에만 들어가는 글(설계 2026-10-17 D6): 부재중 전화 · 팀즈 알림 · 텔레그램 봇. 할 일 판정(judge — 장부 · 요약 · 메뉴 막대 ·
    전체 목록 · 재판정 도구가 모두 이 길)에는 넣지 않는다 — 봇 보고서가 할 일을 채우지 않게, 판정 규칙을 건드리지 않게."""
    return e.source in FEED_ONLY or bool((e.raw or {}).get("bot"))


def judge(events: list[Event], settings, now: datetime, dismissed: set[str]) -> Judged:
    """한 번 훑어 화면에 낼 항목 · 뺀 것 · 문턱 전의 대화를 함께 돌려준다(할 일 장부는 셋째 것으로 '읽었고 끝남'과 '아직 모름'을 가른다 —
    검토 C1 · I4, 2026-10-16: 장부가 문턱을 따로 세면 업무시간 밖에 읽은 글이 다음 업무일까지 '안 읽음'으로 남았다).
    알림에만 들어가는 글(feed_only)은 처음에 뺀다."""
    rules = settings.rules
    events = sorted((e for e in events if not feed_only(e)), key=lambda e: e.received_at)
    events, read_copies = _read_elsewhere(events)
    contacts = getattr(settings, "contacts", None) or {}
    hidden_until = _dismissed_until(events, dismissed)
    by_chat: dict[tuple, list[Event]] = defaultdict(list)
    for e in events:
        by_chat[(e.source, e.chat_id)].append(e)
    # 내가 보낸 메시지마다 '누구에게 보냈나' — 다른 계정·다른 채널로 답한 것도 답으로 치기 위해
    mine, replied = _mine(events, by_chat, contacts)
    tags = _business_tags(events, settings, replied)
    watch = getattr(settings, "watch_rooms", None) or frozenset()
    room_cut = _room_dismissed_until(events, dismissed, watch)
    rooms: dict[tuple, list[Event]] = defaultdict(list)                    # 지켜보는 방 → 그 방의 안 읽은 일반 글
    pending: set[str] = set()

    groups: dict[tuple, list[tuple[Event, Item]]] = defaultdict(list)
    # 다른 메일함에서 읽은 사본: 이 메일함에서는 안 읽음이었다 — 긴급에서 뺀 까닭을 남긴다(조용히 사라지지 않게)
    skipped: list[Skipped] = [Skipped(reason="read_elsewhere", source=e.source, chat_name=e.chat_name, sender_name=e.sender_name,
                                      summary=_summary(e, rules), received_at=e.received_at, key=e.key, link=e.link, account=e.account)
                              for e in events if e.key in read_copies]
    for e in events:
        if e.is_from_me:
            continue
        cut = hidden_until.get(_who(e))
        if cut is not None and e.received_at <= cut:
            continue
        chat = by_chat[(e.source, e.chat_id)]
        my_last = max((x.received_at for x in chat if x.is_from_me and x.received_at < e.received_at), default=None)
        later_same = [x for x in chat if x.received_at > e.received_at]

        def leave_out(reason: str) -> None:
            """안 읽은 것을 뺄 때는 까닭을 남긴다 — 조용히 사라지는 것이 없게(전체 목록 화면 맨 아래)."""
            if not e.is_read:
                skipped.append(Skipped(reason=reason, source=e.source, chat_name=e.chat_name, sender_name=e.sender_name,
                                       summary=_summary(e, rules), received_at=e.received_at, key=e.key, link=e.link, account=e.account))

        # 지켜보는 방은 사용자가 통째로 보겠다고 한 방이다 — 광고 표식("많은 관심과 참여" 같은 공지꼴)으로 빼지 않는다
        junk = "" if _room_of(e) in watch else preprocess.junk_reason(e, rules, replied)
        # 사업에 걸린 자동 알림(결제 · 예약 확인)은 안 읽은 동안 그 상자에 띄운다(사용자 2026-10-16) — 보낸 사람 등록 · 사업 낱말로 걸린 것만
        sure = business.sure(e, settings) if junk in NOTICE_KINDS and not e.is_read else None
        notice = sure is not None
        if notice:
            junk = ""
        if junk or not (notice or directed.is_directed(e, settings, my_last)):       # 나에게 온 것만, 스팸·광고는 빼고
            if not preprocess.is_noise(e, rules):
                room = _room_of(e)
                if not junk and not e.is_read and room in watch:         # 지켜보는 방의 안 읽은 일반 글 — 빼지 않고 방마다 하나로 묶는다
                    if room not in room_cut or e.received_at > room_cut[room]:
                        rooms[room].append(e)
                else:
                    leave_out(junk or ("cc" if e.source in unanswered.MAIL else "group"))
            continue
        if preprocess.is_noise(e, rules):                                # 스티커·추임새·인사만
            leave_out("noise")
            continue
        why_reply = needs_reply.needs_reply(e.text, rules)
        if e.is_read and not why_reply:                                  # 읽었고 답할 것도 없다
            continue
        if not e.is_read:
            # 안 읽은 것: 내가 그 뒤에 같은 곳에서 말했을 때만 '봤다'로 친다(폰에서 답했는데 읽음 반영이 늦는 경우).
            # 남이 대신 답했거나, 내가 다른 채널·다른 건으로 연락했다고 해서 이 메시지를 본 것은 아니다.
            if _i_spoke_after(e, later_same, mine):
                leave_out("answered")
                continue
        else:
            ids = unanswered.identities(e, contacts)
            later_mine = [m for m, addr in mine if m.received_at > e.received_at and addr & ids and _answers(m, e)]
            if not unanswered.is_unanswered(e, later_same + later_mine, rules, same_contact=True):
                continue
        waited = business_minutes(e.received_at, now, rules.calendar)
        ctx = grading.GradeCtx(now=now, waited_min=waited, nudges=_nudges(e, later_same, settings),
                               is_dm=not e.is_group, is_read=e.is_read, needs_reply=bool(why_reply))
        g, why, deadline = grading.grade(e, ctx, rules)
        if g == Grade.NONE:
            if e.is_read and why_reply:                                  # 읽은 요청인데 문턱 전 — 끝난 것이 아니다
                pending.add(conv_id(e))
            continue
        biz = tags[e.key][0]
        groups[_who(e)].append((e, Item(
            grade=g, business=biz, source=e.source, chat_name=e.chat_name, sender_name=e.sender_name,
            summary=_summary(e, rules), received_at=e.received_at, waited_minutes=waited,
            deadline=deadline, reasons=why + why_reply + (["business_notice"] + ([] if sure[1] else ["word_only"]) if notice else []), key=e.key, link=e.link,
            accounts=[e.account.lower()] if e.source in unanswered.MAIL and e.account else [])))

    out: list[Item] = []
    for pairs in groups.values():
        # 보여 줄 것: 등급이 가장 높은 것, 같으면 가장 최근 것
        _, shown = min(pairs, key=lambda p: (ORDER[p[1].grade], -p[0].received_at.timestamp()))
        latest, _ = max(pairs, key=lambda p: p[0].received_at)
        # 기다린 시간: 대기는 묶음에서 가장 오래 기다린 것, 긴급은 가장 오래된 '안 읽은' 것부터(읽고 넘긴 옛 메시지의 시간을 붙이지 않는다)
        same_grade = [it.waited_minutes for _, it in pairs if it.grade == shown.grade]
        shown.waited_minutes = max(same_grade)
        shown.since = min(e.received_at for e, it in pairs if it.grade == shown.grade)      # 그 기다린 시간을 세기 시작한 때(상자의 "어제부터")
        shown.key = latest.key
        shown.conv, shown.chat_id = conv_id(latest), str(latest.chat_id)
        shown.received_at = latest.received_at
        shown.business = next((it.business for _, it in pairs if it.business not in NO_BUSINESS), shown.business)
        shown.business_free = shown.business == "other" and all(tags[e.key][2] for e, _ in pairs)
        shown.accounts = _accounts(pairs, settings)
        shown.text = _joined_text([e for e, _ in pairs], rules)
        # 다른 Claude 세션이 다뤘는지 볼 id: 가장 최근 메일과 그 사본(다른 계정에 같은 때 온 것)만. 묶음 안의 옛 메일은 넣지 않는다 —
        # 옛 메일을 그 뒤에 다뤘다고 새로 온 안 읽은 메일이 빠지면 안 된다
        shown.refs = sorted({x for e, _ in pairs if e.source in unanswered.MAIL
                             and abs((latest.received_at - e.received_at).total_seconds()) <= COPY_WINDOW_SEC
                             for x in (e.message_id, e.chat_id, e.thread_id) if x})
        out.append(shown)
    out.extend(_room_item(evs, tags, now, rules) for evs in rooms.values())
    out.sort(key=lambda i: (0, -i.received_at.timestamp()) if i.grade == Grade.URGENT else (1, -i.waited_minutes))
    skipped.sort(key=lambda x: x.received_at, reverse=True)
    return Judged(out, skipped, pending, _awaiting(events, settings, now, dismissed, tags, by_chat))


NOTICE_KINDS = ("notice", "auto")      # 자동 알림 · 자동 발송 — 사업에 확실히 걸리면 안 읽은 동안 띄운다(광고 · 대량 발송은 아니다)
AWAITING_MAX_TO = 3                    # 받는 사람이 이보다 많으면 공지로 본다(한 사람의 답을 기다리는 것이 아니다)
AWAITING_DAYS = 3                      # 이보다 오래된 내 요청은 답 기다림에 내지 않는다(설정 display.awaiting_days, 0 이면 끔)


def _awaiting(events: list[Event], settings, now: datetime, dismissed: set[str], tags: dict, by_chat: dict) -> list[Item]:
    """답 기다림(사용자 2026-10-16 "답 기다림도 보이기"): 내가 보낸 질문 · 요청 가운데 그 뒤 상대에게서 아무 글도 오지 않은 것.
    메일은 받는 사람(나 말고 1~3명) · 정리한 제목마다, 1:1 대화는 방마다 가장 최근의 내 글 하나. 단톡방 발언은 치지 않는다.
    상대의 답 = 같은 스레드 · 방의 남의 글, 메일은 받는 사람이 같은 제목으로 보낸 글(다른 메일함으로 온 것도). 처리됨으로 지운 것은 뺀다."""
    days = int((getattr(settings, "display", None) or {}).get("awaiting_days", AWAITING_DAYS))
    if days <= 0:
        return []
    rules = settings.rules
    since = now - timedelta(days=days)
    mine_addrs = {a.lower() for a in getattr(settings, "my_addresses", []) or []}
    names: dict[str, str] = {}                                      # 상대 열쇠 → 이름(그 사람이 보낸 글에서)
    their_biz: dict[str, set[str]] = defaultdict(set)               # 상대 열쇠 → 그 사람의 글이 걸린 사업들
    for e in events:
        if e.is_from_me:
            continue
        k = unanswered.contact_key(e)
        if e.sender_name:
            names[k] = e.sender_name
        if tags.get(e.key, ("other",))[0] not in NO_BUSINESS:
            their_biz[k].add(tags[e.key][0])
    latest: dict[tuple, tuple[Event, list[str]]] = {}
    for m in events:
        if not m.is_from_me or m.received_at < since:
            continue
        if m.source in unanswered.MAIL:
            to = sorted({str(a).lower() for a in (m.raw or {}).get("to", []) if "@" in str(a) and str(a).lower() not in mine_addrs})
            if not to:
                continue
            latest[(m.source.value, _subject(m), tuple(to))] = (m, to)
        elif not m.is_group:
            others = [x for x in by_chat[(m.source, m.chat_id)] if not x.is_from_me]
            who = unanswered.contact_key(others[-1]) if others else f"{m.source.value}:{m.chat_id}"
            latest[(m.source.value, str(m.chat_id))] = (m, [who])
    # 같은 글을 여러 방에 따로 보낸 것(단체 문자)은 공지다 — 받는 사람을 합쳐 센다
    spread: dict[str, set] = defaultdict(set)
    for (m, to) in latest.values():
        spread[preprocess.clean_text(m.text, rules)].update(to)
    out: list[Item] = []
    for (m, to) in latest.values():
        if len(spread[preprocess.clean_text(m.text, rules)]) > AWAITING_MAX_TO:
            continue
        # 내 글이 질문 · 요청인가 — '외국어라 규칙이 못 읽음'(foreign)만으로는 치지 않는다(2026-10-17: 외국어 문의를 넓히자 사무국이 외국어로 보낸 안내 메일
        # 9~10건이 답 기다림으로 번졌다 — 사용자 "내가 보낸건데". 받은 글에만 쓰려던 것이다)
        if m.key in dismissed or len(to) > AWAITING_MAX_TO or not [w for w in needs_reply.needs_reply(m.text, rules) if w != "foreign"]:
            continue
        if any(_answered_me(x, m, to) for x in events if not x.is_from_me and x.received_at > m.received_at):
            continue
        out.append(_awaiting_item(m, to, settings, names, their_biz, rules, now))
    out.sort(key=lambda i: i.received_at, reverse=True)
    return out


def _answered_me(x: Event, m: Event, to: list[str]) -> bool:
    if x.source == m.source and x.chat_id == m.chat_id:
        return True
    return m.source in unanswered.MAIL and x.source in unanswered.MAIL and unanswered.contact_key(x) in to and _subject(x) == _subject(m)


def _awaiting_item(m: Event, to: list[str], settings, names: dict, their_biz: dict, rules, now: datetime) -> Item:
    """답 기다림 항목 — 사업은 받는 사람으로 본다(그 사람을 보낸 사람으로 놓고 규칙 · 낱말), 못 찾으면 그 사람의 다른 글이 걸린 사업 하나."""
    who = to[0]
    biz = business.match(replace(m, sender_id=who.split(":", 1)[-1], is_from_me=False), settings)[0]
    if biz == "other":
        known = set().union(*(their_biz.get(k, set()) for k in to))
        if len(known) == 1:
            biz = next(iter(known))
    name = names.get(who) or (m.chat_name if m.source not in unanswered.MAIL else who.split("@")[0])
    if len(to) > 1:
        name += f" 외 {len(to) - 1}명"
    conv = "await|" + "|".join([m.source.value, _subject(m), *to] if m.source in unanswered.MAIL else [m.source.value, str(m.chat_id)])
    return Item(grade=Grade.WAITING, business=biz, source=m.source, chat_name=m.chat_name, sender_name=name, summary=_summary(m, rules),
                received_at=m.received_at, waited_minutes=business_minutes(m.received_at, now, rules.calendar), deadline=None,
                reasons=["awaiting"], key=m.key, link=m.link, text=_joined_text([m], rules), since=m.received_at, conv=conv, chat_id=str(m.chat_id),
                kind="awaiting")


def _i_spoke_after(e: Event, later_same: list[Event], mine: list[tuple[Event, set]]) -> bool:
    """e 뒤에 내가 같은 방(메일은 같은 스레드)에서 말했는가. 메일은 다른 계정에서 같은 제목으로 그 사람에게 답한 것도 친다."""
    if any(x.is_from_me for x in later_same):
        return True
    if e.source in unanswered.MAIL:
        who = unanswered.contact_key(e)
        return any(m.source in unanswered.MAIL and m.received_at > e.received_at and who in addr and _subject(m) == _subject(e)
                   for m, addr in mine)
    return False


def _joined_text(events: list[Event], rules) -> str:
    """묶인 메시지들의 글을 시간순으로 이어 붙인다(같은 글은 한 번만 — 여러 계정에 온 같은 메일). 요약을 만들 때 쓴다."""
    seen: list[str] = []
    for e in sorted(events, key=lambda x: x.received_at):
        t = preprocess.clean_text(e.text, rules)
        if e.source in unanswered.MAIL:
            t = preprocess.SUBJECT_PREFIX.sub("", t)
        if t and t not in seen:
            seen.append(t)
    return " / ".join(seen)[:TEXT_LEN]


# ===== 알림(탭5, 2026-10-17 — 설계 docs/exec-plans/2026-10-17-notice-feed-design.md) =====
def feed(events: list[Event], settings, now: datetime, dismissed: set[str]) -> list[FeedLine]:
    """알림(D2): 나에게 온 글을 읽음과 관계없이 대화마다 한 줄, 최근 것부터. 거르는 규칙은 judge 와 같다 — 내 글 · 처리됨으로 지운 것 · 잡음 ·
    광고 · 자동 발송(사업에 걸린 자동 알림은 안 읽은 동안만) · 나에게 오지 않은 글(지켜보는 방의 안 읽은 일반 글은 방마다 한 줄) ·
    다른 메일함에서 읽은 사본은 읽음. judge 와 달리 읽은 글도 남는다 — 사용자 "읽어도 까먹는다"(2026-10-16), 휴대폰 알림 목록처럼."""
    rules = settings.rules
    events = sorted(events, key=lambda e: e.received_at)
    events, _ = _read_elsewhere(events)
    contacts = getattr(settings, "contacts", None) or {}
    hidden_until = _dismissed_until(events, dismissed)
    by_chat: dict[tuple, list[Event]] = defaultdict(list)
    for e in events:
        by_chat[(e.source, e.chat_id)].append(e)
    mine, replied = _mine(events, by_chat, contacts)
    tags = _business_tags(events, settings, replied)
    watch = getattr(settings, "watch_rooms", None) or frozenset()
    room_cut = _room_dismissed_until(events, dismissed, watch)
    convs: dict[tuple, list[Event]] = defaultdict(list)
    rooms: dict[tuple, list[Event]] = defaultdict(list)
    word_only: set[str] = set()
    bots = bool((getattr(settings, "display", None) or {}).get("feed_bots", True))      # 텔레그램 봇 보고서를 알림에 넣을까(D9 — 기본 켬)
    for e in events:
        bot = bool((e.raw or {}).get("bot"))
        # 봇 보고서는 안 읽은 동안만(D9 — 적용 뒤 실측 2026-10-17: 봇 방 9곳 · 132건이 모두 읽은 것인데 ○ 로 남아 상자 위쪽을 차지했다.
        # 봇 글은 사람의 부탁이 아니라 '까먹지 않게' 남길 까닭이 없다)
        if e.is_from_me or preprocess.is_noise(e, rules) or (bot and (not bots or e.is_read)):
            continue
        cut = hidden_until.get(_who(e))
        if cut is not None and e.received_at <= cut:
            continue
        room = _room_of(e)
        junk = "" if room in watch else preprocess.junk_reason(e, rules, replied)
        sure = business.sure(e, settings) if junk in NOTICE_KINDS and not e.is_read else None
        if sure is not None:
            junk = ""
            if not sure[1]:
                word_only.add(e.key)
        if junk:
            continue
        my_last = max((x.received_at for x in by_chat[(e.source, e.chat_id)] if x.is_from_me and x.received_at < e.received_at), default=None)
        if sure is not None or directed.is_directed(e, settings, my_last):
            # 안 읽음인데 내가 그 뒤에 같은 곳에서 말했다(폰에서 답했는데 읽음 반영이 늦다) — judge 와 같이 '봤다'로 친다(독립 검토 2026-10-17:
            # 할 일 쪽은 이것을 빼는데 알림 쪽은 주황으로 세어 두 화면의 '안 읽음'이 어긋났다)
            if not e.is_read and _i_spoke_after(e, [x for x in by_chat[(e.source, e.chat_id)] if x.received_at > e.received_at], mine):
                e = replace(e, is_read=True)
            convs[_who(e)].append(e)
        elif room in watch and not e.is_read and (room not in room_cut or e.received_at > room_cut[room]):
            rooms[room].append(e)
    out = [_feed_line(evs, tags, rules, word_only) for evs in convs.values()]
    for evs in rooms.values():
        it = _room_item(evs, tags, now, rules)
        out.append(FeedLine(conv=it.conv, key=it.key, business=it.business, source=it.source, chat_name=it.chat_name, sender_name=ROOM_SENDER,
                            text=it.summary, at=it.received_at, unread=it.count, count=it.count, room=True, free=it.business_free))
    out.sort(key=lambda line: line.at, reverse=True)
    return out


def _feed_line(evs: list[Event], tags: dict, rules, word_only: set[str]) -> FeedLine:
    """한 대화의 글들(시간순) → 알림 한 줄. 같은 메일의 사본(여러 메일함)은 한 통으로 센다. 사업은 가장 최근의 아는 사업."""
    last = evs[-1]
    msgs = list({(_copy_id(e) or e.key): e for e in evs}.values())
    biz = next((tags[e.key][0] for e in reversed(evs) if tags[e.key][0] not in NO_BUSINESS), tags[last.key][0])
    free = biz == "other" and all(tags[e.key][2] for e in evs)
    if (last.raw or {}).get("bot") and not tags[last.key][1]:
        # 봇 글에는 온갖 낱말이 섞인다(Claude 출력 · 글 모음 보고서) — 상자에 등록한 봇 방이 아니면 사업 낱말로 고르지 않고 개인 · 기타(2026-10-17 적용 뒤 실측:
        # Claude 를 잇는 봇과 스레드 보고서 봇이 한 사업 상자 맨 위에 떴다). Claude 도 고르지 않는다
        biz, free = "other", False
    return FeedLine(conv=conv_id(last), key=last.key, business=biz, source=last.source, chat_name=last.chat_name, sender_name=last.sender_name,
                    text=_feed_text(last, rules), at=last.received_at, unread=sum(1 for e in msgs if not e.is_read), count=len(msgs),
                    word_only=last.key in word_only, free=free)


def _feed_text(e: Event, rules) -> str:
    """알림 줄의 글(D4 — 휴대폰 알림처럼 원문 앞부분): 메일은 제목(Re:/Fw: 를 뗀다), 그 밖은 글 앞부분."""
    if e.source in unanswered.MAIL:
        subject = preprocess.SUBJECT_PREFIX.sub("", ((e.raw or {}).get("subject") or e.chat_name or "").strip())
        if subject:
            return subject[:SUMMARY_LEN]
    return _summary(e, rules)
