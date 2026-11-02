import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError, PasswordHashInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError

from urgent_board import config, paths, settings_doc as sd
from urgent_board.connect import telegram_login as tl
from urgent_board.store import Store
from urgent_board.web import create_app

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
H = {"Origin": "http://localhost"}
API = dict(api_id=12345, api_hash="0123456789abcdef0123456789abcdef", phone="+821012345678")


class Fake:
    """가짜 텔레그램 클라이언트 — 세션 파일을 만들고, sign_in 은 정해 둔 차례대로 오류를 낸다."""
    plan: list = []
    calls: list = []
    authorized = True                 # 로그인 뒤 실제로 인증됐는가(진짜 Telethon 은 빈 코드면 로그인 대신 코드를 다시 보내고 조용히 끝난다)
    sent_type = None                  # send_code_request 가 돌려주는 코드의 길(SentCodeTypeApp 등)

    def __init__(self, path, api_id, api_hash):
        self.path = Path(path)

    async def connect(self):
        self.path.write_bytes(b"SQLite format 3\x00session")

    async def send_code_request(self, phone):
        Fake.calls.append(("code", phone))
        step = Fake.plan.pop(0) if Fake.plan else None
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(phone_code_hash="HASH", type=Fake.sent_type)

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        Fake.calls.append(("signin", code, password, phone_code_hash))
        step = Fake.plan.pop(0) if Fake.plan else None
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(id=1)

    async def is_user_authorized(self):
        return Fake.authorized

    async def disconnect(self):
        pass


@pytest.fixture(autouse=True)
def reset():
    Fake.plan, Fake.calls, Fake.authorized, Fake.sent_type = [], [], True, None


def login(tmp_path, clock=time.time):
    return tl.TelegramLogin(tmp_path, client_factory=Fake, clock=clock)


def test_code_then_sign_in_writes_session_and_api_values(tmp_path):
    """로그인은 새 세션을 맡겨 둘 뿐 — 돌던 엔진이 쓰는 세션은 건드리지 않는다. 바꿔 놓기는 다시 시작한 엔진이(검토 I4)."""
    (tmp_path / "telethon.session").write_bytes(b"OLD")
    (tmp_path / tl.REMOVE).write_text("")                                         # 앞서 '끊기'를 눌렀어도 새 로그인이 이긴다
    lg = login(tmp_path)
    lg.send_code(**API)
    assert lg.sign_in("12345") == "ok"
    assert (tmp_path / "telethon.session").read_bytes() == b"OLD" and not (tmp_path / tl.NEW_SESSION).exists()
    assert (tmp_path / tl.READY).read_bytes().startswith(b"SQLite") and not (tmp_path / tl.REMOVE).exists()
    assert json.loads((tmp_path / "telegram.json").read_text()) == {"api_id": 12345, "api_hash": API["api_hash"]}
    assert oct((tmp_path / "telegram.json").stat().st_mode & 0o777) == "0o600" and oct((tmp_path / tl.READY).stat().st_mode & 0o777) == "0o600"
    assert ("signin", "12345", None, "HASH") in Fake.calls


def test_adopt_swaps_or_removes_and_drops_only_the_old_sessions_journal(tmp_path):
    """엔진이 시작할 때: 맡겨 둔 세션으로 바꾸거나 지운다. 텔레그램을 훑던 엔진이 다시 시작하면 옛 세션의 -journal 이 남는데,
    그것을 새 세션에 되돌려 쓰면 세션이 깨진다 — 바꾸거나 지울 때만 함께 지운다(그대로 쓰는 세션의 것은 SQLite 가 되돌린다)."""
    a = tmp_path
    (a / "telethon.session").write_bytes(b"OLD"); (a / "telethon.session-journal").write_bytes(b"J")
    assert tl.adopt(a) == "" and (a / "telethon.session-journal").exists()             # 맡긴 것이 없다 — 손대지 않는다
    (a / tl.READY).write_bytes(b"NEW")
    assert tl.adopt(a) == "swapped"
    assert (a / "telethon.session").read_bytes() == b"NEW" and not (a / "telethon.session-journal").exists() and not (a / tl.READY).exists()
    assert oct((a / "telethon.session").stat().st_mode & 0o777) == "0o600"
    (a / "telethon.session-journal").write_bytes(b"J"); (a / "telegram.json").write_text("{}"); (a / tl.REMOVE).write_text("")
    assert tl.adopt(a) == "removed"
    assert not any((a / n).exists() for n in ("telethon.session", "telethon.session-journal", "telegram.json", tl.REMOVE))
    assert tl.adopt(a) == ""


