"""탭5 창을 **실제로** 띄워 돌려 보는 시험 — 창은 화면 밖에서 그리고, 그림은 탭5 대신 임시 폴더에 쓴다(어느 화면에도 아무것도 뜨지 않는다).

다른 창 시험(test_kiosk.py)은 글과 순수 함수만 본다. 그래서 "시험은 통과하는데 창은 뜨자마자 죽는" 고장을 못 막았다
(2026-10-13 품질 반복 4회차: 메서드 이름의 밑줄 때문에 `BadPrototypeError`). 여기서는 창 프로세스를 띄워 기록과 그림 파일을 본다.
화면이 있는 로그인 세션에서만 돈다(봇·SSH 에서는 건너뛴다). 건너뛰려면 URGENT_BOARD_SKIP_GUI=1.
창은 tests/kiosk_offscreen.py 로 띄운다 — 맥의 화면이 잠들었는지 정하고, 스스로 끝낸 기록을 시험 파일에 적게 한다.
"""
import http.server
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OK_PAGE = b"""<!doctype html><meta charset="utf-8"><body style="background:#171717;color:#fff">smoke
<script>let lastOk = Date.now(); function tick() { lastOk = Date.now(); } setInterval(tick, 300);</script></body>"""
TICK_PAGE = b"""<!doctype html><meta charset="utf-8"><body style="background:#171717;color:#fff"><div id="t">0</div>
<script>let lastOk = Date.now(); let n = 0; function tick() { lastOk = Date.now(); } setInterval(tick, 300);
setInterval(function () { document.getElementById("t").textContent = String(++n); }, 1000);</script></body>"""
# 세로 시험 페이지: 맨 위 100px 띠가 가로 미디어 질의에서는 초록, 세로에서는 빨강 — 반쯤 바뀐 그림(가로 꼴을 세로 크기로)을 잡는다(코덱스 검토 중요 3)
MQ_PAGE = b"""<!doctype html><meta charset="utf-8"><style>html,body{margin:0;height:100%;background:#0d1424;color:#fff}
#band{position:fixed;left:0;right:0;top:0;height:100px;background:#00c000}@media (orientation: portrait){#band{background:#ff0000}}</style>
<body><div id="band"></div>mq<script>let lastOk = Date.now(); function tick() { lastOk = Date.now(); } setInterval(tick, 300);</script></body>"""
FROZEN_PAGE = b"""<!doctype html><meta charset="utf-8"><body style="background:#171717;color:#fff">frozen
<script>let lastOk = Date.now(); function tick() {} setTimeout(() => { for (;;) {} }, 300);</script></body>"""


def _gui_session() -> bool:
    if os.environ.get("URGENT_BOARD_SKIP_GUI"):
        return False
    try:
        return subprocess.run(["launchctl", "managername"], capture_output=True, text=True, timeout=5).stdout.strip() == "Aqua"
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _gui_session(), reason="화면이 있는 로그인 세션이 아니다")


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        if self.path.startswith("/slow"):
            time.sleep(12)                               # 연결은 받고 답이 늦는 서버 — 그동안 창에는 빈(흰) 페이지가 있다
        body = (FROZEN_PAGE if self.path.startswith("/frozen") else TICK_PAGE if self.path.startswith("/tick")
                else MQ_PAGE if self.path.startswith("/mq") else OK_PAGE)
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def pages():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown(); srv.server_close()


