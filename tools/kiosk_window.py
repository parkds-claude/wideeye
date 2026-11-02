#!/usr/bin/env python3
"""탭5 현황판 창 — 현황판 페이지를 **화면 밖에서** 그리고, 바뀌었을 때와 60초마다 1280×720 그림(JPEG)으로 떠서 USB-C 직렬로 탭5 에 보낸다
(설계 docs/exec-plans/2026-10-15-tab5-usb-frames-design.md). 창은 어느 화면에도 놓이지 않는다 — Dock · 메뉴 · Cmd+Tab 에도 없다.

그 전(2026-10-13~14)에는 캡처 동글이 만든 세 번째 화면을 꽉 채우는 테두리 없는 창이었다. 동글과 HDMI 선이 뜨겁고
선이 많아(사용자 2026-10-14) 동글을 없앴다. 전체 화면은 지금도 쓰지 않는다(이 맥은 "디스플레이마다 별도의 Spaces"가 꺼져 있다).

그림 보내기:
- 3초마다 페이지에 '지문'(보이는 글 + 상태 이름)을 묻는다. 답한 때가 "페이지가 살아 있던 때"다 — 그림의 나이는 지금에서 그때를 뺀 것이다
  (굳은 페이지의 그림이 새것으로 가지 않게 — 독립 검토 C1). 지문이 바뀌어 두 번 연달아 같으면(그리다 만 중간을 뜨지 않게), 또는 앞의 그림에서
  60초가 지났으면 그림을 뜬다. 한 번에 하나만, 10초 안에 끝나지 않으면 실패로 센다(3번 잇달아면 띠 + 다시 읽기, 6번이면 끝내기).
- 그림은 같은 프로세스의 탭5 연결(tools/tab5_link.py — 일꾼 스레드)이 보낸다. 맥 화면이 잠들거나 맥이 잠기면 탭5 를 끈다(screen 0).
- 10초마다 페이지의 tick() 을 부른다 — 화면 밖 페이지의 타이머를 WebKit 이 늦춰도 자료 당기기가 멈추지 않게(실측 10-15: hidden · 1초 타이머가 3~5초에 한 번).

스스로 돌아오는 장치(그대로):
- 페이지를 못 읽었거나 그리는 프로세스가 죽으면 5초 뒤 다시 읽는다.
- 30초마다 페이지에 "마지막으로 자료를 받은 지 얼마나 됐나"를 묻는다. 우리 화면이 아니거나 2분이 넘었으면 다시 읽는다.
  물어도 15초 안에 답이 없는 일이 두 번 이어지면 먼저 다시 읽어 보고, 네 번 이어지면(스크립트가 굳어 그리는 프로세스가 멎었다) 이 프로세스를
  끝낸다 — launchd 가 새로 띄운다. 보는 사람이 없는 동안(탭5 가 연결돼 있지 않거나 맥의 화면이 잠들었거나 잠겼다)에는 끝내지 않고, 10분에 세 번 넘게
  끝냈으면 한동안 끝내지 않는다. 화면이 깨어나면 그동안 쌓인 놓친 수를 덜어 새로 두 번의 기회를 주고 곧바로 한 번 묻는다.
- 두 번 연달아 답이 없으면 창이 직접 주황 띠 "화면이 멈췄습니다"를 그리고, 그 띠를 그림으로 떠서 보낸다 — 굳은 페이지는 스스로 "끊김"을
  그리지 못한다(낡은 "긴급 없음"이 살아 있는 것처럼 남지 않게). 답이 오면 내린다.
- 5분마다 창에 딸린 WebKit 프로세스(그리기 · 그래픽)와 이 프로세스의 메모리를 잰다. 합이 한도(600MB)를 넘으면 이 프로세스를 끝낸다.
- 탭5 연결 일꾼이 멈췄으면 이 프로세스를 끝낸다(launchd 가 새로 띄운다 — 조용히 반쯤 살아 있지 않게). 탭5 가 없거나 연결이 끊긴 것은 끝낼
  까닭이 아니다(일꾼이 다시 찾고, 탭5 는 3분 뒤 "현황판이 오지 않습니다"를 보인다).
- 서버가 아직 안 떠 있어 페이지를 한 번도 못 읽었으면 빈 화면 대신 "서버를 기다리는 중"을 보인다(그 그림이 간다).
- 이 프로세스 자체가 죽으면 launchd 가 되살린다(tools/kiosk.sh 가 등록한다).

지금 무엇을 그리고 있는지 그림으로 받을 수 있다: `tools/kiosk.sh shot [파일.png]`(탭5 로 가는 그림과 같은 1280×720). 탭5 화면 자체는 `tools/kiosk.sh tab5 -- run shot:<파일>`.

쓰기: .venv/bin/python tools/kiosk_window.py [주소] [--check 초] [--frames-to 폴더] [--fp 초] [--gap 초]     (보통은 tools/kiosk.sh 가 launchd 에 등록해 띄운다)
    --check · --fp · --gap 은 시험에서만 줄인다. --frames-to 는 시험의 가짜 연결이다 — 탭5 대신 그 폴더(임시 폴더 아래만)에 그림을 쓴다.
"""
from __future__ import annotations
import hashlib
import json
import re
import os
import signal
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from urgent_board import paths                                       # noqa: E402 — 자료 폴더 · 설정에서 포트와 탭5 일련번호만(엔진 코드를 불러오지 않는다)
from tools.tab5_link import mono, orient_mode        # noqa: E402 — 잠잔 시간까지 세는 시계(그림의 내용 시각은 연결과 같은 시계로 — 리뷰 8) · 탭5 방향 → 꼴