def test_two_step_password(tmp_path):
    lg = login(tmp_path)
    lg.send_code(**API)
    Fake.plan = [SessionPasswordNeededError(request=None)]
    assert lg.sign_in("12345") == "password_needed" and not (tmp_path / tl.READY).exists()
    Fake.plan = [PasswordHashInvalidError(request=None)]
    with pytest.raises(tl.LoginError, match="비밀번호"):
        lg.sign_in("", password="틀림")
    assert lg.sign_in("", password="맞음") == "ok" and (tmp_path / tl.READY).exists()


def test_wrong_or_expired_code_and_flood(tmp_path):
    lg = login(tmp_path)
    lg.send_code(**API)
    Fake.plan = [PhoneCodeInvalidError(request=None)]
    with pytest.raises(tl.LoginError, match="맞지 않아요"):
        lg.sign_in("00000")
    Fake.plan = [PhoneCodeExpiredError(request=None)]
    with pytest.raises(tl.LoginError, match="만료"):
        lg.sign_in("12345")
    assert not (tmp_path / "telethon.session").exists()               # 실패하면 지금 세션을 건드리지 않는다
    lg2 = login(tmp_path / "b")
    Fake.plan = [FloodWaitError(request=None, capture=30)]
    with pytest.raises(tl.LoginError, match="30초"):
        lg2.send_code(**API)


def test_resend_waits_a_minute_and_inputs_are_checked(tmp_path):
    now = [100.0]
    lg = login(tmp_path, clock=lambda: now[0])
    lg.send_code(**API)
    with pytest.raises(tl.LoginError, match="초 기다"):
        lg.send_code(**API)
    now[0] += tl.RESEND_SECONDS + 1
    lg.send_code(**API)
    for bad in (dict(API, api_id="abc"), dict(API, api_hash="zz"), dict(API, phone="전화")):
        with pytest.raises(tl.LoginError):
            lg.send_code(**bad)
    with pytest.raises(tl.LoginError, match=r"먼저 \[코드 받기\]"):
        login(tmp_path / "c").sign_in("1")


def test_existing_session_survives_a_failed_login(tmp_path):
    (tmp_path / "telethon.session").write_bytes(b"OLD")
    lg = login(tmp_path)
    lg.send_code(**API)
    Fake.plan = [PhoneCodeInvalidError(request=None)]
    with pytest.raises(tl.LoginError):
        lg.sign_in("0")
    assert (tmp_path / "telethon.session").read_bytes() == b"OLD"


def app(tmp_path):
    shutil.copy(HOME / "settings.yaml", tmp_path / "settings.yaml")
    d, v = sd.read(tmp_path); d["accounts"]["telegram"]["enabled"] = False; sd.save(tmp_path, d, v)
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    restarts = []
    a = create_app(live, Store(tmp_path / "t.db"), restart=restarts.append)
    a.config["UB"]["telegram_factory"] = Fake
    return a.test_client(), restarts


def wait(cond):
    end = time.monotonic() + 3
    while time.monotonic() < end and not cond():
        time.sleep(0.05)
    return cond()


def test_routes_turn_telegram_on_and_restart(tmp_path):
    c, restarts = app(tmp_path)
    sent = c.post("/api/connect/telegram/code", json=API, headers=H).get_json()
    assert sent["ok"] is True and "텔레그램" in sent["sent"]          # 코드를 어디서 찾을지 화면에 말한다(2026-10-17)
    Fake.plan = [SessionPasswordNeededError(request=None)]
    assert c.post("/api/connect/telegram/signin", json={"code": "1"}, headers=H).get_json() == {"ok": False, "password_needed": True}
    j = c.post("/api/connect/telegram/signin", json={"code": "", "password": "pw"}, headers=H).get_json()
    assert j["ok"] is True and sd.read(tmp_path)[0]["accounts"]["telegram"]["enabled"] is True and wait(lambda: bool(restarts))
    assert API["api_hash"] not in json.dumps(c.get("/api/connect").get_json())
    restarts.clear()
    auth = paths.auth_dir(tmp_path)
    assert (auth / tl.READY).exists() and not (auth / "telethon.session").exists()         # 바꿔 놓기는 다시 시작한 엔진이
    assert tl.adopt(auth) == "swapped"
    assert c.post("/api/connect/telegram/remove", json={}, headers=H).get_json()["ok"] is True
    assert (auth / "telethon.session").exists() and (auth / tl.REMOVE).exists()              # 돌던 엔진의 세션을 지우지 않는다 — 맡겨 둔다
    assert sd.read(tmp_path)[0]["accounts"]["telegram"]["enabled"] is False and wait(lambda: bool(restarts))
    assert tl.adopt(auth) == "removed" and not (auth / "telethon.session").exists()


