"""탭5 펌웨어 굽기 · 되돌리기(urgent_board/tab5_flash.py · tools/tab5_cli.py — 계획 FL-D1 ~ FL-D9).

기기 대신 가짜 esptool(ctx.run) · 가짜 USB 목록 · 가짜 STAT 을 준다. MAC 은 지어낸 값이다.
esptool 출력의 꼴은 2026-11-03 esptool 5.4.0 을 실제 탭5 에 걸어 얻은 것을 옮겼다(값만 바꿈).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import setup_cli, tab5_cli, ub
from urgent_board import tab5_flash as tf

SER = "AA:BB:CC:11:22:33"
OTHER = "AA:BB:CC:44:55:66"
FULL = tf.FULL


def esptool_out(cmd: str, mac: str = SER.lower(), rev: str = "v1.3", flash: str = "16MB", secure: str = "Disabled",
                chip: str = "ESP32-P4") -> str:
    head = (f"esptool v5.4.0\nSerial port /dev/cu.usbmodem1:\nConnecting...\nConnected to {chip} on /dev/cu.usbmodem1:\n"
            f"Chip type:          {chip} (revision {rev})\nFeatures:           Dual Core + LP Core, 400MHz\n"
            f"Crystal frequency:  40MHz\nUSB mode:           USB-Serial/JTAG\nMAC:                {mac}\n")
    body = {
        "read-mac": f"Uploading stub flasher...\nRunning stub flasher...\nStub flasher running.\nMAC:                {mac}\nStaying in bootloader.\n",
        "flash-id": f"Flash Memory Information:\n=========================\nManufacturer: 46\nDevice: 4018\nDetected flash size: {flash}\nStaying in bootloader.\n",
        "get-security-info": ("Security Information:\n=====================\nFlags: 0x00000000 (0b0)\nChip ID: 18\nAPI Version: 2\n"
                              f"Secure Boot: {secure}\nFlash Encryption: Disabled\nSPI Boot Crypt Count (SPI_BOOT_CRYPT_CNT): 0x0\nStaying in bootloader.\n"),
        "chip-id": f"WARNING: ESP32-P4 has no chip ID. Reading MAC address instead.\nMAC:                {mac}\nHard resetting with a watchdog...\n",
        "write-flash": "Wrote 968544 bytes (500000 compressed) at 0x00002000 in 3.3 seconds.\nHash of data verified.\nHard resetting with a watchdog...\n",
        "read-flash": "Read 16777216 bytes from 0x00000000 in 70.1 seconds.\nStaying in bootloader.\n",
    }[cmd]
    return head + body


class FakeEsptool:
    """ctx.run 자리 — 부른 차례를 적고, 명령마다 출력을 준다. fail = {명령: (rc, 출력)} 로 실패를 흉내 낸다."""
    def __init__(self, **kw):
        self.calls: list[list[str]] = []
        self.kw = kw
        self.fail: dict[str, tuple[int, str]] = {}
        self.second_mac: str | None = None

    def __call__(self, cmd, capture_output=True, text=True, timeout=None):
        assert cmd[1:3] == ["-m", "esptool"]
        args = cmd[3:]
        self.calls.append(args)
        name = next(a for a in args if a in ("read-mac", "flash-id", "get-security-info", "chip-id", "write-flash", "read-flash"))
        if name in self.fail:
            rc, out = self.fail[name]
            return subprocess.CompletedProcess(cmd, rc, out, "")
        kw = dict(self.kw)
        if name == "read-mac" and sum(1 for c in self.calls if "read-mac" in c) == 2 and self.second_mac:
            kw["mac"] = self.second_mac
        if name == "read-flash":
            i = args.index("read-flash")
            Path(args[i + 3]).write_bytes(b"\xab" * int(args[i + 2], 16))
        return subprocess.CompletedProcess(cmd, 0, esptool_out(name, **kw), "")

    def names(self) -> list[str]:
        out = []
        for a in self.calls:
            n = next(x for x in a if x in ("read-mac", "flash-id", "get-security-info", "chip-id", "write-flash", "read-flash"))
            out.append(f"{n}:{a[a.index('--before') + 1]}/{a[a.index('--after') + 1]}")
        return out


def port(serial=SER, device="/dev/cu.usbmodem1"):
    return SimpleNamespace(vid=0x303A, serial_number=serial, device=device, description="USB JTAG/serial debug unit")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    image = tmp_path / "fw.bin"
    image.write_bytes(b"WIDEEYE" * 1000)
    sha = hashlib.sha256(image.read_bytes()).hexdigest()
    rel = tmp_path / "release.json"
    rel.write_text(json.dumps({"version": "9.9.9", "tag": "v9.9.9", "file": "wideeye-tab5-v9.9.9.bin", "offset": "0x2000",
                               "size": image.stat().st_size, "sha256": sha, "chip": "esp32p4", "chip_rev": [100, 199],
                               "flash_size": "16MB", "idf": "5.5.4", "esptool": "5.4.0"}), encoding="utf-8")
    monkeypatch.setattr(tf, "RELEASE_FILE", rel)
    saved = []
    monkeypatch.setattr(setup_cli, "tab5", lambda ctx, serial: saved.append(serial) or 0)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / "settings.yaml"
    settings.write_text(f"display:\n  tab5_usb_serial: '{SER}'\n", encoding="utf-8")     # 설정의 탭5(짚은 기기) — 넘겨짚기는 따로 본다
    said: list[str] = []
    fake = FakeEsptool()
    ctx = ub.Ctx(home=home, run=fake, say=said.append, python="/fake/python", kiosk=False)
    ports = [port()]
    stats = []
    io = tab5_cli.Io(lister=lambda: ports, fetch=None, stat=lambda dev: stats.append(dev) or "STAT up=1 board=none bseq=0 orient=0 temp=30.1",
                     now=lambda: datetime(2031, 1, 2, 3, 4, 5), wait_s=0)
    return SimpleNamespace(ctx=ctx, io=io, fake=fake, said=said, image=image, sha=sha, home=home, ports=ports, saved=saved,
                           stats=stats, text=lambda: "\n".join(said), unconfigure=lambda: settings.unlink())


# ---- 판단 ------------------------------------------------------------------------------------------------------------------

def test_parse_reads_esptool_5_and_4():
    c = tf.parse(esptool_out("read-mac") + esptool_out("flash-id") + esptool_out("get-security-info"))
    assert (c.name, c.rev, c.mac, c.flash, c.secure_boot, c.flash_enc) == ("ESP32-P4", 103, SER.lower(), FULL, False, False)
    old = tf.parse("Chip is ESP32-P4 (revision v1.0)\nMAC: aa:bb:cc:11:22:33\nDetected flash size: 16MB\n")
    assert (old.name, old.rev, old.mac, old.flash) == ("ESP32-P4", 100, SER.lower(), FULL)


def test_chip_problems_refuse_what_was_not_confirmed():
    rel = tf.Release("1", "v1", "a.bin", 0x2000, 1, "0" * 64, "esp32p4", 100, 199, FULL, "5.4.0")
    good = tf.parse(esptool_out("read-mac") + esptool_out("flash-id") + esptool_out("get-security-info"))
    assert tf.chip_problems(good, rel, SER) == []
    assert any("v3.0" in p for p in tf.chip_problems(tf.parse(esptool_out("read-mac", rev="v3.0") + esptool_out("flash-id")
                                                                      + esptool_out("get-security-info")), rel, SER))
    small = tf.parse(esptool_out("read-mac") + esptool_out("flash-id", flash="8MB") + esptool_out("get-security-info"))
    assert any("16MB" in p for p in tf.chip_problems(small, rel, SER))
    locked = tf.parse(esptool_out("read-mac") + esptool_out("flash-id") + esptool_out("get-security-info", secure="Enabled"))
    assert any("보안 부팅" in p for p in tf.chip_problems(locked, rel, SER))
    assert any("MAC" in p for p in tf.chip_problems(good, rel, OTHER))
    s3 = tf.parse(esptool_out("read-mac", chip="ESP32-S3") + esptool_out("flash-id") + esptool_out("get-security-info"))
    assert any("ESP32-P4 가 아니다" in p for p in tf.chip_problems(s3, rel, SER))
    assert len(tf.chip_problems(tf.Chip(), rel, SER)) == 5             # 아무것도 못 읽었으면 다섯 까닭 모두


def test_pick_never_guesses_between_devices():
    a = {"device": "/dev/cu.a", "serial": SER, "description": ""}
    b = {"device": "/dev/cu.b", "serial": OTHER, "description": ""}
    assert tf.pick([a], None, None)[0] is a
    assert tf.pick([a, b], None, None)[0] is None and "2대" in tf.pick([a, b], None, None)[1]
    assert tf.pick([a, b], OTHER.lower(), None)[0] is b                # 대소문자 · 꼴과 관계없이
    assert tf.pick([a, b], None, SER)[0] is a
    assert tf.pick([b], None, SER)[0] is None                           # 설정의 탭5 가 없으면 다른 기기로 넘겨짚지 않는다
    assert tf.pick([a], OTHER, SER)[0] is None
    assert tf.pick([], None, None)[0] is None


def test_release_and_url(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"version": "1"}', encoding="utf-8")
    with pytest.raises(tf.ReleaseError):
        tf.load_release(p)
    rel = tf.load_release()                                             # 저장소의 진짜 파일
    assert rel.offset == 0x2000 and rel.flash_size == FULL and rel.file == f"wideeye-tab5-v{rel.version}.bin"
    assert tf.image_url(rel, "https://example.invalid/x") == f"https://example.invalid/x/releases/download/{rel.tag}/{rel.file}"
    assert tf.image_url(rel, "PLACEHOLDER") is None


def test_backup_stem_uses_only_the_mac_tail():
    assert tf.backup_stem(SER, datetime(2031, 1, 2, 3, 4, 5)) == "tab5-2233-20310102-030405"


# ---- 굽기 ------------------------------------------------------------------------------------------------------------------

def test_flash_without_yes_touches_nothing(rig):
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=False) == 0
    assert rig.fake.calls == []
    assert "--yes" in rig.text() and "아무것도 하지 않았다" in rig.text()
    assert not (rig.home / "state" / "tab5-link.pause").exists()


def test_flash_steps_in_order_and_backup_files(rig):
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 0, rig.text()
    assert rig.fake.names() == ["read-mac:default-reset/no-reset", "flash-id:no-reset/no-reset", "get-security-info:no-reset/no-reset",
                                "read-flash:no-reset/no-reset", "read-mac:no-reset/no-reset", "write-flash:no-reset/watchdog-reset"]
    w = rig.fake.calls[-1]
    staged = Path(w[-1])
    assert w[w.index("write-flash") + 1:-1] == ["0x2000"]
    assert staged.parent == rig.home / "tab5-backups" and staged.name.startswith(".tab5-stage-")   # 확인한 사본(0700 폴더)을 굽고 끝나면 지운다
    assert not staged.exists() and rig.image.exists()
    read = rig.fake.calls[3]
    assert read[read.index("read-flash") + 1:read.index("read-flash") + 3] == ["0", hex(FULL)]
    bdir = rig.home / "tab5-backups"
    stem = "tab5-2233-20310102-030405"
    b = bdir / f"{stem}.bin"
    assert b.stat().st_size == FULL and oct(b.stat().st_mode & 0o777) == "0o600" and oct(bdir.stat().st_mode & 0o777) == "0o700"
    sha = hashlib.sha256(b.read_bytes()).hexdigest()
    assert tf.read_sha_file((bdir / f"{stem}.bin.sha256").read_text()) == sha
    info = json.loads((bdir / f"{stem}.json").read_text())
    assert info["mac"] == SER.lower() and info["sha256"] == sha
    assert not list(bdir.glob("*.part"))
    assert rig.saved == [SER] and rig.stats == ["/dev/cu.usbmodem1"]
    assert not (rig.home / "state" / "tab5-link.pause").exists()        # 포트를 돌려줬다


@pytest.mark.parametrize("change, word", [({"rev": "v3.0"}, "v3"), ({"secure": "Enabled"}, "보안"), ({"flash": "8MB"}, "16MB"),
                                          ({"mac": OTHER.lower()}, "MAC"), ({"chip": "ESP32-S3"}, "ESP32-P4 가 아니다")])
def test_flash_refuses_before_backup_and_rescues(rig, change, word):
    rig.fake.kw = change
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 1
    names = rig.fake.names()
    assert not any(n.startswith(("read-flash", "write-flash")) for n in names)
    assert names[-1] == "chip-id:no-reset/watchdog-reset"              # 원래 펌웨어로 돌려보냈다
    assert word in rig.text() and rig.saved == []
    assert not (rig.home / "state" / "tab5-link.pause").exists()


def test_busy_port_is_explained(rig):
    rig.fake.fail["read-mac"] = (2, "A fatal error occurred: Could not open /dev/cu.usbmodem1, the port is busy or doesn't exist.\n"
                                    "([Errno 16] Resource busy: '/dev/cu.usbmodem1')")
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 1
    assert "포트가 바쁘다" in rig.text()
    assert not any(n.startswith(("read-flash", "write-flash")) for n in rig.fake.names())


def test_mac_changed_after_backup_stops_before_writing(rig):
    rig.fake.second_mac = OTHER.lower()
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 1
    assert not any(n.startswith("write-flash") for n in rig.fake.names())
    assert rig.fake.names()[-1] == "chip-id:no-reset/watchdog-reset"


def test_failed_write_prints_the_restore_command(rig):
    rig.fake.fail["write-flash"] = (2, "A fatal error occurred: Packet content transfer stopped")
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 1
    assert "tab5 restore" in rig.text() and "tab5-2233-20310102-030405.bin --yes" in rig.text()
    assert rig.saved == []


def test_no_answer_after_flash_is_not_success(rig):
    rig.io.stat = lambda dev: None
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 1
    assert "확인하지 못했다" in rig.text() and rig.saved == []


def test_longer_pause_held_by_someone_else_is_kept(rig):
    sd = rig.home / "state"
    sd.mkdir()
    far = str(int(rig.io.wall()) + 5 * 3600)
    (sd / "tab5-link.pause").write_text(far + "\n")
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 0
    assert (sd / "tab5-link.pause").read_text().strip() == far


def test_wrong_image_is_refused_before_the_device(rig, tmp_path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"X" * rig.image.stat().st_size)
    assert tab5_cli.flash(rig.ctx, rig.io, None, bad, yes=True) == 1
    assert rig.fake.calls == [] and "release.json" in rig.text()


def test_two_devices_need_a_serial(rig):
    rig.unconfigure()
    rig.ports.append(port(OTHER, "/dev/cu.usbmodem2"))
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 1
    assert rig.fake.calls == [] and "--serial" in rig.text()
    assert tab5_cli.flash(rig.ctx, rig.io, OTHER, rig.image, yes=False) == 0
    assert f"--serial {OTHER}" in rig.said[-1]


def test_download_is_checked_and_cached(rig, monkeypatch):
    monkeypatch.setattr(tf, "REPO", "https://example.invalid/wideeye")
    got = []

    def fetch(url, dest, limit):
        got.append(url)
        Path(dest).write_bytes(rig.image.read_bytes())
    rig.io.fetch = fetch
    assert tab5_cli.flash(rig.ctx, rig.io, None, None, yes=False) == 0
    assert got == ["https://example.invalid/wideeye/releases/download/v9.9.9/wideeye-tab5-v9.9.9.bin"]
    assert tab5_cli.flash(rig.ctx, rig.io, None, None, yes=False) == 0
    assert len(got) == 1                                               # 받아 둔 것을 다시 쓴다(지문이 맞으면)

    def evil(url, dest, limit):
        Path(dest).write_bytes(b"Y" * rig.image.stat().st_size)
    (rig.home / "cache" / "wideeye-tab5-v9.9.9.bin").unlink()
    rig.io.fetch = evil
    assert tab5_cli.flash(rig.ctx, rig.io, None, None, yes=False) == 1
    assert "지문이 맞지 않는다" in rig.text() and not list((rig.home / "cache").iterdir())


def test_workspace_without_url_asks_for_image(rig, monkeypatch):
    monkeypatch.setattr(tf, "REPO", "placeholder")                     # 작업장(자리표시자) — 공개판에서는 실제 주소로 바뀌므로 직접 둔다
    assert tab5_cli.flash(rig.ctx, rig.io, None, None, yes=False) == 1
    assert "--image" in rig.text()


# ---- 되돌리기 ---------------------------------------------------------------------------------------------------------------

def make_backup(dirp: Path, mac: str | None = SER.lower(), size: int = FULL) -> Path:
    b = dirp / "tab5-2233-20310102-030405.bin"
    b.write_bytes(b"\xcd" * size)
    (dirp / f"{b.name}.sha256").write_text(tf.sha_line(hashlib.sha256(b.read_bytes()).hexdigest(), b.name))
    if mac:
        (dirp / "tab5-2233-20310102-030405.json").write_text(json.dumps({"serial": SER, "mac": mac}))
    return b


def test_restore_dry_run_then_write(rig, tmp_path):
    b = make_backup(tmp_path)
    assert tab5_cli.restore(rig.ctx, rig.io, b, None, yes=False) == 0
    assert rig.fake.calls == []
    assert tab5_cli.restore(rig.ctx, rig.io, b, None, yes=True) == 0, rig.text()
    assert rig.fake.names() == ["read-mac:default-reset/no-reset", "flash-id:no-reset/no-reset", "write-flash:no-reset/watchdog-reset"]
    w = rig.fake.calls[-1]
    assert w[w.index("write-flash") + 1:-1] == ["0x0"] and Path(w[-1]).name.startswith(".tab5-stage-") and not Path(w[-1]).exists()
    assert not (rig.home / "state" / "tab5-link.pause").exists()


def test_restore_refuses_other_units_backup_and_broken_backups(rig, tmp_path):
    other = tmp_path / "o"
    other.mkdir()
    assert tab5_cli.restore(rig.ctx, rig.io, make_backup(other, mac=OTHER.lower()), None, yes=True) == 1
    assert "다른 탭5" in rig.text()
    broken = tmp_path / "b"
    broken.mkdir()
    b = make_backup(broken)
    b.write_bytes(b"\x00" * FULL)
    assert tab5_cli.restore(rig.ctx, rig.io, b, None, yes=True) == 1
    assert "상했다" in rig.text()
    part = tmp_path / "p"
    part.mkdir()
    assert tab5_cli.restore(rig.ctx, rig.io, make_backup(part, size=6 * 1048576), None, yes=True) == 1
    assert rig.fake.calls == []


def test_restore_old_backup_without_info_still_checks_the_chip(rig, tmp_path):
    b = make_backup(tmp_path, mac=None)
    assert tab5_cli.restore(rig.ctx, rig.io, b, None, yes=False) == 0
    assert "확인하지 못했다" in rig.text()
    rig.fake.kw = {"flash": "8MB"}
    assert tab5_cli.restore(rig.ctx, rig.io, b, SER, yes=True) == 1                   # 정보 없는 백업은 --serial 로 짚어야 한다
    assert rig.fake.names()[-1] == "chip-id:no-reset/watchdog-reset"


def test_ub_hands_tab5_to_its_own_parser():
    a = ub.parser().parse_args(["tab5", "flash", "--yes"])
    assert a.cmd == "tab5"
    assert tab5_cli.parser().parse_args(["flash", "--yes", "--serial", SER]).yes
    with pytest.raises(SystemExit):
        tab5_cli.parser().parse_args(["flash", "--force"])


def test_esptool_is_never_imported():
    """GPL 부품은 따로 된 프로그램으로만 부른다(FL-D2)."""
    for f in ("urgent_board/tab5_flash.py", "tools/tab5_cli.py"):
        text = (Path(__file__).resolve().parents[1] / f).read_text(encoding="utf-8")
        assert "import esptool" not in text and "from esptool" not in text
    assert os.path.exists(Path(__file__).resolve().parents[1] / "firmware" / "release.json")


def test_a_guessed_device_is_shown_but_never_written(rig):
    """설정도 --serial 도 없이 꽂힌 기기가 하나면 확인만 보여 주고, 실제로 쓸 때는 짚으라고 한다(독립 검토 2026-11-03 중요 1)."""
    rig.unconfigure()
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=False) == 0
    assert f"--yes --serial {SER}" in rig.said[-1]
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 1
    assert rig.fake.calls == [] and "짚어 주세요" in rig.text()
    assert not (rig.home / "state" / "tab5-link.pause").exists()
    assert tab5_cli.flash(rig.ctx, rig.io, SER, rig.image, yes=True) == 0, rig.text()


def test_a_device_with_another_usb_name_gets_a_warning(rig):
    rig.ports[0].description = "StackChan(UiFlow2)"
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=False) == 0
    assert "USB 이름" in rig.text() and rig.fake.calls == []


def test_failed_backup_never_writes(rig):
    rig.fake.fail["read-flash"] = (2, "A fatal error occurred: Serial data stream stopped")
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 1
    names = rig.fake.names()
    assert not any(n.startswith("write-flash") for n in names) and names[-1] == "chip-id:no-reset/watchdog-reset"
    assert not list((rig.home / "tab5-backups").glob("*.bin*")) and "백업을 읽지 못했다" in rig.text()


def test_esptool_timeout_is_a_refusal_not_a_hang(rig, monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"), output="Connecting....", stderr="")
    rig.ctx.run = slow
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 1
    assert "멈춤" in rig.text() and rig.saved == []
    assert "제한 시간" in (rig.home / "logs" / "tab5-flash.log").read_text()


@pytest.mark.parametrize("change, word", [({"chip": "ESP32-S3"}, "ESP32-P4"), ({"mac": OTHER.lower()}, "MAC")])
def test_restore_checks_the_chip_before_writing(rig, tmp_path, change, word):
    b = make_backup(tmp_path)
    rig.fake.kw = change
    assert tab5_cli.restore(rig.ctx, rig.io, b, None, yes=True) == 1
    assert not any(n.startswith("write-flash") for n in rig.fake.names()) and word in rig.text()
    assert rig.fake.names()[-1] == "chip-id:no-reset/watchdog-reset"


def test_restore_of_an_unknown_backup_needs_a_pointed_device(rig, tmp_path):
    b = make_backup(tmp_path, mac=None)
    (tmp_path / "tab5-2233-20310102-030405.json").write_text("{broken", encoding="utf-8")
    assert tab5_cli.restore(rig.ctx, rig.io, b, None, yes=True) == 1
    assert "깨져" in rig.text() and "직접 짚어" in rig.text() and rig.fake.calls == []
    assert tab5_cli.restore(rig.ctx, rig.io, b, SER, yes=True) == 0, rig.text()


def test_interrupt_gives_the_port_back_and_says_what_to_do(rig, monkeypatch):
    def killed(cmd, **kw):
        if "read-flash" in cmd:
            raise SystemExit(143)                                      # SIGTERM 처리기가 하는 일
        return FakeEsptool()(cmd, **kw)
    rig.ctx.run = killed
    rc = tab5_cli.main(["flash", "--yes", "--image", str(rig.image)], ctx=rig.ctx, io=rig.io)
    assert rc == 143 and "끊겼다" in rig.text() and "아직 쓰지 않았다" in rig.text()
    assert not (rig.home / "state" / "tab5-link.pause").exists()
    assert not list((rig.home / "tab5-backups").glob(".tab5-stage-*"))


def test_interrupt_while_waiting_for_the_window_gives_the_port_back(rig, monkeypatch):
    """__enter__ 안에서 끊기면 __exit__ 가 불리지 않는다 — 놓기 파일을 직접 되돌린다(재검토 2026-11-03 중요)."""
    def stuck(self, t0):
        raise SystemExit(143)
    monkeypatch.setattr(tab5_cli.PortLoan, "_wait_released", stuck)
    assert tab5_cli.main(["flash", "--yes", "--image", str(rig.image)], ctx=rig.ctx, io=rig.io) == 143
    assert not (rig.home / "state" / "tab5-link.pause").exists() and rig.fake.calls == []
    assert not list((rig.home / "tab5-backups").glob(".tab5-stage-*"))


def test_interrupt_while_staging_leaves_no_copy(rig, monkeypatch):
    def half(src, dst):
        Path(dst).write_bytes(b"half")
        raise KeyboardInterrupt
    monkeypatch.setattr(tab5_cli.shutil, "copyfile", half)
    assert tab5_cli.main(["flash", "--yes", "--image", str(rig.image)], ctx=rig.ctx, io=rig.io) == 130
    assert not list((rig.home / "tab5-backups").glob(".tab5-stage-*")) and rig.fake.calls == []


def test_a_copy_left_by_a_killed_run_is_removed(rig):
    bdir = rig.home / "tab5-backups"
    bdir.mkdir()
    (bdir / ".tab5-stage-old.bin").write_bytes(b"x" * 10)
    assert tab5_cli.flash(rig.ctx, rig.io, None, rig.image, yes=True) == 0
    assert not list(bdir.glob(".tab5-stage-*"))


def test_interrupt_after_the_write_says_the_write_is_done(rig, monkeypatch):
    def cut(ctx, serial):
        raise SystemExit(143)
    monkeypatch.setattr(setup_cli, "tab5", cut)
    assert tab5_cli.main(["flash", "--yes", "--image", str(rig.image)], ctx=rig.ctx, io=rig.io) == 143
    assert "다 썼다" in rig.text() and "반쯤" not in rig.said[-1]


def test_sigterm_is_taken_once_then_ignored():
    import signal
    old = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(SystemExit) as e:
            tab5_cli._on_term(signal.SIGTERM, None)
        assert e.value.code == 143 and signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTERM, old)


def test_printed_commands_survive_spaces_in_paths(rig, tmp_path):
    """자료 폴더는 'Application Support' — 안내하는 명령 줄의 경로는 따옴표로(코덱스 검토 2026-11-03)."""
    import shlex
    d = tmp_path / "with space"
    d.mkdir()
    b = make_backup(d)
    assert tab5_cli.restore(rig.ctx, rig.io, b, None, yes=False) == 0
    line = rig.said[-1].split("되돌리기: ", 1)[1]
    words = shlex.split(line)
    assert words[1:4] == ["tab5", "restore", str(b)] and "--yes" in words


def test_info_file_of_another_backup_is_refused(rig, tmp_path):
    b = make_backup(tmp_path)
    info = tmp_path / "tab5-2233-20310102-030405.json"
    info.write_text(json.dumps({"serial": SER, "mac": SER.lower(), "sha256": "0" * 64}))
    assert tab5_cli.restore(rig.ctx, rig.io, b, None, yes=True) == 1
    assert "정보 파일의 지문" in rig.text() and rig.fake.calls == []
