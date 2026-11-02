"""관리 명령(설계 10) — `bin/urgentboard <명령>` 이 이것을 부른다.

    install [--no-open] [--no-menubar]      자료 폴더 · 예시 설정(없으면) · launchd 작업(엔진은 service.engine: launchd 일 때만 · 메뉴 막대 ·
                                            탭5 창은 일련번호가 있을 때 kiosk.sh) → 설정 한눈에(/setup)를 열고 다음 할 일(wideeye guide next)을 찍는다
    setup <명령> …                           AI 와 함께 설정(tools/setup_cli.py — 차례는 setup guide, 안내서 SETUP.md)
    guide <명령> …                           설정 길(tools/guide_cli.py — next · check · confirm · profile · status · marker)
    uninstall                               작업을 내리고 plist 를 ~/Library/LaunchAgents/_disabled/ 로(자료 폴더는 남긴다)
    start | stop | restart [engine|menubar|display|all]
    status                                  떠 있는지 · 건수
    doctor [--bundle <zip>]                 읽기만 해서 하나씩 점검 — 주의가 하나라도 있으면 종료값 1. --bundle 은 가린 진단 묶음
    migrate …                               옛 config/ 를 자료 폴더로(tools/migrate.py 와 같은 인자)
    open [setup|settings|list|board]        기본 브라우저로(열쇠로 쿠키를 심는 /auth 주소)

열쇠는 화면 · 기록에 내지 않는다(`open` 에 넘길 때만). 엔진이 바깥(다른 감시 프로그램)에서 뜨면(`service.engine: external`)
엔진을 launchd 에 올리지 않고, 다시 시작은 엔진에게 `POST /api/restart` 로 맡긴다.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import launchd as ld                     # noqa: E402
from tools.menubar import DIRECT, open_url           # noqa: E402
from urgent_board import paths, settings_doc         # noqa: E402

KIOSK = ROOT / "tools" / "kiosk.sh"
KIOSK_LABEL = "io.github.wideeye.kiosk"
PAGES = {"setup": "/setup", "settings": "/settings", "list": "/list", "board": "/"}


@dataclass
class Ctx:
    home: Path = field(default_factory=paths.home)
    launchd: object = field(default_factory=ld.Launchd)
    run: Callable = subprocess.run
    opener: Callable = DIRECT.open                       # 프록시를 거치지 않는다
    say: Callable = print
    sleep: Callable = time.sleep
    python: str = sys.executable
    # 탭5 창(kiosk.sh)은 운영 이름 하나뿐이다 — 시험 이름으로 돌 때는 건드리지 않는다(2026-10-15 실제 시험의 uninstall 이 운영 창을 내렸다)
    kiosk: bool = field(default_factory=lambda: ld.PREFIX == ld.DEFAULT_PREFIX)


# ---- 작은 도구 -------------------------------------------------------------------------------------------------------

def _doc(ctx: Ctx) -> dict:
    try:
        return settings_doc.read(ctx.home)[0]
    except (OSError, settings_doc.SettingsError):
        return {}


def _engine_mode(ctx: Ctx) -> str:
    """"launchd" · "external" · "unknown"(설정도 last-good 도 못 읽음 — 엔진은 건드리지 않는다). 설정이 깨져도 엔진은 last-good 으로
    돌므로 같은 차례로 읽는다 — 깨진 설정을 '엔진은 launchd' 로 오해해 감시를 둘로 만들지 않게(묶음 3 검토 I6)."""
    for f in (paths.settings_file(ctx.home), paths.last_good_file(ctx.home)):
        try:
            doc = settings_doc.parse(f.read_bytes())
        except FileNotFoundError:
            if f == paths.settings_file(ctx.home) and not paths.last_good_file(ctx.home).exists():
                return "launchd"                                # 아직 설정이 없다(처음 깔기 전) — 기본값
            continue
        except (OSError, settings_doc.SettingsError):
            continue
        return str((doc.get("service") or {}).get("engine") or "launchd")
    return "unknown"


def _display_used(ctx: Ctx) -> bool:
    """탭5 를 쓰는 맥인가 — 설정에 일련번호가 있거나 이미 창 작업이 있다."""
    return bool(_serial(ctx)) or ctx.launchd.path(KIOSK_LABEL).exists()


def _port(ctx: Ctx) -> int:
    return int(paths.read_light(ctx.home)["port"])


def _serial(ctx: Ctx) -> str:
    return str(paths.read_light(ctx.home).get("tab5_usb_serial") or "")


def http(ctx: Ctx, method: str, path: str, token: str = "", timeout: float = 5) -> tuple[int, dict | None]:
    """엔진에 묻는다 — 본문까지 읽고 닫는다. (상태 코드, JSON) · 닿지 않으면 (0, None)."""
    port = _port(ctx)
    headers = {"X-UB-Token": token} if token else {}
    data = None
    if method != "GET":
        headers.update({"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"})
        data = b"{}"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers)
    try:
        with ctx.opener(req, timeout=timeout) as r:
            body, code = r.read(), getattr(r, "status", 200)
    except urllib.error.HTTPError as e:
        try:
            e.read()
        finally:
            e.close()
        return e.code, None
    except (OSError, ValueError):
        return 0, None
    try:
        return code, json.loads(body)
    except ValueError:
        return code, None


def _plist_engine(ctx: Ctx) -> bytes:
    return ld.plist(ld.ENGINE, [ctx.python, str(ROOT / "run_board.py")], workdir=str(ROOT), logs=paths.logs_dir(ctx.home),
                    keepalive="always", home=_custom_home(ctx))


def _plist_menubar(ctx: Ctx) -> bytes:
    return ld.plist(ld.MENUBAR, [ctx.python, str(ROOT / "tools" / "menubar.py")], workdir=str(ROOT), logs=paths.logs_dir(ctx.home),
                    keepalive="crash", gui=True, home=_custom_home(ctx))


def _custom_home(ctx: Ctx) -> Path | None:
    """기본 자료 폴더가 아니면 작업에 넘긴다(시험 · 여러 판)."""
    return ctx.home if Path(ctx.home) != paths.DEFAULT_HOME else None


def _kiosk(ctx: Ctx, *args: str, **kw):
    """tools/kiosk.sh 를 부른다 — 시험 이름이면 부르지 않고 0(아무것도 안 함)."""
    if not ctx.kiosk:
        ctx.say(f"탭5 창: 시험 이름({ld.PREFIX})이라 건드리지 않는다")
        return subprocess.CompletedProcess([str(KIOSK), *args], 0, "", "")
    return ctx.run([str(KIOSK), *args], **kw)


def _wait_engine(ctx: Ctx, seconds: float = 20.0) -> bool:
    end = time.monotonic() + seconds
    while True:
        if http(ctx, "GET", "/healthz")[0] == 200:
            return True
        if time.monotonic() >= end:
            return False
        ctx.sleep(1)


# ---- 명령 ------------------------------------------------------------------------------------------------------------

def install(ctx: Ctx, open_browser: bool = True, menubar: bool = True) -> int:
    paths.ensure(ctx.home)
    if not paths.settings_file(ctx.home).exists():
        settings_doc.save(ctx.home, settings_doc.example(), None)
        ctx.say(f"예시 설정을 만들었다: {paths.settings_file(ctx.home)}")
    mode = _engine_mode(ctx)
    if mode == "launchd":
        ctx.say(f"엔진: {ctx.launchd.install(ld.ENGINE, _plist_engine(ctx))}")
    elif mode == "unknown":
        ctx.say("엔진: 설정을 읽지 못해 엔진은 건드리지 않는다 — 설정 파일을 고친 뒤 다시")
    else:
        ctx.say("엔진: 바깥에서 띄운다(service.engine: external) — 건드리지 않는다")
    if menubar:
        ctx.say(f"메뉴 막대: {ctx.launchd.install(ld.MENUBAR, _plist_menubar(ctx))}")
    if _serial(ctx):
        ctx.say("탭5 창: " + ("떠 있다" if _kiosk(ctx, "start").returncode == 0 else "띄우지 못했다 — tools/kiosk.sh doctor"))
    # 끝의 말은 공개판 2판의 설정 길(GUIDE.md · wideeye guide next) — 1판의 'AI 에게 붙여 넣을 말(setup guide)'과 '지금 허락하고 다시 시작'은
    # 설치 스크립트의 끝 줄과 어긋났다(작업 8 검토 1). 허락은 길의 permit 단계가 그때 알려 준다.
    ctx.say(f"나중에 전체 디스크 접근 권한에 넣을 파일(설정 길의 허락 단계가 알려 준다): {os.path.realpath(ctx.python)}")
    ctx.say("")
    ctx.say(f"다음: AI 에게 '{ROOT / 'bin' / 'wideeye'} guide next' 를 부르라고 한다(AI 가 읽을 안내서: {ROOT / 'GUIDE.md'})")
    if open_browser:
        if not _wait_engine(ctx):
            ctx.say("엔진이 아직 답하지 않는다 — urgentboard doctor")
            return 1
        return open_page(ctx, "setup")
    return 0


def uninstall(ctx: Ctx) -> int:
    for label in (ld.MENUBAR, ld.ENGINE):
        moved = ctx.launchd.remove(label)
        ctx.say(f"{label}: " + (f"내렸다 — {moved}" if moved else "등록 없음"))
    if _serial(ctx) or ctx.launchd.path(KIOSK_LABEL).exists():
        _kiosk(ctx, "uninstall")
    ctx.say(f"자료 폴더는 남겼다: {ctx.home}")
    return 0


def control(ctx: Ctx, verb: str, what: str = "all") -> int:
    names = ["engine", "menubar", "display"] if what == "all" else [what]
    rc = 0
    for name in names:
        if name == "engine":
            mode = _engine_mode(ctx)
            if mode == "unknown":
                ctx.say("엔진: 설정을 읽지 못해 엔진은 건드리지 않는다 — 설정 파일을 고친 뒤 다시")
                rc = 1
                continue
            if mode != "launchd":
                if verb == "restart":
                    code, _ = http(ctx, "POST", "/api/restart", paths.api_token(ctx.home))
                    ok = 200 <= code < 300                                  # 엔진은 202(받았다 · 곧 다시 시작)
                    ctx.say("엔진: 다시 시작한다" if ok else f"엔진: 다시 시작을 받지 않았다({code or '답 없음'})")
                    rc |= not ok
                else:
                    ctx.say("엔진: 바깥에서 띄운다(service.engine: external) — 그쪽에서 켜고 끈다")
                continue
            label, make = ld.ENGINE, _plist_engine
        elif name == "menubar":
            label, make = ld.MENUBAR, _plist_menubar
        elif name == "display":
            if not _display_used(ctx):
                ctx.say("탭5 창: 탭5 를 쓰지 않는다(설정에 일련번호가 없다)")
                continue
            try:
                rc |= _kiosk(ctx, verb).returncode != 0
            except PermissionError:
                ctx.say(f"{name}: 권한이 막혔다 — AI 의 샌드박스일 수 있다. 이 명령은 사람이 터미널에서 직접 돌린다")
                rc = 1
            continue
        else:
            ctx.say(f"모르는 것: {name}")
            return 2
        try:
            if verb == "stop":
                ctx.launchd.stop(label)
            elif verb == "restart" and ctx.launchd.loaded(label):
                ctx.launchd.kick(label)
            else:
                ctx.launchd.install(label, make(ctx))
        except PermissionError:                                 # AI 의 샌드박스가 ~/Library/LaunchAgents 쓰기를 막았다(2026-10-16 Codex 시험)
            ctx.say(f"{name}: 권한이 막혔다 — AI 의 샌드박스일 수 있다. 이 명령은 사람이 터미널에서 직접 돌린다")
            rc = 1
            continue
        ctx.say(f"{name}: {verb}")
    return int(bool(rc))


NO_OPEN = "URGENT_BOARD_NO_OPEN"          # 1 이면 창을 띄우지 않고 갈 곳만 말한다(원격 · 화면 없는 세션 · AI 시험 — 열쇠는 찍지 않는다)


def opens(ctx: Ctx, target: str, where: str = "") -> int:
    """브라우저 · 시스템 설정 · Finder 를 연다. NO_OPEN 이면 열지 않고 갈 곳만(열쇠가 든 주소는 찍지 않는다)."""
    if os.environ.get(NO_OPEN):
        ctx.say(f"창을 열지 않았다({NO_OPEN}) — 사람이 직접 연다: {where or target}")
        return 0
    return ctx.run(["/usr/bin/open", target]).returncode


def open_page(ctx: Ctx, page: str = "settings") -> int:
    if page not in PAGES:
        ctx.say("열 수 있는 것: " + " · ".join(PAGES))
        return 2
    return opens(ctx, open_url(_port(ctx), paths.api_token(ctx.home), PAGES[page]),
                 f"http://127.0.0.1:{_port(ctx)}{PAGES[page]} (열쇠가 없다면 메뉴 막대에서 연다)")


def status(ctx: Ctx) -> int:
    code, st = http(ctx, "GET", "/api/status", paths.api_token(ctx.home))
    if code == 200 and st:
        t = st.get("totals") or {}
        ctx.say(f"엔진: 답함(포트 {_port(ctx)}) · 할 일 {t.get('tasks', 0)} · 안 읽음 {t.get('unread', 0)} · {st.get('checked_at', '')} 확인")
        if st.get("down"):
            ctx.say("수집 중단: " + " · ".join(st["down"]))
        if st.get("settings_error"):
            ctx.say(str(st["settings_error"]))
    else:
        ctx.say(f"엔진: 답하지 않는다(포트 {_port(ctx)}, {code or '연결 안 됨'})")
    for name, label in (("엔진 작업", ld.ENGINE), ("메뉴 막대", ld.MENUBAR)):
        pid = ctx.launchd.pid(label) if ctx.launchd.loaded(label) else None
        ctx.say(f"{name}: " + (f"떠 있다(pid {pid})" if pid else "올라가 있다(멈춤)" if ctx.launchd.loaded(label) else "등록 없음"))
    if _serial(ctx):
        _kiosk(ctx, "status")
    return 0 if code == 200 else 1


# ---- 점검 · 진단 묶음 -------------------------------------------------------------------------------------------------

def checks(ctx: Ctx) -> list[tuple[str, str]]:
    """(좋음 | 주의 | 사람이 볼 것, 글) — 읽기만 한다."""
    out: list[tuple[str, str]] = []
    good, warn, note = (lambda t: out.append(("좋음", t))), (lambda t: out.append(("주의", t))), (lambda t: out.append(("사람이 볼 것", t)))
    h = Path(ctx.home)
    if not h.exists():
        warn(f"자료 폴더가 없다({h}) — urgentboard install")
    else:
        mode = h.stat().st_mode & 0o777
        (good if mode == 0o700 else warn)(f"자료 폴더 {h} 권한 {oct(mode)}" + ("" if mode == 0o700 else " — 0700 이어야 한다"))
    doc = {}
    try:
        doc, _ = settings_doc.read(h)
        from urgent_board import config
        errs = [p for p in config.problems(doc) if p.level == "error"]
        warns = [p for p in config.problems(doc) if p.level != "error"]
        if errs:
            warn(f"설정 파일 오류 {len(errs)}개 — 첫 문제: {errs[0].path} {errs[0].message}")
        else:
            good("설정 파일에 오류가 없다" + (f"(알림 {len(warns)}개)" if warns else ""))
    except FileNotFoundError:
        warn("설정 파일이 없다 — urgentboard install")
    except (OSError, settings_doc.SettingsError) as e:
        warn(f"설정 파일을 읽지 못한다: {str(e)[:120]}")
    (good if paths.last_good_file(h).exists() else note)("마지막으로 잘 돈 설정(last-good)이 " + ("있다" if paths.last_good_file(h).exists() else "아직 없다 — 엔진이 60초 넘게 돌면 생긴다"))
    token = paths.api_token(h)
    (good if token else warn)("접속 열쇠 파일이 " + ("있다" if token else "없다 — 엔진이 처음 뜰 때 만든다"))
    mode = _engine_mode(ctx)
    if mode == "launchd":
        _job(ctx, ld.ENGINE, _plist_engine(ctx), "엔진 작업", good, warn)
    elif mode == "unknown":
        warn("설정도 last-good 도 읽지 못해 엔진이 어떻게 뜨는지 모른다")
    else:
        note("엔진은 바깥에서 띄운다(service.engine: external) — 그쪽의 감시가 되살린다")
    _job(ctx, ld.MENUBAR, _plist_menubar(ctx), "메뉴 막대 작업", good, warn)
    code, st = http(ctx, "GET", "/healthz")
    if code != 200:
        warn(f"엔진이 포트 {_port(ctx)} 에서 답하지 않는다")
    else:
        good(f"엔진이 포트 {_port(ctx)} 에서 답한다")
        _engine_checks(ctx, doc, token, good, warn)
    if _serial(ctx):
        r = _kiosk(ctx, "doctor", capture_output=True, text=True)
        (good if r.returncode == 0 else warn)("탭5 창 점검(tools/kiosk.sh doctor) " + ("통과" if r.returncode == 0 else "— 주의가 있다, 그 명령을 직접 본다"))
    note("시스템 설정 > 일반 > 로그인 항목 및 확장 프로그램 > '백그라운드에서 허용'의 python3 가 켜져 있어야 로그인 때 뜬다")
    return out


def _job(ctx, label, want, name, good, warn):
    p = ctx.launchd.path(label)
    if not p.exists():
        warn(f"{name}이 등록돼 있지 않다 — urgentboard install")
    elif p.read_bytes() != want:
        warn(f"{name}의 등록 파일이 지금 코드와 다르다 — urgentboard install")
    elif not ctx.launchd.loaded(label):
        warn(f"{name}이 올라가 있지 않다 — urgentboard start")
    elif ctx.launchd.pid(label) is None:
        warn(f"{name}이 올라가 있지만 돌지 않는다('끝내기' 뒤) — urgentboard start")
    else:
        good(f"{name}이 떠 있다")


def _engine_checks(ctx, doc, token, good, warn):
    """엔진에게 묻는 것 — 권한은 엔진 프로세스의 것이어야 맞다(터미널의 권한이 아니라)."""
    code, c = http(ctx, "GET", "/api/connect", token, timeout=30)
    if code != 200 or not c:
        warn(f"연결 점검을 받지 못했다({code or '답 없음'})")
        return
    acc = doc.get("accounts") or {}
    on = lambda k: bool((acc.get(k) or {}).get("enabled", k in ("calls", "teams")))      # noqa: E731 — 전화 · 팀즈는 적지 않았으면 켬(2026-10-17)
    src = c.get("sources") or {}
    for g in src.get("gmail") or []:
        st = g.get("state")
        (warn if st in ("stopped", "reconnect") else good)(f"메일 {g.get('email', '')}: {st}" + (f" — {g.get('note', '')}" if st in ("stopped", "reconnect") else ""))
    for k, name in (("telegram", "텔레그램"), ("kakao", "카톡"), ("imessage", "문자"), ("whatsapp", "왓츠앱"), ("calls", "부재중 전화"), ("teams", "팀즈 알림")):
        s = src.get(k) or {}
        if on(k):
            (warn if s.get("state") in ("stopped", "reconnect") else good)(f"{name}: {s.get('state', '?')}" + (f" — {s.get('note', '')}" if s.get("note") and s.get("state") != "ok" else ""))
    if on("imessage"):
        m = c.get("messages") or {}
        (good if m.get("ok") else warn)("엔진이 메시지 앱 자료를 " + ("읽는다" if m.get("ok") else f"못 읽는다 — 전체 디스크 접근 권한: {c.get('python', '')}"))
    if on("whatsapp"):                                                       # 왓츠앱(2026-10-28) — 권한 · 자료 · 앱이 켜져 있는지
        w = c.get("whatsapp") or {}
        if w.get("ok") and w.get("running"):
            good("엔진이 왓츠앱 자료를 읽는다")
        elif w.get("ok"):
            warn("왓츠앱 앱이 꺼져 있다 — 켜 두어야 새 글이 보인다")
        elif "권한" in str(w.get("why", "")):
            warn(f"엔진이 왓츠앱 자료를 못 읽는다 — 전체 디스크 접근 권한: {c.get('python', '')}")
        else:
            warn("왓츠앱 맥 앱의 자료가 없다(쓰지 않으면 setup source whatsapp off)")
    for k, name, eul in (("calls", "통화 기록", "을"), ("teams", "맥 알림 센터", "를")):   # 이 맥의 자료 — 없으면 이 맥에서 안 쓰는 것(멈춤이 아니다). 조사는 이름마다
        if on(k):
            p = c.get(k) or {}
            if p.get("ok"):
                good(f"엔진이 {name}{eul} 읽는다")
            elif "권한" in str(p.get("why", "")):
                warn(f"엔진이 {name}{eul} 못 읽는다 — 전체 디스크 접근 권한: {c.get('python', '')}")
            else:
                good(f"{name}: 이 맥에 자료가 없다(쓰지 않으면 setup source {k} off)")
    if on("kakao"):
        k = c.get("kakaocli") or {}
        (good if k.get("found") else warn)("kakaocli " + ("있다" if k.get("found") else "가 없다 — brew install silver-flight-group/tap/kakaocli"))
    if on("telegram"):
        t = c.get("telegram") or {}
        (good if t.get("session") else warn)("텔레그램 세션이 " + ("있다" if t.get("session") else "없다 — 설정 화면 > 연결 > 텔레그램"))
    if (doc.get("summary") or {}).get("enabled"):
        cl = c.get("claude") or {}
        (good if cl.get("found") else warn)("요약: claude 명령이 " + ("있다" if cl.get("found") else "없다 — Claude Code 를 설치하고 로그인"))


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE_RE = re.compile(r"(\+82[\s-]?|0)1[016789][\s-]?\d{3,4}[\s-]?\d{4}")
KEEP_KEYS = {"key", "color", "active", "enabled", "watch", "source", "engine", "model", "sources", "holidays", "start", "end", "lunch",
             "extra_holidays", "extra_workdays", "id", "chat_id", "port", "tab5_usb_serial", "version", "fill_with_waiting",
             "claude_sessions", "judge"}


def redact(v, key: str | None = None):
    """진단 묶음의 설정 — 허락한 칸(색 · 켬/끔 · 출처 · 방 번호 · 시각 …)의 값만 남기고 다른 글은 길이만(이름 · 별칭 · 주소 · 번호 · 낱말)."""
    if isinstance(v, dict):
        return {k: redact(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [redact(x, key) for x in v]
    if isinstance(v, str) and key not in KEEP_KEYS:
        return f"<글 {len(v)}자>"
    return v


def mask(text: str) -> str:
    text = re.sub(r"([?&]t=)[^&\s]+", r"\1***", text)
    return PHONE_RE.sub("<전화>", EMAIL_RE.sub("<메일>", text))


def doctor(ctx: Ctx, bundle: Path | None = None) -> int:
    rows = checks(ctx)
    lines = [f"{lvl:<6} {text}" for lvl, text in rows]
    for line in lines:
        ctx.say(line)
    if bundle:
        _bundle(ctx, Path(bundle), "\n".join(lines) + "\n")
        ctx.say(f"진단 묶음: {bundle}(이름 · 주소 · 번호는 가렸다 — 보내기 전에 한 번 열어 본다)")
    return 1 if any(lvl == "주의" for lvl, _ in rows) else 0


def _bundle(ctx: Ctx, dest: Path, report: str) -> None:
    import yaml
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doctor.txt", mask(report))
        z.writestr("settings.yaml", mask(yaml.safe_dump(redact(_doc(ctx)), allow_unicode=True, sort_keys=False)))
        for name in ("board.log", "kiosk.log"):
            p = paths.logs_dir(ctx.home) / name
            if p.exists():
                tail = p.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
                z.writestr(f"logs/{name}", mask("\n".join(tail) + "\n"))


# ---- 진입점 ----------------------------------------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    """관리 명령의 인자 — main 과 안내 글 시험(tests/test_guide_docs.check_command)이 함께 쓴다. setup · guide · migrate 는 자리만 있고
    뒤의 인자는 그 명령의 파서가 읽는다(main 이 먼저 넘긴다)."""
    ap = argparse.ArgumentParser(prog="urgentboard", description="긴급현황판 관리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--no-open", action="store_true")
    i.add_argument("--no-menubar", action="store_true")
    sub.add_parser("uninstall")
    for verb in ("start", "stop", "restart"):
        sub.add_parser(verb).add_argument("what", nargs="?", default="all", choices=("engine", "menubar", "display", "all"))
    sub.add_parser("status")
    d = sub.add_parser("doctor")
    d.add_argument("--bundle", type=Path, default=None)
    o = sub.add_parser("open")
    o.add_argument("page", nargs="?", default="setup", choices=tuple(PAGES))
    sub.add_parser("migrate", help="옛 config/ 옮기기 — tools/migrate.py 의 인자")
    sub.add_parser("setup", help="AI 와 함께 설정 — urgentboard setup guide")
    sub.add_parser("guide", help="설정 길 — wideeye guide next")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["migrate"]:
        from tools import migrate
        return migrate.main(argv[1:])
    if argv[:1] == ["setup"]:
        from tools import setup_cli
        return setup_cli.main(argv[1:])
    if argv[:1] == ["guide"]:
        from tools import guide_cli
        return guide_cli.main(argv[1:])
    a = parser().parse_args(argv)
    ctx = Ctx()
    if a.cmd == "install":
        return install(ctx, open_browser=not a.no_open, menubar=not a.no_menubar)
    if a.cmd == "uninstall":
        return uninstall(ctx)
    if a.cmd in ("start", "stop", "restart"):
        return control(ctx, a.cmd, a.what)
    if a.cmd == "status":
        return status(ctx)
    if a.cmd == "doctor":
        return doctor(ctx, a.bundle)
    if a.cmd == "open":
        return open_page(ctx, a.page)
    return 2


if __name__ == "__main__":
    sys.exit(main())
