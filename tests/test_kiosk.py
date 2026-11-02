"""탭5 현황판 창 — 여기서는 창을 띄우지 않고 글과 규칙만 본다. 실제로 띄워 돌려 보는 것은 test_kiosk_smoke.py(화면 밖 창 — 그림을 폴더에 쓴다)."""
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_kiosk_never_uses_full_screen():
    # 실측 2026-10-13: "디스플레이마다 별도의 Spaces"가 꺼진 맥에서 전체 화면 창 하나가 나머지 화면(델·삼성)을 모두 검게 만들었다
    for name in ("kiosk.sh", "kiosk_window.py"):
        code = "\n".join(line for line in (ROOT / "tools" / name).read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))
        code = code.split('"""', 2)[-1] if name.endswith(".py") else code
        assert "--kiosk" not in code and "--start-fullscreen" not in code and "toggleFullScreen" not in code

def test_importing_the_window_module_opens_nothing():
    before = threading.active_count()
    import sys
    had = "AppKit" in sys.modules
    from tools import kiosk_window                      # noqa: F401
    assert threading.active_count() == before and ("AppKit" in sys.modules) == had     # 화면 모듈은 main() 에서만 불러온다
    assert kiosk_window.board_url(5091).startswith("http://127.0.0.1:") and kiosk_window.RETRY_SECONDS > 0     # 주소는 설정의 포트로(main 에서)


# ===== 죽어도 돌아온다 (품질 반복 1회차) — launchd 가 로그인 때 띄우고 죽으면 되살린다 =====
import plistlib
import subprocess

