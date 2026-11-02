"""카톡 맥 DB(SQLCipher) — 사용자가 따로 설치한 kakaocli(MIT)로 읽기 전용 조회. 조사 A §4 SQL. 실험 기능(설계 2026-10-15 7-3).

kakaocli 결과에는 머리글 행이 없다 → 수집기가 COLS 를 붙여 순수 파서(rows_to_events)에 넘긴다.
조회 함수는 collect 때 처음 만든다(만들기만 해서는 하위 프로세스·파일 접근이 없다 — 실패는 run_loop 가 "수집 중단"으로 적는다).
DB 열쇠는 우리 코드가 풀지 않는다 — k-skill 이 찾아 둔 캐시를 읽거나, 없으면 kakaocli 가 스스로 찾는다(load_auth).
명령 줄에 열쇠가 들어가므로 오류 글에는 명령 줄을 넣지 않는다.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..models import PLACEHOLDER_EMOTICON, Event, Source
from .base import Collector

COLS = ["chatId", "chatName", "activeMembersCount", "logId", "authorId", "type", "message", "sentAt", "attachment", "sender", "roomType"]
CHANNEL_ROOM_TYPE = 2            # 알림 채널(카드사·은행·택배 알림톡) 방 — 사람이 아니다(실측 2026-10-13)
SQL = """
SELECT m.chatId, COALESCE(r.chatName, u.displayName, u.friendNickName, u.nickName, '(unknown)') AS chatName, r.activeMembersCount,
       m.logId, m.authorId, m.type, m.message, m.sentAt, m.attachment,
       COALESCE(a.displayName, a.friendNickName, a.nickName, '') AS sender, r.type AS roomType
