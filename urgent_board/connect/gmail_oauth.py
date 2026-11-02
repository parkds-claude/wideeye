"""Gmail 연결 — 사용자 자신의 Google OAuth 클라이언트(데스크톱 앱 종류)로, PKCE · 루프백 주소(설계 7-1). 직접 구현(urllib — 새 의존 없음).

- 클라이언트 파일(client_secret_….json)은 auth/google-client.json(0600). "installed"(데스크톱) 종류만 받는다 — 웹 종류는 돌아오는 주소를 따로 등록해야 한다.
- 로그인 흐름 하나 = state 하나: 한 번만 · 10분 · 시작한 브라우저의 쿠키(nonce)와 같아야 한다(남의 페이지가 돌아오는 길을 흉내 내지 못하게).
- 토큰은 auth/gmail-<별칭>.json(0600) — 수집기(collectors/gmail.py)가 읽는 꼴 그대로. 토큰 끝점의 오류 본문은 기록 · 화면에 넣지 않는다(error 칸만).
- **동의 화면을 "프로덕션"으로 게시하게 안내한다** — "테스트" 상태의 리프레시 토큰은 7일 뒤 만료된다(조사 3)."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CLIENT_FILE = "google-client.json"
FLOW_SECONDS = 600
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
TIMEOUT = 20


class FlowError(Exception):
    """연결 흐름이 실패했다 — 사람이 읽을 까닭 한 줄(비밀 값 없음)."""


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save_client(auth_dir: Path, text: str) -> dict:
    """Google 클라이언트 파일의 글 → 꼴을 보고 auth 폴더에 둔다. 데스크톱 종류만."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        raise FlowError("JSON 파일이 아니에요. Google Cloud 의 '사용자 인증 정보'에서 내려받은 client_secret_….json 파일을 넣어 주세요.") from None
    c = d.get("installed") if isinstance(d, dict) else None
    if not isinstance(c, dict):
        raise FlowError("'데스크톱 앱' 종류로 만든 OAuth 클라이언트가 아니에요. 만들 때 애플리케이션 유형을 '데스크톱 앱'으로 골라 주세요.")
    if not c.get("client_id") or not c.get("client_secret"):
        raise FlowError("파일에 client_id · client_secret 이 없어요. 내려받은 파일이 맞는지 확인해 주세요.")
    clean = {"client_id": str(c["client_id"]), "client_secret": str(c["client_secret"]),
             "auth_uri": AUTH_URI, "token_uri": TOKEN_URI}         # 파일의 주소는 쓰지 않는다 — 코드 · 비밀 · 토큰은 Google 로만(검토 M4)
    _write_private(Path(auth_dir) / CLIENT_FILE, json.dumps({"installed": clean}))
    return clean


def load_client(auth_dir: Path) -> dict | None:
    try:
        return json.loads((Path(auth_dir) / CLIENT_FILE).read_text(encoding="utf-8"))["installed"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class Flows:
    """진행 중인 로그인 흐름(메모리에만 — 엔진이 다시 시작하면 처음부터)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, client: dict, redirect_uri: str, nonce: str) -> tuple[str, str]:
        state = secrets.token_urlsafe(24)
        verifier, challenge = _pkce()
        with self._lock:
            now = self.clock()
            self._pending = {k: v for k, v in self._pending.items() if now - v["at"] <= FLOW_SECONDS}
            self._pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "nonce": nonce, "at": now}
        q = {"client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE, "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"}
        return f"{AUTH_URI}?{urllib.parse.urlencode(q)}", state

    def finish(self, state: str, nonce: str) -> tuple[str, str]:
        """(verifier, redirect_uri). 한 번만 · 10분 안 · 같은 브라우저(쿠키)."""
        with self._lock:
            p = self._pending.pop(state or "", None)
        if p is None:
            raise FlowError("이 로그인 요청을 찾지 못했어요(이미 끝났거나 그사이 현황판이 다시 시작했어요). 설정 화면에서 [메일 계정 더하기]를 다시 눌러 주세요.")
        if self.clock() - p["at"] > FLOW_SECONDS:
            raise FlowError("로그인을 시작한 지 10분이 지났어요. 설정 화면에서 다시 시작해 주세요.")
        if not nonce or not secrets.compare_digest(nonce, p["nonce"]):
            raise FlowError("로그인을 시작한 브라우저가 아니에요. 설정 화면을 연 브라우저에서 다시 해 주세요.")
        return p["verifier"], p["redirect_uri"]


def http_post(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"error": f"http_{e.code}"}


def http_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def exchange(client: dict, code: str, verifier: str, redirect_uri: str, post=http_post) -> dict:
    resp = post(TOKEN_URI, {"code": code, "client_id": client["client_id"], "client_secret": client["client_secret"],
                                                       "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier})
    if not isinstance(resp, dict) or resp.get("error") or not resp.get("access_token"):
        raise FlowError(f"Google 에서 로그인 정보를 받지 못했어요({(resp or {}).get('error', '답 없음') if isinstance(resp, dict) else '답 없음'})")
    return resp


def profile_email(access_token: str, get=http_get) -> str:
    email = str((get(PROFILE_URL, access_token) or {}).get("emailAddress") or "")
    if "@" not in email:
        raise FlowError("메일 주소를 알아내지 못했어요. 다시 해 주세요.")
    return email


def token_payload(client: dict, tok: dict) -> dict:
    """수집기가 읽는 꼴(google.oauth2.credentials 의 칸 이름)."""
    return {"token": tok["access_token"], "refresh_token": tok["refresh_token"], "token_uri": TOKEN_URI,
            "client_id": client["client_id"], "client_secret": client["client_secret"], "scopes": [SCOPE]}


def alias_for(email: str, taken: set[str]) -> str:
    """메일 주소 → 별칭(영문 소문자 · 숫자 · -, 24자까지). 겹치면 -2, -3 …"""
    slug = re.sub(r"[^a-z0-9-]+", "-", email.split("@", 1)[0].lower()).strip("-")[:20] or "mail"
    if slug not in taken:
        return slug
    n = 2
    while f"{slug}-{n}" in taken:
        n += 1
    return f"{slug}-{n}"


def write_token(auth_dir: Path, alias: str, payload: dict) -> str:
    name = f"gmail-{alias}.json"
    _write_private(Path(auth_dir) / name, json.dumps(payload))
    return name
