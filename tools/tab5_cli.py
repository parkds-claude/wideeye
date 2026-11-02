"""탭5 펌웨어 굽기 · 되돌리기 — `wideeye tab5 flash [--serial S] [--image F] [--yes]` · `wideeye tab5 restore <백업.bin> [--serial S] [--yes]`.

판단(esptool 출력 읽기 · 기기 고르기 · 거절할 까닭)은 urgent_board/tab5_flash.py, 여기는 부작용만 — 탭5 창에게서 포트 빌리기 ·
esptool 하위 프로세스 · 이미지 받기 · 백업 파일. 계획 FL-D5 ~ FL-D8(~/.claude/plans 의 계획, 요약은 firmware/README.md).

--yes 가 없으면 확인만 한다 — 기기에 아무 신호도 보내지 않는다(FL-D5). AI 는 그 결과를 사람에게 보이고 승인을 받은 뒤 --yes 로 부른다.
굽기 차례(FL-D7 — 앞 단계가 됐을 때만 다음): 포트 빌리기 → 칩 확인 → 플래시 16MB 백업 → MAC 다시 → 쓰기 → 새 펌웨어가 답하는지 →
설정에 그 탭5 의 일련번호 → 포트 돌려주기(늘). 쓰기 전에 멈추면 감시 타이머 리셋으로 원래 펌웨어로 돌려보낸다
(hard reset 이면 ESP32-P4 가 내려받기 모드에 남는다 — 2026-11-02 겪음).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import signal
import tempfile
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import ub                                   # noqa: E402
from urgent_board import paths, setup_steps           # noqa: E402
from urgent_board import tab5_flash as tf             # noqa: E402
from urgent_board.connect import probes               # noqa: E402

CMD = shlex.quote(str(setup_steps.command(ROOT)))
Q = shlex.quote                      # 안내하는 명령 줄의 경로는 늘 따옴표로(자료 폴더가 'Application Support' — 그대로 붙이면 둘로 갈린다. 코덱스 검토 2026-11-03)
LOAN_MIN = 30                       # 포트를 빌리는 시간(분) — 넘기면 탭5 창이 스스로 다시 잡는다(놓기 파일의 만료)
WAIT_RELEASE_S = 10                 # 탭5 창이 포트를 놓았다는 확인을 기다리는 시간
NEED_FREE = 64 * 1024 * 1024        # 백업 · 사본을 쓰기 전에 남아 있어야 할 자리
STAGE = ".tab5-stage-"              # 확인한 사본의 이름 앞부분(백업 폴더 안 — 끝나면 지운다)
PHASE = {"now": "check"}            # 끊겼을 때 무엇을 알릴지 — check · write(탭5 에 쓰는 중) · done(다 썼다)
T_INFO, T_READ, T_WRITE, T_RESTORE = 60, 600, 300, 900    # esptool 제한 시간(초) — 16MB 읽기는 약 70초(2026-11-02 실측 6MB 26초)


def _fetch(url: str, dest: Path, limit: int) -> None:
    """https 로 받아 dest 에 쓴다. limit 바이트를 넘으면 멈춘다(엉뚱한 큰 파일을 받지 않게)."""
    req = urllib.request.Request(url, headers={"User-Agent": "wideeye-tab5-flash"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:   # noqa: S310 — 주소는 https 만(tab5_flash.image_url)
        got = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            got += len(chunk)
            if got > limit:
                raise OSError(f"받은 것이 {limit} 바이트보다 크다")
            f.write(chunk)


def _read_stat(device: str, timeout: float = 6.0) -> str | None:
    """새 펌웨어에게 `stat` 을 보내 STAT 줄(board= 가 든 것)을 받는다. 포트는 DTR 켬 · RTS 끔으로 연다(열 때 칩이 리셋되지 않게 —
    tools/tab5_link.py 와 같다)."""
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = device, 115200, 0.1
    s.dtr, s.rts = True, False
    s.exclusive = True
    s.open()
    try:
        s.reset_input_buffer()
        s.write(b"stat\n")
        buf, end = b"", time.monotonic() + timeout
        while time.monotonic() < end:
            buf += s.read(4096)
            for line in buf.split(b"\n"):
                if line.startswith(b"STAT ") and b" board=" in line:
                    return line.decode("utf-8", "replace").strip()
        return None
    finally:
        s.close()


@dataclass
class Io:
    """기기 · 바깥과 닿는 것 — 시험은 가짜를 준다."""
    lister: Callable | None = None                      # USB 목록(pyserial list_ports.comports 꼴) — None 이면 진짜
    fetch: Callable = _fetch
    stat: Callable = _read_stat
    now: Callable = datetime.now
    wall: Callable = time.time
    wait_s: float = 1.0                                 # 기다릴 때 한 번 쉬는 시간(시험은 0)


# ---- 탭5 창에게서 포트 빌리기 ----------------------------------------------------------------------------------------------

class PortLoan:
    """탭5 창(tools/tab5_link.py)이 포트를 놓게 한다 — 놓기 파일 state/tab5-link.pause(첫 줄 = 만료 시각, kiosk.sh tab5 와 같은 꼴).
    앞서 누가 더 길게 걸어 둔 놓기는 줄이지 않고, 끝나면 그 값으로 되돌린다(kiosk.sh tab5 -- 와 같은 뜻)."""

    def __init__(self, ctx: ub.Ctx, io: Io, minutes: int = LOAN_MIN):
        sd = paths.state_dir(ctx.home)
        self.ctx, self.io, self.minutes = ctx, io, minutes
        self.file, self.state = sd / "tab5-link.pause", sd / "tab5-link.json"
        self.prev: str | None = None

    def __enter__(self) -> "PortLoan":
        self.file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.prev = self.file.read_text(encoding="utf-8").strip() or None
        except OSError:
            self.prev = None
        t0 = self.io.wall()
        until = int(t0) + self.minutes * 60
        try:
            until = max(until, int(float(self.prev or 0)))
        except ValueError:
            pass
        self.file.write_text(f"{until}\n", encoding="utf-8")
        try:
            self._wait_released(t0)
        except BaseException:                        # 기다리는 10초 사이에 끊기면 __exit__ 가 불리지 않는다 — 직접 되돌린다(재검토 2026-11-03)
            self.__exit__(None, None, None)
            raise
        return self

    def _wait_released(self, t0: float) -> None:
        """창이 포트를 놓았다고 적을 때까지(이번 놓기 뒤의 기록만 믿는다). 상태 파일이 없거나 창이 죽었으면 기다리지 않는다."""
        end = t0 + WAIT_RELEASE_S
        while True:
            try:
                s = json.loads(self.state.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            pid = int(s.get("pid") or 0)
            try:
                os.kill(pid, 0)
                alive = pid > 0
            except ProcessLookupError:
                alive = False
            except OSError:
                alive = True
            if not alive or (s.get("paused") and float(s.get("updated_at") or 0) >= t0):
                return
            if not self.io.wait_s or self.io.wall() >= end:
                break
            time.sleep(min(self.io.wait_s, 0.25))
        self.ctx.say("주의: 탭5 창이 포트를 놓았다는 확인이 없다 — 그래도 해 본다(포트가 바쁘면 멈춘다)")

    def __exit__(self, *exc) -> None:
        try:
            if self.prev and float(self.prev) > self.io.wall():
                self.file.write_text(self.prev + "\n", encoding="utf-8")
            else:
                self.file.unlink(missing_ok=True)
        except (OSError, ValueError):
            self.file.unlink(missing_ok=True)


# ---- esptool ---------------------------------------------------------------------------------------------------------------

class Device:
    def __init__(self, ctx: ub.Ctx, port: str, log: Path):
        self.ctx, self.port, self.log = ctx, port, log

    def esptool(self, before: str, after: str, *args: str, timeout: int = T_INFO) -> tuple[int, str]:
        cmd = [self.ctx.python, "-m", "esptool", "--chip", "esp32p4", "-p", self.port, "--before", before, "--after", after, *args]
        try:
            p = self.ctx.run(cmd, capture_output=True, text=True, timeout=timeout)
            rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired as e:
            rc, out = 124, f"{e.stdout or ''}{e.stderr or ''}\n(제한 시간 {timeout}초를 넘겼다)"
        except OSError as e:
            rc, out = 127, f"esptool 을 부르지 못했다: {e}"
        with open(self.log, "a", encoding="utf-8") as f:            # 이 맥의 기록에만(MAC 이 들어 있다)
            f.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} {' '.join(cmd[2:])}\nrc={rc}\n{out}\n")
        return rc, out

    def rescue(self) -> None:
        """내려받기 모드에서 꺼내 원래 펌웨어로 — 감시 타이머 리셋(hard reset 은 P4 를 내려받기 모드에 남긴다)."""
        self.esptool("no-reset", "watchdog-reset", "chip-id")


def _why_connect(out: str) -> str:
    low = out.lower()
    if "resource busy" in low or "could not exclusively lock" in low or "port is busy" in low:
        return ("포트가 바쁘다 — 다른 프로그램(탭5 창 · 시리얼 모니터)이 쥐고 있다. "
                f"{CMD} stop display 로 탭5 창을 내리고 다시 해 본다")
    if "no serial data received" in low or "failed to connect" in low:
        return "탭5 가 답하지 않는다 — USB-C 선을 뽑았다 다시 꽂고(데이터가 되는 선) 다시 해 본다"
    return "esptool 이 탭5 에 붙지 못했다 — 자세한 것은 기록(logs/tab5-flash.log)"


def _devices(io: Io) -> list[dict]:
    return probes.tab5_devices(io.lister)


def _wait_port(io: Io, serial: str, seconds: float) -> dict | None:
    want = tf.norm_mac(serial) or serial.upper()
    end = io.wall() + seconds
    while True:
        for d in _devices(io):
            if (tf.norm_mac(d["serial"]) or d["serial"].upper()) == want:
                return d
        if io.wall() >= end or not io.wait_s:
            return None
        time.sleep(io.wait_s)


def _stage(ctx: ub.Ctx, src: Path, want: str) -> Path | None:
    """확인한 파일의 사본을 비공개 임시 파일로 떠서 그 사본의 지문을 다시 본다 — 굽는 것은 이 사본(확인한 뒤 백업 1분 반 사이에 원본이
    바뀌어도 확인한 바이트만 쓴다. 코덱스 검토 2026-11-03). 다르면 None. 부른 쪽이 지운다."""
    d = _backup_dir(ctx)                              # 0700 — 백업의 사본에는 그 기기의 설정(Wi-Fi 등)이 들 수 있다(재검토 2026-11-03)
    for old in d.glob(STAGE + "*"):                   # 지난번에 끊겨(SIGKILL · 전원) 남은 사본
        old.unlink(missing_ok=True)
    fd, name = tempfile.mkstemp(prefix=STAGE, suffix=".bin", dir=d)           # 0600
    os.close(fd)
    dst = Path(name)
    try:
        shutil.copyfile(src, dst)
        if _sha256(dst) != want:
            dst.unlink(missing_ok=True)
            return None
    except BaseException:
        dst.unlink(missing_ok=True)
        raise
    return dst


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- 이미지 ------------------------------------------------------------------------------------------------------------------

def _image(ctx: ub.Ctx, io: Io, rel: tf.Release, given: Path | None) -> tuple[Path | None, str]:
    """구울 이미지 — --image 로 준 것이든 받은 것이든 크기 · 지문이 release.json 과 같아야 한다(FL-D4)."""
    def ok(p: Path) -> bool:
        return p.is_file() and p.stat().st_size == rel.size and _sha256(p) == rel.sha256
    if given is not None:
        if not given.is_file():
            return None, f"--image 로 준 파일이 없다: {given}"
        if not ok(given):
            return None, ("--image 로 준 파일의 크기 · 지문이 firmware/release.json 과 다르다 — "
                          "펌웨어를 바꿨으면 firmware/tools/make_release_image.sh <폴더> --write 로 다시 만든다")
        return given, f"주신 파일 {given.name}"
    url = tf.image_url(rel)
    if url is None:
        return None, "받을 주소가 없다(작업장) — firmware/tools/make_release_image.sh 로 만든 파일을 --image 로 준다"
    cache = ctx.home / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / rel.file
    if ok(dest):
        return dest, f"받아 둔 {rel.file}"
    part = dest.with_name(dest.name + ".part")
    try:
        io.fetch(url, part, rel.size)
    except OSError as e:
        part.unlink(missing_ok=True)
        return None, f"이미지를 받지 못했다({e}) — 인터넷 연결을 보고 다시"
    if not ok(part):
        part.unlink(missing_ok=True)
        return None, "받은 이미지의 크기 · 지문이 맞지 않는다 — 쓰지 않는다(다시 해도 같으면 알려 주세요)"
    part.replace(dest)
    return dest, f"받았다 {rel.file}"


# ---- 굽기 ------------------------------------------------------------------------------------------------------------------

def _configured(ctx: ub.Ctx) -> str:
    return paths.read_light(ctx.home).get("tab5_usb_serial") or ""


def _device_notes(ctx: ub.Ctx, dev: dict, why: str, yes: bool, again: str) -> bool:
    """기기 고르기 뒤의 공통 확인 — 쓸 수 있으면 True. 실제로 쓸 때(--yes)는 넘겨짚은 기기(tf.GUESS)를 받지 않는다:
    설정의 탭5 이거나 --serial 로 짚은 것만(다른 ESP32 · UIFlow 기기에 리셋을 걸지 않게 — 독립 검토 2026-11-03 중요 1)."""
    ctx.say(f"탭5: {dev['serial']} ({dev['device']}) — {why}")
    desc = dev.get("description") or ""
    if desc and tf.JTAG_NAME not in desc:
        ctx.say(f"주의: 이 기기의 USB 이름은 '{desc}' 이다 — 탭5 의 USB 이름(USB JTAG/serial debug unit)과 다르다. "
                "다른 기기이거나 다른 프로그램이 USB 를 바꿔 쓰고 있다. 탭5 가 맞는지 눈으로 확인한 뒤에만 --yes 로 부른다")
    if yes and why == tf.GUESS:
        ctx.say(f"멈춤: 실제로 쓰기 전에 이 기기를 짚어 주세요 — {again} --serial {dev['serial']}")
        return False
    return True


def _backup_dir(ctx: ub.Ctx) -> Path:
    d = ctx.home / "tab5-backups"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)              # 백업에는 그 기기에 저장된 것(예: 공장 펌웨어의 Wi-Fi 설정)이 들어 있을 수 있다
    return d


def _log(ctx: ub.Ctx) -> Path:
    d = paths.logs_dir(ctx.home)
    d.mkdir(parents=True, exist_ok=True)
    return d / "tab5-flash.log"


def flash(ctx: ub.Ctx, io: Io, serial: str | None, image: Path | None, yes: bool) -> int:
    try:
        rel = tf.load_release()
    except tf.ReleaseError as e:
        ctx.say(f"멈춤: {e}")
        return 1
    dev, why = tf.pick(_devices(io), serial, _configured(ctx))
    if dev is None:
        ctx.say(f"멈춤: {why}")
        return 1
    img, how = _image(ctx, io, rel, image)
    if img is None:
        ctx.say(f"멈춤: {how}")
        return 1
    sel = f" --serial {dev['serial']}" if serial or _configured(ctx) != dev["serial"] else ""
    if not _device_notes(ctx, dev, why, yes, f"{CMD} tab5 flash --yes" + (f" --image {Q(str(img))}" if image else "")):
        return 1
    ctx.say(f"펌웨어: WideEye 탭5 {rel.version} · {rel.size:,} 바이트 · 지문 {rel.sha256[:12]}… 확인함 ({how})")
    ctx.say(f"백업: 탭5 의 지금 내용 16MB 를 통째로 {_backup_dir(ctx)}/ 에 둔다(되돌리기: {CMD} tab5 restore <백업 파일> --yes — "
            "그 백업을 뜬 때의 내용으로 돌아간다. 처음 굽는 것이면 공장에서 들어 있던 프로그램)")
    ctx.say("할 일: 탭5 창이 포트를 놓음 → 칩 확인(ESP32-P4 · v1.x · 16MB · 보안 꺼짐) → 백업(약 1분 반) → 굽기(몇 초) → 새 펌웨어 확인 → 설정에 이 탭5")
    ctx.say("알아 둘 것: 공장에서 들어 있던 프로그램은 지워진다(백업으로 되돌릴 수 있다). 탭5 의 방향 · 밝기 설정은 처음으로 돌아간다. "
            "도는 동안 USB 선을 뽑지 않는다.")
    if not yes:
        ctx.say(f"확인만 했다 — 기기에는 아무것도 하지 않았다. 굽기: {CMD} tab5 flash --yes{sel}" + (f" --image {Q(str(img))}" if image else ""))
        return 0
    bdir = _backup_dir(ctx)
    if shutil.disk_usage(bdir).free < NEED_FREE:
        ctx.say(f"멈춤: 디스크에 백업을 둘 자리({NEED_FREE // 1048576}MB)가 없다")
        return 1
    rc, staged = 1, None
    try:
        staged = _stage(ctx, img, rel.sha256)
        if staged is None:
            ctx.say("멈춤: 이미지를 옮겨 담는 사이에 내용이 바뀌었다 — 다시 한다")
            return 1
        with PortLoan(ctx, io):
            rc = _flash_steps(ctx, io, rel, dev, staged, bdir)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    ctx.say("탭5 창에 포트를 돌려줬다")
    if rc != 0:
        return rc
    from tools import setup_cli                       # 저장 길 하나(setup tab5 <번호> 와 같다 — 판 확인 · 엔진 반영 기다림)
    setup_cli.tab5(ctx, dev["serial"])
    ctx.say(f"다음: 탭5 창이 꺼져 있으면 {CMD} start display — 30초 안에 탭5 에 현황판이 뜬다")
    return 0


def _flash_steps(ctx: ub.Ctx, io: Io, rel: tf.Release, dev: dict, img: Path, bdir: Path) -> int:
    d = Device(ctx, dev["device"], _log(ctx))
    rc, out = d.esptool("default-reset", "no-reset", "read-mac")
    if rc != 0:
        ctx.say(f"멈춤: {_why_connect(out)}")
        d.rescue()
        return 1
    chip = tf.parse(out)
    for args in (("flash-id",), ("get-security-info",)):
        rc, out = d.esptool("no-reset", "no-reset", *args)
        tf.parse(out, chip)
    ctx.say(f"칩: {chip.name or '?'} {chip.rev_text()} · 플래시 {chip.flash // 1048576 if chip.flash else '?'}MB")
    bad = tf.chip_problems(chip, rel, dev["serial"])
    if bad:
        for b in bad:
            ctx.say(f"멈춤: {b}")
        d.rescue()
        ctx.say("굽지 않았다 — 탭5 는 원래 프로그램으로 다시 켜진다")
        return 1

    stem = tf.backup_stem(dev["serial"], io.now())
    part, final = bdir / f"{stem}.bin.part", bdir / f"{stem}.bin"
    ctx.say(f"백업: {final.name} 읽는 중(약 1분 반)…")
    rc, out = d.esptool("no-reset", "no-reset", "read-flash", "0", hex(chip.flash), str(part), timeout=T_READ)
    if rc != 0 or not part.is_file() or part.stat().st_size != chip.flash:
        part.unlink(missing_ok=True)
        ctx.say("멈춤: 백업을 읽지 못했다 — 굽지 않았다(기록 logs/tab5-flash.log)")
        d.rescue()
        return 1
    os.chmod(part, 0o600)
    sha = _sha256(part)
    part.replace(final)
    (bdir / f"{stem}.bin.sha256").write_text(tf.sha_line(sha, final.name), encoding="utf-8")
    info = {"serial": dev["serial"], "mac": chip.mac, "chip": chip.name, "rev": chip.rev_text(), "flash_bytes": chip.flash,
            "sha256": sha, "made_at": io.now().isoformat(timespec="seconds"), "why": f"wideeye tab5 flash {rel.version} 전에 뜬 전체 백업"}
    (bdir / f"{stem}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for p in (bdir / f"{stem}.bin.sha256", bdir / f"{stem}.json"):
        os.chmod(p, 0o600)
    ctx.say(f"백업 끝: {final} · 지문 {sha[:12]}…")

    rc, out = d.esptool("no-reset", "no-reset", "read-mac")
    if rc != 0 or tf.parse(out).mac != tf.norm_mac(dev["serial"]):
        ctx.say("멈춤: 백업 뒤 다시 본 MAC 이 다르다(또는 읽지 못함) — 굽지 않았다")
        d.rescue()
        return 1

    ctx.say("굽는 중…")
    PHASE["now"] = "write"
    rc, out = d.esptool("no-reset", "watchdog-reset", "write-flash", hex(rel.offset), str(img), timeout=T_WRITE)
    if rc == 0 and "Hash of data verified" in out:
        PHASE["now"] = "done"
    if rc != 0 or "Hash of data verified" not in out:
        ctx.say(f"멈춤: 굽기가 끝나지 않았다 — 되돌리기: {CMD} tab5 restore {Q(str(final))} --yes (기록 logs/tab5-flash.log)")
        return 1
    ctx.say("구웠다 — 지문 확인됨. 탭5 가 다시 켜지기를 기다린다…")
    return _confirm_board(ctx, io, dev["serial"])


def _confirm_board(ctx: ub.Ctx, io: Io, serial: str) -> int:
    """새 펌웨어가 `stat` 에 STAT 줄(board=…)로 답하는가 — 30초까지."""
    end = io.wall() + 30
    while True:
        found = _wait_port(io, serial, 15)
        if found:
            try:
                line = io.stat(found["device"])
            except Exception as e:                   # noqa: BLE001 — 막 다시 켜진 포트는 열기가 실패할 수 있다(다시 해 본다)
                line, err = None, e
            else:
                err = None
            if line:
                ctx.say("새 펌웨어가 답했다: " + " ".join(w for w in line.split() if w.split("=")[0] in ("board", "orient", "temp")))
                return 0
        if io.wall() >= end or not io.wait_s:
            ctx.say("주의: 구웠지만 새 펌웨어가 답하는지 확인하지 못했다 — 탭5 화면을 보고, 까만 채면 USB-C 선을 뽑았다 다시 꽂는다"
                    + (f" ({type(err).__name__})" if found and err else ""))
            return 1
        time.sleep(io.wait_s)


# ---- 되돌리기 ---------------------------------------------------------------------------------------------------------------

def restore(ctx: ub.Ctx, io: Io, backup: Path, serial: str | None, yes: bool) -> int:
    if not backup.is_file():
        ctx.say(f"멈춤: 백업 파일이 없다: {backup}")
        return 1
    size = backup.stat().st_size
    if size < tf.FULL or size % (1 << 20):
        ctx.say(f"멈춤: 백업이 플래시 전체(16MB 이상)가 아니다({size:,} 바이트) — 이 명령은 {CMD} tab5 flash 가 뜬 전체 백업만 되돌린다")
        return 1
    shafile = backup.with_name(backup.name + ".sha256")
    want = tf.read_sha_file(shafile.read_text(encoding="utf-8")) if shafile.is_file() else None
    if want is None:
        ctx.say(f"멈춤: 지문 파일이 없다({shafile.name}) — 백업이 온전한지 알 수 없어 쓰지 않는다")
        return 1
    if _sha256(backup) != want:
        ctx.say("멈춤: 백업의 지문이 지문 파일과 다르다 — 백업이 상했다")
        return 1
    info_file = backup.with_suffix(".json")
    info, info_note = {}, " · 정보 파일이 없어 어느 탭5 것인지는 확인하지 못했다"
    if info_file.is_file():
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
            info = info if isinstance(info, dict) else {}
        except ValueError:
            info = {}
        info_note = (f" · {info.get('made_at')} 에 뜸" if info.get("mac") else
                     " · 정보 파일이 깨져 있어 어느 탭5 것인지는 확인하지 못했다")
    dev, why = tf.pick(_devices(io), serial, _configured(ctx))
    if dev is None:
        ctx.say(f"멈춤: {why}")
        return 1
    if info.get("sha256") and str(info["sha256"]).lower() != want:
        ctx.say("멈춤: 정보 파일의 지문이 백업과 다르다 — 다른 백업의 정보 파일이다")
        return 1
    if info.get("mac") and tf.norm_mac(info["mac"]) != tf.norm_mac(dev["serial"]):
        ctx.say(f"멈춤: 이 백업은 다른 탭5({info.get('serial')})의 것이다 — 고른 탭5 는 {dev['serial']}")
        return 1
    sel = f" --serial {dev['serial']}" if serial or _configured(ctx) != dev["serial"] else ""
    if not _device_notes(ctx, dev, why, yes, f"{CMD} tab5 restore {Q(str(backup))} --yes"):
        return 1
    ctx.say(f"되돌릴 백업: {backup} · 지문 확인함{info_note}")
    if not info.get("mac"):                       # 어느 탭5 의 백업인지 모르면 사람이 --serial 로 짚은 때만 쓴다(독립 검토 2026-11-03)
        sel = f" --serial {dev['serial']}"
        if yes and not serial:
            ctx.say(f"멈춤: 어느 탭5 의 백업인지 모르므로 되돌릴 탭5 를 직접 짚어 주세요 — {CMD} tab5 restore {Q(str(backup))} --yes{sel}")
            return 1
    ctx.say("할 일: 탭5 창이 포트를 놓음 → 칩 · MAC 확인 → 16MB 쓰기(1~3분) → 다시 켬. 도는 동안 USB 선을 뽑지 않는다.")
    if not yes:
        ctx.say(f"확인만 했다 — 기기에는 아무것도 하지 않았다. 되돌리기: {CMD} tab5 restore {Q(str(backup))} --yes{sel}")
        return 0
    if shutil.disk_usage(_backup_dir(ctx)).free < size + NEED_FREE:
        ctx.say(f"멈춤: 디스크에 자리가 없다(백업 사본 {size // 1048576}MB + 여유 {NEED_FREE // 1048576}MB)")
        return 1
    rc, staged = 1, None
    try:
        staged = _stage(ctx, backup, want)
        if staged is None:
            ctx.say("멈춤: 백업을 옮겨 담는 사이에 내용이 바뀌었다 — 다시 한다")
            return 1
        rc = _restore_steps(ctx, io, dev, staged, size)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    ctx.say("탭5 창에 포트를 돌려줬다")
    return rc


def _restore_steps(ctx: ub.Ctx, io: Io, dev: dict, backup: Path, size: int) -> int:
    rc = 1
    with PortLoan(ctx, io):
        d = Device(ctx, dev["device"], _log(ctx))
        rc, out = d.esptool("default-reset", "no-reset", "read-mac")
        if rc != 0:
            ctx.say(f"멈춤: {_why_connect(out)}")
            d.rescue()
        else:
            chip = tf.parse(out)
            _, out2 = d.esptool("no-reset", "no-reset", "flash-id")
            tf.parse(out2, chip)
            bad = []
            if chip.name != "ESP32-P4":
                bad.append(f"칩이 ESP32-P4 가 아니다({chip.name or '읽지 못함'})")
            if chip.mac != tf.norm_mac(dev["serial"]):
                bad.append("MAC 이 USB 일련번호와 다르다(또는 읽지 못함)")
            if chip.flash != size:
                bad.append("플래시 크기가 백업과 다르다(또는 읽지 못함)")          # 16MB 보다 큰 플래시의 전체 백업도 여기서 맞춰 본다
            if bad:
                for b in bad:
                    ctx.say(f"멈춤: {b}")
                d.rescue()
                rc = 1
            else:
                ctx.say("되돌리는 중(1~3분)…")
                PHASE["now"] = "write"
                rc, out = d.esptool("no-reset", "watchdog-reset", "write-flash", "0x0", str(backup), timeout=T_RESTORE)
                if rc == 0 and "Hash of data verified" in out:
                    PHASE["now"] = "done"
                if rc != 0 or "Hash of data verified" not in out:
                    ctx.say("멈춤: 되돌리기가 끝나지 않았다 — 같은 명령을 다시 한다(기록 logs/tab5-flash.log)")
                    rc = 1
                else:
                    rc = 0
                    back = _wait_port(io, dev["serial"], 20)
                    ctx.say("되돌렸다 — 지문 확인됨. " + ("탭5 가 다시 USB 에 보인다. 화면을 보세요" if back
                                                         else "탭5 가 아직 USB 에 다시 보이지 않는다 — 화면을 보세요"))
    return rc


# ---- 진입점 --------------------------------------------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wideeye tab5", description="탭5 에 WideEye 펌웨어 굽기 · 되돌리기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("flash", help="확인만(--yes 를 붙이면 실제로 굽는다)")
    f.add_argument("--serial", help="구울 탭5 의 USB 일련번호(여러 대가 꽂혔을 때)")
    f.add_argument("--image", type=Path, help="받지 않고 이 파일을 굽는다(크기 · 지문이 firmware/release.json 과 같아야 한다)")
    f.add_argument("--yes", action="store_true", help="실제로 굽는다")
    r = sub.add_parser("restore", help="tab5 flash 가 뜬 전체 백업을 되돌린다(확인만 — --yes 로 실제로)")
    r.add_argument("backup", type=Path)
    r.add_argument("--serial")
    r.add_argument("--yes", action="store_true")
    return ap


def _on_term(signum, frame):
    """첫 SIGTERM 만 받는다 — 정리(포트 돌려주기 · 사본 지우기) 도중의 두 번째 신호가 정리를 끊지 않게(재검토 2026-11-03)."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise SystemExit(143)


