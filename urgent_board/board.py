"""탭5 화면에 무엇이 뜨는가 — 한 곳에서 정한다(웹과 요약 고리가 함께 쓴다).

- visible: 화면에 낼 항목. 대기에서 뺀 것(볼 필요 없다는 판단)과 다른 Claude 세션이 다룬 것을 뺀다.
- 할 일 장부(tasks.sync) → build_task_cards: 할 일 → 사업 상자. 차례는 설정에 적힌 진행 사업 순서 그대로이고 건수에 따라 바뀌지 않는다
  (2026-10-16 할 일 현황판 — 사업 요약은 없앴다, 상자는 할 일 줄을 보인다). 지금은 맥의 메뉴 막대 · 전체 목록이 쓴다.
- 알림(pipeline.feed → settle_feed → build_feed_cards): 탭5 가 그린다(2026-10-17 — 읽음과 관계없이 대화마다 한 줄, 같은 상자 자리).
"""
from __future__ import annotations
import logging
import functools
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import NamedTuple

from . import summarize, tasks as taskbook
from .collectors import claude_sessions
from .models import Card, FeedLine, Grade, Item, Skipped, Task
from .store import Store
from .triage import preprocess
from .triage.pipeline import feed as feed_lines, judge
from .workhours import KST

log = logging.getLogger("board")

SOURCE_LABEL = {"imessage": "문자", "kakao": "카톡", "telegram": "텔레그램", "gmail": "메일", "naver": "메일", "calls": "전화", "teams": "팀즈", "whatsapp": "왓츠앱"}
SOURCE_ORDER = ("imessage", "kakao", "whatsapp", "telegram", "gmail", "calls", "teams")
# 탭5 알림 줄의 출처 꼬리표 — 모두 두 글자(사용자 2026-10-17 "텔레그램-->텔레 로 2글자로 모두 줄일 것. 카카오톡 -> 카톡 식으로").
# 맥의 목록 · 메뉴 막대 · 고장 줄은 SOURCE_LABEL(긴 이름) 그대로
SHORT_LABEL = {**SOURCE_LABEL, "telegram": "텔레", "whatsapp": "왓츠"}
MAIL = ("gmail", "naver")
OWN_SLOTS = 5                              # 사업 이름을 단 상자 수(화면은 세 칸 두 줄 — 여섯째는 아래의 ETC)
ETC = "etc"                                # 여섯째 상자: 개인 · 기타, 진행 사업이 다섯을 넘으면 넘친 사업까지
ETC_NAME, OVERFLOW_NAME = "개인 · 기타", "그 밖"


class Visible(NamedTuple):
    items: list[Item]                      # 화면에 낼 것
    unneeded: set[str]                     # 대기에서 뺀 것의 key
    handled: dict[str, datetime]           # 다른 Claude 세션이 다룬 것 key → 다룬 때


def visible(store: Store, settings, items: list[Item], recipe: str) -> Visible:
    """화면에 낼 항목을 고른다. 대기를 내용에 따라 한 번 더 거르고(긴급은 거르지 않는다), 도착한 뒤 다른 Claude 세션이
    도구로 다룬 메일은 긴급이어도 뺀다(사용자 결정). 뺀 것은 전체 목록(/list)에 남기도록 함께 돌려준다."""
    drop = summarize.unneeded(store, items, recipe) if settings.summary.get("judge") else set()
    done = claude_sessions.handled(store, items) if settings.handled.get("claude_sessions") else {}
    drop -= set(done)
    return Visible([i for i in items if i.key not in drop and i.key not in done], drop, done)


