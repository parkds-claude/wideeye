"""시험용 도우미(시험 모듈이 아니다): 탭5 창을 시험 설정으로 띄운다 — 그림은 탭5 대신 건네받은 폴더에 쓴다(`--frames-to`).

창은 늘 화면 밖이라 어느 화면에도 아무것도 뜨지 않는다(2026-10-15~). 여기서는 맥의 화면이 잠들었는지를 정해 주고(잠금은 늘 풀림),
스스로 끝낸 기록은 실제 `state/` 가 아니라 건네받은 파일에 적는다.

쓰기: python tests/kiosk_offscreen.py <주소> <확인 간격 초> <awake|asleep|sleep-then-wake> <스스로 끝낸 기록 파일> <그림 폴더> [tiny-memory|fast]
    tiny-memory: 메모리 한도를 1MB 로 줄인다 — 그리는 프로세스가 한도를 넘었을 때 스스로 끝내는 길을 탄다.
    fast: 지문 0.3초 · 그림 간격 2초 — 바뀔 때와 간격마다 그림이 나오는지 볼 때.
    slow-gap: 지문 0.3초 · 그림 간격 60초 — 그대로인 화면은 한 장, 바뀌는 화면만 더 나오는지 볼 때.
    link-record:<파일>: 그림 폴더 대신 가짜 탭5 연결 — 창이 연결에 맡긴 그림의 나이(ms) · 크기와 screen 값을 그 파일에 한 줄씩(JSON).
    dead-link: 가짜 탭5 연결이 1초 뒤 멈춘다(일꾼이 죽음) — 창이 30초 감시(여기서는 1초)로 보고 종료값 5 로 끝나는지.
    sleep-then-wake: 처음에는 맥의 화면이 잠든 것으로, ASLEEP_SECONDS 뒤 깨어난 것으로(깨어남 알림을 보낸다) —
    잠든 동안 놓친 수가 쌓여도 끝내지 않다가, 깨어나면 덜어 새로 두 번을 본 뒤 끝내는 길을 탄다.
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import kiosk_window          # noqa: E402

ASLEEP_SECONDS = 6.5                    # sleep-then-wake 에서 잠든 채로 두는 시간(그동안 놓친 수가 쌓인다)


def wake_later(state: dict) -> None:
    """맥의 화면이 깨어난다 — 창에게는 NSWorkspace 의 깨어남 알림으로 온다(주 스레드에서 보낸다)."""
    from AppKit import NSWorkspace, NSWorkspaceScreensDidWakeNotification
    from PyObjCTools import AppHelper
    time.sleep(ASLEEP_SECONDS)

    def run():
        state["asleep"] = False
        NSWorkspace.sharedWorkspace().notificationCenter().postNotificationName_object_(NSWorkspaceScreensDidWakeNotification, None)
    AppHelper.callAfter(run)


class FakeLink:
    """탭5 연결 흉내(tools/tab5_link.Tab5Link 의 겉) — 받은 것을 파일에 적거나, 정해진 때 뒤에 '죽는다'."""

    def __init__(self, record=None, die_after=None, orient_dir=None):
        import json
        from tools import tab5_link
        self.json, self.mono, self.record, self.die_after = json, tab5_link.mono, record, die_after
        self.t0 = self.mono(); self.n = 0; self.screen = None
        self.orient_dir = orient_dir                    # 탭5 방향 흉내 — 시험이 이 폴더의 orient 파일에 적는다

    def _write(self, row):
        if self.record:
            with open(self.record, "a", encoding="utf-8") as f:
                f.write(self.json.dumps(row) + "\n")

    def start(self):
        self.t0 = self.mono()

    def offer(self, jpeg, content, mode="landscape"):
        self.n += 1
        self._write({"age_ms": int((self.mono() - content) * 1000), "size": len(jpeg), "mode": mode})

    def set_screen(self, on):
        if on != self.screen:
            self.screen = on
            self._write({"screen": bool(on)})

    def connected(self):
        return True

    def wait_screen(self, on, timeout):
        return self.screen == on

    def alive(self):
        return self.die_after is None or self.mono() - self.t0 < self.die_after

    def status(self):
        return {"frames_ok": self.n, "orient": kiosk_window.read_orient_file(Path(self.orient_dir)) if self.orient_dir else None}


def main() -> int:
    url, check, mode, exit_log, frames = sys.argv[1:6]
    extra = sys.argv[6] if len(sys.argv) > 6 else ""
    state = {"asleep": mode in ("asleep", "sleep-then-wake")}
    kiosk_window.display_asleep = lambda: state["asleep"]
    kiosk_window.session_locked = lambda: False
    kiosk_window.exit_log = lambda: Path(exit_log)
    args = [url, "--check", check]
    if extra.startswith("link-record:") or extra == "dead-link":
        # 연결 흉내를 쓰는 길 — 설정 파일(시험의 빈 자료 폴더) 대신 일련번호가 있는 것으로 둔다(포트는 주소 인자로 따로 받는다)
        kiosk_window.paths.read_light = lambda h=None: {"port": 5091, "tab5_usb_serial": "TEST-SERIAL"}
    if extra.startswith("link-record:"):
        kiosk_window.make_link = lambda serial, log: FakeLink(record=extra.split(":", 1)[1], orient_dir=frames)
    elif extra == "dead-link":
        kiosk_window.make_link = lambda serial, log: FakeLink(die_after=1.0)
        kiosk_window.LINK_CHECK_SECONDS = 1.0
    else:
        args += ["--frames-to", frames]
    if extra == "tiny-memory":
        kiosk_window.MEMORY_SECONDS = 1.0               # 메모리를 재는 간격과 한도를 줄여, 한도를 넘었을 때의 길을 탄다
        kiosk_window.MEMORY_LIMIT_MB = 1.0
    if extra == "fast":
        args += ["--fp", "0.3", "--gap", "2"]
    if extra in ("slow-gap",) or extra.startswith("link-record:"):
        args += ["--fp", "0.3", "--gap", "60"]
    if mode == "sleep-then-wake":
        threading.Thread(target=wake_later, args=(state,), daemon=True).start()
    sys.argv = [sys.argv[0]] + args
    return kiosk_window.main()


if __name__ == "__main__":
    raise SystemExit(main())
