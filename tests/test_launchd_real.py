"""실제 launchd 로 올려 본다(설계 10 — 시험 이름 · 다른 자료 폴더 · 다른 포트). 실제 작업을 올리므로 URGENT_BOARD_LAUNCHD_TEST=1 일 때만.

    URGENT_BOARD_LAUNCHD_TEST=1 .venv/bin/python -m pytest -q tests/test_launchd_real.py     # 2분쯤

운영 이름(io.examplecorp.urgent-board.* · io.github.wideeye.kiosk) · 운영 자료 폴더 · 5091 은 쓰지 않는다. 탭5 창은 올리지 않는다.
메뉴 막대 시험 동안 맥 맨 위 막대에 시험용 글자가 잠깐 하나 더 보인다. 작업을 올릴 때마다 macOS 가 "앱 백그라운드 활동"
알림('sh' · 'python3' 앱은 백그라운드에서 실행될 수 있습니다)을 띄운다 — 해는 없고 닫으면 된다(2026-10-15 실측).
"""
import os
import plistlib
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from urgent_board import paths, settings_doc as sd
from tools import launchd as ld

ROOT = Path(__file__).resolve().parents[1]
PORT = 5192
PREFIX = "io.examplecorp.urgent-board-test"

pytestmark = pytest.mark.skipif(os.environ.get("URGENT_BOARD_LAUNCHD_TEST") != "1", reason="실제 launchd 시험은 URGENT_BOARD_LAUNCHD_TEST=1 일 때만")


def wait(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.5)
    return cond()


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=2) as r:
            r.read()
            return r.status == 200
    except OSError:
        return False


def runs(label: str) -> int:
    out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.strip().startswith("runs = "):
            return int(line.split("=")[1])
    return 0


@pytest.fixture
def home(tmp_path):
    assert ld.PREFIX == PREFIX                                                  # conftest — 운영 이름이 아니다
    h = tmp_path / "home"
    d = sd.example()
    d["server"]["port"] = PORT
    d["accounts"]["imessage"]["enabled"] = False                               # 아무것도 모으지 않는 엔진
    sd.save(h, d, None)
    env = dict(os.environ, URGENT_BOARD_HOME=str(h), URGENT_BOARD_LABEL_PREFIX=PREFIX)
    L = ld.Launchd()
    kiosk = L.path("io.github.wideeye.kiosk")
    before = (kiosk.read_bytes() if kiosk.exists() else None, L.loaded("io.github.wideeye.kiosk"))
    yield h, env
    after = (kiosk.read_bytes() if kiosk.exists() else None, L.loaded("io.github.wideeye.kiosk"))
    assert after == before, "운영 탭5 창 작업이 바뀌었다 — tools/kiosk.sh start 로 되살린다"      # 검토 C1
    for label in (ld.ENGINE, ld.MENUBAR, f"{PREFIX}.exit0", f"{PREFIX}.crash"):
        L.stop(label)
        L.path(label).unlink(missing_ok=True)
    for p in (L.agents / "_disabled").glob(f"{PREFIX}.*"):
        p.unlink()
    assert not subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout.count(PREFIX)


def ub(env, *args):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "ub.py"), *args], env=env, capture_output=True, text=True, timeout=120)


def test_engine_installs_answers_comes_back_after_a_kill_and_uninstalls(home):
    h, env = home
    r = ub(env, "install", "--no-open", "--no-menubar")
    assert r.returncode == 0, r.stdout + r.stderr
    L = ld.Launchd()
    assert L.loaded(ld.ENGINE) and wait(healthy, 40) and paths.api_token(h)            # 열쇠는 엔진이 처음 뜰 때
    pid = L.pid(ld.ENGINE)
    os.kill(pid, signal.SIGKILL)
    assert wait(lambda: (L.pid(ld.ENGINE) or pid) != pid and healthy(), 60)              # ThrottleInterval 30 안쪽
    assert ub(env, "status").returncode == 0
    assert ub(env, "stop", "engine").returncode == 0 and wait(lambda: not healthy(), 15) and not L.loaded(ld.ENGINE)
    assert ub(env, "uninstall").returncode == 0
    assert not L.path(ld.ENGINE).exists() and list((L.agents / "_disabled").glob(f"{ld.ENGINE}.plist.disabled-*"))
    assert (h / "settings.yaml").exists()


def test_crash_only_keepalive_respects_a_clean_exit(home):
    """메뉴 막대의 KeepAlive 꼴('끝내기' = 0 은 쉬고, 그 밖은 되살림)을 실제 launchd 로."""
    L = ld.Launchd()
    logs = paths.logs_dir(home[0])
    logs.mkdir(parents=True, exist_ok=True)
    L.install(f"{PREFIX}.exit0", ld.plist(f"{PREFIX}.exit0", ["/bin/sh", "-c", "sleep 1; exit 0"], workdir="/", logs=logs, keepalive="crash"))
    L.install(f"{PREFIX}.crash", ld.plist(f"{PREFIX}.crash", ["/bin/sh", "-c", "sleep 1; exit 3"], workdir="/", logs=logs, keepalive="crash"))
    assert wait(lambda: runs(f"{PREFIX}.crash") >= 2, 45)
    assert runs(f"{PREFIX}.exit0") == 1


def test_menubar_job_shows_and_comes_back_after_a_crash(home):
    h, env = home
    r = ub(env, "start", "menubar")
    assert r.returncode == 0, r.stdout + r.stderr
    L = ld.Launchd()
    assert wait(lambda: L.pid(ld.MENUBAR) is not None, 20)
    data = plistlib.loads(L.path(ld.MENUBAR).read_bytes())
    assert data["LimitLoadToSessionType"] == "Aqua" and data["EnvironmentVariables"]["URGENT_BOARD_HOME"] == str(h)
    pid = L.pid(ld.MENUBAR)
    time.sleep(3)
    assert L.pid(ld.MENUBAR) == pid                                                     # 엔진이 없어도 살아 있다(확인 불가)
    os.kill(pid, signal.SIGKILL)
    assert wait(lambda: (L.pid(ld.MENUBAR) or pid) != pid, 45)
    assert ub(env, "stop", "menubar").returncode == 0 and not L.loaded(ld.MENUBAR)