def confirm_notices(store: Store, items: list[Item], skipped: list, recipe: str) -> tuple[list[Item], list]:
    """낱말로만 사업에 걸린 자동 알림은 Claude 도 같은 사업을 골랐을 때만 띄운다(실제 자료 2026-10-16: OpenAI 홍보 메일이 낱말 '항공'으로
    특정 사업 상자에 떴다). 아니면 원래대로 '자동 알림'으로 빼고 까닭을 남긴다. 보낸 사람 등록으로 걸린 것은 곧바로 띄운다."""
    doubt = [i for i in items if "word_only" in i.reasons]
    if not doubt:
        return items, skipped
    agree = summarize.picks(store, doubt, recipe)
    drop = {i.key for i in doubt if agree.get(i.key) != i.business}
    if not drop:
        return items, skipped
    out = [Skipped(reason="notice", source=i.source, chat_name=i.chat_name, sender_name=i.sender_name, summary=i.summary,
                   received_at=i.received_at, key=i.key, link=i.link, account=(i.accounts or [""])[0]) for i in items if i.key in drop]
    return [i for i in items if i.key not in drop], sorted(skipped + out, key=lambda x: x.received_at, reverse=True)


def apply_picks(store: Store, settings, items: list[Item], recipe: str) -> int:
    """Claude 가 고른 사업을 규칙이 아무 데도 못 건 항목(business_free)에만 얹는다 — 진행 사업의 key 일 때만(2026-10-16 분류 키우기).
    등록 · 개인 · 기타로 둔 사람 · 낱말 · 확정 전 일 · 물려받기가 늘 먼저다. 얹은 수."""
    free = [i for i in items if i.business_free]
    if not free:
        return 0
    active = {b.key for b in settings.businesses if b.active}
    got = summarize.picks(store, free, recipe)
    n = 0
    for i in free:
        if got.get(i.key) in active:
            i.business, i.guessed = got[i.key], True
            n += 1
    return n


class Screen(NamedTuple):
    items: list[Item]                      # 판정한 항목 전부(대기에서 뺀 것 · 다룬 것 포함)
    skipped: list                          # 안 읽었지만 뺀 것
    seen: Visible                          # 화면에 낼 것과 뺀 것
    cards: list[Card]                      # 할 일 상자(차례 고정 — build_task_cards)
    recipe: str
    tasks: list[Task] = []                 # 열린 할 일 전부(장부 + 이번 판정)
    book_error: bool = False               # 장부를 읽거나 쓰지 못했다 — 7일 안의 할 일로만 보인다
    awaiting: list[Task] = []              # 답 기다림(장부에 없다 — 판정이 그때그때 찾는다). 상자에는 할 일과 함께 최근 것부터, 처리할 일 수에는 들지 않는다
    awaiting_items: list[Item] = []        # 그 판정 항목(요약 · 판단의 열쇠 — 글의 표식을 셀 때)
    feed: list[FeedLine] = []              # 알림(탭5, 2026-10-17) — 읽음과 관계없이 최근 것부터, 대화마다 한 줄
    feed_cards: list[Card] = []            # 알림 상자(차례는 할 일 상자와 같다 — build_feed_cards)


class _NoBook:
    """장부가 고장 났을 때의 빈 장부 흉내 — 이번 판정만으로 할 일을 만든다."""
    def tasks(self, state="open"):
        return []

    def replied_after(self, *a):
        return False