def kiosk_plist(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    r = subprocess.run(["/bin/sh", str(ROOT / "tools" / "kiosk.sh"), "print"], capture_output=True, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    return plistlib.loads(r.stdout)

def test_kiosk_is_a_launchd_job_that_starts_at_login_and_is_kept_alive(tmp_path):
    # 실측 2026-10-13: 로그아웃하자 창이 닫혀 탭5 가 비었고, 창이 죽어도 되살리는 것이 없었다
    d = kiosk_plist(tmp_path)
    assert d["Label"] == "io.github.wideeye.kiosk" and d["RunAtLoad"] is True and d["KeepAlive"] is True
    assert d["ProgramArguments"] == [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "tools" / "kiosk_window.py")]
    logs = tmp_path / "Library" / "Application Support" / "UrgentBoard" / "logs"          # 자료 폴더(HOME 아래 · URGENT_BOARD_HOME 이 없을 때)
    assert d["StandardOutPath"] == d["StandardErrorPath"] == str(logs / "kiosk.log") and d["ThrottleInterval"] >= 10
    assert not list(tmp_path.rglob("*.plist"))                         # print 는 아무것도 깔지 않는다

# ===== 2026-10-15 — 창은 화면에 놓이지 않는다. 화면 밖에서 그리고 그림을 떠 USB-C 로 탭5 에 보낸다(설계 2026-10-15) =====
def test_capture_due_waits_for_a_settled_change_or_the_gap():
    from tools import kiosk_window as kw
    assert kw.capture_due("b", "b", "a", 5.0, 60.0) is True        # 바뀌었고 두 번 연달아 같다 → 뜬다
    assert kw.capture_due("b", "a", "a", 5.0, 60.0) is False       # 막 바뀜 — 그리다 만 중간일 수 있다
    assert kw.capture_due("a", "a", "a", 59.9, 60.0) is False      # 그대로
    assert kw.capture_due("a", "a", "a", 60.0, 60.0) is True       # 60초 — 그대로여도 보낸다(시계 · 살아 있음)
    assert kw.capture_due("a", None, None, 999.0, 60.0) is False   # 처음 한 번은 다음 지문을 기다린다
    assert kw.capture_due("a", "a", None, 0.0, 60.0) is True       # 아직 한 장도 안 보냈다 → 두 번 같으면 곧바로


def test_frames_go_only_under_a_temp_folder(tmp_path):
    from tools import kiosk_window as kw
    import tempfile
    d = Path(tempfile.mkdtemp())
    assert kw.frames_target(str(d)) == d.resolve()
    assert kw.frames_target(str(Path.home())) is None and kw.frames_target("relative") is None
    from urgent_board import paths
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    assert kw.frames_target(str(paths.logs_dir())) is None                      # 그림에 메시지 요약이 든다 — 시험의 가짜 연결은 임시 폴더에만(기록 폴더는 안 된다)
    assert kw.frames_target(str(d / "없는 폴더")) is None


def test_screen_state_helpers_never_raise():
    from tools import kiosk_window as kw
    assert isinstance(kw.session_locked(), bool) and isinstance(kw.screen_on(), bool)
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "CGSSessionScreenIsLocked" in src and "CFRelease" in src             # 잠기면 탭5 도 끈다(설계 0) · 복사해 받은 사전은 놓는다


def test_window_never_goes_on_a_display():
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    code = src.split('"""', 2)[-1]
    assert "orderFront" not in code and "display_origin" not in code and "setFrame_display_" not in code
    assert "NSApplicationActivationPolicyProhibited" in code                    # Dock · 메뉴 · Cmd+Tab 에 나오지 않는다
    assert not (ROOT / "tools" / "display_origin.py").exists()

def test_window_reloads_a_page_that_has_gone_stale():
    from tools import kiosk_window as kw
    assert kw.needs_reload(None, "오류") is True                                # 페이지에 물어도 답이 없다(그리는 프로세스가 굳었다)
    assert kw.needs_reload(-1, None) is True                                   # 우리 화면이 아니다(읽다 실패한 빈 페이지)
    assert kw.needs_reload(kw.STUCK_MS + 1, None) is True                      # 마지막 성공이 너무 오래됐다
    assert kw.needs_reload(9000, None) is False and kw.needs_reload(0, None) is False


def test_window_can_be_asked_for_a_picture_of_what_it_shows(tmp_path):
    # 탭5 에 무엇이 떠 있는지는 화면 캡처로만 알 수 있었는데, 탭5 가 빠져 있으면 그것도 못 한다. 창에게 직접 그림을 달라고 한다
    from tools import kiosk_window as kw
    good = tmp_path / "now.png"
    assert kw.shot_target(str(good) + "\n") == good
    assert kw.shot_target("") is None and kw.shot_target("relative.png") is None and kw.shot_target(str(tmp_path / "x.txt")) is None
    assert kw.shot_target("/Users/tester/Desktop/a.png") is None and kw.shot_target("/etc/a.png") is None       # 임시 폴더와 이 프로젝트의 logs 아래로만(검수: 아무 데나 덮어쓸 수 있었다)
    from urgent_board import paths
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    assert kw.shot_target(str(paths.logs_dir() / "kiosk-shot.png")) == (paths.logs_dir() / "kiosk-shot.png").resolve()
    assert kw.shot_target(str(tmp_path / "없는 폴더" / "a.png")) is None            # 폴더가 없으면 쓰지 않는다
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    assert "shot)" in sh and "kiosk-shot.$" in sh and kw.shot_request(123).name == "kiosk-shot.123.request"      # 그 창(pid)에게만 묻는다 — 다른 창 프로세스가 가로채지 않게(검수에서 실제로 일어남)


def test_window_retries_one_at_a_time_and_backs_off():
    # 코드로 찾은 결함: 서버가 오래 죽어 있으면 30초마다의 확인과 5초마다의 재시도가 서로 새 재시도를 낳아 끝없이 불어났다.
    # 새로 읽기가 앞의 읽기를 취소하면 그 취소(-999)가 또 재시도를 불렀다.
    from tools import kiosk_window as kw
    assert kw.is_cancelled(-999) is True and kw.is_cancelled(-1004) is False and kw.is_cancelled(None) is False
    assert [kw.next_delay(d) for d in (5, 10, 20, 40, 60)] == [10, 20, 40, 60, 60]      # 5초에서 시작해 1분까지 늘린다
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "self.pending" in src and "is_cancelled(" in src and "self.delay = RETRY_SECONDS" in src     # 한 번에 하나 · 취소는 무시 · 읽히면 간격을 되돌린다


# ===== 품질 반복 2회차 — 검수: 굳은 페이지(스크립트 무한 루프)는 물어도 답이 없어 영영 못 잡았다 =====
def test_window_gives_up_on_a_page_that_never_answers():
    from tools import kiosk_window as kw
    assert kw.PROBE_DEADLINE < kw.CHECK_SECONDS                                 # 다음 물음 전에 마감이 온다
    # 3회차(검수): 답이 늦을 뿐인데 끝내기를 되풀이하면 안 된다 — 먼저 다시 읽어 보고, 그래도 답이 없을 때만 끝낸다. 창이 숨어 있으면 끝내지 않는다
    assert kw.MISSES_BEFORE_RELOAD < kw.MISSES_BEFORE_EXIT
    assert kw.on_miss(0, visible=True) == (1, "wait")
    assert kw.on_miss(kw.MISSES_BEFORE_RELOAD - 1, visible=True) == (kw.MISSES_BEFORE_RELOAD, "reload")
    assert kw.on_miss(kw.MISSES_BEFORE_EXIT - 1, visible=True) == (kw.MISSES_BEFORE_EXIT, "exit")
    assert kw.on_miss(kw.MISSES_BEFORE_EXIT - 1, visible=False)[1] != "exit" and kw.on_miss(kw.MISSES_BEFORE_EXIT + 5, visible=False)[1] != "exit"
    # 그래도 끝내기가 잦으면(10분에 세 번) 한동안 끝내지 않는다 — 밤새 2분마다 다시 뜨는 일을 막는다
    now = 10_000.0
    assert kw.may_exit(now, []) is True and kw.may_exit(now, [now - 30, now - 200]) is True
    assert kw.may_exit(now, [now - 30, now - 200, now - 500]) is False and kw.may_exit(now, [now - 700, now - 800, now - 900]) is True
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "os._exit(" in src and "deadline:" in src and "may_exit(" in src

def test_window_shows_a_waiting_note_instead_of_a_blank_screen_before_the_server_is_up():
    from tools import kiosk_window as kw
    assert "기다리는 중" in kw.WAITING_HTML and "#0d1424" in kw.WAITING_HTML and "<script" not in kw.WAITING_HTML
    css = (ROOT / "urgent_board" / "static" / "board.css").read_text(encoding="utf-8")
    assert "--bg:#%02x%02x%02x" % kw.BACKGROUND in css                          # 창의 바탕색 = 화면의 바탕색(페이지가 뜨기 전에 다른 색이 번쩍이지 않게)
    assert kw.is_board("http://127.0.0.1:5091/") is True and kw.is_board("about:blank") is False and kw.is_board(None) is False

def test_frames_carry_how_old_the_page_answer_is_and_only_one_capture_runs_at_a_time():
    # 그림의 나이 = 지금 − 페이지가 마지막으로 답한 때(굳은 페이지의 그림이 "새것"으로 가지 않게 — 독립 검토 C1)
    from tools import kiosk_window as kw
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "self.page_alive" in src and "offer(" in src and "self.capturing" in src
    assert kw.OUT_W == 1280 and kw.OUT_H == 720 and kw.FP_SECONDS <= 5 and kw.FRAME_GAP == 60
    assert kw.CAPTURE_FAILS_RELOAD < kw.CAPTURE_FAILS_EXIT
    # 그림은 화면 갱신을 기다리지 않고 뜬다 — 방향을 바꾼 첫 그림만 갱신 뒤에(세로 D7: settleMode_ 만 fresh_next 를 켜고, 뜨면 곧바로 끈다)
    assert "setAfterScreenUpdates_(self.fresh_next)" in src and "setSnapshotWidth_" in src
    grab = src[src.index("def grab_("):src.index("def finish_token_(")]
    assert grab.index("setAfterScreenUpdates_(self.fresh_next)") < grab.index("self.fresh_next = False")     # 한 번 쓰고 곧바로 끈다
    assert src.count("mono(), True") == 1 and "self.settled_gen, self.page_alive, self.fresh_next = gen, mono(), True" in src     # 켜는 곳은 settleMode_ 하나


def test_kiosk_script_hands_the_tab5_port_over_and_takes_it_back():
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    body = sh[sh.index("  tab5)"):]
    for word in ("pause)", "resume)", "--)", "trap ", "tab5-link.pause", "mon_ctl.py", "paused"):
        assert word in body, word                                               # 놓기 → 놓았다는 확인 → mon_ctl → 되돌리기(끊겨도)

def test_kiosk_script_notices_stray_windows_and_retries_bootstrap():
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    assert "LC_ALL=C awk" in sh                                                  # 실측: 다른 프로세스 인자의 깨진 글자에 awk 가 멈춰 겹친 창을 못 찾았다
    assert "strays()" in sh and "kiosk_window" in sh and "ucomm=" in sh          # 손으로 띄운 창이 겹쳐 있는지 프로세스 이름으로 찾는다(셸의 명령 글자에 속지 않게)
    assert "bootstrap" in sh and "for try in" in sh                             # 내린 직후 올리기가 실패하면 다시 해 본다


# ===== 품질 반복 4회차 — 재검수(91점)가 짚은 것 =====
def test_window_does_not_exit_while_nobody_can_see_it_and_gives_fresh_chances_when_seen_again():
    from tools import kiosk_window as kw
    # 숨어 있거나 화면이 잠든 동안 놓친 수가 쌓였다가, 다시 보이는 순간의 첫 늦은 답에 곧바로 끝나던 구멍 — 끝내기까지 새로 두 번이 남게 던다
    assert kw.on_show(0) == 0 and kw.on_show(1) == 1
    capped = kw.on_show(9)
    assert kw.on_miss(capped, visible=True)[1] != "exit" and kw.on_miss(capped + 1, visible=True)[1] == "exit"
    assert isinstance(kw.display_asleep(), bool)                                 # 알 수 없으면 깨어 있는 것으로(False) — 예외를 내지 않는다
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "screen_on()" in src and "NSWorkspaceScreensDidWakeNotification" in src       # 맥의 화면이 잠든(잠긴) 동안에는 끝내지 않고, 깨어나면 바로 묻는다
    # 물음은 앞의 마감이 온 뒤에만 — 실측: 화면을 깨운 직후 덧물음과 제때의 물음이 5초 간격으로 나가 "새로 두 번"이 20초 만에 지나갔다
    assert kw.too_soon(now=100.0, asked_at=100.0 - kw.PROBE_DEADLINE + 1) is True and kw.too_soon(now=100.0, asked_at=100.0 - kw.PROBE_DEADLINE) is False
    assert "too_soon(time.monotonic(), self.asked_at, deadline_s)" in src


def test_window_draws_its_own_stall_band_over_a_frozen_page():
    # 굳은 페이지는 스스로 "끊김"을 그리지 못한다 — 낡은 "긴급 없음"이 살아 있는 것처럼 남지 않게 창이 직접 띠를 그린다
    from tools import kiosk_window as kw
    assert kw.stalled(kw.MISSES_BEFORE_RELOAD - 1) is False and kw.stalled(kw.MISSES_BEFORE_RELOAD) is True and kw.stalled(99) is True
    assert "멈췄습니다" in kw.STALL_TEXT and kw.STALL_COLOR == (0xff, 0x91, 0x00)   # board.css 의 --neon-orange(확인 불가·끊김과 같은 색)
    css = (ROOT / "urgent_board" / "static" / "board.css").read_text(encoding="utf-8")
    assert "--neon-orange:#ff9100" in css
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "self.showStall_(False)" in src and "self.showStall_(stalled(self.misses))" in src      # 답이 오면 내리고, 놓칠 때마다 다시 본다


def test_window_only_clears_shot_requests_whose_owner_is_gone(tmp_path):
    # 검수: 창이 뜰 때 요청 파일을 전부 지워, 살아 있는 다른 창(손으로 띄운 것)의 요청까지 지웠다
    from tools import kiosk_window as kw
    files = [tmp_path / "kiosk-shot.10.request", tmp_path / "kiosk-shot.20.request", tmp_path / "kiosk-shot.30.request", tmp_path / "kiosk-shot.x.request"]
    gone = kw.dead_requests(files, me=30, alive=lambda pid: pid == 10)
    assert files[0] not in gone and set(gone) == {files[1], files[2], files[3]}   # 살아 있는 10 의 것은 두고 — 죽은 20 · 내 번호 30 · 꼴이 이상한 것만
    assert kw.pid_alive(os.getpid()) is True and kw.pid_alive(2 ** 22 + 12345) is False


def test_doctor_checks_login_readiness_without_changing_anything():
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    body = sh[sh.index("doctor() {"):sh.index("\ncase ")]
    assert "doctor) doctor ;;" in sh
    for word in ("bootstrap", "bootout", "kickstart", "rm -", "mv ", "kill ", "> \"$PLIST\"", "launchctl enable \"", "launchctl disable"):
        assert word not in body, word                                            # 읽기만 한다
    for check in ("plutil -lint", "print-disabled", "import AppKit, WebKit", "healthz", "tab5-link.json", "로그인 항목"):
        assert check in body, check


def test_window_methods_follow_the_objc_naming_rule():
    # 4회차에 실제로 겪음: 창 클래스(NSObject)의 메서드 이름에 든 밑줄은 인자 자리로 읽힌다 — `lay_stall_(self)` 는 인자 둘짜리 `lay:stall:` 로 읽혀
    # 창이 뜨자마자 죽었다(BadPrototypeError). 시험은 창을 띄우지 않으므로 이름과 인자 수가 맞는지를 글로 본다
    import ast
    tree = ast.parse((ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8"))
    board = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Board")
    methods = [n for n in board.body if isinstance(n, ast.FunctionDef)]
    assert len(methods) > 10
    for m in methods:
        assert m.name.count("_") == len(m.args.args) - 1, f"{m.name}: 밑줄 {m.name.count('_')}개인데 인자 {len(m.args.args) - 1}개"


def test_a_page_frozen_all_night_does_not_flood_the_log():
    # 화면이 잠든 동안 굳은 페이지는 끝내지 않으므로 30초마다 놓친다 — 처음 몇 번만 다 적고 그 뒤로는 열 번에 한 번
    from tools import kiosk_window as kw
    assert all(kw.should_log_miss(n) for n in range(1, kw.MISSES_BEFORE_EXIT + 1))
    assert [n for n in range(5, 31) if kw.should_log_miss(n)] == [10, 20, 30]
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert 'todo == "exit" or should_log_miss(self.misses)' in src               # 끝낼 때는 언제나 적는다


# ===== 품질 반복 5회차 — 재검수(94점)가 짚은 것 =====
def test_stall_band_does_not_promise_a_restart_it_cannot_do():
    # 끝내기가 막혀 있을 때(10분에 세 번 끝냈다)에도 띠가 "다시 띄우는 중"이라고 했다 — 그때는 "잠시 뒤"라고 한다
    from tools import kiosk_window as kw
    assert kw.stall_text(True) == kw.STALL_TEXT and "띄우는 중" in kw.STALL_TEXT
    assert kw.stall_text(False) == kw.STALL_TEXT_HELD and "띄우는 중" not in kw.STALL_TEXT_HELD and "멈췄습니다" in kw.STALL_TEXT_HELD
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "self.stall.setStringValue_(stall_text(allowed))" in src


def test_misses_pile_up_no_faster_than_the_regular_check():
    # 검수(추론): 다시 보일 때의 덧물음이 이어지면 놓친 수가 30초가 아니라 15초마다 쌓일 수 있었다
    from tools import kiosk_window as kw
    assert kw.miss_counts(now=100.0, missed_at=float("-inf")) is True            # 첫 놓침
    assert kw.miss_counts(now=100.0, missed_at=100.0 - kw.PROBE_DEADLINE) is False      # 15초 뒤의 놓침은 세지 않는다
    assert kw.miss_counts(now=100.0, missed_at=100.0 - kw.CHECK_SECONDS + 0.5) is True  # 제때(30초)의 놓침은 타이머가 조금 일러도 센다
    assert kw.miss_counts(now=10.0, missed_at=8.0, check=2.0) is True and kw.miss_counts(now=10.0, missed_at=9.0, check=2.0) is False
    assert kw.too_soon(now=10.0, asked_at=9.5, deadline=1.0) is True and kw.too_soon(now=10.0, asked_at=9.0, deadline=1.0) is False


def test_every_selector_the_window_schedules_names_a_real_method():
    # 검수: 이름 규칙 시험은 타이머·알림에 건네는 선택자 글자의 오타("screensWoke:")를 못 막는다 — 건넨 글자마다 그 메서드가 있는지 본다
    import ast
    tree = ast.parse((ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8"))
    board = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Board")
    methods = {n.name for n in board.body if isinstance(n, ast.FunctionDef)}
    asked = set()
    for call in (n for n in ast.walk(board) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
        if call.func.attr in ("scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_", "addObserver_selector_name_object_"):
            asked |= {a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.endswith(":")}
    assert {"check:", "poll:", "deadline:", "reload:", "fp:", "tickPage:", "screensWoke:", "screensSlept:", "captureTimeout:", "memory:"} <= asked
    for sel in asked:
        assert sel.replace(":", "_") in methods, sel


def test_every_name_the_window_imports_exists():
    # `from AppKit import …` 의 이름 오타는 창이 뜰 때에야 드러난다 — 불러오는 이름이 모두 있는지 본다(불러오기만 한다, 창은 안 뜬다)
    import ast, importlib
    tree = ast.parse((ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    froms = [n for n in ast.walk(main) if isinstance(n, ast.ImportFrom) and n.module in ("AppKit", "Foundation", "WebKit")]
    assert {n.module for n in froms} == {"AppKit", "Foundation", "WebKit"}
    for n in froms:
        mod = importlib.import_module(n.module)
        for alias in n.names:
            assert hasattr(mod, alias.name), f"{n.module}.{alias.name}"


# ===== 2026-10-14 — 창에 딸린 그래픽 프로세스가 밤새 2.4GB 까지 샜다 =====
def test_window_measures_real_memory_not_rss_and_knows_when_it_is_over_budget():
    from tools import kiosk_window as kw
    mine = kw.footprint_mb(os.getpid())
    assert mine is not None and 5 < mine < 2000                                  # 이 시험 프로세스의 실제 메모리(MB)
    assert kw.footprint_mb(2 ** 22 + 12345) is None                              # 없는 프로세스는 모른다고 한다(예외를 내지 않는다)
    assert kw.over_budget([40.0, 30.0], limit=600) is False and kw.over_budget([40.0, 2400.0], limit=600) is True
    assert kw.over_budget([None, None], limit=600) is False and kw.over_budget([], limit=600) is False      # 못 쟀으면 끝내지 않는다
    assert kw.over_budget([None, 700.0], limit=600) is True
    assert 200 <= kw.MEMORY_LIMIT_MB <= 1000 and kw.EXIT_MEMORY not in (0, 3)    # 평소(50MB)보다 한참 위 · 기기를 위협하기 한참 전
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert '"_gpuProcessIdentifier"' in src and "respondsToSelector_" in src     # 그래픽 프로세스까지 잰다(샌 곳이 거기다) · 없으면 건너뛴다


def _run_tab5(tmp_path, *args, prev=None):
    """kiosk.sh tab5 를 시험 폴더로 돌린다(실제 state/ 와 mon_ctl 을 건드리지 않는다). 가짜 mon_ctl 은 도는 동안 놓기 파일을 세 번 읽어 찍는다."""
    import os
    import subprocess
    state = tmp_path / "state"; state.mkdir(exist_ok=True)
    pause = state / "tab5-link.pause"
    if prev is not None:
        pause.write_text(f"{prev}\n")
    tool = tmp_path / "fake_monctl.py"
    tool.write_text("import pathlib, sys, time\n"
                    "p = pathlib.Path(sys.argv[-1])\n"
                    "for _ in range(3):\n"
                    "    print('PAUSE', p.read_text().split()[0] if p.exists() else 'none', ' '.join(sys.argv[1:3]), flush=True); time.sleep(0.4)\n")
    env = dict(os.environ, TAB5_STATE_DIR=str(state), TAB5_MONCTL=str(tool), TAB5_MONCTL_ARGS="--unit 9")
    t0 = time.monotonic()
    r = subprocess.run(["sh", str(ROOT / "tools" / "kiosk.sh"), "tab5", *args, str(pause)], env=env, capture_output=True, text=True, timeout=60)
    return r, pause, time.monotonic() - t0


def test_tab5_passthrough_lends_the_port_while_the_tool_runs_and_then_takes_it_back(tmp_path):
    r, pause, took = _run_tab5(tmp_path, "--")
    seen = [line.split() for line in r.stdout.splitlines() if line.startswith("PAUSE")]
    assert len(seen) == 3 and all(s[1] != "none" and int(s[1]) > time.time() for s in seen), r.stdout + r.stderr
    assert all(s[2:] == ["--unit", "9"] for s in seen)                          # 도구 인자는 TAB5_MONCTL_ARGS 그대로
    assert not pause.exists()                                                   # 끝나면 돌려준다
    assert took < 8, took                                                       # 창이 없으면 "놓았다" 확인을 10초 기다리지 않는다


def test_tab5_passthrough_restores_a_longer_pause_someone_set_before(tmp_path):
    # 리뷰 1: 30분 놓기를 걸고 펌웨어를 올리는 중에 다른 세션이 `tab5 --` 를 쓰면, 끝나며 놓기를 지워 창이 포트를 다시 열었다
    far = int(time.time()) + 3600
    r, pause, _ = _run_tab5(tmp_path, "--", prev=far)
    seen = [line.split() for line in r.stdout.splitlines() if line.startswith("PAUSE")]
    assert seen and all(int(s[1]) >= far for s in seen), r.stdout                # 더 긴 놓기를 줄이지 않는다
    assert pause.read_text().split()[0] == str(far)                             # 앞의 놓기로 되돌린다(지우지 않는다)


def test_tab5_tool_runs_with_a_python_that_has_pyserial_and_pil(tmp_path):
    # mon_ctl 은 포트(pyserial)와 화면 찍기 shot(PIL)이 필요하다. 2026-10-18: brew 파이썬으로 고정했더니 맥북에는 pyserial 이 없었고,
    #   프로젝트 가상환경으로 바꿨더니 PIL 이 없어 shot 이 깨졌다 — 둘 다 있는 파이썬은 맥마다 다르다(맥북 /usr/bin/python3, 맥미니 brew)
    import os
    import subprocess
    state = tmp_path / "state"; state.mkdir()
    tool = tmp_path / "fake_monctl.py"
    tool.write_text("import serial, PIL, sys\nprint('TOOLS', serial.__version__, sys.executable, flush=True)\n")
    env = dict(os.environ, TAB5_STATE_DIR=str(state), TAB5_MONCTL=str(tool))
    r = subprocess.run(["sh", str(ROOT / "tools" / "kiosk.sh"), "tab5", "--"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "TOOLS" in r.stdout, r.stdout + r.stderr


def test_tab5_passthrough_keeps_renewing_the_lend_for_long_tools():
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    body = sh[sh.index("  tab5)"):]
    assert "kill -0" in body                                                    # 도구가 살아 있는 동안 놓기를 갱신한다(10분 넘는 일)
    assert "TAB5_STATE_DIR" in sh and "TAB5_MONCTL" in sh


def test_doctor_checks_the_serial_module_too():
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    body = sh[sh.index("doctor() {"):sh.index("\ncase ")]
    assert "import AppKit, WebKit, serial" in body                             # pyserial 이 없으면 일꾼이 죽는다(리뷰 6)


def test_doctor_says_which_way_the_tab5_faces(tmp_path):
    # 세로 화면(2026-10-18 설계 D3): 상태 파일의 orient 를 사람 말로 — 포트는 열지 않는다(읽기만)
    import json
    import os
    import subprocess
    (tmp_path / "state").mkdir()
    for orient, want in ((1, "탭5 방향: 세로 (orient=1)"), (2, "탭5 방향: 가로 — 뒤집힘 (orient=2)"), (None, "탭5 방향: 모름")):
        (tmp_path / "state" / "tab5-link.json").write_text(json.dumps({"updated_at": time.time(), "connected": True, "port": "/dev/x",
                                                                          "tab5": {"board": "live", "bseq": 1, "scr": 1, "bage": 1, "temp": 40.0}, "orient": orient}))
        r = subprocess.run(["sh", str(ROOT / "tools" / "kiosk.sh"), "doctor"], env=dict(os.environ, URGENT_BOARD_HOME=str(tmp_path)),
                           capture_output=True, text=True, timeout=60)
        assert want in r.stdout, r.stdout


# ── 세로(2026-10-18 설계 docs/exec-plans/2026-10-18-tab5-portrait-design.md D1 · D2 · D7) ──
PAGE_FP_BEFORE_PORTRAIT = ('(function(){var b=document.body;if(!b)return "";var c=Array.prototype.map.call(document.querySelectorAll("[class]"),'
                           'function(e){return e.className}).join(",");return b.innerText+"|"+c})()')


def test_portrait_page_size_and_snapshot_points():
    from tools import kiosk_window as kw
    assert kw.PAGE_SIZE == {"landscape": (960, 540), "portrait": (540, 960)}
    assert kw.snapshot_points("landscape", 2.0) == 640.0 and kw.snapshot_points("portrait", 2.0) == 360.0     # 배율 2 → 1280 · 720 픽셀 폭
    assert kw.snapshot_points("portrait", 1.0) == 720.0 and kw.snapshot_points("portrait", 0) == 720.0       # 배율을 모르면 1


def test_the_fingerprint_is_unchanged_so_the_blank_page_guard_holds():
    # 코덱스 검토 치명 2: 지문 끝에 innerWidth 를 붙이면 page_ready(마지막 "|" 앞에 글이 있는가)가 빈 페이지를 살아 있다고 본다
    from tools import kiosk_window as kw
    assert kw.PAGE_FP == PAGE_FP_BEFORE_PORTRAIT
    assert not kw.page_ready("|a,b") and kw.page_ready("WideEye|card")


def test_layout_answer_the_window_waits_for_after_turning():
    from tools import kiosk_window as kw
    assert kw.layout_want("portrait") == "540,true,1" and kw.layout_want("landscape") == "960,false,1"
    assert "matchMedia" in kw.LAYOUT_JS and "innerWidth" in kw.LAYOUT_JS and "innerText" in kw.LAYOUT_JS     # 빈 페이지면 마지막이 0


def test_fake_link_orient_file(tmp_path):
    from tools import kiosk_window as kw
    assert kw.read_orient_file(tmp_path) is None
    for text, want in (("1\n", 1), ("3", 3), ("0", 0), ("x", None), ("7", None), ("", None)):
        (tmp_path / "orient").write_text(text)
        assert kw.read_orient_file(tmp_path) == want, text


def test_only_a_page_with_visible_text_counts_as_alive():
    from tools import kiosk_window as kw
    assert not kw.page_ready(None) and not kw.page_ready("") and not kw.page_ready("|") and not kw.page_ready(" \n |a,b")
    assert kw.page_ready("smoke|") and kw.page_ready("긴급처리 업무 없음\n09:28|card quiet")


def test_window_and_link_share_the_clock_that_counts_sleep():
    from tools import kiosk_window as kw, tab5_link as tl
    assert kw.mono is tl.mono
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "self.page_alive = mono()" in src and "time.monotonic() if fp == \"stall\"" not in src


def test_link_is_watched_on_its_own_30_second_timer_and_tick_waits_for_a_frozen_page():
    from tools import kiosk_window as kw
    assert kw.LINK_CHECK_SECONDS == 30.0
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert '"linkCheck:"' in src and "def linkCheck_" in src
    tick = src[src.index("def tickPage_"):src.index("def captureFrame_")]
    assert "fp_waiting" in tick                                                  # 굳은 페이지에 tick 물음을 쌓지 않는다(리뷰 11)


def test_the_tab5_goes_off_when_the_session_is_locked_or_not_on_the_console():
    from tools import kiosk_window as kw
    assert kw.session_off({"CGSSessionScreenIsLocked": True})
    assert kw.session_off({"kCGSSessionOnConsoleKey": False})                   # 빠른 사용자 전환 — 다른 사람이 콘솔에 있다(마지막 검토 중요 2)
    assert not kw.session_off({"kCGSSessionOnConsoleKey": True}) and not kw.session_off({})


def test_the_window_hears_lock_and_unlock_at_once_and_turns_the_tab5_off_when_it_ends():
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "com.apple.screenIsLocked" in src and "com.apple.screenIsUnlocked" in src     # 3초 폴링을 기다리지 않는다(마지막 검토 사소 5)
    assert "signal.SIGTERM" in src and "wait_screen" in src                               # 끝날 때(로그아웃 · 재시작) 탭5 를 끈다(중요 2)


def test_tab5_passthrough_stops_the_tool_when_interrupted(tmp_path):
    # 마지막 검토 사소 4: Ctrl-C 뒤에도 mon_ctl 이 포트를 쥔 채 남아, 놓기가 풀리면 창과 바이트가 섞일 수 있었다
    import os, signal, subprocess
    state = tmp_path / "state"; state.mkdir()
    pid_file = tmp_path / "tool.pid"
    tool = tmp_path / "slow_monctl.py"
    tool.write_text(f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)\n")
    env = dict(os.environ, TAB5_STATE_DIR=str(state), TAB5_MONCTL=str(tool))
    p = subprocess.Popen(["sh", str(ROOT / "tools" / "kiosk.sh"), "tab5", "--", "run", "stat"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.monotonic() + 10
    while not pid_file.exists() and time.monotonic() < end:
        time.sleep(0.1)
    child = int(pid_file.read_text())
    p.send_signal(signal.SIGINT); p.wait(timeout=10)

    def alive(pid):
        try:
            os.kill(pid, 0); return True
        except ProcessLookupError:
            return False
    end = time.monotonic() + 3
    while alive(child) and time.monotonic() < end:
        time.sleep(0.1)
    assert not alive(child)                                                     # 도구도 끝났다
    assert not (state / "tab5-link.pause").exists()                            # 그리고 포트를 돌려줬다


def test_making_jpegs_does_not_keep_the_jpeg_data_alive():
    # 2026-10-15: 창이 그림마다 JPEG 자료(NSData)를 놓지 않아 시간당 14MB 씩 늘었다(운영 3시간에 Foundation 영역 260개 = 보낸 그림 256장).
    #   PyObjC 12 에서 bytes(NSData) 가 원본을 붙든다 — 바꾸는 법을 고친 것을 실제 증상(남는 VM 영역 수)으로 본다
    import subprocess
    from AppKit import NSBitmapImageRep, NSDeviceRGBColorSpace
    from tools import kiosk_window as kw
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, 1280, 720, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    with memoryview(rep.bitmapData()) as view:
        view[:] = os.urandom(len(view))                                     # 잡음 — JPEG 이 커져(1MB 안팎) 별도 VM 영역에 놓인다

    def regions():
        out = subprocess.run(["vmmap", str(os.getpid())], capture_output=True, text=True, timeout=60).stdout
        return sum(1 for line in out.splitlines() if line.startswith("Foundation "))
    before = regions()
    sizes = [len(kw.jpeg_bytes(rep)) for _ in range(20)]
    after = regions()
    assert min(sizes) > 200_000                                                 # 정말 별도 VM 영역에 놓일 만큼 크다
    assert after - before <= 2, (before, after)                               # 20장을 만들어도 남는 것이 없다


# ===== 2026-10-15 제품판 — 상태 · 기록은 자료 폴더, 포트와 탭5 일련번호는 설정에서 =====
def test_window_reads_port_and_serial_from_settings_and_exits_when_they_change():
    from tools import kiosk_window as kw
    assert kw.settings_changed({"port": 5091, "tab5_usb_serial": "A"}, {"port": 5091, "tab5_usb_serial": "A"}) is False
    assert kw.settings_changed({"port": 5091, "tab5_usb_serial": "A"}, {"port": 5191, "tab5_usb_serial": "A"}) is True
    assert kw.settings_changed({"port": 5091, "tab5_usb_serial": "A"}, {"port": 5091, "tab5_usb_serial": ""}) is True
    assert kw.settings_changed({"port": 5091, "tab5_usb_serial": "A"}, {"port": 5091, "tab5_usb_serial": "", "source": "default"}) is False   # 읽지 못한 것은 바뀐 것이 아니다(검토 I1)
    assert kw.board_url(5191) == "http://127.0.0.1:5191/"
    src = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "5091" not in src and "EXIT_SETTINGS = 6" in src and "paths.read_light" in src
    assert 'ROOT / "state"' not in src and 'ROOT / "logs"' not in src


def test_window_state_files_live_in_the_home_folder():
    from tools import kiosk_window as kw
    from urgent_board import paths
    assert kw.exit_log() == paths.state_dir() / "kiosk-selfexit.json" and kw.shot_request(7).parent == paths.state_dir()


def test_kiosk_sh_uses_the_home_folder():
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    lines = [l for l in sh.splitlines() if not l.startswith("PIDFILE=")]      # 옛 pid 파일 정리만 코드 폴더의 옛 자리를 본다
    assert not any('"$DIR/state' in l or '"$DIR/logs' in l for l in lines) and "5091" not in sh
    assert 'STATE_DIR="$HOME_DIR/state"' in sh and 'LOGS_DIR="$HOME_DIR/logs"' in sh


def test_start_waits_for_the_old_job_to_go_before_registering_again(tmp_path):
    """2026-10-15 바꿔 끼울 때 실제로 겪음: 등록 내용이 바뀌어 옛 작업을 내렸는데(bootout 은 비동기) 곧바로 '아직 올라가 있다'고 보고
    다시 올리지 않아 창이 내려간 채 남았다(탭5 가 꺼짐). 가짜 launchctl 이 내린 뒤에도 두 번 더 '올라가 있음'이라고 답하게 해서 본다."""
    import os
    import subprocess
    fake, state = tmp_path / "bin", tmp_path / "state"
    fake.mkdir(); state.mkdir()
    (fake / "launchctl").write_text('''#!/bin/sh
S="$FAKE_STATE"
case "$1" in
  print)
    [ -f "$S/loaded" ] || exit 113
    if [ -f "$S/going" ]; then n=$(( $(cat "$S/n" 2>/dev/null || echo 0) + 1 )); echo $n > "$S/n"; [ $n -ge 3 ] && rm -f "$S/loaded" "$S/going"; fi
    printf '\\tpid = 4242\\n'; exit 0 ;;
  bootout) echo bootout >> "$S/calls"; touch "$S/going"; exit 0 ;;
  bootstrap) echo bootstrap >> "$S/calls"; touch "$S/loaded"; rm -f "$S/going" "$S/n"; exit 0 ;;
  *) exit 0 ;;
esac
''')
    (fake / "launchctl").chmod(0o755)
    agents = tmp_path / "Library" / "LaunchAgents"; agents.mkdir(parents=True)
    (agents / "io.github.wideeye.kiosk.plist").write_text("<옛 등록 내용>\n")
    (state / "loaded").touch()
    # 자료 폴더는 기본(임시 HOME 아래) — 기본이 아닌 자료 폴더로는 kiosk.sh 가 창 작업을 건드리지 않는다(작업 8 검토 3)
    env = {"HOME": str(tmp_path), "PATH": f"{fake}:/usr/bin:/bin", "FAKE_STATE": str(state)}
    r = subprocess.run(["/bin/sh", str(ROOT / "tools" / "kiosk.sh"), "start"], env=env, capture_output=True, text=True, timeout=60)
    calls = (state / "calls").read_text().split()
    assert calls == ["bootout", "bootstrap"], (calls, r.stdout, r.stderr)


def test_window_opens_the_page_through_the_token():
    from tools import kiosk_window as kw
    from urgent_board import paths
    assert kw.page_url(5091, "") == "http://127.0.0.1:5091/"                      # 열쇠가 아직 없다(엔진이 처음 뜨기 전)
    t = paths.api_token(create=True)
    assert kw.page_url(5091, "") == f"http://127.0.0.1:5091/auth?t={t}&next=/"
    assert kw.page_url(5091, "http://127.0.0.1:6000/") == "http://127.0.0.1:6000/"  # 시험이 준 주소는 그대로
    assert t not in kw.masked(kw.page_url(5091, ""))                                # 기록에 열쇠를 남기지 않는다


def _kiosk_with_fake_launchctl(tmp_path, verb, **env_over):
    """kiosk.sh 를 가짜 launchctl(부른 것을 적기만)과 임시 HOME 으로 — 실제 launchd 를 부르지 않는다."""
    import subprocess
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    log = tmp_path / "launchctl.log"
    (fake / "launchctl").write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexit 1\n')
    (fake / "launchctl").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": f"{fake}:/usr/bin:/bin:/usr/sbin:/sbin"}
    env.update({k: v for k, v in env_over.items() if v is not None})
    r = subprocess.run(["/bin/sh", str(ROOT / "tools" / "kiosk.sh"), verb], env=env, capture_output=True, text=True, timeout=30)
    return r, (log.read_text().splitlines() if log.exists() else []), home


import pytest as _pytest


@_pytest.mark.parametrize("verb", ["start", "restart", "stop", "uninstall"])
@_pytest.mark.parametrize("which", ["prefix", "home"])
def test_kiosk_refuses_to_touch_the_window_job_under_a_test_name_or_another_data_folder(tmp_path, verb, which):
    """탭5 창 작업 이름(io.github.wideeye.kiosk)은 운영 하나뿐이다 — 시험 이름 · 다른 자료 폴더로 부르면(메뉴 막대의 '탭5 연결 시작'
    등) 쉬운 말 한 줄로 멈추고 launchctl 을 부르지도 plist 를 쓰지도 않는다(검토 3 — 운영 plist 가 시험 사본을 가리키게 바뀌었다)."""
    over = ({"URGENT_BOARD_LABEL_PREFIX": "io.examplecorp.urgent-board-test"} if which == "prefix"
            else {"URGENT_BOARD_HOME": str(tmp_path / "other-data")})
    r, calls, home = _kiosk_with_fake_launchctl(tmp_path, verb, **over)
    assert r.returncode == 1 and "탭5 창" in r.stdout + r.stderr
    assert len([l for l in (r.stdout + r.stderr).splitlines() if l.strip()]) == 1
    assert calls == [] and not (home / "Library" / "LaunchAgents").exists()


def test_kiosk_still_runs_with_the_default_name_and_data_folder(tmp_path):
    from tools import launchd as ld
    for over in ({}, {"URGENT_BOARD_LABEL_PREFIX": ld.DEFAULT_PREFIX},
                 {"URGENT_BOARD_HOME": str(tmp_path / "home" / "Library" / "Application Support" / "UrgentBoard")}):
        r, calls, _ = _kiosk_with_fake_launchctl(tmp_path, "stop", **over)
        assert r.returncode == 0 and any(c.startswith("print gui/") for c in calls), (over, r.stdout, r.stderr)
        (tmp_path / "launchctl.log").unlink(missing_ok=True)


def test_kiosk_knows_the_same_default_prefix_as_launchd():
    from tools import launchd as ld
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    assert f'DEFAULT_PREFIX="{ld.DEFAULT_PREFIX}"' in sh


def test_tab5_tool_gets_the_serial_from_the_settings(tmp_path):
    """TB-D5(2026-11-02): 탭5 도구는 이 저장소의 firmware/tools/mon_ctl.py 이고, 인자를 따로 주지 않으면 설정의 탭5 일련번호를 --serial 로 받는다.
    일련번호가 없으면 아무 인자도 주지 않는다(도구가 꽂힌 Espressif 기기가 하나일 때만 그것을 쓴다). 기기 번호 표(--unit)는 없다."""
    import os
    import subprocess
    from urgent_board import settings_doc as sd
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    assert '$DIR/firmware/tools/mon_ctl.py' in sh and "TAB5_MONCTL_ARGS:---unit" not in sh
    tool = tmp_path / "args_monctl.py"
    tool.write_text("import sys\nprint('ARGS', sys.argv[1:], flush=True)\n")
    for serial, want in (("TEST-SER-1", "['--serial', 'TEST-SER-1']"), ("", "[]")):
        home = tmp_path / ("home-" + (serial or "none")); home.mkdir()
        d = sd.example(); d.setdefault("display", {})["tab5_usb_serial"] = serial
        sd.save(home, d, None)
        state = home / "st"; state.mkdir()
        env = {k: v for k, v in os.environ.items() if k != "TAB5_MONCTL_ARGS"}
        env.update(URGENT_BOARD_HOME=str(home), TAB5_STATE_DIR=str(state), TAB5_MONCTL=str(tool))
        # 저장소 밖 폴더에서 불러도 같아야 한다 — 예전에는 모듈을 못 찾아 일련번호가 조용히 빠졌다(독립 검토 2026-11-02)
        r = subprocess.run(["sh", str(ROOT / "tools" / "kiosk.sh"), "tab5", "--"], env=env, capture_output=True, text=True, timeout=60, cwd=str(tmp_path))
        assert r.returncode == 0 and ("ARGS " + want) in r.stdout, (serial, r.stdout + r.stderr)
