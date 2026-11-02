"""launchd 작업 한 곳(설계 10) — plist 는 이 파일의 plist() 하나가 만들고, 올리고 내리기는 Launchd 가 한다.

엔진(io.github.wideeye.engine — 늘 살림)과 메뉴 막대(…menubar — 비정상 종료 때만 되살림, '끝내기'는 다음 로그인까지 쉼)를
같은 꼴로 만든다. 탭5 창은 tools/kiosk.sh 가 맡는다(지금 이름 그대로). launchctl 의 bootout 은 비동기다 — 다시 올릴 때는
옛 작업이 다 내려갈 때까지 기다린다(2026-10-15 kiosk.sh 에서 겪은 경합: 내려가는 중에 "떠 있다"고 보고 올리지 않았다).
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import time
from datetime import date
from pathlib import Path

DEFAULT_PREFIX = "io.github.wideeye"
PREFIX = os.environ.get("URGENT_BOARD_LABEL_PREFIX", DEFAULT_PREFIX)        # 시험은 다른 접두(tests/conftest.py)
ENGINE = f"{PREFIX}.engine"
MENUBAR = f"{PREFIX}.menubar"
PATH_ENV = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"     # launchd 의 PATH 는 좁다(홈 CLAUDE.md)
THROTTLE = 30
GONE_SECONDS = 15.0      # bootout 뒤 다 내려가기를 기다리는 한도
BOOTSTRAP_TRIES = 5      # 내린 직후의 올리기는 잠깐 실패할 수 있다(kiosk.sh 와 같은 수)


def plist(label: str, args: list[str], *, workdir: str, logs: Path, keepalive: str, gui: bool = False,
          home: Path | None = None) -> bytes:
    """keepalive: "always"(죽으면 늘 되살림) · "crash"(종료값 0 이 아니면만 — 사람이 끝낸 것은 그대로 둔다).
    gui: 로그인한 화면에서만(메뉴 막대). home: 자료 폴더를 기본이 아닌 곳으로(시험)."""
    env = {"PATH": PATH_ENV, "PYTHONUNBUFFERED": "1"}
    if home is not None:
        env["URGENT_BOARD_HOME"] = str(home)
    if PREFIX != DEFAULT_PREFIX:          # 시험 · 다른 판의 이름 — 그 작업이 부르는 도구(메뉴 막대 → kiosk.sh)도 운영 이름이 아님을 알게(작업 8 검토 3)
        env["URGENT_BOARD_LABEL_PREFIX"] = PREFIX
    out = str(Path(logs) / f"{label}.out.log")
    d = {"Label": label, "ProgramArguments": [str(a) for a in args], "WorkingDirectory": str(workdir), "RunAtLoad": True,
         "KeepAlive": True if keepalive == "always" else {"SuccessfulExit": False}, "ThrottleInterval": THROTTLE,
         "EnvironmentVariables": env, "StandardOutPath": out, "StandardErrorPath": out}
    if gui:
        d.update(LimitLoadToSessionType="Aqua", ProcessType="Interactive")
    return plistlib.dumps(d, sort_keys=True)


class Launchd:
    def __init__(self, domain: str | None = None, agents: Path | None = None, run=subprocess.run, sleep=time.sleep):
        self.domain = domain or f"gui/{os.getuid()}"
        self.agents = Path(agents) if agents else Path.home() / "Library" / "LaunchAgents"
        self.run, self.sleep = run, sleep

    def _ctl(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(["launchctl", *args], capture_output=True, text=True, timeout=30)

    def path(self, label: str) -> Path:
        return self.agents / f"{label}.plist"

    def _print(self, label: str) -> subprocess.CompletedProcess:
        return self._ctl("print", f"{self.domain}/{label}")

    def loaded(self, label: str) -> bool:
        return self._print(label).returncode == 0

    def pid(self, label: str) -> int | None:
        r = self._print(label)
        m = re.search(r"^\s*pid = (\d+)", r.stdout or "", re.M) if r.returncode == 0 else None
        return int(m.group(1)) if m else None

    def _wait_gone(self, label: str) -> None:
        end = time.monotonic() + GONE_SECONDS
        while self.loaded(label):
            if time.monotonic() >= end:
                raise RuntimeError(f"{label} 가 {GONE_SECONDS:.0f}초 넘게 내려가지 않는다 — launchctl print {self.domain}/{label}")
            self.sleep(0.3)

    def install(self, label: str, data: bytes) -> str:
        """"same"(같은 등록 · 돌고 있음) · "started"(새로 · 또는 올라가 있었지만 돌지 않던 것을 띄움) · "replaced"(옛 등록을 내리고 새로)."""
        p = self.path(label)
        old = p.read_bytes() if p.exists() else None
        was = self.loaded(label)
        if old == data and was:
            if self.pid(label) is None:                         # '끝내기'(0) 뒤 — 올라가 있지만 돌지 않는다
                self._ctl("kickstart", f"{self.domain}/{label}")
                return "started"
            return "same"
        if was:
            self._ctl("bootout", f"{self.domain}/{label}")
            self._wait_gone(label)                              # 못 내리면 새 판을 쓰기 전에 멈춘다
        self.agents.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        err = ""
        for i in range(BOOTSTRAP_TRIES):
            r = self._ctl("bootstrap", self.domain, str(p))
            if r.returncode == 0 or self.loaded(label):
                return "replaced" if was else "started"
            err = (r.stderr or r.stdout or "").strip()[:200]
            self.sleep(1)
        raise RuntimeError(f"{label} 를 올리지 못했다: {err}")

    def kick(self, label: str) -> None:
        self._ctl("kickstart", "-k", f"{self.domain}/{label}")

    def stop(self, label: str) -> None:
        """내린다(다음 로그인 때 다시 뜬다 — 아예 빼려면 remove)."""
        if self.loaded(label):
            self._ctl("bootout", f"{self.domain}/{label}")
            self._wait_gone(label)

    def remove(self, label: str) -> Path | None:
        """내리고 plist 를 _disabled/ 로 옮긴다(지우지 않는다). 옮긴 곳 — 없었으면 None."""
        self.stop(label)
        p = self.path(label)
        if not p.exists():
            return None
        dest = self.agents / "_disabled" / f"{label}.plist.disabled-{date.today().isoformat()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, dest)
        return dest