def screen(store: Store, settings, now: datetime, days: int = 7, write: bool = False) -> Screen:
    """판정 → 화면에 낼 것 → 할 일 장부 → 상자 — 웹 · 설정 화면의 미리 보기 · 옮기기가 함께 쓰는 한 길(서로 다른 항목을 보이지 않게).
    장부는 엔진(웹)만 쓴다(write=True) — 미리 보기 · 옮기기는 장부를 읽기만 한다."""
    events, dismissed = store.recent_events(days=days), store.dismissed_keys(now)
    items, skipped, pending, awaiting = judge(events, settings, now, dismissed)
    recipe = summarize.recipe_for(settings)
    items, skipped = confirm_notices(store, items, skipped, recipe)
    apply_picks(store, settings, items, recipe)
    seen = visible(store, settings, items, recipe)
    lines = summarize.task_lines(store, seen.items, recipe)
    # 뺀 것은 대화(conv)로 — 장부의 key 는 지난번 마지막 글이라 그사이 새 글이 오면 key 로는 못 찾는다
    dropped = {i.conv: "not_needed" for i in items if i.key in seen.unneeded} | {i.conv: "handled" for i in items if i.key in seen.handled}
    try:
        ts = taskbook.sync(store, seen.items, dropped, store.dismissed_keys_all(), lines, now, write=write, pending=pending)
        broken = False
    except (sqlite3.Error, OSError):
        log.warning("할 일 장부를 읽거나 쓰지 못했다 — 이번에는 7일 안의 할 일만 보인다", exc_info=True)
        ts = taskbook.sync(_NoBook(), seen.items, dropped, set(), lines, now, write=False, pending=pending)
        broken = True
    old_days = int(settings.display.get("old_task_days") or OLD_TASK_DAYS)
    # 답 기다림: Claude 가 "상대가 할 일이 없다"(안내 · 공지)고 본 것은 뺀다. 문구는 같은 호출이 준 "상대가 할 일"(없으면 내 글의 제목 · 앞부분)
    drop = summarize.unneeded(store, awaiting, recipe)
    kept = [i for i in awaiting if i.key not in drop]
    wlines = summarize.task_lines(store, kept, recipe)
    orgs = tuple(str(o) for o in settings.display.get("greeting_orgs") or [])
    waits = [awaiting_task(i, wlines.get(i.key, ("", None))[0], orgs) for i in kept]
    tcards = build_task_cards(ts + waits, settings.businesses, settings.etc_name, now, settings.rules.calendar, old_days)
    fl = feed_lines(events, settings, now, dismissed)                                       # 알림(탭5) — 같은 글 · 같은 처리됨으로
    notes = settle_feed(fl, items, seen, saved_picks(store, settings, [line.key for line in fl if line.free]))
    return Screen(items, skipped, seen, tcards, recipe, ts, broken, waits, kept, notes, build_feed_cards(notes, settings.businesses, settings.etc_name))


def saved_picks(store: Store, settings, keys: list[str]) -> dict[str, str]:
    """글 key → Claude 가 예전에 고른 진행 사업(글의 해시는 보지 않는다 — 알림 줄에는 할 일 항목의 묶은 글이 없다). 읽지 못하면 빈 것."""
    active = {b.key for b in settings.businesses if b.active}
    try:
        got = store.get_picks(keys)
    except (sqlite3.Error, OSError):
        log.warning("Claude 가 고른 사업을 읽지 못했다 — 알림 줄은 규칙의 사업으로만", exc_info=True)
        return {}
    return {k: v[1] for k, v in got.items() if v[1] in active}


def settle_feed(lines: list[FeedLine], items: list[Item], seen: Visible, picked: dict[str, str] | None = None) -> list[FeedLine]:
    """알림 줄을 할 일 쪽 판단과 맞춘다(같은 대화 = 같은 conv): 낱말로만 사업에 걸린 자동 알림은 Claude 도 같은 사업을 골랐을 때만
    (confirm_notices 를 지난 항목이 있을 때만) 남기고, 다른 Claude 세션이 다룬 대화는 뺀다(할 일과 같다 — 탭5 의 "다룬 것 N건 뺌"이 맞게),
    규칙이 못 건 줄에는 Claude 가 고른 사업을 얹는다 — 지금 할 일 항목에 얹힌 것, 없으면 그 글에 예전에 고른 것(picked). 읽어서 할 일에서
    빠진 뒤에도 같은 상자에 남게(독립 검토 2026-10-17: 안 읽은 동안 Claude 가 고른 상자에 있다가 읽는 순간 '개인 · 기타'로 옮겨 갔다)."""
    by_conv = {i.conv: i for i in items}
    agreed = {i.conv for i in items if "word_only" in i.reasons}
    handled = {i.conv for i in items if i.key in seen.handled}
    out = []
    for line in lines:
        if (line.word_only and line.conv not in agreed) or line.conv in handled:
            continue
        it = by_conv.get(line.conv)
        if line.free and it is not None and it.guessed:
            line.business = it.business
        elif line.free and (picked or {}).get(line.key):
            line.business = picked[line.key]
        out.append(line)
    return out


