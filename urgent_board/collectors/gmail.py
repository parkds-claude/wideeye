"""Gmail — 계정 하나당 수집기 하나. 토큰은 설정의 token 참조로 읽는다 — file:<auth 폴더 안의 이름>(연결 화면이 만든다) 또는
keychain:<서비스>/<계정>(다른 앱이 만든 항목을 읽기만 — 다른 메일 도우미 앱이 만든 항목, 조사 A §3)."""
from __future__ import annotations
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr
from pathlib import Path
from urllib.parse import quote

from ..models import Event, Source
from .base import Collector

log = logging.getLogger("collector")

Q ="in:inbox newer_than:7d -category:promotions -category:social"
SENT_Q = "in:sent newer_than:7d"     # 7일 넘은 스레드에 단 내 답장은 받은편지함 질의에 안 걸릴 수 있다 — 할 일 장부가 답장으로 끝내게(검토 I5). 내 글만 쓴다
META_HEADERS = ["From", "To", "Cc", "Subject", "Date", "List-Unsubscribe", "List-Id", "Auto-Submitted", "Precedence"]
MAX_EVENTS = 400
RETRY_STATUS = (429, 500, 502, 503)
# D1(2026-10-28): 네트워크가 잠깐 끊긴 오류도 그 자리에서 다시 해 본다(간격, 초 — 합 60초). 맥북은 잠에서 깬 직후 · 와이파이가 바뀔 때 · 5분 쉰
# 연결을 다시 쓸 때 하루 5~14번 끊겼고(httplib2 는 쉰 연결이 끊기면 다시 하지 않는다), 한 번 끊기면 다음 회차까지 5분 동안 "수집 중단"이었다.
# 비용: 정말 끊긴 때는 "수집 중단"이 최대 60초 늦게 뜬다. 되돌리기: NET_WAITS = ()
NET_WAITS = (2, 4, 8, 16, 30)


def transient(e: Exception) -> bool:
    """네트워크가 잠깐 끊긴 오류인가 — 연결 끊김 · 시간 초과 · 주소 할당 실패 · 이름 찾기 실패 · SSL 끊김(모두 OSError), 서버 못 찾음(httplib2),
    토큰을 새로 받다 끊김(google-auth). 토큰이 틀린 것(RefreshError) · HTTP 오류는 아니다."""
    import httplib2
    from google.auth.exceptions import TransportError
    return isinstance(e, (OSError, httplib2.ServerNotFoundError, TransportError))


def parse_thread(th: dict, my_addrs: set[str], account: str) -> list[Event]:
    """스레드 하나 → Event 목록. 내가 보낸 것(SENT 라벨 또는 내 주소)은 is_from_me. To 에 내 주소가 없으면(CC·목록) is_group."""
    mine_addrs = {a.lower() for a in my_addrs} | {account.lower()}
    out: list[Event] = []
    for m in th.get("messages", []):
        hdr = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        labels = set(m.get("labelIds", []))
        name, frm = parseaddr(hdr.get("from", ""))
        frm = frm.lower()
        mine = "SENT" in labels or frm in mine_addrs
        to = hdr.get("to", "").lower()
        to_me = any(a in to for a in mine_addrs)
        subject = hdr.get("subject", "")
        recipients = [a.lower() for _, a in getaddresses([hdr.get("to", ""), hdr.get("cc", "")]) if a]
        out.append(Event(source=Source.GMAIL, chat_id=th["id"], message_id=m["id"], chat_name=subject or "(제목 없음)",
                         sender_id=frm, sender_name=name or frm, text=(subject + " — " + m.get("snippet", "")).strip(" —"),
                         received_at=datetime.fromtimestamp(int(m.get("internalDate", "0")) / 1000, tz=timezone.utc), is_from_me=mine,
                         is_read=mine or "UNREAD" not in labels, is_group=not to_me and not mine, thread_id=th["id"], account=account,
                         link=f"https://mail.google.com/mail/u/?authuser={quote(account, safe='')}#all/{th['id']}",   # 그 계정으로 그 스레드를 연다
                         raw={"subject": subject, "headers": hdr, "to": recipients, "labels": sorted(labels)}))
    return out


