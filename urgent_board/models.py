"""형(type)만 둔다 — 다른 모듈을 import 하지 않는다(레이어 맨 안쪽)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


# 수집기가 본문 없는 메시지에 넣는 자리표시 — 사람이 쓴 말이 아니므로 판정에서는 잡음이다
PLACEHOLDER_EMOTICON = "[이모티콘]"
PLACEHOLDER_ATTACHMENT = "[첨부]"
PLACEHOLDER_MEDIA = "[미디어]"
PLACEHOLDERS = frozenset({PLACEHOLDER_EMOTICON, PLACEHOLDER_ATTACHMENT, PLACEHOLDER_MEDIA})


class Source(str, Enum):
    IMESSAGE = "imessage"
    KAKAO = "kakao"
    TELEGRAM = "telegram"
    GMAIL = "gmail"
    NAVER = "naver"          # 수집기는 없다(사용자 결정 2026-10-13) — 형만 남겨 둔다
    CALLS = "calls"          # 부재중 전화(맥의 통화 기록 — 2026-10-17 알림 2단계). 탭5 알림에만(pipeline.feed_only)
    TEAMS = "teams"          # 팀즈 알림(맥 알림 센터 — 2026-10-17 알림 2단계). 탭5 알림에만
    WHATSAPP = "whatsapp"    # 맥 왓츠앱 앱의 DB(2026-10-28 — 카톡과 같은 길)


class Grade(str, Enum):
    URGENT = "urgent"
    WAITING = "waiting"
    NONE = "none"


@dataclass
class Event:
    source: Source
    chat_id: str
    message_id: str
    chat_name: str
    sender_id: str
    sender_name: str
    text: str
    received_at: datetime          # tz-aware
    is_from_me: bool
    is_read: bool
    is_group: bool
    mentioned_me: bool = False
    reply_to_me: bool = False
    thread_id: str = ""
    link: str = ""
    account: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source.value}:{self.chat_id}:{self.message_id}"


@dataclass
class Item:
    grade: Grade
    business: str                  # 사업 key 또는 "personal"/"other"
    source: Source
    chat_name: str
    sender_name: str
    summary: str
    received_at: datetime
    waited_minutes: int
    deadline: datetime | None
    reasons: list[str]
    key: str
    link: str = ""
    accounts: list[str] = field(default_factory=list)      # 메일이면 이 메일이 온 내 계정들(여러 계정에 온 같은 메일은 하나로 묶인다)
    text: str = ""                                         # 묶인 메시지들의 전체 글(요약을 만들 때 쓴다, 화면에는 summary)
    refs: list[str] = field(default_factory=list)          # 메일이면 묶인 메시지들의 메시지 id·스레드 id(다른 Claude 세션이 다뤘는지 볼 때 쓴다)
    count: int = 0                                         # 방 묶음이면 묶인 안 읽은 글 수(0 = 묶음이 아니다)
    since: datetime | None = None                          # 기다린 시간을 세기 시작한 때 — 묶인 것 가운데 같은 등급의 가장 오래된 메시지(received_at 은 가장 최근 것)
    conv: str = ""                                         # 대화 하나(같은 방의 같은 상대 · 메일은 보낸 사람 + 제목 · 방 묶음은 방) — 할 일 장부의 열쇠
    chat_id: str = ""                                      # 마지막 글의 방 · 스레드(장부가 7일 밖에서 내 답장을 찾을 때)
    business_free: bool = False                            # 규칙(등록 · 개인 · 낱말 · 확정 전 일 · 물려받기)이 아무 데도 못 걸었다 — Claude 가 사업을 골라도 된다
    guessed: bool = False                                  # 사업을 Claude 가 골랐다(board.screen 이 얹는다 — 전체 목록에 "Claude 추정")
    kind: str = "todo"                                     # "todo"(내가 할 일) · "awaiting"(내가 보낸 질문 · 요청에 상대 답을 기다림 — 2026-10-16)


@dataclass
class Task:
    """할 일 하나(설계 2026-10-16) — 대화 하나에서 내가 끝내야 하는 것. 끝날 때까지(답장 · 처리됨 · 읽고 할 일 아님) 장부에 남는다."""
    conv: str
    key: str                       # 마지막 글의 key(처리됨 · 원문 열기)
    business: str
    source: Source
    chat_id: str
    chat_name: str
    sender_name: str
    what: str                      # 할 일 문구(Claude) — 없으면 요약
    summary: str
    due: date | None
    link: str
    unread: bool
    opened_at: datetime
    last_at: datetime
    count: int = 0                 # 방 묶음이면 안 읽은 글 수
    guessed: bool = False          # 사업을 Claude 가 골랐다(이번 판정에서 — 장부에는 적지 않는다)
    kind: str = "todo"             # "awaiting" = 답 기다림(장부에 적지 않는다 — 판정이 7일 안에서 그때그때 찾는다)


@dataclass
class FeedLine:
    """알림 한 줄(탭5, 2026-10-17 — 설계 docs/exec-plans/2026-10-17-notice-feed-design.md). 대화 하나의 가장 최근 글이고, 읽음과 관계없이 최근 것부터 선다."""
    conv: str                      # 대화 열쇠 — 할 일 장부와 같은 꼴(pipeline.conv_id)이라 할 일 쪽의 사업 고르기 · 다룬 것을 가져올 수 있다
    key: str                       # 가장 최근 글의 key
    business: str
    source: Source
    chat_name: str
    sender_name: str
    text: str                      # 화면에 낼 원문 앞부분(메일은 제목) — Claude 요약이 아니다(D4)
    at: datetime                   # 가장 최근 글이 온 때
    unread: int                    # 이 대화의 안 읽은 글 수(0 = 다 읽음 → ○)
    count: int = 1                 # 이 줄에 묶인 글 수(같은 메일의 사본은 하나로 센다)
    room: bool = False             # 지켜보는 방 묶음(안 읽은 글이 있는 동안만)
    word_only: bool = False        # 사업 낱말로만 걸린 자동 알림 — board 가 Claude 도 같은 사업을 골랐을 때만 남긴다
    free: bool = False             # 규칙이 아무 데도 못 걸었다(Claude 가 고른 사업을 얹어도 된다)


@dataclass
class Card:
    """탭5 의 사업 상자 하나. 차례와 수는 board.build_task_cards 가 정한다."""
    key: str                       # 사업 key, 여섯째 상자는 "etc"
    name: str
    color: str                     # 사업 색 이름(없으면 빈 글)
    state: str                     # "urgent"(긴급 있음) · "waiting"(대기만) · "quiet"(확인할 것 없음)
    items: list[Item] = field(default_factory=list)                # 이 상자가 말하는 항목 — 긴급이 있으면 긴급만, 없으면 대기
    sources: list[tuple[str, int]] = field(default_factory=list)   # (출처 종류, 건수) — 건수 많은 순
    since: datetime | None = None  # 그 항목들 가운데 가장 오래된 것이 온 때
    waited_minutes: int = 0        # 가장 오래 기다린 항목의 업무시간 분
    urgent: int = 0                # 이 상자의 긴급 건수
    waiting: int = 0               # 이 상자의 대기 건수(상태와 상관없이)
    tasks: list = field(default_factory=list)                     # 할 일 상자(2026-10-16): 이 상자의 할 일(차례대로) — urgent = 안 읽음 · 기한 임박 수


@dataclass
class Skipped:
    """안 읽었지만 긴급에서 뺀 것 — 전체 목록 화면에서 까닭과 함께 보인다(조용히 사라지는 것이 없게)."""
    reason: str                    # ad(광고) · bulk(대량 발송) · notice(자동 알림) · auto(자동 발송) · cc(참조·단체 메일) · group(단톡방 일반 글) · noise(인사·스티커) · answered(내가 그 뒤에 답함)
    source: Source
    chat_name: str
    sender_name: str
    summary: str
    received_at: datetime
    key: str
    link: str = ""
    account: str = ""