OLD_TASK_DAYS = 14
TAG_HEAD = re.compile(r"^(?:\s*[\[【][^\]】]{0,30}[\]】]\s*)+")      # 제목 앞의 말머리([기관 이름] · 【…】) — 상자 한 줄이 짧다


@functools.lru_cache(maxsize=8)
def greeting_re(orgs: tuple = ()) -> re.Pattern:
    """인사말("안녕하세요 ○○님 기관 이름 홍길동입니다")을 떼는 무늬. 기관 이름은 설정 display.greeting_orgs 에서(코드에 두지 않는다)."""
    org = r"(?:(?:주식회사\s*)?(?:" + "|".join(re.escape(o) for o in orgs) + r")\s*)?" if orgs else ""
    return re.compile(r"^\s*(?:안녕하세요[,.!~\s]*)?(?:[^\s,.]{1,12}\s*님[,.!~\s]*)?" + org + r"(?:[가-힣]{2,4}\s*)?입니다[.,!~\s]*")


def feed_what(line: FeedLine, orgs: tuple = ()) -> str:
    """알림 줄의 글 — 말머리([기관 이름] · 【…】), 그 뒤의 Re:/Fw:, 인사말을 뗀다(상자 한 줄이 짧다). 떼고 나서 비면 원문 그대로."""
    return greeting_re(tuple(orgs)).sub("", preprocess.SUBJECT_PREFIX.sub("", TAG_HEAD.sub("", line.text))).strip() or line.text


def awaiting_task(i: Item, what: str = "", orgs: tuple = ()) -> Task:
    """답 기다림 항목 → 상자에 놓을 줄. 무엇 = Claude 가 준 "상대가 할 일", 없으면 내 글의 제목 · 앞부분(말머리 · 인사말을 뗀다), 누가 = 기다리는 상대."""
    if not what:
        what = greeting_re(tuple(orgs)).sub("", TAG_HEAD.sub("", i.summary)).strip() or i.summary
    return Task(conv=i.conv, key=i.key, business=i.business, source=i.source, chat_id=i.chat_id, chat_name=i.chat_name, sender_name=i.sender_name,
                what=what, summary=i.summary, due=None, link=i.link, unread=False, opened_at=i.received_at, last_at=i.received_at, kind="awaiting")


def _next_workday(d: date, cal) -> date:
    n = d + timedelta(days=1)
    for _ in range(14):
        if cal.is_workday(n):
            return n
        n += timedelta(days=1)
    return n


def due_soon(t: Task, now: datetime, cal) -> bool:
    """기한이 지났거나 오늘 · 다음 업무일까지 — 탭5 에서 주황."""
    if not t.due:
        return False
    return t.due <= _next_workday(now.astimezone(KST).date(), cal)


def is_old(t: Task, now: datetime, old_days: int = OLD_TASK_DAYS) -> bool:
    """열린 지 오래된 읽은 할 일(흐리게 · 아래로). 안 읽은 할 일은 오래됨이 아니다 — 새 글이 와서 안 읽음이 됐는데 묻히면 안 된다(검토 I6)."""
    return not t.unread and t.kind == "todo" and now - t.opened_at > timedelta(days=old_days)


def task_order(t: Task, now: datetime, cal, old_days: int = OLD_TASK_DAYS) -> tuple:
    """상자 안 차례: 최근 것부터(그 대화의 마지막 글이 온 때) — 상자는 앞의 두 줄만 보인다(사용자 2026-10-16 "너무 과거내용만 있는데 ·
    최근 2개를 띄어줄수 있어?" — 안 읽음 → 기한 → 먼저 열린 것 차례에서는 10/09 메일이 앞을 차지했다). 안 읽음 · 기한 임박은 주황 수가 알린다."""
    return (-t.last_at.timestamp(),)