FROM NTChatMessage m JOIN NTChatRoom r ON r.chatId = m.chatId
LEFT JOIN NTUser u ON u.userId = r.directChatMemberUserId AND u.linkId = 0
LEFT JOIN NTUser a ON a.userId = m.authorId AND a.linkId = 0
WHERE m.sentAt >= {since} AND m.logId < 9223372036854775807 AND m.type < 16384 AND m.type <> 0
ORDER BY m.sentAt
"""
SQL_ME = "SELECT userId FROM NTChatContext LIMIT 1"
SQL_UNREAD = "SELECT chatId, countOfNewMessage FROM NTChatRoom"
EMOTICON_TYPES = {6, 12, 20, 22}


def _attachment(v) -> dict:
    if isinstance(v, dict):
        return v
    if not isinstance(v, str) or not v:
        return {}
    try:
        d = json.loads(v)
    except json.JSONDecodeError:
        return {}
    return d if isinstance(d, dict) else {}


def rows_to_events(rows: list[list], my_user_id: int | None) -> list[Event]:
    """rows[0] = 열 이름, 나머지 = 값. 순수 함수."""
    if not rows:
        return []
    cols = rows[0]
    out: list[Event] = []
    for raw in rows[1:]:
        r = dict(zip(cols, raw))
        if r.get("roomType") == CHANNEL_ROOM_TYPE:
            continue
        att = _attachment(r.get("attachment"))
        mentions = [m for m in (att.get("mentions") or []) if isinstance(m, dict)]
        mentioned = bool(att.get("all_mention")) or \
            (my_user_id is not None and any(str(m.get("user_id", "")) == str(my_user_id) for m in mentions))
        reply_to_me = my_user_id is not None and str(att.get("src_userId", "")) == str(my_user_id)
        author = int(r["authorId"])
        text = r.get("message") or (PLACEHOLDER_EMOTICON if int(r.get("type") or 0) in EMOTICON_TYPES else "")
        out.append(Event(source=Source.KAKAO, chat_id=str(r["chatId"]), message_id=str(r["logId"]), chat_name=r.get("chatName") or "",
                         sender_id=str(author), sender_name=r.get("sender") or str(author), text=text,
                         received_at=datetime.fromtimestamp(int(r["sentAt"]), tz=timezone.utc),
                         is_from_me=(my_user_id is not None and author == my_user_id),
                         is_read=False, is_group=int(r.get("activeMembersCount") or 2) > 2,
                         mentioned_me=mentioned, reply_to_me=reply_to_me))
    return out


def mark_read(events: list[Event], unread_by_chat: dict[str, int]) -> None:
    """방별 '안 읽은 수' N → 그 방의 마지막 수신 N건만 안 읽음, 나머지와 내 것은 읽음."""
    incoming: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        if e.is_from_me:
            e.is_read = True
        else:
            incoming[e.chat_id].append(e)
    for chat_id, evs in incoming.items():
        n = max(0, unread_by_chat.get(chat_id, 0))
        evs.sort(key=lambda e: (e.received_at, int(e.message_id)))
        for i, e in enumerate(evs):
            e.is_read = i < len(evs) - n


K_SKILL_CACHE = Path.home() / ".cache" / "k-skill" / "kakaotalk-mac-auth.json"      # 이 맥: k-skill 이 찾아 둔 열쇠
K_SKILL_DIR = "~/k-skill"                                                           # 도우미 폴더의 기본(설정 accounts.kakao.helper_dir 로 바꾼다)
HELPER_SUB = Path("scripts") / "kakaotalk_mac.py"


def helper_path(helper_dir: str | None = None) -> Path:
    """캐시를 새로 만드는 도우미 파일 — 설정의 helper_dir(없으면 기본 폴더) 안."""
    return Path((helper_dir or "").strip() or K_SKILL_DIR).expanduser() / HELPER_SUB


def configured_helper(accounts: dict | None) -> Path:
    """설정 accounts.kakao.helper_dir → 도우미 파일 경로 — 수집기와 설정 화면의 확인이 함께 쓴다(빈칸뿐이면 기본 자리)."""
    d = ((accounts or {}).get("kakao") or {}).get("helper_dir")
    return helper_path(d if isinstance(d, str) else None)


K_SKILL_HELPER = helper_path()
KAKAOCLI_PATHS = ("/opt/homebrew/bin/kakaocli", "/usr/local/bin/kakaocli")
QUERY_TIMEOUT = 60


class KakaocliError(RuntimeError):
    """kakaocli 가 실패했다. 글에는 명령 줄을 넣지 않는다 — 명령 줄에 DB 열쇠가 들어 있다."""


def find_kakaocli() -> str | None:
    """launchd 로 뜬 프로세스는 PATH 가 좁다(홈 함정) — 흔한 자리도 본다."""
    return shutil.which("kakaocli") or next((p for p in KAKAOCLI_PATHS if Path(p).exists()), None)


def load_auth(cache: Path = K_SKILL_CACHE, helper: Path = K_SKILL_HELPER, run=subprocess.run) -> dict | None:
    """DB 열쇠 — ① k-skill 캐시 ② 캐시가 없고 도우미가 있으면 한 번 새로 고침 ③ 둘 다 없으면 None(kakaocli 가 스스로 찾는다, 설계 7-3)."""
    if not cache.exists() and helper.exists():
        try:
            run([sys.executable, str(helper), "auth", "--refresh"], check=True, capture_output=True, timeout=QUERY_TIMEOUT)
        except Exception:                                    # noqa: BLE001 — 도우미가 실패하면 kakaocli 에 맡긴다
            pass
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("database_path") and d.get("key") else None


def query(sql: str, auth: dict | None, binary: str, run=subprocess.run) -> list[list]:
    """kakaocli query — 결과는 [[값, …], …](머리글 행 없음). 열쇠가 없으면 --db · --key 없이(kakaocli 가 스스로 찾는다)."""
    cmd = [binary, "query", sql] + (["--db", str(auth["database_path"]), "--key", str(auth["key"])] if auth else [])
    try:
        r = run(cmd, capture_output=True, text=True, check=False, timeout=QUERY_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise KakaocliError(f"kakaocli 가 {QUERY_TIMEOUT}초 안에 답하지 않았다") from None
    if r.returncode != 0:
        raise KakaocliError(f"kakaocli query rc={r.returncode}: {(r.stderr or '').strip()[:300]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise KakaocliError(f"kakaocli 응답이 JSON 이 아니다: {e.msg}") from None


class KakaoCollector(Collector):
    name = "kakao"
    poll_seconds = 30

    def __init__(self, query=None, auth: dict | None = None, helper_dir: str | None = None):
        self.helper = helper_path(helper_dir) if not isinstance(helper_dir, Path) else helper_dir
        self._q = query                 # (sql, auth=...) -> list[list] — 시험에서는 끼워 넣는다
        self._auth = auth
        self.my_user_id: int | None = None
        self._ready = False

    def _ensure(self) -> None:
        if self._ready:
            return
        if self._q is None:
            binary = find_kakaocli()
            if not binary:
                raise KakaocliError("kakaocli 가 없다 — brew install silver-flight-group/tap/kakaocli")
            self._auth = load_auth(helper=self.helper)
            self._q = lambda sql, auth=None: query(sql, auth, binary)
        me = self._q(SQL_ME, auth=self._auth)
        self.my_user_id = int(me[0][0]) if me and me[0] else None
        self._ready = True

    def collect(self, since: datetime) -> list[Event]:
        self._ensure()
        rows = self._q(SQL.format(since=int(since.timestamp())), auth=self._auth)
        evs = rows_to_events([COLS] + rows, self.my_user_id)
        unread = {str(r[0]): int(r[1] or 0) for r in self._q(SQL_UNREAD, auth=self._auth)}
        mark_read(evs, unread)
        return evs
