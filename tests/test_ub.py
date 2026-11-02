"""관리 명령(tools/ub.py) — 가짜 launchd · 가짜 HTTP · 가짜 명령 실행으로. 운영 이름 · 운영 자료 폴더 · 5091 을 건드리지 않는다."""
import io
import json
import plistlib
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from urgent_board import paths, settings_doc as sd
from tools import launchd as ld
from tools import ub

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "home" / "settings.yaml"


class FakeLD:
    def __init__(self, agents):
        self.agents, self.jobs, self.calls = Path(agents), {}, []

    def path(self, label):
        return self.agents / f"{label}.plist"

    def install(self, label, data):
        self.calls.append(("install", label))
        same = self.jobs.get(label) == data
        self.jobs[label] = data
        self.agents.mkdir(parents=True, exist_ok=True)
        self.path(label).write_bytes(data)
        return "same" if same else "started"

    def loaded(self, label):
        return label in self.jobs

    def pid(self, label):
        return 42 if label in self.jobs and label not in getattr(self, "quit", ()) else None

    def kick(self, label):
        self.calls.append(("kick", label))

    def stop(self, label):
        self.calls.append(("stop", label))

    def remove(self, label):
        self.calls.append(("remove", label))
        self.path(label).unlink(missing_ok=True)
        return self.jobs.pop(label, None) and self.agents / "_disabled" / label


class Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def make(tmp_path, settings=None, answers=None):
    home = tmp_path / "home"
    if settings is not None:
        paths.ensure(home)
        shutil.copy(settings, home / "settings.yaml")
    runs, reqs = [], []

    def run(args, **kw):
        runs.append([str(a) for a in args])
        return subprocess.CompletedProcess(args, 0, "", "")

    def opener(req, timeout):
        reqs.append(req)
        path = urlparse(req.full_url).path
        body = (answers or {}).get(path, {"ok": True})
        if body is None:
            raise ConnectionRefusedError(61, "refused")
        r = Resp(json.dumps(body).encode())
        r.status = 202 if path == "/api/restart" else 200                               # 엔진의 실제 답(web_settings.restart)
        return r
    out = []
    ctx = ub.Ctx(home=home, launchd=FakeLD(tmp_path / "agents"), run=run, opener=opener, say=out.append, sleep=lambda s: None, kiosk=True)
    return ctx, runs, reqs, out


def test_install_on_a_new_mac_writes_an_example_and_registers_engine_and_menubar(tmp_path):
    ctx, runs, reqs, out = make(tmp_path)
    assert ub.install(ctx, open_browser=False) == 0
    assert sd.read(ctx.home)[0]["service"]["engine"] == "launchd"                         # 예시 설정
    engine = plistlib.loads(ctx.launchd.jobs[ld.ENGINE])
    assert engine["ProgramArguments"][-1].endswith("run_board.py") and engine["KeepAlive"] is True
    assert engine["EnvironmentVariables"]["URGENT_BOARD_HOME"] == str(ctx.home)        # 기본이 아닌 자료 폴더를 넘긴다
    menubar = plistlib.loads(ctx.launchd.jobs[ld.MENUBAR])
    assert menubar["ProgramArguments"][-1].endswith("menubar.py") and menubar["KeepAlive"] == {"SuccessfulExit": False}
    assert not any("kiosk.sh" in " ".join(r) for r in runs) and not any(r[0] == "/usr/bin/open" for r in runs)
    assert any("전체 디스크 접근" in x for x in out)
    text = "\n".join(out)                                                              # 끝의 말은 2판의 길(검토 1)
    assert f"{ROOT}/bin/wideeye guide next" in text and "GUIDE.md" in text
    assert "setup guide" not in text and "붙여 넣는다" not in text and "다시 시작한다" not in text
    ctx.launchd.calls.clear()
    assert ub.install(ctx, open_browser=False) == 0                                    # 두 번 해도 된다
    assert all(c[0] == "install" for c in ctx.launchd.calls)
    assert ld.ENGINE.startswith("io.examplecorp.urgent-board-test.")                       # 시험은 운영 이름을 쓰지 않는다(conftest)