INTERRUPTED = {"check": "탭5 에는 아직 쓰지 않았다 — 같은 명령을 다시 하면 된다",
               "write": ("탭5 에 쓰는 도중이었다 — 반쯤 쓰였을 수 있다. 같은 명령을 다시 하거나, 굽기였다면 위 '백업 끝:' 줄의 파일로 "
                         f"{CMD} tab5 restore <그 파일> --yes (탭5 는 내려받기 모드에서 기다린다 — 다시 하면 이어진다)"),
               "done": "탭5 에는 다 썼다(지문 확인됨) — 그 뒤 확인 · 설정 저장만 끊겼다. 화면을 보고, 설정은 {cmd} setup tab5 <번호> 로"}


def main(argv: list[str] | None = None, ctx: ub.Ctx | None = None, io: Io | None = None) -> int:
    a = parser().parse_args(sys.argv[1:] if argv is None else argv)
    ctx, io = ctx or ub.Ctx(), io or Io()
    # 끊기면(AI 의 제한 시간 · 창 닫기 — SIGTERM · Ctrl-C) 도는 esptool 을 멈추고(subprocess.run 이 죽인다) 포트를 돌려준 뒤 알린다
    old = signal.signal(signal.SIGTERM, _on_term) if a.yes else None   # 확인만(--yes 없음)은 기기 · 포트에 손대지 않으므로 기본 동작 그대로
    PHASE["now"] = "check"
    try:
        if a.cmd == "flash":
            return flash(ctx, io, a.serial, a.image.expanduser().resolve() if a.image else None, a.yes)
        return restore(ctx, io, a.backup.expanduser().resolve(), a.serial, a.yes)
    except (SystemExit, KeyboardInterrupt) as e:
        if isinstance(e, KeyboardInterrupt) or e.code == 143:
            ctx.say("멈춤: 도중에 끊겼다 — " + INTERRUPTED[PHASE["now"]].replace("{cmd}", CMD))
            return 130 if isinstance(e, KeyboardInterrupt) else 143
        raise
    finally:
        if old is not None:
            signal.signal(signal.SIGTERM, old)


if __name__ == "__main__":
    sys.exit(main())
