"""메뉴 막대 앱(설계 8) — 맥 맨 위 막대에 `할 일 3` 을 띄우고, 차림표에서 상자 · 전체 목록 · 현황판 · 설정을 기본 브라우저로 연다.
설정 쪽은 2026-10-16 에 줄였다(AI 와 함께 설정): `설정…` 은 설정 한눈에(/setup — 읽기 전용 + AI 에게 줄 말)를 열고, 자세한 설정 화면 ·
엔진 다시 시작 · 기록 폴더 열기는 하위 차림표 `관리` 로.

엔진의 HTTP(`GET /api/status`)만 쓰는 얇은 껍데기 — 엔진 코드 가운데 `urgent_board.paths` 만 불러온다(포트 · 탭5 일련번호 · 열쇠).
엔진이 죽어도 메뉴 막대는 살아서 `확인 불가` 를 보인다. 화면은 웹뷰로 띄우지 않는다(탭5 창이 겪은 메모리 · 굳음을 새로 들이지 않게 ·
Google 로그인은 웹뷰에서 막힌다). 계산(막대 글 · 차림표)은 아래 순수 함수 — AppKit 은 main() 안에서만 부른다.

    .venv/bin/python tools/menubar.py                       # launchd 작업 io.github.wideeye.menubar 가 띄운다
    .venv/bin/python tools/menubar.py --selftest <상태.json|none>   # 막대 · 차림표를 만들어 글을 적고 끝낸다(시험)
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 로 곧바로 — 시스템 프록시로 열쇠가 가지 않게
POLL_SECONDS = 10
TIMEOUT = 5
TAB_STOP = 330           # 상자 줄 · 할 일 줄의 오른쪽 글이 끝나는 자리(pt)
SAFE_LINKS = ("https://mail.google.com/", "tg://", "imessage://", "sms://", "whatsapp://")     # 원문 열기 — 엔진이 내보내는 꼴(web.SAFE_LINK)만


class Item(NamedTuple):
    label: str
    action: str | None = None          # open(arg=길) · task · menu(하위 차림표) · open_link · dismiss · restart · display_start · logs · quit · None(글만)
    arg: str | None = None
    enabled: bool = True
    alert: bool = False                # 주황 글자(고장 · 설정 오류 · 안 읽음 · 기한 임박 할 일)
    children: tuple = ()               # 하위 차림표 (이름, 동작, 인자) — 할 일 줄의 "원문 열기 · 처리됨", 관리의 세 줄


SEP = Item("-", None, None, False)


def title(status: dict | None) -> list[tuple[str, bool]]:
    """막대 글 — (조각, 주황인가). '할 일 N'(2026-10-16) — 숫자는 안 읽은 할 일이나 기한 임박이 있을 때만 주황, '확인 불가'는 주황.
    일부 멈춤 · 설정 오류면 끝에 ' !'."""
    if not status or status.get("blind") == "all":
        return [("확인 불가", True)]
    t = status.get("totals") or {}
    n = int(t.get("tasks") or 0)
    hot = int(t.get("unread") or 0) > 0 or any(x.get("due_soon") and not x.get("old") for x in status.get("tasks") or [])
    out = [("할 일 ", False), (str(n), hot)] if n else [("할 일 없음", False)]
    if status.get("blind") == "some" or status.get("settings_error") or status.get("legacy_config"):
        out.append((" !", True))
    return out


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def display_allowed(env=None) -> bool:
    """탭5 창 작업(tools/kiosk.sh — 이름은 운영 하나뿐)을 이 메뉴 막대가 켜도 되는가. 시험 이름 · 다른 자료 폴더로 뜬 메뉴 막대는
    켜지 않는다 — 켜면 운영 창의 등록이 그 사본을 가리키게 바뀐다(작업 8 검토 3). kiosk.sh 도 같은 규칙으로 거절한다."""
    from tools.launchd import DEFAULT_PREFIX
    from urgent_board import paths
    env = os.environ if env is None else env
    prefix, home = (env.get("URGENT_BOARD_LABEL_PREFIX") or "").strip(), (env.get("URGENT_BOARD_HOME") or "").strip()
    return prefix in ("", DEFAULT_PREFIX) and (not home or Path(home).expanduser() == paths.DEFAULT_HOME)


def _tab5_line(tab5: dict | None, now: float, alive, display_ok: bool = True) -> Item:
    """창이 살아 있는지는 상태 파일의 pid 로 본다 — 맥 화면이 잠든 동안 창은 그림을 보내지 않아 파일이 몇 분씩 묵는다(2026-10-15 실측)."""
    if not tab5 or not alive(tab5.get("pid")):
        if not display_ok:
            return Item("탭5: 이 판(시험 이름 · 다른 자료 폴더)에서는 켜지 않는다", enabled=False)
        return Item("탭5 연결 시작", "display_start")
    if tab5.get("paused"):
        return Item("탭5: 포트를 잠시 놓음(진단 중)", enabled=False)
    if not tab5.get("connected"):
        return Item("탭5: 찾는 중(USB 를 본다)", enabled=False)
    if tab5.get("screen") is False:
        return Item("탭5: 쉼(맥 화면이 잠들었거나 잠김)", enabled=False)
    age = max(0, int(now - float(tab5.get("last_ok_at") or now)))
    return Item(f"탭5: 그림 {age}초 전", enabled=False)


def _card_detail(c: dict) -> str:
    """상자 줄의 오른쪽 — 할 일 수, 없으면 '없음'."""
    n = int(c.get("n") or 0)
    return f"할 일 {n}" if n else "없음"


def _task_item(t: dict) -> Item:
    """할 일 한 줄(●안 읽음 · ○읽음 + 무엇 · 오른쪽에 상자) — 누르면 하위 차림표 "원문 열기 · 처리됨"."""
    return Item(f"{'●' if t.get('unread') else '○'} {t.get('what', '')}\t{t.get('box', '')}", "task", t.get("key"),
                alert=bool(t.get("unread") or (t.get("due_soon") and not t.get("old"))),
                children=(("원문 열기", "open_link", t.get("link") or ""), ("처리됨", "dismiss", t.get("key"))))


MANAGE = (("자세한 설정 화면", "open", "/settings"), ("엔진 다시 시작", "restart", None), ("기록 폴더 열기", "logs", None))


def settings_path(status: dict | None) -> str:
    """'설정…'이 여는 쪽 — 설정 한눈에(/setup). 상태에 setup_left 칸이 없는 엔진(2026-10-16 전 — 메뉴 막대만 먼저 새로 뜬 경우)은 /setup 이
    없으니 예전처럼 자세한 설정 화면."""
    return "/setup" if status is not None and "setup_left" in status else "/settings"


def items(status: dict | None, *, tab5_serial: str, now: float | None = None, alive=pid_alive, display_ok: bool = True) -> list[Item]:
    """차림표 — 머리줄 · 고장 · 설정 오류 · 남은 설정 · 할 일 · 상자 · 열기 · 설정… · 탭5 · 관리(하위) · 끝내기."""
    now = time.time() if now is None else now
    out: list[Item] = []
    setup = settings_path(status)
    if status is None:
        out.append(Item("엔진이 답하지 않는다", enabled=False))
    else:
        t = status.get("totals") or {}
        out.append(Item(f"할 일 {t.get('tasks', 0)} · 안 읽음 {t.get('unread', 0)} · {status.get('checked_at', '')} 확인", enabled=False))
        if status.get("down"):
            out.append(Item("수집 중단: " + " · ".join(status["down"]), enabled=False, alert=True))
        if status.get("book_error"):
            out.append(Item("할 일 장부 오류", enabled=False, alert=True))
        if status.get("settings_error"):
            out.append(Item(str(status["settings_error"])[:120], "open", setup, alert=True))
        left = status.get("setup_left")
        if isinstance(left, int) and left > 0:                     # 필수 설정이 덜 끝났다(처음 깐 사람) — 설정 한눈에가 다음 할 일을 말한다
            out.append(Item(f"설정이 남았다 — {left}단계", "open", "/setup", alert=True))
        if status.get("legacy_config"):
            out.append(Item("옛 설정이 남아 있다 — urgentboard migrate", enabled=False))
        out.append(SEP)
        for task in status.get("tasks") or []:                     # 위 10개(엔진이 차례를 정한다 — 안 읽음 · 임박 먼저)
            out.append(_task_item(task))
        if status.get("tasks"):
            out.append(SEP)
        for c in status.get("cards") or []:
            out.append(Item(f"{c.get('name', '')}\t{_card_detail(c)}", "open", "/list"))
    out += [SEP, Item("전체 목록 열기", "open", "/list"), Item("현황판 열기", "open", "/"), Item("설정…", "open", setup)]
    if tab5_serial:
        out.append(_tab5_line((status or {}).get("tab5"), now, alive, display_ok))
    out += [SEP, Item("관리", "menu", children=MANAGE), Item("끝내기", "quit")]
    return out


def fetch(port: int, token: str, timeout: float = TIMEOUT, opener=DIRECT.open) -> dict | None:
    """GET /api/status — 본문까지 읽고 닫는다(다 읽지 않고 끊으면 Flask 개발 서버가 kqueue 를 쥔다 — 홈 CLAUDE.md). 실패는 None."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status", headers={"X-UB-Token": token} if token else {})
    try:
        with opener(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:            # 401 등 — 오류 본문도 읽고 닫는다
        try:
            e.read()
        finally:
            e.close()
        return None
    except (OSError, ValueError, http.client.HTTPException):     # 엔진이 다시 시작하는 순간 — 본문이 잘림 · HTTP 가 아님
        return None
    try:
        d = json.loads(body)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


STALE_SECONDS = 30


def poll_once(get_light, get_token, fetcher=None) -> tuple[dict, dict | None]:
    """한 번 묻기 — 무엇이 터져도 (설정, None) 으로 돌려준다. 묻는 스레드가 예외로 죽으면 막대에 옛 숫자가 영영 남는다."""
    try:
        light = get_light()
    except Exception:                                            # noqa: BLE001
        light = {"port": 5091, "tab5_usb_serial": ""}
    try:
        return light, (fetcher or fetch)(int(light.get("port") or 5091), get_token())
    except Exception:                                            # noqa: BLE001
        return light, None


def stale(last_done: float, now: float, limit: float = STALE_SECONDS) -> bool:
    return now - last_done > limit


def encode_action(it: Item) -> str:
    """차림표 줄이 들고 있는 동작 — 차림표가 열린 채 다시 만들어져도 누른 줄의 뜻이 바뀌지 않게(차례 번호를 쓰지 않는다)."""
    return f"{it.action or ''}\x1f{it.arg or ''}"


def decode_action(s: str) -> tuple[str | None, str | None]:
    action, _, arg = str(s or "").partition("\x1f")
    return action or None, arg or None


def open_url(port: int, token: str, path: str) -> str:
    """브라우저로 열 주소 — 열쇠가 있으면 쿠키를 심고 그 길로, 없으면 그 길 그대로(잠긴 쪽이 열쇠 칸을 준다)."""
    base = f"http://127.0.0.1:{port}"
    if not token:
        return base + path
    return f"{base}/auth?t={urllib.parse.quote(token, safe='-_')}&next={urllib.parse.quote(path, safe='/')}"


def link_or_list(port: int, token: str, link: str) -> str:
    """할 일의 원문 — 엔진이 내보내는 꼴만 연다. 없거나 모르는 꼴이면 전체 목록."""
    return link if link and link.startswith(SAFE_LINKS) else open_url(port, token, "/list")


def _post_restart(port: int, token: str) -> bool:
    """엔진 다시 시작 — 엔진이 스스로(service.engine 과 상관없이)."""
    return _post(port, token, "/api/restart", {})


def _post(port: int, token: str, path: str, body: dict) -> bool:
    """엔진에 쓰기 요청 — 쓰기 문지기를 지나게 Origin · JSON · 열쇠를 붙인다. 본문까지 읽고 닫는다."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"X-UB-Token": token, "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"})
    try:
        with DIRECT.open(req, timeout=TIMEOUT) as r:
            r.read()
            return True
    except urllib.error.HTTPError as e:
        try:
            e.read()
        finally:
            e.close()
        return False
    except OSError:
        return False


def make_bar():
    """막대 항목 하나(NSStatusItem)와 그것을 다루는 객체 — 앱(NSApplication)은 부르는 쪽이 만든다. show_(상태)로 그린다."""
    import subprocess
    import threading

    from AppKit import (NSApplication, NSColor, NSFont, NSFontAttributeName,
                        NSForegroundColorAttributeName, NSMenu, NSMenuItem, NSMutableAttributedString, NSAttributedString,
                        NSMutableParagraphStyle, NSParagraphStyleAttributeName, NSStatusBar, NSTextAlignmentRight, NSTextTab,
                        NSVariableStatusItemLength)
    from Foundation import NSObject

    from urgent_board import paths

    class Bar(NSObject):
        def setup(self):
            self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
            self.current: list[Item] = []
            self.light = paths.read_light()
            return self

        def show_(self, status):
            """막대 글과 차림표를 다시 만든다(주 스레드) — 보이는 것이 같으면 그대로 둔다(10초마다 새 차림표를 쌓지 않게)."""
            shape = (tuple(title(status)), tuple(items(status, tab5_serial=self.light.get("tab5_usb_serial") or "",
                                                       display_ok=display_allowed())))
            if shape == getattr(self, "shape", None):
                return
            self.shape = shape
            font = NSFont.menuBarFontOfSize_(0)
            text = NSMutableAttributedString.alloc().init()
            for piece, orange in title(status):
                attrs = {NSFontAttributeName: font}
                if orange:
                    attrs[NSForegroundColorAttributeName] = NSColor.systemOrangeColor()
                text.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(piece, attrs))
            self.item.button().setAttributedTitle_(text)
            self.current = list(shape[1])
            menu = NSMenu.alloc().init()
            menu.setAutoenablesItems_(False)
            for i, it in enumerate(self.current):
                if it.label == "-":
                    menu.addItem_(NSMenuItem.separatorItem())
                    continue
                m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(it.label, "pick:" if it.action and not it.children else None, "")
                if it.alert or "\t" in it.label:
                    m.setAttributedTitle_(self.styled_(it))
                m.setRepresentedObject_(encode_action(it))
                m.setTarget_(self)
                m.setEnabled_(bool(it.enabled and it.action))
                if it.children:                                 # 할 일 줄: 원문 열기 · 처리됨 / 관리: 자세한 설정 화면 · 엔진 다시 시작 · 기록 폴더
                    subm = NSMenu.alloc().init()
                    subm.setAutoenablesItems_(False)
                    for label, action, arg in it.children:
                        c = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "pick:", "")
                        c.setRepresentedObject_(encode_action(Item(label, action, arg)))
                        c.setTarget_(self)
                        c.setEnabled_(True)
                        subm.addItem_(c)
                    m.setSubmenu_(subm)
                menu.addItem_(m)
            self.item.setMenu_(menu)

        def styled_(self, it):
            """고장 줄 · 안 읽은 할 일은 시스템 주황, 상자 줄 · 할 일 줄은 왼쪽 글 + 오른쪽 맞춤 보조색(탭 하나)."""
            font = NSFont.menuFontOfSize_(0)
            if "\t" not in it.label:
                return NSAttributedString.alloc().initWithString_attributes_(
                    it.label, {NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.systemOrangeColor()})
            name, detail = it.label.split("\t", 1)
            para = NSMutableParagraphStyle.alloc().init()
            para.setTabStops_([NSTextTab.alloc().initWithTextAlignment_location_options_(NSTextAlignmentRight, TAB_STOP, {})])
            head = {NSFontAttributeName: font, NSParagraphStyleAttributeName: para}
            if it.alert:
                head[NSForegroundColorAttributeName] = NSColor.systemOrangeColor()
            text = NSMutableAttributedString.alloc().initWithString_attributes_(name + "\t", head)
            text.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                detail, {NSFontAttributeName: font, NSParagraphStyleAttributeName: para, NSForegroundColorAttributeName: NSColor.secondaryLabelColor()}))
            return text

        def pick_(self, sender):
            action, arg = decode_action(sender.representedObject())
            it = Item("", action, arg)
            port, token = int(self.light.get("port") or 5091), paths.api_token()
            if it.action == "open":
                subprocess.Popen(["/usr/bin/open", open_url(port, token, it.arg or "/")])
            elif it.action == "open_link":
                subprocess.Popen(["/usr/bin/open", link_or_list(port, token, it.arg or "")])
            elif it.action == "dismiss":
                def done(key=it.arg):
                    _post(port, token, "/api/dismiss", {"key": key})
                    poke = getattr(self, "poke", None)
                    if poke:
                        poke()                                  # 곧바로 다시 물어 차림표에서 뺀다
                threading.Thread(target=done, daemon=True).start()
            elif it.action == "restart":
                threading.Thread(target=_post_restart, args=(port, token), daemon=True).start()
            elif it.action == "display_start" and display_allowed():        # 차림표에 없어도 한 번 더(kiosk.sh 도 거절한다)
                subprocess.Popen([str(ROOT / "tools" / "kiosk.sh"), "start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif it.action == "logs":
                subprocess.Popen(["/usr/bin/open", str(paths.logs_dir())])
            elif it.action == "quit":
                NSApplication.sharedApplication().terminate_(None)      # 종료값 0 — launchd 는 다음 로그인까지 되살리지 않는다

    return Bar.alloc().init().setup()


def main(argv: list[str] | None = None) -> int:
    import threading

    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from PyObjCTools import AppHelper

    from urgent_board import paths

    argv = sys.argv[1:] if argv is None else argv
    selftest = argv[1] if argv[:1] == ["--selftest"] and len(argv) > 1 else None
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)    # Dock 아이콘 없음
    bar = make_bar()

    if selftest is not None:
        status = None if selftest == "none" else json.loads(Path(selftest).read_text(encoding="utf-8"))
        bar.show_(status)
        print("TITLE " + str(bar.item.button().attributedTitle().string()))
        print("ORANGE " + "|".join(p for p, orange in title(status) if orange))
        menu = bar.item.menu()
        for i in range(menu.numberOfItems()):
            m = menu.itemAtIndex_(i)
            print("MENU " + ("-" if m.isSeparatorItem() else str(m.title())))
            if m.hasSubmenu():
                sm = m.submenu()
                print("SUB " + "|".join(str(sm.itemAtIndex_(k).title()) for k in range(sm.numberOfItems())))
        sys.stdout.flush()
        AppHelper.callLater(1.0, AppHelper.stopEventLoop)
        AppHelper.runEventLoop()
        return 0

    done = {"at": time.time()}

    def take(light, status):                                    # 주 스레드 — 화면과 그 화면이 쓰는 값은 여기서만 바꾼다
        bar.light = light
        done["at"] = time.time()
        bar.show_(status)

    def poll():
        while True:
            light, status = poll_once(paths.read_light, paths.api_token)
            AppHelper.callAfter(take, light, status)
            time.sleep(POLL_SECONDS)

    def watch():
        """묻는 스레드가 멈췄거나 답이 30초 넘게 안 오면 옛 숫자 대신 확인 불가(검토 I2 — 죽은 것을 '긴급 없음'으로 보이지 않는다)."""
        if stale(done["at"], time.time()):
            bar.show_(None)
        AppHelper.callLater(POLL_SECONDS, watch)

    def poke():
        """처리됨 뒤 — 10초를 기다리지 않고 한 번 더 묻는다(뒤 스레드에서 불린다)."""
        light, status = poll_once(paths.read_light, paths.api_token)
        AppHelper.callAfter(take, light, status)

    bar.poke = poke
    bar.show_(None)
    threading.Thread(target=poll, name="poll", daemon=True).start()
    AppHelper.callLater(POLL_SECONDS, watch)
    AppHelper.runEventLoop(installInterrupt=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