def test_install_here_leaves_the_external_engine_alone_and_starts_tab5(tmp_path):
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE)                            # service.engine: external · 탭5 일련번호 있음
    assert ub.install(ctx, open_browser=False) == 0
    assert ld.ENGINE not in ctx.launchd.jobs and ld.MENUBAR in ctx.launchd.jobs
    assert [str(ROOT / "tools" / "kiosk.sh"), "start"] in runs


def test_install_opens_the_setup_page_and_prints_the_next_step(tmp_path):
    ctx, runs, reqs, out = make(tmp_path)
    paths.ensure(ctx.home)
    (paths.auth_dir(ctx.home) / "api-token").write_text("A" * 43 + "\n")
    assert ub.install(ctx) == 0
    opened = [r for r in runs if r[0] == "/usr/bin/open"]
    assert opened and opened[-1][1].endswith("/auth?t=" + "A" * 43 + "&next=/setup")           # 설정 한눈에(2026-10-16 AI 와 함께 설정)
    assert any(x.startswith("다음: AI 에게") and "guide next" in x for x in out)               # 2판의 길(작업 8 검토 1 — 1판 'setup guide' 말 대신)
    assert any("healthz" in r.full_url for r in reqs)
    assert not any("A" * 43 in x for x in out)                                       # 열쇠를 화면에 내지 않는다


def test_uninstall_moves_jobs_aside_and_keeps_the_data(tmp_path):
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE)
    ub.install(ctx, open_browser=False)
    assert ub.uninstall(ctx) == 0
    assert ("remove", ld.MENUBAR) in ctx.launchd.calls and ("remove", ld.ENGINE) in ctx.launchd.calls
    assert [str(ROOT / "tools" / "kiosk.sh"), "uninstall"] in runs
    assert (ctx.home / "settings.yaml").exists()


def test_restart_engine_asks_the_engine_itself_when_it_is_external(tmp_path):
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE)
    paths.api_token(ctx.home, create=True)
    assert ub.control(ctx, "restart", "engine") == 0
    r = reqs[-1]
    assert r.full_url.endswith("/api/restart") and r.get_method() == "POST" and r.get_header("X-ub-token")
    assert r.get_header("Origin") == f"http://127.0.0.1:{paths.read_light(ctx.home)['port']}" and r.get_header("Content-type") == "application/json"
    ctx2, *_ = make(tmp_path / "b")
    sd.save(ctx2.home, sd.example(), None)
    ub.control(ctx2, "restart", "all")
    assert ("kick", ld.ENGINE) in ctx2.launchd.calls or ("install", ld.ENGINE) in ctx2.launchd.calls


def test_open_uses_the_token_and_status_reads_counts(tmp_path):
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE,
                                answers={"/api/status": {"totals": {"tasks": 5, "unread": 2}, "blind": "", "down": [], "checked_at": "09:00", "cards": [], "tasks": []}})
    t = paths.api_token(ctx.home, create=True)
    assert ub.open_page(ctx, "list") == 0 and runs[-1] == ["/usr/bin/open", f"http://127.0.0.1:5091/auth?t={t}&next=/list"]
    assert ub.status(ctx) == 0
    assert any("할 일 5 · 안 읽음 2" in x for x in out) and not any(t in x for x in out)


def test_doctor_warns_on_an_empty_mac_and_bundles_without_names(tmp_path):
    ctx, runs, reqs, out = make(tmp_path, answers={"/healthz": None})
    assert ub.doctor(ctx) == 1
    assert any(x.startswith("주의") for x in out)
    ctx, runs, reqs, out = make(tmp_path / "b", settings=FIXTURE, answers={"/healthz": None})
    (paths.logs_dir(ctx.home) / "board.log").write_text("보낸 사람 someone@example.com · 010-1234-5678 에게\n", encoding="utf-8")
    z = tmp_path / "diag.zip"
    ub.doctor(ctx, bundle=z)
    with zipfile.ZipFile(z) as f:
        names = set(f.namelist())
        text = "".join(f.read(n).decode("utf-8") for n in names)
    assert {"doctor.txt", "settings.yaml", "logs/board.log"} <= names
    assert "합창단" not in text and "@example.com" not in text and "010-1234-5678" not in text and "<메일>" in text


