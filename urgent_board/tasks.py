"""할 일 장부(설계 docs/exec-plans/2026-10-16-todo-board-design.md 4) — 판정(지난 7일)이 보인 할 일을 기억하고 끝날 때까지 남긴다.

끝나는 길: 그 대화에 내가 답함(reply) · 처리됨(dismiss) · Claude 가 할 일 없음으로 봄(not_needed) · 다른 Claude 세션이 다룸(handled) ·
읽었고 할 일이 아님(read). 읽은 요청이 문턱 전이라 판정에 안 나온 대화는 판정이 따로 알려 준다(pending — pipeline.judge) — 그것만 ○(읽음)으로
남기고, 그 밖에 판정에서 사라진 7일 안의 대화는 곧바로 끝낸다(검토 C1 · I4: 장부가 문턱을 따로 세면 저녁에 읽은 글이 다음 업무일까지 ● 였다).
7일 밖의 할 일은 판정에 안 나와도 남고 답장 · 처리됨으로 끝난다 — 메일 답장은 다른 계정의 스레드에 있어도 친다(보낸 사람 + 제목),
방 묶음은 그 방에 안 읽은 글이 없으면 끝난다. 그 밖의 읽음은 모른다(메일 수집이 7일만 본다). write=False 면 장부를 건드리지 않는다(미리 보기 · 옮기기).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .models import Grade, Item, Source, Task
from .triage import preprocess

WINDOW_DAYS = 7                     # 판정이 보는 날 수(board.screen 과 같다)
FIELDS = ("conv", "key", "business", "source", "chat_id", "chat_name", "sender_name", "what", "summary", "due", "link", "unread",
          "opened_at", "last_at", "count")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()          # 저장소의 다른 시각과 같은 UTC 글자(글자로 줄 세운다)


def _row(t: Task) -> dict:
    return {"conv": t.conv, "state": "open", "key": t.key, "business": t.business, "source": t.source.value, "chat_id": t.chat_id,
            "chat_name": t.chat_name, "sender_name": t.sender_name, "what": t.what, "summary": t.summary,
            "due": t.due.isoformat() if t.due else "", "link": t.link, "unread": int(t.unread), "count": t.count,
            "opened_at": _iso(t.opened_at), "last_at": _iso(t.last_at), "closed_at": "", "closed_why": ""}


def _task(r: dict) -> Task:
    return Task(conv=r["conv"], key=r["key"], business=r["business"] or "other", source=Source(r["source"]), chat_id=r["chat_id"] or "",
                chat_name=r["chat_name"] or "", sender_name=r["sender_name"] or "", what=r["what"] or "", summary=r["summary"] or "",
                due=date.fromisoformat(r["due"]) if r["due"] else None, link=r["link"] or "", unread=bool(r["unread"]),
                opened_at=datetime.fromisoformat(r["opened_at"]), last_at=datetime.fromisoformat(r["last_at"]), count=int(r["count"] or 0))


MAIL = (Source.GMAIL.value, Source.NAVER.value)


def _replied(store, t: Task) -> bool:
    """그 할 일에 내가 답했는가 — 그 방 · 스레드에 그 뒤 내 글이 있는가. 메일은 하나 더: 대화 열쇠(출처|보낸 사람|정리한 제목 — pipeline.conv_id)로
    그 뒤에 내가 보낸 메일 가운데 제목이 같고 받는 사람에 그 주소가 든 것(어느 계정 · 스레드든 — 사본은 다른 계정에 있다)."""
    if store.replied_after(t.source.value, t.chat_id, t.last_at):
        return True
    if t.source.value in MAIL and not t.conv.startswith("room|"):
        _, sender, subject = (t.conv.split("|", 2) + ["", ""])[:3]
        return any(preprocess.norm_subject((m.raw or {}).get("subject") or m.chat_name) == subject
                   and sender in {str(a).lower() for a in (m.raw or {}).get("to", [])}
                   for m in store.my_mail_after(t.last_at))
    return False


def _room_read(store, t: Task) -> bool:
    """7일 밖의 방 묶음 — 그 방에 안 읽은 남의 글이 더 없으면 읽은 것이다."""
    return t.conv.startswith("room|") and not store.unread_in(t.source.value, t.chat_id)


def sync(store, shown: list[Item], dropped: dict[str, str], dismissed: set[str], lines: dict, now: datetime, write: bool = True,
         pending: set[str] = frozenset()) -> list[Task]:
    """이번 판정이 보인 할 일(shown — 화면에 낼 항목)로 장부를 맞추고 열린 할 일 전부를 돌려준다.
    dropped: 이번에 뺀 대화(conv) → 까닭(not_needed · handled). dismissed: 처리됨으로 누른 key 전부. lines: key → (할 일 문구, 기한).
    pending: 읽은 요청인데 문턱 전이라 판정에 안 나온 대화(pipeline.judge) — 닫지 않고 읽음으로 남긴다. 바뀐 줄만 쓴다."""
    rows = {r["conv"]: r for r in store.tasks("open")}
    book = {conv: _task(r) for conv, r in rows.items()}
    # '읽음 — 할 일 아님'으로 닫은 대화가 지금 판정에서는 읽은 요청(문턱 전)이면 다시 연다 — 판정 규칙이 넓어졌을 때다(2026-10-17 외국어 문의:
    # 고치기 전에 '읽음'으로 닫힌 사무국 문의가 업무시간 문턱(월요일 아침)까지 안 보였다). 답함 · 처리됨 · 할 일 아님 · 다룸으로 닫은 것은 그대로 둔다
    if pending:
        for r in store.tasks("done"):
            if r["conv"] in pending and r["conv"] not in book and r.get("closed_why") == "read":
                book[r["conv"]] = _task(r)
    out: dict[str, Task] = {}

    def keep(t: Task) -> None:
        out[t.conv] = t
        row = _row(t)
        if write and {k: rows.get(t.conv, {}).get(k) for k in row} != row:
            store.put_task(row)

    for it in shown:
        if it.grade not in (Grade.URGENT, Grade.WAITING) or not it.conv:
            continue
        what, due = lines.get(it.key, ("", None))
        old = book.get(it.conv)
        keep(Task(conv=it.conv, key=it.key, business=it.business, source=it.source, chat_id=it.chat_id, chat_name=it.chat_name,
                  sender_name=it.sender_name, what=what or it.summary, summary=it.summary,
                  due=due or (it.deadline.date() if it.deadline else None) or (old.due if old else None), link=it.link,
                  unread=it.grade == Grade.URGENT, opened_at=old.opened_at if old else (it.since or it.received_at), last_at=it.received_at,
                  count=it.count, guessed=it.guessed))
    start = now - timedelta(days=WINDOW_DAYS)
    for conv, t in book.items():
        if conv in out:
            continue
        why = None
        if t.key in dismissed:
            why = "dismiss"
        elif _replied(store, t):
            why = "reply"
        elif conv in pending:
            pass                                                  # 읽었고 문턱 전 — 끝나지 않았다(○)
        elif conv in dropped:
            why = dropped[conv]
        elif t.last_at >= start or _room_read(store, t):
            why = "read"                                          # 7일 안인데 판정에 없다 = 읽었고 할 일이 아니다
        if why is None:
            t.unread = False                                      # 문턱 전(읽었다) · 7일 밖(읽었는지 모른다)
            keep(t)
        elif write:
            store.close_task(conv, why, now)
    return list(out.values())


def recently_closed(store, now: datetime, days: int = 7) -> list[dict]:
    """최근 끝난 할 일(전체 목록의 '최근 끝난 것') — 까닭과 함께."""
    return store.closed_since(now - timedelta(days=days))