RETRY_SECONDS = 5.0                 # 서버가 아직 안 떴거나 죽어 있을 때 다시 읽는 간격(실패가 이어지면 1분까지 늘린다)
RETRY_MAX = 60.0
CANCELLED = -999                    # NSURLErrorCancelled — 우리가 새로 읽느라 앞의 읽기가 취소된 것. 고장이 아니다
BACKGROUND = (0x0d, 0x14, 0x24)     # board.css 의 --bg(짙은 남색) — 페이지가 뜨기 전에도 다른 색이 번쩍이지 않게
PAGE_W, PAGE_H = 960, 540           # 페이지가 그려지는 크기(board.css) — 가로
# 세로(2026-10-18 설계 docs/exec-plans/2026-10-18-tab5-portrait-design.md): 탭5 가 세로(orient 1 · 3)면 페이지를 540×960 으로 그려
#   시계 방향 90° 눕혀(세로 화면의 위쪽 → 그림의 오른쪽, D2) 같은 1280×720 그림으로 보낸다. 뒤집힘(180°)은 탭5 가 돌린다
PAGE_SIZE = {"landscape": (960, 540), "portrait": (540, 960)}
# 방향을 바꾼 뒤 첫 그림을 뜨기 전에 묻는 말(D7): 폭 · 세로 미디어 질의 · 보이는 글(빈 페이지면 0 — 빈 화면을 보내지 않는다)
LAYOUT_JS = 'innerWidth + "," + matchMedia("(orientation: portrait)").matches + "," + (document.body && document.body.innerText.trim() ? 1 : 0)'
SETTLE_RETRY, SETTLE_LIMIT = 0.3, 5.0   # 새 꼴이 자리 잡았는지 다시 묻는 간격 · 이만큼 지나도 아니면 평소 길(지문)로
CHECK_SECONDS = 30.0                # 페이지가 살아 있는지 묻는 간격
STUCK_MS = 120_000                  # 마지막으로 자료를 받은 지 이만큼 지났으면 굳은 것으로 보고 다시 읽는다
POLL_SECONDS = 2.0
PROBE_DEADLINE = 15.0               # 페이지에 물은 뒤 이 초 안에 답이 없으면 한 번 놓친 것으로 센다
MISSES_BEFORE_RELOAD = 2            # 연달아 이만큼 놓치면 먼저 다시 읽어 본다
MISSES_BEFORE_EXIT = 4              # 그래도 이만큼 놓치면 굳은 것으로 보고 프로세스를 끝낸다(launchd 가 새로 띄운다)
EXIT_WINDOW = 600.0                 # 이 초 안에
EXIT_LIMIT = 3                      # 이만큼 스스로 끝냈으면 더는 끝내지 않는다(답이 늦을 뿐인데 밤새 다시 뜨기를 되풀이하지 않게)
STALL_TEXT = "화면이 멈췄습니다 — 다시 띄우는 중"       # 굳은 페이지 위에 창이 직접 그리는 띠(페이지는 스스로 그리지 못한다)
STALL_TEXT_HELD = "화면이 멈췄습니다 — 잠시 뒤 다시 띄웁니다"      # 끝내기가 막혀 있을 때(요사이 여러 번 끝냈다) — 지금 "띄우는 중"이라고 하면 거짓이다
MEMORY_SECONDS = 300.0              # 창에 딸린 WebKit 프로세스의 메모리를 재는 간격
MEMORY_LIMIT_MB = 600.0             # 그리기 + 그래픽 프로세스의 합이 이만큼을 넘으면 창을 새로 띄운다(평소 50MB 안팎)
MEMORY_LOG_EVERY = 12               # 이만큼 잴 때마다(1시간) 잰 값을 한 줄 적는다 — 새는지 나중에 기록으로 볼 수 있게
EXIT_MEMORY = 4                     # 메모리 때문에 끝낼 때의 종료값(굳어서 끝낼 때는 3)
EXIT_LINK = 5                       # 탭5 연결 일꾼이 멈춰서 끝낼 때의 종료값
EXIT_SETTINGS = 6                   # 설정의 포트 · 탭5 일련번호가 바뀌어 끝낼 때의 종료값(launchd 가 새 값으로 다시 띄운다)
LINK_CHECK_SECONDS = 30.0           # 탭5 연결 일꾼이 살아 있는지 보는 간격(설계 2-3)
OUT_W, OUT_H = 1280, 720            # 탭5 로 가는 그림의 크기(탭5 패널과 1:1)
FAR = (-30000, -30000)              # 창의 자리 — 어느 화면에도 걸치지 않는다(앞으로 내지도 않는다)
FP_SECONDS = 3.0                    # 페이지에 지문을 묻는 간격
TICK_SECONDS = 10.0                 # 페이지의 tick() 을 부르는 간격(화면 밖 페이지의 타이머가 늦춰져도 자료를 당기게)
FRAME_GAP = 60.0                    # 그대로인 화면도 이만큼마다 그림을 보낸다(시계 · 살아 있음 — 탭5 는 내용이 3분 지나면 LOST)
CAPTURE_TIMEOUT = 10.0              # 그림 뜨기가 이 초 안에 끝나지 않으면 실패로 센다
CAPTURE_FAILS_RELOAD, CAPTURE_FAILS_EXIT = 3, 6     # 잇달아 이만큼 못 뜨면 띠 + 다시 읽기 · 끝내기
JPEG_QUALITY = 0.8                  # 실측 10-15: 1280×720 한 장 114~142KB(품질 0.7~0.85)
MISS_GAP = 0.8                      # 앞의 놓침에서 확인 간격의 이만큼이 지나야 다음 놓침으로 센다(물음이 몰렸을 때 한꺼번에 쌓이지 않게)
STALL_COLOR = (0xff, 0x91, 0x00)    # board.css 의 --neon-orange — "확인 불가"·"연결 끊김"과 같은 색
STALL_FONT = 22.0
# 서버가 아직 없어 페이지를 한 번도 못 읽었을 때 보이는 것(스크립트 없음 — 창이 주기적으로 다시 읽는다)
WAITING_HTML = ('<!doctype html><meta charset="utf-8"><body style="margin:0;background:#0d1424;color:#9aa7bd;'
                'font:500 20px -apple-system,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh">'
                '현황판 서버를 기다리는 중</body>')
# 페이지의 지문: 보이는 글 + 상태 이름(클래스) — 바뀌었는지만 본다(그림을 3초마다 뜨지 않으려는 장치)
PAGE_FP = ('(function(){var b=document.body;if(!b)return "";'
           'var c=Array.prototype.map.call(document.querySelectorAll("[class]"),function(e){return e.className}).join(",");'
           'return b.innerText+"|"+c})()')
TICK_JS = 'typeof tick === "function" ? (tick(), 1) : 0'
# 페이지에 묻는 말: 우리 화면이면 마지막 성공 뒤 지난 밀리초, 아니면 -1
PROBE = '(typeof lastOk === "number" && typeof tick === "function") ? (lastOk > 0 ? Date.now() - lastOk : 0) : -1'


def snapshot_points(mode: str, scale: float) -> float:
    """그림 뜨기 폭(포인트): 탭5 그림의 픽셀 폭(가로 1280 · 세로 720)을 창의 배율로 나눈다(배율 2 → 640 · 360)."""
    return (OUT_W if mode == "landscape" else OUT_H) / (scale or 1.0)


def layout_want(mode: str) -> str:
    """방향을 바꾼 뒤 페이지가 새 꼴로 자리 잡았을 때 LAYOUT_JS 의 답."""
    return f"{PAGE_SIZE[mode][0]},{'true' if mode == 'portrait' else 'false'},1"


def read_orient_file(folder) -> int | None:
    """시험의 가짜 연결: 그림 폴더의 orient 파일(정수 한 줄)을 탭5 의 방향으로. 없거나 못 읽으면 None(가로)."""
    try:
        t = (Path(folder) / "orient").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(t) if t.isdigit() and int(t) <= 3 else None


def needs_reload(age_ms, error) -> bool:
    """페이지에 물은 결과로 다시 읽을지 정한다."""
    if error is not None or age_ms is None:
        return True                                  # 답이 없다 — 그리는 프로세스가 굳었거나 페이지가 깨졌다
    try:
        age = float(age_ms)
    except (TypeError, ValueError):
        return True
    return age < 0 or age > STUCK_MS                 # 우리 화면이 아니거나(읽다 실패한 빈 페이지) 너무 오래 못 받았다


def shot_request(pid: int) -> Path:
    """이 창(pid)에게 그림을 달라고 할 때 경로를 적는 파일. pid 를 넣어 다른 창 프로세스가 가로채지 않게 한다."""
    return paths.state_dir() / f"kiosk-shot.{pid}.request"


def on_miss(misses: int, visible: bool) -> tuple[int, str]:
    """물음에 답이 없었다 → (연달아 놓친 수, 할 일: "wait" · "reload" · "exit").
    먼저 다시 읽어 보고, 그래도 답이 없을 때만 끝낸다. 보이지 않으면(탭5 가 없거나 맥의 화면이 잠들었다) 끝내지 않는다 —
    아무도 보지 않는 동안 다시 뜨기를 되풀이할 까닭이 없고, 잠든 동안에는 답이 늦을 수 있다."""
    n = misses + 1
    if n >= MISSES_BEFORE_EXIT and visible:
        return n, "exit"
    if n == MISSES_BEFORE_RELOAD or (n > MISSES_BEFORE_RELOAD and n % MISSES_BEFORE_RELOAD == 0):
        return n, "reload"
    return n, "wait"