def _slots(businesses, etc_name: str) -> tuple[list[tuple[str, str, str]], set[str]]:
    """상자 자리(key, 이름, 색) — 진행 사업 다섯 + 여섯째(개인 · 기타, 진행 사업이 다섯을 넘으면 '그 밖'). 할 일 상자와 알림 상자가 함께 쓴다."""
    active = [b for b in businesses if b.active]
    own = active[:OWN_SLOTS]
    slots = [(b.key, b.name, b.color) for b in own] + [(ETC, OVERFLOW_NAME if len(active) > OWN_SLOTS else etc_name, "")]
    return slots, {b.key for b in own}


def build_feed_cards(lines: list[FeedLine], businesses, etc_name: str) -> list[Card]:
    """알림 → 사업 상자(할 일 상자와 같은 자리 · 차례 고정). 어느 줄도 빠지지 않는다.
    상자 안 차례(D12): 안 읽은 것 먼저, 그 안에서 최근 것부터 — 그다음 읽은 것도 최근 것부터. 상자는 앞의 두 줄만 보이는데, 최근 것부터만 세우면
    읽은 새 글 둘이 안 읽은 옛 글을 밀어내 주황 수(안 읽음 1)와 보이는 줄이 어긋난다(실제 자료로 뽑아 보다 찾음, 2026-10-17).
    상태: 안 읽은 줄이 있으면 urgent(urgent = 그 수), 읽은 줄만 있으면 waiting, 없으면 quiet."""
    slots, own_keys = _slots(businesses, etc_name)
    groups: dict[str, list[FeedLine]] = {key: [] for key, _, _ in slots}
    for line in lines:
        groups[line.business if line.business in own_keys else ETC].append(line)
    cards: list[Card] = []
    for key, name, color in slots:
        ls = sorted(groups[key], key=lambda line: (line.unread == 0, -line.at.timestamp()))
        hot = sum(1 for line in ls if line.unread)
        cards.append(Card(key=key, name=name, color=color, state="urgent" if hot else "waiting" if ls else "quiet", tasks=ls, urgent=hot, waiting=len(ls) - hot))
    return cards


def build_task_cards(tasks: list[Task], businesses, etc_name: str, now: datetime, cal, old_days: int = OLD_TASK_DAYS) -> list[Card]:
    """할 일 → 사업 상자(진행 사업 다섯 + 여섯째, 넘치면 '그 밖' — 차례 고정). 어느 할 일도 빠지지 않는다.
    상태: 안 읽은 할 일이나 기한이 임박한 할 일이 있으면 urgent, 읽은 할 일만 있으면 waiting, 없으면 quiet."""
    slots, own_keys = _slots(businesses, etc_name)
    groups: dict[str, list[Task]] = {key: [] for key, _, _ in slots}
    for t in tasks:
        groups[t.business if t.business in own_keys else ETC].append(t)
    cards: list[Card] = []
    for key, name, color in slots:
        ts = sorted(groups[key], key=lambda t: task_order(t, now, cal, old_days))
        hot = sum(1 for t in ts if (t.unread or due_soon(t, now, cal)) and not is_old(t, now, old_days))
        state = "urgent" if hot else "waiting" if ts else "quiet"
        cards.append(Card(key=key, name=name, color=color, state=state, tasks=ts, urgent=hot, waiting=len(ts) - hot))
    return cards


def _kind(i: Item) -> str:
    """출처 꼬리표의 종류. 메일은 어느 서비스든 하나로 센다(계정은 맥의 /list 에서 본다)."""
    return "gmail" if i.source.value in MAIL else i.source.value


def summary_allowed(i: Item, sources) -> bool:
    """이 항목의 글을 Claude 요약에 보내도 되는가(설정 summary.sources). None 이면 모두."""
    return sources is None or _kind(i) in sources