FILE_TOKEN = re.compile(r"^file:([A-Za-z0-9._-]+)$")
KEYCHAIN_TOKEN = re.compile(r"^keychain:([^/\s]+)/([^/\s]+)$")


def read_token(ref: str, auth_dir: Path, get_password=None) -> dict:
    """토큰 참조 → 토큰 사전. 꼴이 틀리면 ValueError, 없으면 RuntimeError(수집 중단으로 보인다). 값은 기록에 남기지 않는다."""
    m = FILE_TOKEN.match(ref or "")
    if m:
        try:
            raw = (Path(auth_dir) / m.group(1)).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise RuntimeError(f"메일 토큰 파일이 없다({m.group(1)}) — 설정 > 연결에서 다시 연결") from None
    else:
        m = KEYCHAIN_TOKEN.match(ref or "")
        if not m:
            raise ValueError("token 은 file:<이름> 또는 keychain:<서비스>/<계정>")
        if get_password is None:
            import keyring
            get_password = keyring.get_password
        raw = get_password(m.group(1), m.group(2))
        if not raw:
            raise RuntimeError(f"키체인에 메일 토큰이 없다({m.group(1)})")
    return json.loads(raw)


class GmailCollector(Collector):
    poll_seconds = 300

    def __init__(self, alias: str, email: str, token_ref: str, my_addrs: set[str], auth_dir: Path, service_factory=None):
        self.alias, self.email, self.token_ref, self.my_addrs, self.auth_dir = alias, email, token_ref, my_addrs, Path(auth_dir)
        self.name = f"gmail:{alias}"
        self._factory = service_factory or self._build_service      # 시험에서는 가짜 서비스를 끼워 넣는다
        self._svc = None

    def _build_service(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        t = read_token(self.token_ref, self.auth_dir)
        creds = Credentials(token=t["token"], refresh_token=t["refresh_token"], token_uri=t["token_uri"],
                            client_id=t["client_id"], client_secret=t["client_secret"], scopes=t["scopes"])
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def collect(self, since: datetime) -> list[Event]:
        """연결은 한 번 만들어 계속 쓴다(매 주기 토큰을 새로 받지 않게). 실패하면 버려 다음 주기에 Keychain 을 다시 읽는다."""
        if self._svc is None:
            self._svc = self._factory()
        try:
            return self._sweep(self._svc)
        except Exception:
            self._svc = None
            raise

    def _sweep(self, svc) -> list[Event]:
        out: list[Event] = []

        def call(req):
            for n in range(max(5, len(NET_WAITS) + 1)):
                try:
                    return req.execute()
                except Exception as e:                            # noqa: BLE001 — HttpError 의 일시 오류 · 네트워크가 잠깐 끊긴 오류만 다시 해 본다
                    status = getattr(getattr(e, "resp", None), "status", None)
                    if status in RETRY_STATUS and n < 4:
                        time.sleep(min(2 ** n + random.random(), 64))
                        continue
                    if transient(e) and n < len(NET_WAITS):
                        http = getattr(req, "http", None)         # 끊긴 연결을 버린다 — 다음 시도가 새로 연다
                        if http is not None:
                            http.close()
                        log.warning("%s 네트워크 끊김(%s) — %d초 뒤 다시", self.name, type(e).__name__, NET_WAITS[n])
                        time.sleep(NET_WAITS[n])
                        continue
                    raise

        seen: set[str] = set()
        for q, mine_only in ((Q, False), (SENT_Q, True)):
            token = None
            while True:
                res = call(svc.users().threads().list(userId="me", q=q, maxResults=50, pageToken=token))
                for t in res.get("threads", []):
                    if t["id"] in seen:                           # 두 질의에 함께 걸린 스레드는 한 번만 읽는다
                        continue
                    seen.add(t["id"])
                    th = call(svc.users().threads().get(userId="me", id=t["id"], format="metadata", metadataHeaders=META_HEADERS))
                    evs = parse_thread(th, self.my_addrs, self.email)
                    out.extend(e for e in evs if e.is_from_me or not mine_only)
                token = res.get("nextPageToken")
                if not token or len(out) > MAX_EVENTS:
                    break
        return out