def on_show(misses: int) -> int:
    """숨어 있거나 화면이 잠들어 있다가 다시 보이게 됐다 → 놓친 수. 그동안 쌓인 것으로 곧바로 끝내지 않게, 끝내기까지 새로 두 번이 남도록 던다."""
    return min(misses, MISSES_BEFORE_EXIT - MISSES_BEFORE_RELOAD)


def too_soon(now: float, asked_at: float, deadline: float = PROBE_DEADLINE) -> bool:
    """방금 물었는가 — 앞의 물음의 마감이 오기 전에는 또 묻지 않는다. 물음이 몰리면 답이 잠깐 늦을 뿐인데도
    놓친 수가 한꺼번에 쌓인다(실측: 화면을 깨운 직후 덧물음과 제때의 물음이 5초 간격으로 나가 20초 만에 두 번을 놓쳤다)."""
    return now - asked_at < deadline


def miss_counts(now: float, missed_at: float, check: float = CHECK_SECONDS) -> bool:
    """이 놓침을 셀 것인가 — 앞의 놓침에서 확인 간격만큼(의 MISS_GAP) 지났을 때만. 다시 보일 때의 덧물음이 이어져도
    놓친 수는 제때의 물음과 같은 빠르기로만 쌓인다(검수: 15초마다 쌓일 수 있었다)."""
    return now - missed_at >= check * MISS_GAP


def stall_text(can_exit: bool) -> str:
    """멈춤 띠의 글. 끝내기가 막혀 있으면(요사이 여러 번 끝냈다) "띄우는 중"이라고 하지 않는다."""
    return STALL_TEXT if can_exit else STALL_TEXT_HELD


def should_log_miss(misses: int) -> bool:
    """놓친 것을 기록에 적을 때인가 — 처음 몇 번은 다 적고, 그 뒤로는 열 번에 한 번(굳은 채 밤새 있으면 30초마다 한 줄씩 불어난다)."""
    return misses <= MISSES_BEFORE_EXIT or misses % 10 == 0


def stalled(misses: int) -> bool:
    """페이지가 굳었다고 화면에 알릴 때인가 — 다시 읽기를 해 볼 만큼 연달아 놓쳤을 때부터."""
    return misses >= MISSES_BEFORE_RELOAD


def display_asleep() -> bool:
    """맥의 화면이 잠들어 있는가(화면은 모두 함께 잠든다). 알 수 없으면 깨어 있는 것으로 본다."""
    try:
        import ctypes
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGDisplayIsAsleep.argtypes = [ctypes.c_uint32]
        cg.CGDisplayIsAsleep.restype = ctypes.c_int
        return bool(cg.CGDisplayIsAsleep(cg.CGMainDisplayID()))
    except (OSError, AttributeError):
        return False


def session_off(d) -> bool:
    """세션 사전(CGSessionCopyCurrentDictionary)으로 탭5 를 끌 때인가: 잠금 화면이거나, 이 세션이 콘솔에 없다(빠른 사용자 전환 —
    다른 사람이 앉아 있다. 마지막 검토 중요 2)."""
    return bool(d.get("CGSSessionScreenIsLocked", False)) or d.get("kCGSSessionOnConsoleKey", True) is False


def session_locked() -> bool:
    """맥이 잠겨 있거나 이 세션이 콘솔에 없는가(session_off). 알 수 없으면 잠기지 않은 것으로 본다."""
    try:
        import ctypes
        import objc
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        ptr = cg.CGSessionCopyCurrentDictionary()
        if not ptr:
            return False
        try:
            d = objc.objc_object(c_void_p=ptr)
            return session_off({k: d.get(k) for k in ("CGSSessionScreenIsLocked", "kCGSSessionOnConsoleKey") if d.get(k) is not None})
        finally:
            cf.CFRelease(ptr)                        # Copy 로 받은 것은 놓는다(3초마다 부른다)
    except (OSError, AttributeError, ImportError, TypeError, ValueError):
        return False


def screen_on() -> bool:
    """탭5 를 켤 때인가 — 맥의 화면이 깨어 있고 잠겨 있지 않다(사용자 결정: 맥 화면이 꺼지면 같이 · 잠기면 끈다 — 설계 0)."""
    return not display_asleep() and not session_locked()


def page_ready(result) -> bool:
    """지문의 답이 그릴 만한 페이지인가 — 보이는 글이 있어야 한다(PAGE_FP = 보이는 글 + "|" + 클래스).
    첫 읽기가 끝나기 전의 빈 페이지(about:blank)도 "|" 로 답한다 — 그것을 "살아 있는 새 그림"으로 보내면 탭5 가 흰 화면을 LIVE 로 보인다(리뷰 3)."""
    if not result:
        return False
    return bool(str(result).rsplit("|", 1)[0].strip())


def jpeg_bytes(rep, quality: float = JPEG_QUALITY) -> bytes:
    """비트맵(NSBitmapImageRep)을 JPEG 바이트로. NSData 는 memoryview 로 복사하고 그 view 를 곧바로 놓고, 자동 해제 풀 안에서 만든다 —
    PyObjC 12 에서 `bytes(NSData)` · `NSData.bytes().tobytes()` 는 원본 NSData 를 영영 붙든다(2026-10-15 실측: 그림 한 장마다
    JPEG 하나가 남아 창이 시간당 14MB 씩 늘었다 — 운영 3시간에 Foundation 영역 260개 = 보낸 그림 256장)."""
    import objc
    from AppKit import NSBitmapImageFileTypeJPEG, NSImageCompressionFactor
    with objc.autorelease_pool():                    # 돌려받은 NSData 는 자동 해제 대상 — 이벤트를 받지 않는 이 앱의 콜백에는 비워지는 풀이 없다
        data = rep.representationUsingType_properties_(NSBitmapImageFileTypeJPEG, {NSImageCompressionFactor: quality})
        with memoryview(data) as view:
            out = view.tobytes()
        del data
    return out


def make_link(serial: str, log):
    """탭5 연결(일꾼 스레드)을 만든다 — 시험은 이 함수를 가짜로 바꾼다(tests/kiosk_offscreen.py). 만들기만 하고 띄우지 않는다."""
    from tools.tab5_link import Tab5Link
    return Tab5Link(serial, paths.state_dir(), log=log)


def capture_due(fp: str, prev: str | None, sent: str | None, since_capture: float, gap: float = FRAME_GAP) -> bool:
    """그림을 뜰 때인가: 지문이 보낸 것과 다르고 두 번 연달아 같을 때(그리다 만 중간을 뜨지 않게), 또는 앞의 그림에서 gap 이 지났을 때.
    처음 한 번(앞의 지문이 없을 때)은 다음 지문을 기다린다."""
    if prev is None:
        return False
    return (fp != sent and fp == prev) or since_capture >= gap