def test_route_errors_are_plain_messages(tmp_path):
    c, _ = app(tmp_path)
    r = c.post("/api/connect/telegram/signin", json={"code": "1"}, headers=H)
    assert r.status_code == 400 and "먼저 [코드 받기]" in r.get_json()["error"]


def test_signin_without_settings_or_with_broken_settings(tmp_path):
    """설정 파일이 없으면 만들어 켜고, 깨져 있으면 로그인하기 전에 멈춘다 — 500 이 아니라 읽을 수 있는 글로(검토 I5)."""
    c, restarts = app(tmp_path)
    (tmp_path / "settings.yaml").unlink()
    assert c.post("/api/connect/telegram/code", json=API, headers=H).get_json()["ok"] is True
    r = c.post("/api/connect/telegram/signin", json={"code": "1"}, headers=H)
    assert r.status_code == 200 and sd.read(tmp_path)[0]["accounts"]["telegram"]["enabled"] is True
    (tmp_path / "settings.yaml").write_text("boxes: [\n", encoding="utf-8")
    assert c.post("/api/connect/telegram/code", json=dict(API, phone="010-9999-0000"), headers=H).get_json()["ok"] is True
    Fake.calls.clear()
    r = c.post("/api/connect/telegram/signin", json={"code": "1"}, headers=H)
    assert r.status_code == 409 and "설정 파일" in r.get_json()["error"] and not any(x[0] == "signin" for x in Fake.calls)
    r = c.post("/api/connect/telegram/remove", json={}, headers=H)
    assert r.status_code == 409 and "설정 파일" in r.get_json()["error"]
    assert c.post("/api/connect/gmail/remove", json={"alias": "x"}, headers=H).status_code == 409


# ===== 2026-10-17 맥북에서 처음 실제 텔레그램으로 로그인하다 찾은 결함 =====
def test_empty_code_is_refused_without_touching_the_session(tmp_path):
    """빈 코드로 [로그인] — 진짜 Telethon 은 sign_in(code="") 을 '코드 다시 보내기'로 처리하고 오류 없이 끝난다.
    그것을 성공으로 읽어 인증 안 된 세션을 바꿔 끼웠다(맥북 17:57 — '로그인했다' 뒤 'session not authorized')."""
    L = login(tmp_path)
    L.send_code(**API)
    for code in ("", "   "):
        with pytest.raises(tl.LoginError, match="받은 코드"):
            L.sign_in(code)
    assert [c[0] for c in Fake.calls] == ["code"]                       # 텔레그램에 로그인을 부르지도 않는다
    assert not (tmp_path / tl.READY).exists() and not (tmp_path / tl.API_FILE).exists()


def test_sign_in_that_does_not_authorize_is_not_a_success(tmp_path):
    L = login(tmp_path)
    L.send_code(**API)
    Fake.authorized = False
    with pytest.raises(tl.LoginError, match="로그인이 끝나지 않았어요"):
        L.sign_in("12345")
    assert not (tmp_path / tl.READY).exists() and not (tmp_path / tl.API_FILE).exists()


def test_send_code_says_where_the_code_went(tmp_path):
    """'코드도 안 오는데' — 텔레그램이 코드를 어디로 보냈는지(앱 · 문자 · 이메일 …) 사람에게 말한다."""
    Fake.sent_type = type("SentCodeTypeApp", (), {})()
    assert "'Telegram' 대화방" in login(tmp_path).send_code(**API)
    Fake.sent_type = type("SentCodeTypeSetUpEmailRequired", (), {})()
    assert "로그인 이메일" in login(tmp_path / "b").send_code(**API)
    Fake.sent_type = None
    assert "텔레그램" in login(tmp_path / "c").send_code(**API)       # 모르는 길이어도 빈말이 아니다
