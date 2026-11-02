import os
import shutil
import threading
from pathlib import Path

from urgent_board import config, paths, settings_doc as sd

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"


def test_engine_lock_is_exclusive(tmp_path):
    import run_board
    a = run_board.engine_lock(tmp_path)
    assert a is not None
    assert run_board.engine_lock(tmp_path) is None                 # 둘째 엔진은 잠금을 못 쥔다 — 수집기를 하나도 띄우지 않고 끝난다
    os.close(a)
    b = run_board.engine_lock(tmp_path)
    assert b is not None
    os.close(b)


def test_watch_loop_swaps_then_restarts_only_for_connections(tmp_path):
    import run_board
    sd.save(tmp_path, sd.example(), None)
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    stop, why = threading.Event(), []
    t = threading.Thread(target=run_board.watch_loop, args=(live, stop, why.append), kwargs={"every": 0.05}, daemon=True)
    t.start()
    d, v = sd.read(tmp_path); d["boxes"][0]["keywords"] = ["바뀜"]; v = sd.save(tmp_path, d, v)
    for _ in range(100):
        if live.get().businesses[0].keywords == ["바뀜"]:
            break
        stop.wait(0.02)
    assert live.get().businesses[0].keywords == ["바뀜"] and why == []
    d["accounts"]["kakao"]["enabled"] = True; sd.save(tmp_path, d, v)
    for _ in range(100):
        if why:
            break
        stop.wait(0.02)
    stop.set(); t.join(2)
    assert why and "연결" in why[0]


def test_restart_kills_children_then_execs(monkeypatch):
    import run_board
    from urgent_board import summarize
    calls = []
    monkeypatch.setattr(summarize, "stop_running", lambda: calls.append("stop") or 0)
    run_board.restart("시험", execv=lambda exe, argv: calls.append(("exec", exe, tuple(argv))))
    assert calls[0] == "stop" and calls[1][0] == "exec" and calls[1][2][0] == calls[1][1]


def test_health_loop_promotes_once_after_first_success():
    import run_board
    stop, promoted, n = threading.Event(), [], [0]
    def check(port):
        n[0] += 1
        if n[0] >= 3:
            stop.set()
        return True
    run_board.health_loop(5091, stop, check=check, die=lambda c: None, every=0.01, on_healthy=lambda: promoted.append(1))
    assert promoted == [1]


def test_create_app_follows_live_settings(tmp_path):
    from urgent_board.store import Store
    from urgent_board.web import create_app
    shutil.copy(HOME / "settings.yaml", tmp_path / "settings.yaml")
    live = config.Live(tmp_path, config.load_for_engine(tmp_path))
    app = create_app(live, Store(tmp_path / "t.db"))
    first = app.test_client().get("/api/items").get_json()["cards"][0]["name"]
    d, v = sd.read(tmp_path); d["boxes"][0]["name"] = "새 이름"; sd.save(tmp_path, d, v)
    assert live.check() == "swapped"
    assert app.test_client().get("/api/items").get_json()["cards"][0]["name"] == "새 이름" != first


def test_find_claude_prefers_path_then_common_places(monkeypatch, tmp_path):
    from urgent_board import summarize
    monkeypatch.setattr(summarize.shutil, "which", lambda name: None)
    monkeypatch.setattr(summarize, "CLAUDE_PATHS", (str(tmp_path / "none"), str(tmp_path / "claude")))
    assert summarize.find_claude() == "claude"                                   # 못 찾으면 이름 그대로(부를 때 실패가 "요약 실패"로 보인다)
    (tmp_path / "claude").write_text("#!/bin/sh\n"); (tmp_path / "claude").chmod(0o755)
    assert summarize.find_claude() == str(tmp_path / "claude")


def test_run_tracked_records_and_stops_children():
    import time
    from urgent_board import summarize
    out = summarize.run_tracked(["/bin/echo", "hi"], capture_output=True, text=True, timeout=5, stdin=summarize.subprocess.DEVNULL)
    assert out.returncode == 0 and out.stdout.strip() == "hi" and not summarize._running
    got = {}
    t = threading.Thread(target=lambda: got.setdefault("r", summarize.run_tracked(["/bin/sleep", "30"], capture_output=True, text=True, timeout=60)))
    t.start()
    for _ in range(100):
        if summarize._running:
            break
        time.sleep(0.02)
    assert summarize.stop_running() == 1
    t.join(5)
    assert not t.is_alive() and got["r"].returncode != 0 and not summarize._running


def test_handled_loop_reads_live_settings_each_cycle(tmp_path, monkeypatch):
    import run_board
    from urgent_board.store import Store
    off = config.load(HOME); off.handled = {**off.handled, "claude_sessions": False}
    live = config.Live(tmp_path, off)
    stop, seen = threading.Event(), []
    def fake_cycle(store, settings, now):
        seen.append(settings); stop.set(); return 0
    monkeypatch.setattr(run_board, "handled_cycle", fake_cycle)
    monkeypatch.setattr(run_board, "HANDLED_EVERY", 0.01)
    on = config.load(HOME); on.handled = {**on.handled, "claude_sessions": True}
    threading.Timer(0.05, lambda: setattr(live, "_s", on)).start()          # 꺼진 채 시작했다가 그 사이 켬
    t = threading.Thread(target=run_board.handled_loop, args=(Store(tmp_path / "t.db"), live, stop), daemon=True)
    t.start(); t.join(3)
    assert seen and seen[0] is on                                          # 회차마다 지금 설정 — 다시 시작하지 않아도 켜면 돈다