def frames_target(text: str) -> Path | None:
    """시험의 가짜 연결이 그림을 쓸 폴더 — 임시 폴더 아래의 있는 폴더만(그림에 메시지 요약이 든다 · logs 도 안 된다)."""
    raw = (text or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute() or not p.is_dir():
        return None
    p, logs = p.resolve(), paths.logs_dir().resolve()
    if p == logs or logs in p.parents:
        return None                                  # 프로젝트가 임시 폴더 아래에 있어도(작업 폴더) logs 는 안 된다
    temps = [r for r in _shot_roots() if r != logs]
    return p if any(p == r or r in p.parents for r in temps) else None


def footprint_mb(pid: int) -> float | None:
    """프로세스가 실제로 차지한 메모리(MB — 활성 상태 보기의 '메모리'와 같은 값. 압축·스왑된 것까지 든다). 알 수 없으면 None.
    `ps` 의 RSS 는 쓰지 않는다: 새어 쌓인 메모리는 곧 압축돼 RSS 에는 안 보인다(2.4GB 가 샌 프로세스의 RSS 가 17MB 였다)."""
    try:
        import ctypes

        class Usage(ctypes.Structure):
            _fields_ = [("uuid", ctypes.c_uint8 * 16), ("values", ctypes.c_uint64 * 40)]
        usage = Usage()
        if ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(int(pid), 2, ctypes.byref(usage)) != 0:
            return None
        return usage.values[7] / 1048576              # ri_phys_footprint
    except (OSError, AttributeError, ValueError, TypeError):
        return None


def over_budget(sizes, limit: float = MEMORY_LIMIT_MB) -> bool:
    """잰 메모리들(MB, 모르는 것은 None)의 합이 한도를 넘었는가. 하나도 못 쟀으면 넘지 않은 것으로 본다."""
    known = [x for x in sizes if x is not None]
    return bool(known) and sum(known) > limit


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                                  # 있지만 남의 것이다
    return True


def dead_requests(paths, me: int, alive=pid_alive) -> list[Path]:
    """치워도 되는 그림 요청 파일: 주인(pid)이 죽은 것과 내 번호의 것(앞서 같은 번호를 쓴 창이 남긴 것). 살아 있는 다른 창의 요청은 두고 간다."""
    out = []
    for p in paths:
        parts = Path(p).name.split(".")
        pid = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else None
        if pid is None or pid == me or not alive(pid):
            out.append(Path(p))
    return out


def may_exit(now: float, history: list[float]) -> bool:
    """스스로 끝내도 되는가 — 요사이(EXIT_WINDOW) 이미 EXIT_LIMIT 번 끝냈으면 안 된다."""
    return sum(1 for t in history if now - t < EXIT_WINDOW) < EXIT_LIMIT


def exit_log() -> Path:
    """스스로 끝낸 때의 기록(자료 폴더의 state/)."""
    return paths.state_dir() / "kiosk-selfexit.json"


def board_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def page_url(port: int, explicit: str) -> str:
    """창이 읽을 주소 — 시험이 준 주소는 그대로, 아니면 접속 열쇠로 쿠키를 심고 현황판으로(열쇠가 아직 없으면 그냥 현황판).
    읽을 때마다 부른다 — 엔진이 열쇠를 처음 만든 뒤에도 따라간다(설계 9)."""
    if explicit:
        return explicit
    t = paths.api_token()
    return f"{board_url(port)}auth?t={t}&next=/" if t else board_url(port)


def masked(url: str) -> str:
    """기록에 남길 주소 — 열쇠를 가린다."""
    return re.sub(r"([?&]t=)[^&]+", r"\1***", url)


def settings_changed(before: dict, now: dict) -> bool:
    """설정 파일의 포트 · 탭5 일련번호가 바뀌었는가 — 바뀌었으면 창이 끝나고 launchd 가 새 값으로 다시 띄운다.
    읽지 못한 것(source "default" — 파일이 깨졌고 last-good 도 없음)은 바뀐 것으로 치지 않는다(탭5 를 끄지 않는다, 검토 I1)."""
    if now.get("source") == "default":
        return False
    return (before.get("port"), before.get("tab5_usb_serial")) != (now.get("port"), now.get("tab5_usb_serial"))


def read_exits() -> list[float]:
    try:
        return [float(x) for x in json.loads(exit_log().read_text(encoding="utf-8"))][-20:]
    except (OSError, ValueError, TypeError):
        return []


def note_exit(now: float) -> None:
    try:
        exit_log().parent.mkdir(parents=True, exist_ok=True)
        exit_log().write_text(json.dumps((read_exits() + [now])[-20:]), encoding="utf-8")
    except OSError:
        pass


def is_board(url) -> bool:
    """읽힌 페이지가 현황판인가(기다리는 안내 화면·빈 페이지가 아닌가)."""
    return bool(url) and str(url).startswith("http")


def _shot_roots() -> list[Path]:
    """그림을 써도 되는 곳: 자료 폴더의 logs 와 임시 폴더들."""
    roots = [paths.logs_dir(), Path("/tmp"), Path("/private/tmp"), Path(tempfile.gettempdir())]
    return [r.resolve() for r in roots if r.exists()]


def shot_target(text: str) -> Path | None:
    """그림을 뜰 파일. 절대 경로의 .png 이고, 그 폴더가 있고, logs 나 임시 폴더 아래일 때만(아무 데나 덮어쓰지 않는다)."""
    raw = (text or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute() or path.suffix.lower() != ".png" or not path.parent.is_dir():
        return None
    parent = path.parent.resolve()
    if not any(parent == r or r in parent.parents for r in _shot_roots()):
        return None
    return path


def is_cancelled(code) -> bool:
    return code == CANCELLED


def next_delay(delay: float) -> float:
    """실패가 이어질 때의 다음 재시도 간격."""
    return min(RETRY_MAX, delay * 2)


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="탭5 현황판 창(화면 밖) — 그림을 떠 USB-C 로 탭5 에 보낸다")
    paths.ensure()                                                      # 자료 폴더(state · logs) — 엔진보다 먼저 떠도 그림 요청 · 기록을 쓸 수 있게
    light = paths.read_light()                                          # 포트 · 탭5 일련번호(설정 파일 — 못 읽으면 기본값)
    ap.add_argument("url", nargs="?", default="")                          # 비우면 설정의 포트 · 접속 열쇠로(page_url) — 시험은 주소를 준다
    ap.add_argument("--check", type=float, default=CHECK_SECONDS)        # 시험에서만 줄인다(마감은 그 절반)
    ap.add_argument("--frames-to", default="")                           # 시험의 가짜 연결: 탭5 대신 이 폴더(임시 폴더 아래만)에 그림을 쓴다
    ap.add_argument("--fp", type=float, default=FP_SECONDS)
    ap.add_argument("--gap", type=float, default=FRAME_GAP)
    a = ap.parse_args(sys.argv[1:])
    explicit, check_s, fp_s, gap_s = a.url, a.check, a.fp, a.gap
    deadline_s = check_s * PROBE_DEADLINE / CHECK_SECONDS
    frames_dir = None
    if a.frames_to:
        frames_dir = frames_target(a.frames_to)
        if frames_dir is None:
            log(f"그림 폴더로 쓸 수 없다(임시 폴더 아래의 있는 폴더만): {a.frames_to}")
            return 2
    # 화면 모듈(pyobjc)은 여기서만 불러온다 — import 만으로는 아무것도 뜨지 않는다
    from AppKit import (NSApplication, NSApplicationActivationPolicyProhibited, NSBackingStoreBuffered, NSBezierPath,
                        NSBitmapImageFileTypeJPEG, NSBitmapImageRep, NSColor, NSCompositingOperationCopy, NSDeviceRGBColorSpace, NSFont,
                        NSFontAttributeName, NSForegroundColorAttributeName, NSGraphicsContext, NSImageCompressionFactor,
                        NSImageInterpolationHigh, NSMutableParagraphStyle, NSParagraphStyleAttributeName, NSPNGFileType,
                        NSTextAlignmentCenter, NSTextField, NSViewMinYMargin, NSViewWidthSizable, NSWindow, NSWindowStyleMaskBorderless,
                        NSWorkspace, NSWorkspaceScreensDidSleepNotification, NSWorkspaceScreensDidWakeNotification)
    from Foundation import (NSActivityUserInitiatedAllowingIdleSystemSleep, NSAffineTransform, NSDistributedNotificationCenter, NSMakeRect, NSMakeSize, NSObject,
                            NSProcessInfo, NSString, NSTimer, NSURL, NSURLRequest, NSZeroRect)
    from WebKit import WKSnapshotConfiguration, WKWebView, WKWebViewConfiguration, WKWebsiteDataStore

    link = None                                                          # 탭5 연결(일꾼 스레드) — 시험의 가짜 연결이면 없다
    serial = light["tab5_usb_serial"] if frames_dir is None else ""
    if frames_dir is None:
        if serial:
            link = make_link(serial, log)
        else:
            log("탭5 일련번호(설정 display.tab5_usb_serial)가 없다 — 그림을 보내지 않는다")

    class Board(NSObject):
        def open(self):
            conf = WKWebViewConfiguration.alloc().init()
            conf.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
            self.mode = orient_mode(self.orientNow())            # 탭5 의 방향(첫 STAT 전에는 연결이 기억한 것 — 설계 D3) → 꼴
            self.gen = self.settled_gen = 0                      # 방향 세대(D7) — 바뀔 때마다 하나 올리고, 새 꼴이 자리 잡으면 settled_gen 이 따라온다
            self.settle_started = 0.0
            self.fresh_next = False                              # 다음 그림 뜨기는 화면 갱신 뒤에(방향을 바꾼 첫 그림)
            page_w, page_h = PAGE_SIZE[self.mode]
            rect = NSMakeRect(0, 0, page_w, page_h)
            self.pending = None                                  # 예약해 둔 재시도(한 번에 하나만 — 겹쳐 쌓이지 않게)
            self.delay = RETRY_SECONDS
            self.loaded = False                                  # 현황판 페이지를 한 번이라도 읽었는가
            self.asked = 0                                       # 페이지에 물은 번호(답이 오면 answered 가 따라온다)
            self.asked_at = time.monotonic() - deadline_s        # 마지막으로 물은 때 — 물음이 몰리지 않게(too_soon)
            self.missed_at = float("-inf")                       # 마지막으로 놓침을 센 때(miss_counts)
            self.measured = 0                                    # 메모리를 잰 횟수(가끔 한 줄 적으려고)
            self.answered = 0
            self.misses = 0
            self.page_alive = 0.0                                # 페이지가 마지막으로 지문에 답한 때(monotonic) — 그림의 나이는 여기서 잰다
            self.fp_prev = None                                  # 바로 앞의 지문
            self.fp_sent = None                                  # 마지막으로 뜬 그림의 지문
            self.fp_waiting = False                              # 지문을 물었는데 아직 답이 없다(굳는 중이면 쌓지 않는다)
            self.fp_asked_at = float("-inf")
            self.captured_at = float("-inf")                     # 마지막으로 그림을 뜬 때
            self.capturing = False                               # 그림을 뜨는 중(한 번에 하나)
            self.capture_token = 0
            self.capture_done = None
            self.capture_fails = 0                               # 잇달아 못 뜬 수(페이지 그림 — 띠 그림은 세지 않는다)
            self.written = 0                                     # 가짜 연결에 쓴 그림 수
            self.request = shot_request(os.getpid())
            for stale in dead_requests(self.request.parent.glob("kiosk-shot.*.request"), os.getpid()):     # 죽은 창이 남긴 요청 파일만 치운다
                try:
                    stale.unlink()
                except OSError:
                    pass
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(FAR[0], FAR[1], page_w, page_h), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
            self.window.setReleasedWhenClosed_(False)
            self.window.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(*(c / 255 for c in BACKGROUND), 1.0))
            self.web = WKWebView.alloc().initWithFrame_configuration_(rect, conf)
            self.web.setNavigationDelegate_(self)
            self.window.setContentView_(self.web)                # 앞으로 내지 않는다 — 어느 화면에도 놓이지 않는다
            self.stall = NSTextField.labelWithString_(STALL_TEXT)                 # 굳은 페이지 위에 창이 직접 그리는 띠 — 평소에는 숨겨 둔다
            self.stall.setFont_(NSFont.boldSystemFontOfSize_(STALL_FONT))
            self.stall.setAlignment_(NSTextAlignmentCenter)
            self.stall.setTextColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(*(c / 255 for c in BACKGROUND), 1.0))
            self.stall.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(*(c / 255 for c in STALL_COLOR), 1.0))
            self.stall.setDrawsBackground_(True)
            self.stall.sizeToFit()
            self.stall.setHidden_(True)
            self.web.addSubview_(self.stall)
            self.layStall()
            ws = NSWorkspace.sharedWorkspace().notificationCenter()
            ws.addObserver_selector_name_object_(self, "screensWoke:", NSWorkspaceScreensDidWakeNotification, None)
            ws.addObserver_selector_name_object_(self, "screensSlept:", NSWorkspaceScreensDidSleepNotification, None)
            dn = NSDistributedNotificationCenter.defaultCenter()            # 잠금 · 풀림을 곧바로(3초 지문을 기다리지 않는다 — 마지막 검토 사소 5)
            dn.addObserver_selector_name_object_(self, "screenLock:", "com.apple.screenIsLocked", None)
            dn.addObserver_selector_name_object_(self, "screenUnlock:", "com.apple.screenIsUnlocked", None)
            if self.mode == "portrait":
                log(f"방향: 세로로 시작(orient={self.orientNow()})")
            self.updateScreen()
            self.reload_(None)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(check_s, self, "check:", None, True)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(POLL_SECONDS, self, "poll:", None, True)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(MEMORY_SECONDS, self, "memory:", None, True)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(fp_s, self, "fp:", None, True)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(TICK_SECONDS, self, "tickPage:", None, True)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(LINK_CHECK_SECONDS, self, "linkCheck:", None, True)

        def orientNow(self):
            """탭5 의 방향(0~3, 모르면 None) — 연결이 STAT 으로 안 것(가짜 연결 · 그림 폴더면 orient 파일)."""
            if frames_dir is not None:
                return read_orient_file(frames_dir)
            return (link.status() or {}).get("orient") if link is not None else None

        def followOrient(self):
            """탭5 의 방향이 꼴(가로 · 세로)을 바꿨으면 따라간다(poll_ 이 2초마다 부른다)."""
            mode = orient_mode(self.orientNow())
            if mode != self.mode:
                self.setMode_(mode)

        def setMode_(self, mode):
            """꼴을 바꾼다(D7): 세대를 올려 앞 세대의 지문 답 · 뜨던 그림을 버리고, 창 · 웹뷰 크기를 바꾼 뒤 새 꼴이 자리 잡기를 기다려 한 장 뜬다.
            웹뷰 크기는 여기서만 바꾼다(그래픽 프로세스 누수 이력 — 저장소 CLAUDE.md)."""
            self.mode, self.gen = mode, self.gen + 1
            self.capture_token += 1                              # 뜨던 그림의 완료는 버린다(옛 꼴 — 기다리면 5초를 넘긴다, 코덱스 중요 2)
            self.capturing, self.capture_done = False, None
            w, h = PAGE_SIZE[mode]
            self.window.setContentSize_(NSMakeSize(w, h))
            self.web.setFrame_(NSMakeRect(0, 0, w, h))
            self.layStall()
            self.fp_prev = self.fp_sent = None
            self.fp_waiting = False
            log(f"방향: {'세로' if mode == 'portrait' else '가로'}로(orient={self.orientNow()})")
            self.settle_started = time.monotonic()
            self.settleMode_(None)

        def settleMode_(self, timer):
            """새 꼴이 자리 잡았나 묻는다(LAYOUT_JS) — 맞으면 화면 갱신 뒤에 한 장 뜬다. 아니면 SETTLE_RETRY 뒤 다시, SETTLE_LIMIT 넘으면 평소 길로."""
            gen = self.gen if timer is None else timer.userInfo()
            if gen != self.gen or self.settled_gen == gen:
                return
            if not self.stall.isHidden():                        # 굳은 페이지 — 띠는 창이 새 꼴로 그린다(다음 지문 차례에 곧바로)
                self.settled_gen, self.captured_at = gen, float("-inf")
                return
            want = layout_want(self.mode)

            def answered(result, error):
                if gen != self.gen or self.settled_gen == gen:
                    return
                if error is None and str(result) == want and not self.capturing:
                    self.settled_gen, self.page_alive, self.fresh_next = gen, mono(), True
                    self.captureFrame_(f"settle-{gen}")
                elif time.monotonic() - self.settle_started < SETTLE_LIMIT:
                    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(SETTLE_RETRY, self, "settleMode:", gen, False)
                else:
                    log(f"방향을 바꾼 뒤 {SETTLE_LIMIT:.0f}초 안에 페이지가 새 꼴로 자리 잡지 않았다(답 {result!r}) — 지문 길로 뜬다")
                    self.settled_gen = gen
            self.web.evaluateJavaScript_completionHandler_(LAYOUT_JS, answered)

        def visible(self):
            """보는 사람이 있을 수 있는가 — 탭5 가 연결돼 있고(가짜 연결이면 늘) 맥의 화면이 깨어 있고 잠기지 않았다."""
            return (link is None or link.connected()) and screen_on()

        def updateScreen(self):
            """탭5 를 켤지 맞춘다(맥 화면 잠듦 · 잠김이면 끈다). 연결이 30초마다 다시 보낸다."""
            on = screen_on()
            if link is not None:
                link.set_screen(on)
            return on

        def linkCheck_(self, _timer):
            """탭5 연결 일꾼이 멈췄으면 끝낸다 — launchd 가 새로 띄운다(조용히 반쯤 살아 있지 않게). 연결이 끊긴 것은 끝낼 까닭이 아니다.
            설정의 포트 · 탭5 일련번호가 바뀌었어도 끝낸다(새 값으로 다시 뜨게 — 설계 4-3)."""
            if link is not None and not link.alive():
                log("탭5 연결 일꾼이 멈췄다 — 프로세스를 끝낸다(launchd 가 새로 띄운다)")
                os._exit(EXIT_LINK)
            if frames_dir is None and settings_changed(light, paths.read_light()):
                log("설정의 포트 · 탭5 일련번호가 바뀌었다 — 탭5 를 끄고 끝낸다(launchd 가 새 값으로 다시 띄운다)")
                if link is not None:
                    link.set_screen(False)
                    link.wait_screen(False, 1.5)
                os._exit(EXIT_SETTINGS)

        def memory_(self, _timer):
            """창에 딸린 WebKit 프로세스(그리기 · 그래픽)와 이 프로세스의 메모리를 재서, 한도를 넘으면 끝낸다 — launchd 가 새로 띄운다."""
            pids = [getattr(self.web, name)() for name in ("_webProcessIdentifier", "_gpuProcessIdentifier") if self.web.respondsToSelector_(name)]
            sizes = [footprint_mb(p) for p in pids + [os.getpid()] if p]
            total = sum(x for x in sizes if x is not None)
            self.measured += 1
            if not over_budget(sizes, MEMORY_LIMIT_MB):
                if self.measured % MEMORY_LOG_EVERY == 0:
                    log(f"그리는 프로세스와 창의 메모리 {total:.0f}MB" + (f" · 탭5 {link.status().get('frames_ok')}장" if link is not None else ""))
                return
            if not may_exit(time.time(), read_exits()):
                log(f"그리는 프로세스와 창의 메모리가 {total:.0f}MB(한도 {MEMORY_LIMIT_MB:.0f}MB) — 요사이 여러 번 끝내서 이번에는 두고 본다")
                return
            log(f"그리는 프로세스와 창의 메모리가 {total:.0f}MB(한도 {MEMORY_LIMIT_MB:.0f}MB) — 프로세스를 끝낸다(launchd 가 새로 띄운다)")
            note_exit(time.time())
            os._exit(EXIT_MEMORY)

        def fp_(self, _timer):
            """지문을 묻는다(3초). 답한 때가 페이지가 살아 있던 때 — 바뀌어서 자리 잡았거나 간격이 지났으면 그림을 뜬다.
            굳어서 띠가 떠 있으면 페이지 대신 창이 그린 띠를 간격마다 보낸다(띠는 지금의 사실이라 내용 시각 = 지금)."""
            self.updateScreen()
            now = mono()
            if not self.stall.isHidden():
                if not self.capturing and now - self.captured_at >= gap_s:
                    self.captureFrame_("stall")
                return
            if self.fp_waiting and time.monotonic() - self.fp_asked_at < max(deadline_s, fp_s):
                return                                           # 앞의 물음에 아직 답이 없다 — 굳는 페이지에 물음을 쌓지 않는다
            self.fp_waiting, self.fp_asked_at = True, time.monotonic()
            gen = self.gen

            def answered(result, error):
                if gen != self.gen:
                    return                                       # 앞 세대(옛 꼴)에 물은 답 — 버린다(D7)
                self.fp_waiting = False
                if error is not None or not page_ready(result):
                    return                                       # 답이 없거나 빈 페이지 — 살아 있는 때로 치지 않는다(흰 화면을 보내지 않는다)
                self.page_alive = mono()
                fp = hashlib.sha1(str(result).encode("utf-8")).hexdigest()
                due = capture_due(fp, self.fp_prev, self.fp_sent, self.page_alive - self.captured_at, gap_s)
                self.fp_prev = fp
                if due and not self.capturing and self.settled_gen == self.gen:     # 꼴을 바꾸는 중에는 settleMode_ 가 뜬다
                    self.captureFrame_(fp)
            self.web.evaluateJavaScript_completionHandler_(PAGE_FP, answered)

        def tickPage_(self, _timer):
            """페이지가 자료를 당기게 한다 — 화면 밖 페이지의 타이머는 WebKit 이 늦춘다(창과의 약속 tick())."""
            if self.loaded and not self.fp_waiting:          # 지문에도 답이 없는(굳는) 페이지에 물음을 쌓지 않는다(리뷰 11)
                self.web.evaluateJavaScript_completionHandler_(TICK_JS, None)

        def captureFrame_(self, fp):
            content = mono() if fp == "stall" else self.page_alive
            gen = self.gen

            def done(rep):
                if gen != self.gen:
                    return                                       # 뜨는 사이 꼴이 바뀌었다 — 옛 꼴의 그림은 보내지 않는다(D7)
                if rep is None:
                    if fp != "stall":
                        self.captureFailed()
                    return
                if fp != "stall":
                    self.capture_fails = 0
                jpeg = jpeg_bytes(rep)
                self.captured_at, self.fp_sent = mono(), fp
                self.deliver_content_(jpeg, content)
            self.grab_(done)

        def captureFailed(self):
            self.capture_fails += 1
            if self.capture_fails == CAPTURE_FAILS_RELOAD:
                log(f"그림을 {CAPTURE_FAILS_RELOAD}번 잇달아 못 떴다 — 멈춤 띠를 띄우고 다시 읽는다")
                self.showStall_(True)
                self.reload_(None)
            elif self.capture_fails >= CAPTURE_FAILS_EXIT and self.visible() and may_exit(time.time(), read_exits()):
                log(f"그림을 {CAPTURE_FAILS_EXIT}번 잇달아 못 떴다 — 프로세스를 끝낸다(launchd 가 새로 띄운다)")
                note_exit(time.time())
                os._exit(3)

        def deliver_content_(self, jpeg, content):
            """뜬 그림을 탭5 연결에 맡긴다(가짜 연결이면 폴더에 쓴다). content = 그 내용이 살아 있던 때(monotonic)."""
            if frames_dir is not None:
                self.written += 1
                tmp = frames_dir / f".frame-{self.written:05d}.jpg"
                tmp.write_bytes(jpeg)
                tmp.replace(frames_dir / f"frame-{self.written:05d}.jpg")
                if self.written == 1:
                    log(f"그림을 폴더에 쓴다: {frames_dir}")
            elif link is not None:
                link.offer(jpeg, content, mode=self.mode)        # 연결은 탭5 의 방향과 다른 꼴의 그림을 보내지 않는다(D7)

        def grab_(self, done):
            """지금 그리는 것을 1280×720 비트맵으로 → done(비트맵 또는 None). 한 번에 하나 — 뜨는 중이면 False(나중에 다시).
            멈춤 띠가 떠 있으면 창이 그린 것(바탕과 띠 — 굳은 페이지는 그림을 돌려주지 않는다)."""
            if self.capturing:
                return False
            self.capture_token += 1
            token = self.capture_token
            self.capturing, self.capture_done = True, done
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(CAPTURE_TIMEOUT, self, "captureTimeout:", token, False)
            if not self.stall.isHidden():
                self.finish_token_(self.stallBitmap(), token)
                return True
            cfg = WKSnapshotConfiguration.alloc().init()
            cfg.setSnapshotWidth_(snapshot_points(self.mode, self.window.backingScaleFactor()))     # 배율 2 면 640 포인트 = 1280 픽셀(실측 10-15) · 세로면 720
            cfg.setAfterScreenUpdates_(self.fresh_next)          # 방향을 바꾼 첫 그림만 화면 갱신 뒤에(D7)
            self.fresh_next = False

            def got(image, _error):
                self.finish_token_(self.bitmap_(image) if image is not None else None, token)
            self.web.takeSnapshotWithConfiguration_completionHandler_(cfg, got)
            return True

        def finish_token_(self, rep, token):
            if token != self.capture_token or not self.capturing:
                return                                           # 늦게 온 완료 — 이미 시간 초과로 셌다
            self.capturing = False
            done, self.capture_done = self.capture_done, None
            if done is not None:
                done(rep)

        def captureTimeout_(self, timer):
            if timer.userInfo() == self.capture_token and self.capturing:
                log(f"그림 뜨기가 {CAPTURE_TIMEOUT:.0f}초 안에 끝나지 않았다")
                self.finish_token_(None, self.capture_token)

        def canvas_(self, draw):
            """정확히 1280×720 픽셀 비트맵(탭5 패널과 1:1)을 만들어 draw(ctx) 로 그린다."""
            rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                None, OUT_W, OUT_H, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
            rep.setSize_(NSMakeSize(OUT_W, OUT_H))
            NSGraphicsContext.saveGraphicsState()
            ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
            NSGraphicsContext.setCurrentContext_(ctx)
            ctx.setImageInterpolation_(NSImageInterpolationHigh)
            draw(ctx)
            ctx.flushGraphics()
            NSGraphicsContext.restoreGraphicsState()
            return rep

        def upright_(self, draw_page):
            """화면 한 장을 1280×720 비트맵으로: draw_page(폭, 높이) 가 화면을 그린다. 세로면 720×1280 화면을 시계 방향 90° 눕혀
            (세로 화면의 위쪽 → 그림의 오른쪽 — 설계 D2, 펌웨어가 orient 1 · 3 에서 바로 세운다) 그린다."""
            portrait = self.mode == "portrait"

            def draw(_ctx):
                if portrait:
                    t = NSAffineTransform.transform()
                    t.translateXBy_yBy_(0, OUT_H)                # 그리는 점에는 회전이 먼저, 그다음 옮김
                    t.rotateByDegrees_(-90)
                    t.concat()
                    draw_page(OUT_H, OUT_W)
                else:
                    draw_page(OUT_W, OUT_H)
            return self.canvas_(draw)

        def bitmap_(self, image):
            """뜬 그림을 정확히 1280×720 픽셀 비트맵으로 다시 그린다(창의 배율이 무엇이든 · 세로면 눕혀서)."""
            return self.upright_(lambda w, h: image.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(0, 0, w, h), NSZeroRect, NSCompositingOperationCopy, 1.0))

        def stallBitmap(self):
            """멈춤 띠 그림: 현황판의 남색 바탕 + 맨 위 주황 띠와 글(창이 직접 그린다 — 굳은 페이지는 그림을 돌려주지 않고,
            보기(view)를 캐시로 뜨면 WKWebView 자리가 흰색으로 나온다 — 리뷰 4: 밤에 탭5 를 흰 화면으로 밝혔다)."""
            k = OUT_W / PAGE_W
            band_h = self.stall.frame().size.height * k
            font = NSFont.boldSystemFontOfSize_(STALL_FONT * k)
            style = NSMutableParagraphStyle.alloc().init()
            style.setAlignment_(NSTextAlignmentCenter)
            ink = NSColor.colorWithSRGBRed_green_blue_alpha_(*(c / 255 for c in BACKGROUND), 1.0)
            attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: ink, NSParagraphStyleAttributeName: style}
            text = NSString.stringWithString_(self.stall.stringValue())

            def draw(w, h):                                     # 화면 한 장(세로면 720×1280 — upright_ 가 눕힌다)
                ink.setFill()
                NSBezierPath.fillRect_(NSMakeRect(0, 0, w, h))
                NSColor.colorWithSRGBRed_green_blue_alpha_(*(c / 255 for c in STALL_COLOR), 1.0).setFill()
                NSBezierPath.fillRect_(NSMakeRect(0, h - band_h, w, band_h))                   # 화면의 맨 위(그리개의 y 는 아래에서 위로)
                text_h = font.ascender() - font.descender()
                text.drawInRect_withAttributes_(NSMakeRect(0, h - band_h + (band_h - text_h) / 2, w, text_h), attrs)
            return self.upright_(draw)

        def poll_(self, _timer):
            """탭5 의 방향을 따라가고(2초), 그림을 달라는 요청이 있으면 지금 그리는 것을 뜬다(탭5 로 가는 것과 같은 1280×720). 뜨는 중이면 다음 차례에."""
            self.followOrient()
            try:
                if not self.request.exists():
                    return
                target = shot_target(self.request.read_text(encoding="utf-8"))
            except OSError:
                return
            if target is None:
                self.request.unlink(missing_ok=True)
                log("그림 요청을 읽었지만 쓸 수 없는 경로다")
                return
            note = " (멈춤 띠가 떠 있다 — 페이지 대신 창이 그린 것)" if not self.stall.isHidden() else ""

            def done(rep):
                ok = rep is not None and bool(rep.representationUsingType_properties_(NSPNGFileType, None).writeToFile_atomically_(str(target), True))
                log(f"화면을 그림으로 {'떴다' if ok else '뜨지 못했다'}{note}: {target}")
            if self.grab_(done):
                self.request.unlink(missing_ok=True)

        def layStall(self):
            """멈춤 띠를 창의 맨 위에 가로로 꽉 차게 놓는다."""
            size = self.web.bounds().size
            h = self.stall.frame().size.height
            y = 0 if self.web.isFlipped() else size.height - h
            self.stall.setFrame_(NSMakeRect(0, y, size.width, h))
            self.stall.setAutoresizingMask_(NSViewWidthSizable | (0 if self.web.isFlipped() else NSViewMinYMargin))

        def showStall_(self, on):
            if bool(self.stall.isHidden()) != on:
                return                                           # 이미 그 상태다
            self.stall.setHidden_(not on)
            log("멈춤 띠를 띄운다" if on else "멈춤 띠를 내린다(페이지가 답했다)")
            if on:
                self.captured_at = float("-inf")                 # 띠를 곧바로 탭5 에 보낸다(다음 지문 차례에)

        def seenAgain(self):
            """맥의 화면이 잠들어 있다가 다시 보이게 됐다: 바로 새 자료를 받게 하고(그 답이 오기 전에 "끊김"이 번쩍이지 않게),
            그동안 쌓인 놓친 수를 덜고, 30초를 기다리지 않고 곧바로 한 번 묻는다."""
            self.web.evaluateJavaScript_completionHandler_(
                'typeof woke === "function" ? (woke(), 1) : (typeof tick === "function" && (tick(), 1))', None)
            self.misses = on_show(self.misses)
            self.check_(None)                                    # 방금 물었으면 check_ 가 건너뛴다

        def screensWoke_(self, _note):
            self.updateScreen()
            if self.loaded:
                log("맥의 화면이 깨어났다")
                self.seenAgain()

        def screensSlept_(self, _note):
            self.updateScreen()

        def screenLock_(self, _note):
            self.updateScreen()

        def screenUnlock_(self, _note):
            self.updateScreen()
            if self.loaded:
                log("맥의 잠금이 풀렸다")
                self.seenAgain()                                 # 잠긴 동안 쌓인 놓친 수를 덜고 곧바로 묻는다(깸과 같다)

        def reload_(self, _timer):
            self.pending = None
            self.web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(page_url(light["port"], explicit))))

        def retry_(self, why):
            if self.pending is not None and self.pending.isValid():
                return                                           # 이미 예약돼 있다
            log(f"다시 읽는다({why}) — {self.delay:.0f}초 뒤")
            self.pending = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(self.delay, self, "reload:", None, False)
            self.delay = next_delay(self.delay)

        def check_(self, _timer):
            """페이지가 살아 있는지 묻는다."""
            if self.pending is not None and self.pending.isValid():
                return                                           # 재시도가 예약돼 있다 — 겹쳐 읽지 않는다
            if too_soon(time.monotonic(), self.asked_at, deadline_s):
                return                                           # 앞의 물음의 마감이 아직 안 왔다(다시 보일 때의 덧물음과 제때의 물음이 몰리지 않게)

            self.asked += 1
            self.asked_at = time.monotonic()
            mine = self.asked

            def answered(result, error):
                self.answered = max(self.answered, mine)
                self.misses = 0
                self.showStall_(False)
                if needs_reload(result, error):
                    log(f"페이지가 낡았다(답 {result!r}, 오류 {'있음' if error is not None else '없음'}) — 다시 읽는다")
                    self.reload_(None)
            self.web.evaluateJavaScript_completionHandler_(PROBE, answered)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(deadline_s, self, "deadline:", mine, False)

        def deadline_(self, timer):
            """물은 지 마감(15초)이 지났다. 답이 없으면 스크립트가 굳어 그리는 프로세스가 멎은 것이다 — 다시 읽기로는 못 푼다."""
            mine = timer.userInfo()
            if self.answered >= mine:
                return
            now = time.monotonic()
            if not miss_counts(now, self.missed_at, check_s):
                return                                           # 앞의 놓침과 너무 가깝다 — 물음이 몰렸을 뿐이다
            self.missed_at = now
            allowed = may_exit(time.time(), read_exits())
            self.misses, todo = on_miss(self.misses, visible=self.visible())
            if todo == "exit" and not allowed:
                todo = "reload"                                  # 요사이 여러 번 끝냈다 — 답이 늦을 뿐일 수 있다. 다시 읽기만 한다
            self.stall.setStringValue_(stall_text(allowed))      # 끝내기가 막혀 있으면 "띄우는 중"이라고 하지 않는다
            self.showStall_(stalled(self.misses))
            note = {"exit": " — 프로세스를 끝낸다(launchd 가 새로 띄운다)", "reload": " — 다시 읽어 본다", "wait": ""}[todo]
            if todo == "exit" or should_log_miss(self.misses):
                log(f"페이지가 물음에 답하지 않는다({self.misses}번째){note}")
            if todo == "exit":
                note_exit(time.time())
                os._exit(3)
            if todo == "reload":
                self.reload_(None)

        def webView_didFinishNavigation_(self, web, _nav):
            url_now = web.URL()
            if not is_board(url_now.absoluteString() if url_now is not None else None):
                return                                           # 기다리는 안내 화면이 떴을 뿐이다
            self.loaded = True
            self.delay = RETRY_SECONDS                           # 읽혔다 — 다음 실패는 다시 짧은 간격부터
            log("페이지를 읽었다")                              # 처음 뜰 때 · 다시 읽을 때 · 화면 코드가 바뀌어 페이지가 스스로 새로 읽을 때

        # 페이지를 못 읽었거나 그리던 프로세스가 죽으면 잠시 뒤 다시 읽는다(읽힌 뒤에는 페이지가 스스로 10초마다 자료를 당긴다)
        def webView_didFailProvisionalNavigation_withError_(self, _web, _nav, err):
            if is_cancelled(err.code() if err is not None else None):
                return
            if not self.loaded:                                  # 한 번도 못 읽었다 — 꺼진 화면처럼 보이지 않게 안내를 띄운다
                self.web.loadHTMLString_baseURL_(WAITING_HTML, None)
            self.retry_("페이지를 읽지 못함")

        def webView_didFailNavigation_withError_(self, _web, _nav, err):
            if not is_cancelled(err.code() if err is not None else None):
                self.retry_("읽다가 끊김")

        def webViewWebContentProcessDidTerminate_(self, _web):
            self.retry_("그리는 프로세스가 죽음")

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)    # Dock · 메뉴 · Cmd+Tab 없음 — 맥 화면 어디에도 나타나지 않는다
    activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(   # 앱 낮잠으로 타이머가 늦춰지지 않게(토큰을 끝까지 붙든다)
        NSActivityUserInitiatedAllowingIdleSystemSleep, "urgent-board: 탭5 현황판 그림")
    log(f"시작: {masked(page_url(light['port'], explicit))} → " + (f"그림 폴더 {frames_dir}" if frames_dir is not None else f"탭5 {serial or '(일련번호 없음)'}"))
    board = Board.alloc().init()
    board.open()
    if link is not None:
        link.start()

    def ending(_signum, _frame):
        """launchd 가 끝내라고 한다(로그아웃 · kiosk.sh restart/stop): 탭5 를 끄고 끝낸다 — 메시지가 든 마지막 그림이
        탭5 에 3분까지 켜져 있지 않게(마지막 검토 중요 2). 파이썬 신호 처리는 다음 타이머 때(2초 안) 돈다."""
        if link is not None:
            link.set_screen(False)
            sent = link.wait_screen(False, 1.5)
            log("끝내라는 신호 — 탭5 를 " + ("껐다" if sent else "끄지 못했다(연결 없음 — 탭5 는 3분 뒤 '오지 않습니다')") + " · 끝낸다")
        else:
            log("끝내라는 신호 — 끝낸다")
        os._exit(0)
    signal.signal(signal.SIGTERM, ending)
    app.run()
    del activity
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
