"""launchd 작업 한 곳(tools/launchd.py) — plist 꼴과 올리고 내리는 차례. 실제 launchctl 은 부르지 않는다(가짜 run)."""
import plistlib
import subprocess

from tools import launchd as ld


def test_engine_plist_keeps_alive_with_path_workdir_and_logs(tmp_path):
    p = plistlib.loads(ld.plist("io.x.engine", ["/v/python", "/c/run_board.py"], workdir="/c", logs=tmp_path, keepalive="always",
                                home=tmp_path / "home"))
    assert p["Label"] == "io.x.engine" and p["ProgramArguments"] == ["/v/python", "/c/run_board.py"]
    assert p["RunAtLoad"] is True and p["KeepAlive"] is True and p["ThrottleInterval"] == 30 and p["WorkingDirectory"] == "/c"
    assert "/opt/homebrew/bin" in p["EnvironmentVariables"]["PATH"].split(":")
    assert p["EnvironmentVariables"]["URGENT_BOARD_HOME"] == str(tmp_path / "home")
    assert p["StandardOutPath"] == str(tmp_path / "io.x.engine.out.log") and p["StandardErrorPath"] == str(tmp_path / "io.x.engine.out.log")
    assert "LimitLoadToSessionType" not in p


def test_menubar_plist_restarts_only_after_a_crash_and_only_in_the_gui(tmp_path):
    p = plistlib.loads(ld.plist("io.x.menubar", ["/v/python", "/c/tools/menubar.py"], workdir="/c", logs=tmp_path, keepalive="crash", gui=True))
    assert p["KeepAlive"] == {"SuccessfulExit": False}                      # '끝내기'(0)는 다음 로그인까지 쉰다
    assert p["LimitLoadToSessionType"] == "Aqua" and p["ProcessType"] == "Interactive"
    assert "URGENT_BOARD_HOME" not in p["EnvironmentVariables"]            # 기본 자료 폴더


def test_a_test_or_other_prefix_is_passed_to_the_job(tmp_path, monkeypatch):
    """기본이 아닌 이름(시험 · 다른 판)으로 올린 작업은 그 이름을 환경으로 받는다 — 그 작업이 부르는 도구(메뉴 막대 → kiosk.sh)가
    운영 이름을 건드리지 않게(검토 3). 운영 이름이면 넣지 않는다(지금 운영 작업의 plist 가 그대로 — '같음')."""
    p = plistlib.loads(ld.plist("io.x.menubar", ["/v/python"], workdir="/c", logs=tmp_path, keepalive="crash", gui=True))
    assert ld.PREFIX != ld.DEFAULT_PREFIX and p["EnvironmentVariables"]["URGENT_BOARD_LABEL_PREFIX"] == ld.PREFIX     # conftest 의 시험 이름
    monkeypatch.setattr(ld, "PREFIX", ld.DEFAULT_PREFIX)
    p = plistlib.loads(ld.plist("io.x.menubar", ["/v/python"], workdir="/c", logs=tmp_path, keepalive="crash", gui=True))
    assert "URGENT_BOARD_LABEL_PREFIX" not in p["EnvironmentVariables"]


class FakeLaunchctl:
    """launchctl 흉내 — bootout 은 두 번 물어본 뒤에야 내려간다(실제처럼 비동기)."""
    def __init__(self):
        self.calls, self.loaded, self.pending_out = [], set(), 0

    def __call__(self, args, **kw):
        self.calls.append(args[1:])
        cmd, target = args[1], args[2] if len(args) > 2 else ""
        label = target.split("/")[-1]
        if cmd == "print":
            if label in self.loaded and self.pending_out:
                self.pending_out -= 1
                if not self.pending_out:
                    self.loaded.discard(label)
                return subprocess.CompletedProcess(args, 0, "pid = 42\n", "")
            return subprocess.CompletedProcess(args, 0 if label in self.loaded else 113, "pid = 42\n" if label in self.loaded else "", "")
        if cmd == "bootstrap":
            self.loaded.add(args[3].split("/")[-1].removesuffix(".plist"))
            return subprocess.CompletedProcess(args, 0, "", "")
        if cmd == "bootout":
            self.pending_out = 2
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")