def test_restart_does_not_hand_open_sockets_to_the_new_engine(monkeypatch):
    """werkzeug 는 듣는 소켓을 늘 물려줌(inheritable)으로 둔다 — exec 뒤 새 엔진이 같은 포트를 못 열었다(실측 2026-10-15)."""
    import run_board
    from urgent_board import summarize
    monkeypatch.setattr(summarize, "stop_running", lambda: 0)
    r, w = os.pipe()
    os.set_inheritable(r, True)
    monkeypatch.setenv("WERKZEUG_SERVER_FD", str(r))
    try:
        run_board.restart("시험", execv=lambda exe, argv: None)
        assert os.get_inheritable(r) is False and "WERKZEUG_SERVER_FD" not in os.environ
    finally:
        os.close(r); os.close(w)


def test_fallback_start_does_not_touch_the_rejected_versions_record(tmp_path):
    """검토 C1: last-good 으로 뜬 엔진이 boot.json 을 덮어쓰면 나쁜 판의 시작 횟수가 지워져 그 판으로 다시 돈다."""
    import run_board
    v = sd.save(tmp_path, sd.example(), None); sd.boot_ok(tmp_path, v)
    bad = sd.save(tmp_path, dict(sd.example(), etc_name="못 버팀"), None)
    for _ in range(config.BOOT_LIMIT):
        sd.boot_begin(tmp_path, bad)
    s = config.load_for_engine(tmp_path)
    assert run_board.boot_record(tmp_path, s) is False and sd.boot_attempts(tmp_path, bad) == config.BOOT_LIMIT
    sd.save(tmp_path, dict(sd.example(), etc_name="고침"), None)
    s2 = config.load_for_engine(tmp_path)
    assert run_board.boot_record(tmp_path, s2) is True and sd.boot_attempts(tmp_path, s2.version) == 1


def test_bind_first_refuses_a_busy_port():
    import socket
    import run_board
    from urgent_board.store import Store
    from urgent_board.web import create_app
    busy = socket.socket(); busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); busy.bind(("127.0.0.1", 0)); busy.listen(1)
    port = busy.getsockname()[1]
    try:
        assert run_board.bind(object(), port) is None                                   # 옛 엔진이 포트를 쥐고 있다 — 아무것도 띄우지 않는다
    finally:
        busy.close()


def test_engine_with_a_busy_port_starts_no_collectors(tmp_path):
    """검토 I3: 옛 엔진(잠금을 모름)이 살아 있는 동안 새 엔진이 수집기(텔레그램 세션)를 띄우면 세션이 폐기된다 — 포트를 먼저 연다."""
    import socket
    import subprocess
    import sys
    busy = socket.socket(); busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); busy.bind(("127.0.0.1", 0)); busy.listen(1)
    port = busy.getsockname()[1]
    try:
        d = sd.example(); d["server"]["port"] = port; d["accounts"]["imessage"]["enabled"] = True
        sd.save(tmp_path, d, None)
        env = dict(os.environ, URGENT_BOARD_HOME=str(tmp_path))
        r = subprocess.run([sys.executable, str(ROOT / "run_board.py")], env=env, capture_output=True, text=True, timeout=60)
        log = (tmp_path / "logs" / "board.log").read_text(encoding="utf-8")
        assert r.returncode == 1 and "포트" in log and "urgent-board 시작" not in log      # 수집기를 띄우는 시작 줄이 없다
    finally:
        busy.close()


def test_engine_adopts_a_staged_telegram_session_only_after_it_owns_the_port(tmp_path):
    """연결 화면이 맡겨 둔 세션은 포트를 연 엔진이 수집기를 띄우기 전에 바꿔 놓는다 — 옛 엔진이 살아 있으면(포트를 쥠) 손대지 않는다(검토 I4)."""
    import socket
    import subprocess
    import sys
    import time
    auth = tmp_path / "auth"
    busy = socket.socket(); busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); busy.bind(("127.0.0.1", 0)); busy.listen(1)
    port = busy.getsockname()[1]
    d = sd.example(); d["server"]["port"] = port
    sd.save(tmp_path, d, None)
    auth.mkdir(mode=0o700, exist_ok=True)
    (auth / "telethon.session").write_bytes(b"OLD"); (auth / "telethon.session-journal").write_bytes(b"J"); (auth / "telegram-ready.session").write_bytes(b"NEW")
    env = dict(os.environ, URGENT_BOARD_HOME=str(tmp_path))
    try:
        r = subprocess.run([sys.executable, str(ROOT / "run_board.py")], env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 1 and (auth / "telethon.session").read_bytes() == b"OLD"          # 옛 엔진이 쓰는 중 — 그대로
    finally:
        busy.close()
    p = subprocess.Popen([sys.executable, str(ROOT / "run_board.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.monotonic() + 30
        while time.monotonic() < end and (auth / "telethon.session").read_bytes() != b"NEW":
            time.sleep(0.2)
        assert (auth / "telethon.session").read_bytes() == b"NEW" and not (auth / "telethon.session-journal").exists()
    finally:
        p.terminate(); p.wait(timeout=20)


def test_log_lines_go_to_the_rotating_file_only_unless_a_person_watches(tmp_path):
    """launchd 가 띄운 엔진의 표준 출력은 돌려 쓰지 않는 <작업>.out.log 로 간다 — 줄마다 거기에도 쓰면 끝없이 자란다(검토)."""
    import logging
    import run_board
    paths.ensure(tmp_path)
    hs = run_board.log_handlers(tmp_path, tty=False)
    assert [type(h).__name__ for h in hs] == ["RotatingFileHandler"]
    assert any(isinstance(h, logging.StreamHandler) and type(h).__name__ == "StreamHandler" for h in run_board.log_handlers(tmp_path, tty=True))
    for h in hs:
        h.close()