def test_the_command_wrapper_runs_from_a_link(tmp_path):
    venv = ROOT / ".venv" / "bin" / "python"
    if not venv.exists():
        import pytest
        pytest.skip("작업 폴더에 가상 환경이 없다")
    link = tmp_path / "urgentboard"
    link.symlink_to(ROOT / "bin" / "urgentboard")
    r = subprocess.run([str(link), "--help"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "install" in r.stdout and "doctor" in r.stdout


def test_install_script_is_valid_sh():
    r = subprocess.run(["sh", "-n", str(ROOT / "install.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "install/wideeye-install.sh" in text                       # 2판: 설치 스크립트에 맡긴다(tests/test_install_script.py)
    assert '"$WE" install --no-open' in (ROOT / "install" / "wideeye-install.sh").read_text(encoding="utf-8")    # WE=…/bin/wideeye


def test_test_names_never_touch_the_tab5_window(tmp_path):
    """시험 이름(접두가 운영과 다름)으로 돌 때는 탭5 창(kiosk.sh — 운영 이름 하나뿐)을 건드리지 않는다(검토 C1 — 실제 시험의 uninstall 이
    운영 탭5 창 작업을 내렸다)."""
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE)
    assert ub.Ctx().kiosk is False                                                     # conftest 의 시험 접두
    ctx.kiosk = False
    ctx.launchd.agents.mkdir(parents=True, exist_ok=True)
    ctx.launchd.path(ub.KIOSK_LABEL).write_bytes(b"<plist/>")
    ub.install(ctx, open_browser=False)
    ub.control(ctx, "restart", "all")
    ub.status(ctx)
    ub.doctor(ctx)
    ub.uninstall(ctx)
    assert not any("kiosk.sh" in " ".join(r) for r in runs)


def test_restart_engine_counts_202_as_accepted(tmp_path):
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE)
    paths.api_token(ctx.home, create=True)
    assert ub.control(ctx, "restart", "engine") == 0 and any("다시 시작한다" in x for x in out)


def test_no_tab5_means_the_display_is_never_touched(tmp_path):
    """탭5 가 없는 맥에서 restart(기본 all)가 탭5 창 작업을 새로 올리지 않는다(검토 I4)."""
    ctx, runs, reqs, out = make(tmp_path)
    sd.save(ctx.home, sd.example(), None)
    for verb in ("start", "restart", "stop"):
        ub.control(ctx, verb, "all")
        ub.control(ctx, verb, "display")
    assert not any("kiosk.sh" in " ".join(r) for r in runs)


def test_a_broken_settings_file_never_makes_an_engine_job(tmp_path):
    """설정이 깨져도 엔진은 last-good 으로 돈다 — 그때 관리 명령이 '엔진은 launchd' 로 오해해 감시를 둘로 만들지 않는다(검토 I6)."""
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE)                            # external
    shutil.copy(FIXTURE, paths.last_good_file(ctx.home))
    paths.settings_file(ctx.home).write_text("boxes: [\n", encoding="utf-8")
    ub.install(ctx, open_browser=False)
    ub.control(ctx, "restart", "all")
    assert ld.ENGINE not in ctx.launchd.jobs                                             # last-good 이 external
    paths.last_good_file(ctx.home).unlink()
    ub.install(ctx, open_browser=False)
    ub.control(ctx, "start", "engine")
    assert ld.ENGINE not in ctx.launchd.jobs and any("엔진은 건드리지 않는다" in x for x in out)


def test_requests_to_the_engine_skip_proxies(tmp_path, monkeypatch):
    """시스템 · 환경 프록시가 있어도 127.0.0.1 로 곧바로 — 열쇠가 프록시로 가지 않게(검토 I7)."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"totals": {"urgent": 1, "waiting": 0}, "blind": "", "down": [], "cards": []}'
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    try:
        from tools import menubar as mb
        assert mb.fetch(srv.server_port, "T") is not None
        home = tmp_path / "h"
        d = sd.example(); d["server"]["port"] = srv.server_port
        sd.save(home, d, None)
        assert ub.http(ub.Ctx(home=home), "GET", "/api/status")[0] == 200
    finally:
        srv.shutdown()


def test_doctor_bundle_keeps_only_safe_fields(tmp_path):
    """가린 묶음에는 허락한 칸(색 · 켬/끔 · 출처 · 방 번호 · 시각 …)만 — 로마자 이름 · 별칭 · 거래처 주소 · 전화번호는 남지 않는다(검토 I10)."""
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE, answers={"/healthz": None})
    doc = sd.read(ctx.home)[0]
    doc.setdefault("contacts", []).append({"name": "Hong", "ids": ["010-1234-5678", "kim@clientco.example", "hgd77"]})
    sd.save(ctx.home, doc, None)
    z = tmp_path / "d.zip"
    ub.doctor(ctx, bundle=z)
    with zipfile.ZipFile(z) as f:
        text = f.read("settings.yaml").decode("utf-8")
    for leaked in ("010-1234-5678", "clientco", "hgd77", "snack", "hobby", "Hong", "@"):
        assert leaked not in text, leaked
    assert "external" in text and "yellow" in text


def test_doctor_says_when_the_menubar_was_quit(tmp_path):
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE, answers={"/healthz": None})
    ub.install(ctx, open_browser=False)
    ctx.launchd.quit = {ld.MENUBAR}                                                     # '끝내기' 뒤 — 올라가 있지만 돌지 않는다
    out.clear()
    ub.doctor(ctx)
    assert any("메뉴 막대 작업" in x and "돌지 않는다" in x for x in out)


def test_control_says_blocked_instead_of_crashing_when_a_sandbox_denies_launchagents(tmp_path):
    """2026-10-16 Codex 시험: AI 의 샌드박스가 ~/Library/LaunchAgents 쓰기를 막으면 날 오류 대신 한 줄로."""
    ctx, runs, reqs, out = make(tmp_path)
    def deny(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    ctx.launchd.install = deny
    assert ub.control(ctx, "restart", "menubar") == 1
    assert any("권한이 막혔다" in x for x in out)


def test_doctor_whatsapp_lines(tmp_path):                   # 왓츠앱 — 엔진의 /api/connect 값으로만 말한다(터미널의 권한이 아니라)
    from urgent_board.connect.probes import NO_ACCESS
    doc = {"accounts": {"whatsapp": {"enabled": True}}}
    for probe, want in (({"ok": False, "why": NO_ACCESS, "running": True}, "전체 디스크 접근 권한"),
                        ({"ok": True, "why": "", "running": False}, "앱이 꺼져 있다"),
                        ({"ok": False, "why": "자료가 없어요(이 맥에서 그 앱을 쓴 적이 없을 수 있어요).", "running": False}, "자료가 없다")):
        ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE, answers={"/api/connect": {"sources": {"whatsapp": {"state": "ok"}}, "whatsapp": probe, "python": "/py"}})
        good, warn = [], []
        ub._engine_checks(ctx, doc, "T", good.append, warn.append)
        assert any(want in w and "왓츠앱" in w for w in warn), (probe, warn)
    ctx, runs, reqs, out = make(tmp_path, settings=FIXTURE, answers={"/api/connect": {"sources": {"whatsapp": {"state": "ok"}}, "whatsapp": {"ok": True, "why": "", "running": True}}})
    good, warn = [], []
    ub._engine_checks(ctx, doc, "T", good.append, warn.append)
    assert any("왓츠앱" in g for g in good) and not any("왓츠앱" in w for w in warn)
