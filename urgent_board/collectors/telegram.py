"""텔레그램(Telethon 사용자 세션) — 주기마다 한 스윕. 세션은 이 봇 전용 복사본(auth/telethon.session)만 연다."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone

from ..models import PLACEHOLDER_MEDIA, Event, Source
from .base import Collector

MESSAGES_PER_DIALOG = 30
UNREAD_FETCH_MAX = 100            # 안 읽은 글이 많은 방은 이만큼까지 — 텔레그램은 100건까지 한 번의 요청으로 준다(요청 수는 그대로)
SERVICE_USER_ID = 777000          # 텔레그램 공식 알림(로그인 코드) 계정


class TelegramCollector(Collector):
    name = "telegram"
    poll_seconds = 120

    source = Source.TELEGRAM

    def __init__(self, session_path: str, api_id: int, api_hash: str):
        self.session_path, self.api_id, self.api_hash = session_path, api_id, api_hash
        self._marks: dict[str, int] = {}

    def read_marks(self) -> dict[str, int]:
        return dict(self._marks)

    def collect(self, since: datetime) -> list[Event]:
        return asyncio.run(self._collect(since))

    async def _collect(self, since: datetime) -> list[Event]:
        from telethon import TelegramClient
        from telethon.errors import FloodWaitError
        client = TelegramClient(self.session_path, self.api_id, self.api_hash)
        client.flood_sleep_threshold = 0
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("telegram session not authorized")
            me = await client.get_me()
            out: list[Event] = []
            self._marks = {}
            async for d in client.iter_dialogs(archived=False):
                e = d.entity
                if not wanted_dialog(e, me.id, d.is_user):
                    continue
                if d.date and d.date < since:
                    continue
                read_max = d.dialog.read_inbox_max_id
                self._marks[str(d.id)] = read_max         # 이번에 다시 안 보는 옛 글도 이 번호까지는 읽은 것이다(Store.mark_read_upto)
                try:
                    msgs = [m async for m in client.iter_messages(e, limit=fetch_limit(d.unread_count))]
                except FloodWaitError as fw:
                    await asyncio.sleep(min(fw.seconds + 3, 60))
                    break
                out_by_id = {m.id: bool(m.out) for m in msgs}
                for m in msgs:
                    if m.date < since:
                        break
                    if not (m.message or m.media):
                        continue
                    sender = me if m.out else (m.sender or await m.get_sender())
                    rid = getattr(m.reply_to, "reply_to_msg_id", None) if m.reply_to else None
                    if not rid:
                        reply_to_me = False
                    elif rid in out_by_id:
                        reply_to_me = out_by_id[rid]
                    else:
                        reply_to_me = await _is_mine(client, e, rid)
                    out.append(to_event(m, chat_id=d.id, chat_name=d.name or str(d.id), is_user=d.is_user, read_max=read_max,
                                        me_id=me.id, sender=sender, reply_to_me=reply_to_me, is_bot=bool(d.is_user and getattr(e, "bot", False))))
            return out
        finally:
            await client.disconnect()


def fetch_limit(unread_count: int) -> int:
    """방 하나에서 가져올 글 수: 보통 최근 30건, 안 읽은 글이 더 많으면 그만큼(100건까지) — 쌓인 글을 '안 읽은 글 N건'에 다 세게."""
    return max(MESSAGES_PER_DIALOG, min(int(unread_count or 0), UNREAD_FETCH_MAX))


def wanted_dialog(entity, me_id: int, is_user: bool) -> bool:
    """사람 · 그룹 · 채널 · 봇. 탈퇴 계정, 나에게 보낸 메모, 텔레그램 공식 알림은 뺀다.
    봇은 2026-10-17 부터 받는다(알림 2단계 D9 — 입찰 보고서 같은 봇 보고서). 봇의 글은 탭5 알림에만 간다(to_event 의 raw["bot"] · pipeline.feed_only)."""
    if not is_user:
        return True
    if getattr(entity, "deleted", False):
        return False
    return getattr(entity, "id", None) not in (me_id, SERVICE_USER_ID)


def to_event(m, *, chat_id: int, chat_name: str, is_user: bool, read_max: int, me_id: int, sender, reply_to_me: bool, is_bot: bool = False) -> Event:
    """텔레그램 메시지 하나 → Event. 순수 함수(네트워크 없음). 봇 방의 글은 raw["bot"] — 탭5 알림에만 간다(pipeline.feed_only, 2026-10-17 D6 · D9)."""
    sname = getattr(sender, "first_name", None) or getattr(sender, "title", None) or chat_name or ""
    mentioned = bool(m.mentioned) or any(getattr(x, "user_id", None) == me_id for x in (m.entities or []))
    return Event(source=Source.TELEGRAM, chat_id=str(chat_id), message_id=str(m.id), chat_name=chat_name,
                 sender_id=str(getattr(sender, "id", "")), sender_name=sname, text=m.message or PLACEHOLDER_MEDIA,
                 received_at=m.date.astimezone(timezone.utc), is_from_me=bool(m.out),
                 is_read=bool(m.out) or m.id <= read_max, is_group=not is_user,
                 mentioned_me=mentioned, reply_to_me=reply_to_me,
                 link=f"tg://openmessage?chat_id={abs(chat_id)}&message_id={m.id}", raw={"bot": True} if is_bot else {})


async def _is_mine(client, entity, msg_id: int) -> bool:
    try:
        m = await client.get_messages(entity, ids=msg_id)
        return bool(m and m.out)
    except Exception:
        return False