class _Window:
    """창 프로세스 하나(화면 밖). 기록을 줄 단위로 읽고, 그림은 frames 폴더에 쌓인다."""

    def __init__(self, url: str, check_seconds: float, mode: str, exit_log: Path, extra: str = "", orient: int | None = None):
        self.frames = Path(tempfile.mkdtemp(prefix="urgent-board-frames-"))   # 임시 폴더 아래여야 창이 쓴다(frames_target)
        if orient is not None:
            self.set_orient(orient)                      # 탭5 의 방향 흉내 — 창(가짜 연결)이 이 파일을 읽는다
        cmd = [sys.executable, str(ROOT / "tests" / "kiosk_offscreen.py"), url, str(check_seconds), mode, str(exit_log), str(self.frames)]
        self.proc = subprocess.Popen(cmd + ([extra] if extra else []), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.lines: list[str] = []
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [self._q.put(x) for x in self.proc.stdout], daemon=True).start()

    def wait_for(self, text: str, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if any(text in x for x in self.lines):
                return True
            try:
                self.lines.append(self._q.get(timeout=0.2))
            except queue.Empty:
                if self.proc.poll() is not None and self._q.empty():
                    break
        return any(text in x for x in self.lines)

    def set_orient(self, orient: int) -> None:
        tmp = self.frames / ".orient"
        tmp.write_text(f"{orient}\n"); tmp.replace(self.frames / "orient")

    def frame_files(self) -> list[Path]:
        return sorted(self.frames.glob("frame-*.jpg"))

    def wait_frames(self, n: int, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if len(self.frame_files()) >= n:
                return True
            self.wait_for("\0never", 0.2)                # 기록을 읽어 두면서 기다린다
        return len(self.frame_files()) >= n

    def ask_for_picture(self, target: Path) -> None:
        from tools import kiosk_window as kw
        kw.shot_request(self.proc.pid).write_text(str(target) + "\n", encoding="utf-8")

    def close(self) -> None:
        from tools import kiosk_window as kw
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=10)
        kw.shot_request(self.proc.pid).unlink(missing_ok=True)
        for f in self.frames.glob("*"):
            f.unlink(missing_ok=True)
        self.frames.rmdir()


@pytest.fixture
def window(tmp_path):
    made = []

    def start(url, check_seconds=2.0, mode="awake", extra="", orient=None):
        made.append(_Window(url, check_seconds, mode, tmp_path / "exits.json", extra, orient)); return made[-1]
    yield start
    for w in made:
        w.close()


def _size(picture: Path) -> tuple[int, int]:
    from AppKit import NSBitmapImageRep
    rep = NSBitmapImageRep.imageRepWithContentsOfFile_(str(picture))
    return rep.pixelsWide(), rep.pixelsHigh()


def _is_band(color) -> bool:
    """띠의 주황인가(그림의 색 공간 · JPEG 에 따라 값이 조금 다르다)."""
    c = color.colorUsingColorSpace_(__import__("AppKit").NSColorSpace.sRGBColorSpace())
    return c.redComponent() > 0.8 and 0.35 < c.greenComponent() < 0.75 and c.blueComponent() < 0.4


def _is_navy(color) -> bool:
    """현황판 바탕의 짙은 남색(#0d1424)인가."""
    c = color.colorUsingColorSpace_(__import__("AppKit").NSColorSpace.sRGBColorSpace())
    return c.redComponent() < 0.15 and c.greenComponent() < 0.2 and c.blueComponent() < 0.25 and c.blueComponent() > c.redComponent()


def _middle_is_navy(picture: Path) -> bool:
    from AppKit import NSBitmapImageRep
    rep = NSBitmapImageRep.imageRepWithContentsOfFile_(str(picture))
    w, h = rep.pixelsWide(), rep.pixelsHigh()
    return all(_is_navy(rep.colorAtX_y_(x, y)) for x, y in ((w // 2, h // 2), (20, h - 20), (w - 20, h // 2)))


def _band_spans(picture: Path) -> bool:
    """그림 맨 위의 띠가 왼쪽 끝부터 오른쪽 끝까지 이어지는가."""
    from AppKit import NSBitmapImageRep
    rep = NSBitmapImageRep.imageRepWithContentsOfFile_(str(picture))
    w = rep.pixelsWide()
    return all(_is_band(rep.colorAtX_y_(x, 8)) for x in (10, w // 2, w - 10))


def _color(rep, x, y) -> str:
    c = rep.colorAtX_y_(x, y).colorUsingColorSpace_(__import__("AppKit").NSColorSpace.sRGBColorSpace())
    r, g, b = c.redComponent(), c.greenComponent(), c.blueComponent()
    return "red" if r > 0.7 and g < 0.3 else "green" if g > 0.5 and r < 0.3 else "navy" if r < 0.15 and b > r else "other"


def _layout(picture: Path) -> str:
    """MQ_PAGE 그림이 어느 꼴인가: 위 가운데가 초록이고 오른쪽 가운데가 남색 → landscape, 오른쪽 가운데가 빨강이고 위 가운데가 남색 → portrait
    (세로 화면의 위쪽이 그림의 오른쪽 — 설계 D2), 그 밖은 mixed(반쯤 바뀐 그림 · 돌리지 않은 세로)."""
    from AppKit import NSBitmapImageRep
    rep = NSBitmapImageRep.imageRepWithContentsOfFile_(str(picture))
    w, h = rep.pixelsWide(), rep.pixelsHigh()
    top, right = _color(rep, w // 2, 8), _color(rep, w - 8, h // 2)
    if (w, h) != (1280, 720):
        return f"size {w}x{h}"
    return "landscape" if (top, right) == ("green", "navy") else "portrait" if (top, right) == ("navy", "red") else f"mixed {top}/{right}"


def test_portrait_frames_are_turned_so_the_top_of_the_page_is_on_the_right(pages, window):
    w = window(pages + "/mq", extra="fast", orient=1)
    assert w.wait_frames(2, 20), "".join(w.lines)
    assert [_layout(f) for f in w.frame_files()] == ["portrait"] * len(w.frame_files()), "".join(w.lines)
    assert w.wait_for("방향: 세로", 1), "".join(w.lines)


def test_switching_orient_sends_a_settled_portrait_frame_within_5_seconds(pages, window):
    w = window(pages + "/mq", extra="fast")
    assert w.wait_frames(1, 20), "".join(w.lines)
    assert _layout(w.frame_files()[-1]) == "landscape"
    before = len(w.frame_files())
    t0 = time.monotonic(); w.set_orient(3)
    got = None
    while time.monotonic() - t0 < 5 and got is None:
        got = next((f for f in w.frame_files()[before:] if _layout(f) == "portrait"), None)
        w.wait_for("\0never", 0.2)
    assert got is not None, [(_layout(f)) for f in w.frame_files()] + w.lines
    assert not [f for f in w.frame_files() if _layout(f).startswith(("mixed", "size"))], [_layout(f) for f in w.frame_files()]
    text = "".join(w.lines)
    assert "자리 잡지 않았다" not in text and "끝나지 않았다" not in text, text      # 새 꼴 확인 → 화면 갱신 뒤 뜨기가 제때 끝났다(지문 길로 미끄러지지 않았다)


def test_at_production_intervals_a_turn_reaches_the_tab5_within_4_seconds(pages, window):
    # 최종 검토(2026-10-18) Important 1: 빠른 간격(fast)으로는 지문 길만으로도 5초 안에 세로 그림이 나와 settle 길(D7 — 새 꼴을 물어 곧바로
    #   한 장)이 없어져도 시험이 초록이었다. 운영 간격(지문 3초 · 간격 60초)으로 잰다: settle 이 있으면 2.3초, 없으면 6.2초(검토자 실측)
    w = window(pages + "/mq")
    assert w.wait_frames(1, 30), "".join(w.lines)
    before = len(w.frame_files())
    w.wait_for("\0never", 1)
    t0 = time.monotonic(); w.set_orient(1)
    took = None
    while time.monotonic() - t0 < 8 and took is None:
        if any(_layout(f) == "portrait" for f in w.frame_files()[before:]):
            took = time.monotonic() - t0
        w.wait_for("\0never", 0.1)
    assert took is not None and took < 4.0, (took, [_layout(f) for f in w.frame_files()], "".join(w.lines))


def test_fast_turns_end_on_the_last_orientation(pages, window):
    w = window(pages + "/mq", extra="fast")
    assert w.wait_frames(1, 20), "".join(w.lines)
    for o in (1, 3, 0):
        w.set_orient(o); time.sleep(0.3)
    last_turn = len(w.frame_files())
    w.wait_for("\0never", 6)
    after = [_layout(f) for f in w.frame_files()]
    assert after[-1] == "landscape", after
    assert not [x for x in after if x.startswith(("mixed", "size"))], after
    assert "portrait" not in after[last_turn + 1:], after                       # 마지막 바꿈 뒤에 늦게 온 세로 그림이 없다


def test_the_stall_band_is_on_top_of_the_portrait_screen(pages, window):
    w = window(pages + "/frozen", check_seconds=1.0, orient=1)
    assert w.wait_for("멈춤 띠를 띄운다", 30), "".join(w.lines)
    assert w.wait_for("\0never", 3) or True
    from AppKit import NSBitmapImageRep
    bands = []
    for f in w.frame_files():
        rep = NSBitmapImageRep.imageRepWithContentsOfFile_(str(f))
        if all(_is_band(rep.colorAtX_y_(rep.pixelsWide() - 8, y)) for y in (10, 360, 710)):
            bands.append(f)
    assert bands, "".join(w.lines)                                               # 세로 화면의 맨 위 = 그림의 오른쪽 끝 열이 위에서 아래까지 주황
    for f in bands:                                                              # 띠 밖(그림의 가운데 · 왼쪽 · 아래)은 남색 바탕
        rep = NSBitmapImageRep.imageRepWithContentsOfFile_(str(f))
        assert all(_is_navy(rep.colorAtX_y_(x, y)) for x, y in ((640, 360), (20, 360), (640, 700))), f


def test_a_blank_page_is_never_sent_in_portrait_either(pages, window):
    w = window(pages + "/slow", check_seconds=30.0, extra="fast", orient=1)
    w.wait_for("\0never", 9)
    assert w.frame_files() == [], "".join(w.lines)                              # 빈 흰 페이지를 세로 꼴 확인만으로 보내지 않는다(코덱스 치명 2)
    assert w.wait_for("페이지를 읽었다", 15), "".join(w.lines)
    assert w.wait_frames(1, 10), "".join(w.lines)


def test_the_link_gets_the_orientation_with_each_picture(pages, window, tmp_path):
    record = tmp_path / "link.jsonl"
    w = window(pages + "/tick", extra=f"link-record:{record}", orient=1)
    assert w.wait_for("페이지를 읽었다", 30), "".join(w.lines)
    end = time.monotonic() + 20
    rows = []
    while time.monotonic() < end and not [r for r in rows if "age_ms" in r]:
        w.wait_for("\0never", 0.5)
        rows = [json.loads(x) for x in record.read_text(encoding="utf-8").splitlines()] if record.exists() else []
    pics = [r for r in rows if "age_ms" in r]
    assert pics and all(r["mode"] == "portrait" for r in pics), rows


def test_the_window_reads_the_page_writes_a_1280x720_frame_and_hands_over_a_picture(pages, window, tmp_path):
    w = window(pages + "/ok", extra="fast")
    assert w.wait_for("페이지를 읽었다", 30), "".join(w.lines)                   # 뜨자마자 죽으면(이름·불러오기 오류) 여기서 걸린다
    assert w.wait_frames(1, 15), "".join(w.lines)
    assert _size(w.frame_files()[0]) == (1280, 720)                              # 탭5 화면 그대로의 크기
    shot = tmp_path / "now.png"
    w.ask_for_picture(shot)
    assert w.wait_for("화면을 그림으로 떴다", 20), "".join(w.lines)
    assert shot.stat().st_size > 1000 and _size(shot) == (1280, 720)
    assert not w.wait_for("답하지 않는다", 3.5)                                  # 살아 있는 페이지는 확인(2초 뒤, 마감 1초)에 답한다
    text = "".join(w.lines)
    assert "페이지가 낡았다" not in text, text
    assert w.proc.poll() is None and "Traceback" not in text and "멈춤 띠" not in text, text


def test_a_changing_page_sends_new_frames_but_a_still_page_only_every_gap(pages, window):
    # 간격 60초로 — 바뀐 화면은 간격이 아니라 지문이 바뀐 것으로만 새 그림이 나와야 한다(리뷰 10: 간격 2초로는 지문 비교가 고장 나도 통과했다)
    moving = window(pages + "/tick", extra="slow-gap")
    still = window(pages + "/ok", extra="slow-gap")
    assert moving.wait_frames(3, 25), "".join(moving.lines)                     # 글이 바뀔 때마다(화면 밖 페이지의 타이머는 늦춰져도 돈다)
    assert still.wait_frames(1, 15), "".join(still.lines)
    still.wait_for("\0never", 4)
    assert len(still.frame_files()) == 1                                         # 그대로인 화면은 첫 한 장뿐(간격 60초 전에는 더 없다)
    assert "Traceback" not in "".join(moving.lines + still.lines)


def test_a_blank_page_before_the_first_load_is_never_sent(pages, window):
    # 리뷰 3: 서버가 연결은 받고 답이 늦으면 첫 읽기 동안 빈 흰 페이지가 지문에 답해 "새 그림"으로 갔다(탭5 가 흰 화면을 LIVE 로)
    w = window(pages + "/slow", check_seconds=30.0, extra="fast")               # 확인은 운영 간격(30초) — 더 짧으면 늦은 읽기를 스스로 새로 시작한다
    w.wait_for("\0never", 9)
    assert w.frame_files() == [], "".join(w.lines)
    assert w.wait_for("페이지를 읽었다", 15), "".join(w.lines)                   # 답이 오면 정상으로 돌아온다
    assert w.wait_frames(1, 10), "".join(w.lines)


def test_the_window_hands_fresh_pictures_and_the_screen_state_to_the_link(pages, window, tmp_path):
    # 리뷰 5: 창 → 연결의 길(그림의 나이 · screen)이 실제로 도는 것을 본다 — 가짜 연결이 받은 것을 적는다
    record = tmp_path / "link.jsonl"
    w = window(pages + "/tick", extra=f"link-record:{record}")
    assert w.wait_for("페이지를 읽었다", 30), "".join(w.lines)

    def rows():
        return [json.loads(x) for x in record.read_text(encoding="utf-8").splitlines()] if record.exists() else []
    end = time.monotonic() + 20
    while time.monotonic() < end and len([r for r in rows() if "age_ms" in r]) < 2:
        w.wait_for("\0never", 0.5)
    got = rows()
    pics = [r for r in got if "age_ms" in r]
    assert len(pics) >= 2 and all(0 <= r["age_ms"] < 4000 and r["size"] > 1000 for r in pics), got     # 살아 있는 페이지의 그림은 몇 초 안의 것
    assert {"screen": True} in got, got                                          # 맥 화면이 깨어 있고 안 잠김 → 켬


def test_the_window_exits_with_5_when_the_link_worker_dies(pages, window):
    # 설계 2-3: 메인 스레드가 30초마다 일꾼을 본다 — 멈췄으면 끝낸다(launchd 가 띄운다). 여기서는 1초
    w = window(pages + "/ok", extra="dead-link")
    assert w.wait_for("탭5 연결 일꾼이 멈췄다", 20), "".join(w.lines)
    assert w.proc.wait(timeout=10) == 5, "".join(w.lines)


def test_a_window_someone_can_see_sends_the_stall_band_then_exits_with_3_on_a_frozen_page(pages, window, tmp_path):
    w = window(pages + "/frozen", check_seconds=1.0)
    assert w.wait_for("멈춤 띠를 띄운다", 30), "".join(w.lines)
    assert w.wait_for("프로세스를 끝낸다", 20), "".join(w.lines)                 # 네 번 연달아 답이 없으면
    assert w.proc.wait(timeout=10) == 3, "".join(w.lines)                        # 종료값 3 — 0 이면 launchd 가 "잘 끝났다"고 볼 수 있다
    assert len(json.loads((tmp_path / "exits.json").read_text(encoding="utf-8"))) == 1
    bands = [f for f in w.frame_files() if _band_spans(f)]
    assert bands, "".join(w.lines)                                               # 굳은 동안 탭5 에는 창이 그린 띠가 갔다(낡은 화면이 아니라)
    assert all(_middle_is_navy(f) for f in bands)                                # 띠 아래는 현황판의 남색 바탕 — 밤에 흰 화면으로 밝히지 않는다(리뷰 4)


def test_a_window_while_the_mac_screen_sleeps_does_not_exit_then_gets_fresh_chances_when_it_wakes(pages, window, tmp_path):
    # 맥의 화면이 잠든 동안에는 아무도 보지 않는다 — 놓친 수가 쌓여도 끝내지 않는다. 깨어나면 쌓인 수를 덜어 새로 두 번을 본 뒤 끝낸다
    w = window(pages + "/frozen", check_seconds=1.0, mode="sleep-then-wake")
    assert w.wait_for("4번째", 30), "".join(w.lines)
    assert "프로세스를 끝낸다" not in "".join(w.lines)
    assert w.wait_for("맥의 화면이 깨어났다", 20), "".join(w.lines)
    assert w.wait_for("프로세스를 끝낸다", 20), "".join(w.lines)
    last = next(x for x in w.lines if "프로세스를 끝낸다" in x)
    assert "(4번째)" in last, "".join(w.lines)                                   # 쌓인 수(다섯쯤)가 아니라 덜어 낸 수에서 두 번 더
    assert w.proc.wait(timeout=10) == 3 and "Traceback" not in "".join(w.lines), "".join(w.lines)


def test_a_window_on_a_sleeping_mac_screen_never_exits_on_a_frozen_page(pages, window, tmp_path):
    w = window(pages + "/frozen", check_seconds=1.0, mode="asleep")
    assert w.wait_for("4번째", 30), "".join(w.lines)
    assert not w.wait_for("프로세스를 끝낸다", 3.5), "".join(w.lines)
    assert w.proc.poll() is None and not (tmp_path / "exits.json").exists(), "".join(w.lines)


def test_the_window_restarts_itself_when_its_webkit_processes_eat_memory(pages, window, tmp_path):
    # 2026-10-14: 조용한 화면에서 그래픽 프로세스가 1분에 6.6MB 씩 새어 밤새 2.4GB 가 됐다. "넘으면 스스로 끝낸다"를 한도 1MB 로 본다
    w = window(pages + "/ok", extra="tiny-memory")
    assert w.wait_for("페이지를 읽었다", 30), "".join(w.lines)
    assert w.wait_for("메모리가", 20), "".join(w.lines)
    assert w.proc.wait(timeout=10) == 4, "".join(w.lines)                        # 굳어서 끝낼 때(3)와 다른 값
    assert len(json.loads((tmp_path / "exits.json").read_text(encoding="utf-8"))) == 1 and "Traceback" not in "".join(w.lines)


def test_the_window_turns_the_tab5_off_before_it_ends(pages, window, tmp_path):
    # 마지막 검토 중요 2: 로그아웃 · 재시작 때 launchd 가 보내는 SIGTERM 에 탭5 를 끄지 않고 끝나, 메시지가 든 마지막 그림이 3분까지 켜져 있었다
    import signal
    record = tmp_path / "link.jsonl"
    w = window(pages + "/ok", extra=f"link-record:{record}")
    assert w.wait_for("페이지를 읽었다", 30), "".join(w.lines)
    w.wait_for("\0never", 2)
    w.proc.send_signal(signal.SIGTERM)
    assert w.proc.wait(timeout=10) == 0, "".join(w.lines)
    rows = [json.loads(x) for x in record.read_text(encoding="utf-8").splitlines()]
    screens = [r["screen"] for r in rows if "screen" in r]
    assert screens[-1] is False, rows                                            # 마지막으로 연결에 맡긴 것은 "끔"


def test_the_window_opens_the_board_behind_the_access_token(window, tmp_path):
    """설계 9 · 계획 2 검토 초점 5: 접속 열쇠가 켜진 엔진에서 창(WebKit, 저장하지 않는 쿠키)이 /auth 로 쿠키를 받고,
    현황판 페이지의 10초 당김(/api/items)이 그 쿠키로 열린다 — 401 이 한 번도 없다."""
    from werkzeug.serving import make_server
    from urgent_board import config
    from urgent_board.store import Store
    from urgent_board.web import create_app
    seen = []
    app = create_app(config.load(ROOT / "tests" / "fixtures" / "home"), Store(tmp_path / "t.db"), token="SMOKE-TOKEN")
    inner = app.wsgi_app

    def record(environ, start_response):
        def sr(status, headers, *a):
            seen.append((environ.get("PATH_INFO"), int(status.split()[0])))
            return start_response(status, headers, *a)
        return inner(environ, sr)
    app.wsgi_app = record
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        w = window(f"http://127.0.0.1:{srv.server_port}/auth?t=SMOKE-TOKEN&next=/", check_seconds=2.0)
        assert w.wait_frames(1, 30), w.lines
        end = time.monotonic() + 20
        while time.monotonic() < end and not any(p == "/api/items" and s == 200 for p, s in seen):
            time.sleep(0.2)
        assert any(p == "/api/items" and s == 200 for p, s in seen), seen
        assert not any(s == 401 for _, s in seen), seen
    finally:
        srv.shutdown()
