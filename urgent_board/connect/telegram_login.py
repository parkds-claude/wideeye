"""텔레그램 연결 — 전화번호 → 코드 → (필요하면) 2단계 비밀번호(설계 7-2). 비공식 사용자 세션(Telegram API) — api_id · api_hash 는 사람마다 받는다.

단계마다 새 클라이언트를 열고 닫는다(asyncio.run) — 단계 사이에는 임시 세션 파일과 코드 해시만 기억한다. 임시 세션은
auth/telegram-new.session(telethon 은 .session 으로 끝나지 않으면 덧붙인다 — 검토 20). 로그인이 끝나면 auth/telegram-ready.session 으로
맡겨 두고, 다시 시작한 엔진이 수집기를 띄우기 전에 auth/telethon.session 으로 바꿔 놓는다(adopt — 돌던 엔진이 쓰는 세션 파일을
그 밑에서 바꾸면 옛 세션의 -journal 이 새 세션에 되돌려 쓰여 깨질 수 있다, 검토 I4). 실패하면 지금 쓰는 세션을 건드리지 않는다.
'끊기'도 표시(telegram-remove)만 남기고 지우기는 다음 엔진이. 코드 다시 받기는 60초에 한 번."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

NEW_SESSION = "telegram-new.session"
READY = "telegram-ready.session"
REMOVE = "telegram-remove"
SESSION = "telethon.session"
API_FILE = "telegram.json"
RESEND_SECONDS = 60
HASH_RE = re.compile(r"^[0-9a-f]{32}$")
PHONE_RE = re.compile(r"^\+?[0-9 ()-]{8,20}$")
log = logging.getLogger(__name__)
# 텔레그램이 코드를 보낸 길 → 사람이 코드를 어디서 찾을지(2026-10-17 맥북 "코드도 안 오는데" — "보냈다"만 알려 어디서 찾을지 몰랐다)
SENT_VIA = {
    "SentCodeTypeApp": "휴대폰 텔레그램 앱의 'Telegram' 대화방(파란 인증 표시)으로 5자리 코드를 보냈어요. 대화 목록이나 보관함을 확인해 주세요.",
    "SentCodeTypeSms": "문자 메시지로 코드를 보냈어요.",
    "SentCodeTypeSmsWord": "문자 메시지로 코드(낱말)를 보냈어요.",
    "SentCodeTypeSmsPhrase": "문자 메시지로 코드(문장)를 보냈어요.",
    "SentCodeTypeFirebaseSms": "문자 메시지로 코드를 보냈어요.",
    "SentCodeTypeCall": "전화로 코드를 불러 드려요. 걸려 오는 전화를 받아 주세요.",
    "SentCodeTypeFlashCall": "짧은 전화로 코드를 보냈어요 — 걸려 온 번호의 끝자리가 코드예요.",
    "SentCodeTypeMissedCall": "부재중 전화로 코드를 보냈어요 — 걸려 온 번호의 끝자리가 코드예요.",
    "SentCodeTypeEmailCode": "로그인 이메일로 코드를 보냈어요. 메일함을 확인해 주세요.",
    "SentCodeTypeFragmentSms": "Fragment 의 가상 번호로 코드를 보냈어요.",
    "SentCodeTypeSetUpEmailRequired": "텔레그램이 로그인 이메일 등록을 먼저 요구해요. 휴대폰 텔레그램의 설정 > 개인정보 및 보안 > 로그인 이메일에서 이메일을 등록한 뒤 [코드 받기]를 다시 눌러 주세요.",
}
SENT_ELSE = "텔레그램에 코드를 요청했어요. 휴대폰 텔레그램 앱의 'Telegram' 대화방이나 문자를 확인해 주세요."


class LoginError(Exception):
    """사람이 읽을 까닭 한 줄."""


def _default_factory(path: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    return TelegramClient(path, api_id, api_hash)


def _phone(s: str) -> str:
    """+82 꼴로(010-… 이면 +8210…)."""
    d = re.sub(r"\D", "", s)
    return "+" + d if s.strip().startswith("+") else ("+82" + d[1:] if d.startswith("0") else "+" + d)


class TelegramLogin:
    def __init__(self, auth_dir: Path, client_factory=None, clock=time.time):
        self.auth_dir, self.factory, self.clock = Path(auth_dir), client_factory or _default_factory, clock
        self.pending: dict | None = None

    def _run(self, coro_fn):
        from telethon.errors import FloodWaitError
        try:
            return asyncio.run(coro_fn())
        except FloodWaitError as e:
            raise LoginError(f"텔레그램이 요청이 너무 잦다고 해요. {e.seconds}초 뒤에 다시 해 주세요.") from None

    def send_code(self, api_id, api_hash: str, phone: str) -> str:
        """코드를 받게 한다 → 그 코드를 어디서 찾을지 사람에게 할 말(SENT_VIA)."""
        try:
            api_id = int(api_id)
        except (TypeError, ValueError):
            raise LoginError("api_id 는 숫자예요(my.telegram.org > API development tools 에서 확인해 주세요).") from None
        if not HASH_RE.match(str(api_hash or "")):
            raise LoginError("api_hash 는 영문과 숫자 32자리예요.")
        if not PHONE_RE.match(str(phone or "")):
            raise LoginError("전화번호 꼴이 맞지 않아요(예: +82 10-1234-5678).")
        phone = _phone(phone)
        p = self.pending
        if p and p["phone"] == phone and self.clock() - p["sent_at"] < RESEND_SECONDS:
            raise LoginError(f"코드를 다시 받으려면 {int(RESEND_SECONDS - (self.clock() - p['sent_at'])) + 1}초 기다려 주세요.")
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        new = self.auth_dir / NEW_SESSION
        new.unlink(missing_ok=True)
        from telethon.errors import PhoneNumberInvalidError

        async def go():
            c = self.factory(str(new), api_id, api_hash)
            await c.connect()
            try:
                sent = await c.send_code_request(phone)
                return sent.phone_code_hash, type(getattr(sent, "type", None)).__name__
            finally:
                await c.disconnect()
        try:
            code_hash, via = self._run(go)
        except PhoneNumberInvalidError:
            raise LoginError("텔레그램에 가입되지 않은 전화번호예요.") from None
        except LoginError:
            raise
        except Exception as e:                                   # noqa: BLE001 — 네트워크 등
            raise LoginError(f"코드를 받지 못했어요({type(e).__name__}). 잠시 뒤 다시 해 주세요.") from None
        self.pending = {"api_id": api_id, "api_hash": api_hash, "phone": phone, "hash": code_hash, "sent_at": self.clock(), "need_password": False}
        log.info("텔레그램 로그인 코드를 요청했다 — 보낸 길 %s", via)          # 번호 · 값은 적지 않는다
        return SENT_VIA.get(via, SENT_ELSE)

    def sign_in(self, code: str = "", password: str = "") -> str:
        """"ok"(끝남 — 세션을 바꿔 놓았다) 또는 "password_needed"(2단계 비밀번호를 넣어 다시)."""
        p = self.pending
        if not p:
            raise LoginError("먼저 [코드 받기]를 눌러 코드를 받아 주세요.")
        from telethon.errors import PasswordHashInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError
        new = self.auth_dir / NEW_SESSION
        code = str(code or "").strip()
        # 빈 코드를 텔레그램에 넘기지 않는다 — Telethon 의 sign_in(phone, code="") 은 로그인 대신 코드를 다시 보내고 오류 없이 끝난다
        # (2026-10-17 맥북: 그것을 성공으로 읽어 인증 안 된 세션을 바꿔 끼웠다)
        if not (p["need_password"] and password) and not code:
            raise LoginError("2단계 비밀번호를 넣고 [로그인]을 눌러 주세요." if p["need_password"] else "텔레그램에서 받은 코드를 [받은 코드] 칸에 넣고 [로그인]을 눌러 주세요.")

        async def go():
            c = self.factory(str(new), p["api_id"], p["api_hash"])
            await c.connect()
            try:
                if p["need_password"] and password:
                    await c.sign_in(password=password)
                else:
                    await c.sign_in(phone=p["phone"], code=code, phone_code_hash=p["hash"])
                if not await c.is_user_authorized():          # 성공처럼 끝났어도 정말 로그인됐는지 텔레그램에 묻는다
                    return "not_authorized"
                return "ok"
            except SessionPasswordNeededError:
                return "password_needed"
            finally:
                await c.disconnect()
        try:
            result = self._run(go)
        except PhoneCodeInvalidError:
            raise LoginError("코드가 맞지 않아요. 텔레그램 앱으로 온 코드를 다시 확인해 주세요.") from None
        except PhoneCodeExpiredError:
            raise LoginError("코드가 만료됐어요. [코드 받기]로 새 코드를 받아 주세요.") from None
        except PasswordHashInvalidError:
            raise LoginError("2단계 비밀번호가 맞지 않아요.") from None
        except LoginError:
            raise
        except Exception as e:                                   # noqa: BLE001
            raise LoginError(f"로그인하지 못했어요({type(e).__name__}). 잠시 뒤 다시 해 주세요.") from None
        if result == "not_authorized":
            log.warning("텔레그램 로그인이 오류 없이 끝났지만 인증되지 않았다 — 세션을 바꾸지 않는다")
            raise LoginError("로그인이 끝나지 않았어요. [코드 받기]부터 다시 해 주세요.")
        if result == "password_needed":
            p["need_password"] = True
            return result
        os.chmod(new, 0o600)
        os.replace(new, self.auth_dir / READY)
        (self.auth_dir / REMOVE).unlink(missing_ok=True)
        api = self.auth_dir / API_FILE
        fd = os.open(api.with_name(API_FILE + ".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"api_id": p["api_id"], "api_hash": p["api_hash"]}))
        os.replace(api.with_name(API_FILE + ".tmp"), api)
        self.pending = None
        return "ok"


def mark_remove(auth_dir: Path) -> None:
    """'끊기' — 맡겨 둔 새 세션은 버리고, 지금 세션은 다음 엔진이 지우게 표시한다."""
    a = Path(auth_dir)
    (a / READY).unlink(missing_ok=True)
    (a / REMOVE).write_text("", encoding="utf-8")


def adopt(auth_dir: Path) -> str:
    """엔진이 시작할 때(포트를 열어 옛 엔진이 없음을 확인한 뒤 · 수집기를 띄우기 전) 맡겨 둔 일을 한다 — "swapped" · "removed" · "".
    옛 세션의 -journal 은 그 세션을 바꾸거나 지울 때만 함께 지운다(그대로 쓰는 세션의 것은 SQLite 가 되돌려야 한다)."""
    a = Path(auth_dir)
    stale = [SESSION + x for x in ("-journal", "-wal", "-shm")]
    if (a / REMOVE).exists():
        for name in (SESSION, *stale, API_FILE, NEW_SESSION, READY, REMOVE):
            (a / name).unlink(missing_ok=True)
        return "removed"
    if (a / READY).exists():
        for name in stale:
            (a / name).unlink(missing_ok=True)
        os.replace(a / READY, a / SESSION)
        os.chmod(a / SESSION, 0o600)
        return "swapped"
    return ""