def test_install_writes_once_and_replaces_only_after_the_old_job_is_gone(tmp_path):
    fake = FakeLaunchctl()
    L = ld.Launchd(domain="gui/501", agents=tmp_path, run=fake, sleep=lambda s: None)
    data = ld.plist("io.x.engine", ["/v/python", "a"], workdir="/c", logs=tmp_path, keepalive="always")
    assert L.install("io.x.engine", data) == "started" and L.path("io.x.engine").read_bytes() == data
    assert L.loaded("io.x.engine") and L.pid("io.x.engine") == 42
    fake.calls.clear()
    assert L.install("io.x.engine", data) == "same" and not any(c[0] in ("bootout", "bootstrap") for c in fake.calls)
    new = ld.plist("io.x.engine", ["/v/python", "b"], workdir="/c", logs=tmp_path, keepalive="always")
    fake.calls.clear()
    assert L.install("io.x.engine", new) == "replaced"
    order = [c[0] for c in fake.calls]
    assert order.index("bootout") < order.index("bootstrap") and order.count("print") >= 3      # 내려갈 때까지 물었다
    assert L.path("io.x.engine").read_bytes() == new and L.loaded("io.x.engine")


def test_remove_moves_the_plist_aside_and_stop_kick(tmp_path):
    fake = FakeLaunchctl()
    L = ld.Launchd(domain="gui/501", agents=tmp_path, run=fake, sleep=lambda s: None)
    L.install("io.x.menubar", b"<plist/>")
    L.kick("io.x.menubar")
    assert ["kickstart", "-k", "gui/501/io.x.menubar"] in fake.calls
    moved = L.remove("io.x.menubar")
    assert moved and moved.parent == tmp_path / "_disabled" and moved.name.startswith("io.x.menubar.plist.disabled-")
    assert not L.path("io.x.menubar").exists() and not L.loaded("io.x.menubar")
    assert L.remove("io.x.menubar") is None                                    # 두 번 해도 된다
    L.stop("io.x.none")                                                        # 없는 작업을 멈춰도 오류가 아니다


def test_a_failed_bootstrap_is_reported_not_swallowed(tmp_path):
    def run(args, **kw):
        if args[1] == "bootstrap":
            return subprocess.CompletedProcess(args, 5, "", "Bootstrap failed: 5: Input/output error")
        return subprocess.CompletedProcess(args, 113, "", "")
    L = ld.Launchd(domain="gui/501", agents=tmp_path, run=run, sleep=lambda s: None)
    import pytest
    with pytest.raises(RuntimeError, match="Input/output"):
        L.install("io.x.engine", b"<plist/>")


def test_same_job_loaded_but_not_running_is_started_again(tmp_path):
    """'끝내기'(종료값 0) 뒤 작업은 올라가 있지만 돌지 않는다 — start · install 이 다시 띄운다(검토 I5)."""
    calls = []

    def run(args, **kw):
        calls.append(args[1:])
        if args[1] == "print":
            return subprocess.CompletedProcess(args, 0, "state = not running\n", "")    # 올라가 있음 · pid 없음
        return subprocess.CompletedProcess(args, 0, "", "")
    L = ld.Launchd(domain="gui/501", agents=tmp_path, run=run, sleep=lambda s: None)
    L.path("io.x.menubar").write_bytes(b"<plist/>")
    assert L.install("io.x.menubar", b"<plist/>") == "started"
    assert ["kickstart", "gui/501/io.x.menubar"] in calls and not any(c[0] in ("bootout", "bootstrap") for c in calls)


def test_bootstrap_is_retried_right_after_a_bootout(tmp_path):
    """내린 직후의 올리기는 잠깐 실패할 수 있다(kiosk.sh 가 겪은 것) — 몇 번 다시 해 본다(검토 I9)."""
    fails = {"n": 2}
    loaded = set()

    def run(args, **kw):
        if args[1] == "print":
            return subprocess.CompletedProcess(args, 0 if "io.x.engine" in loaded else 113, "pid = 5\n", "")
        if args[1] == "bootstrap":
            if fails["n"]:
                fails["n"] -= 1
                return subprocess.CompletedProcess(args, 5, "", "Bootstrap failed: 5: Input/output error")
            loaded.add("io.x.engine")
        return subprocess.CompletedProcess(args, 0, "", "")
    L = ld.Launchd(domain="gui/501", agents=tmp_path, run=run, sleep=lambda s: None)
    assert L.install("io.x.engine", b"<plist/>") == "started" and fails["n"] == 0


def test_an_old_job_that_will_not_go_away_is_reported(tmp_path):
    def run(args, **kw):
        return subprocess.CompletedProcess(args, 0, "pid = 5\n", "")                   # 언제 물어도 떠 있다
    L = ld.Launchd(domain="gui/501", agents=tmp_path, run=run, sleep=lambda s: None)
    L.path("io.x.engine").write_bytes(b"<old/>")
    import pytest
    ld.GONE_SECONDS, saved = 0.05, ld.GONE_SECONDS
    try:
        with pytest.raises(RuntimeError, match="내려가지 않는다"):
            L.install("io.x.engine", b"<new/>")
    finally:
        ld.GONE_SECONDS = saved
    assert L.path("io.x.engine").read_bytes() == b"<old/>"                               # 새 판을 써 놓고 옛 판이 도는 일이 없다
