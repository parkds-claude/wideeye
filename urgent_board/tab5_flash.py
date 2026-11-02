"""탭5 펌웨어 굽기 · 되돌리기의 판단 — esptool 출력 읽기, 기기 고르기, 거절할 까닭, 백업 이름(계획 FL-D1 ~ FL-D9).

하위 프로세스 · 포트 · 파일 쓰기 · 받기는 tools/tab5_cli.py 가 한다. esptool 은 GPL-2.0-or-later 라서 import 하지 않는다(FL-D2 —
`python -m esptool` 로만 부른다). 이 파일은 그 출력 글을 읽기만 한다.

릴리스 이미지의 지문 · 크기 · 쓰는 위치는 firmware/release.json 하나에만 적는다(FL-D4 — firmware/tools/make_release_image.sh 가 쓴다).
받을 주소의 앞부분 REPO 는 공개판을 내보낼 때 실제 저장소 주소로 바뀐다(작업장에서는 자리표시자 그대로 — 그때는 --image 로 준다).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILE = CODE_ROOT / "firmware" / "release.json"
REPO = "https://github.com/parkds-claude/wideeye"
ESPRESSIF_VID = 0x303A
GUESS = "꽂힌 Espressif 기기가 하나뿐"   # pick 의 까닭 — 넘겨짚은 것. 실제로 쓸 때(--yes)는 받지 않는다(독립 검토 2026-11-03 중요 1)
JTAG_NAME = "JTAG"                       # 탭5(공장 · WideEye 펌웨어)의 USB 이름 "USB JTAG/serial debug unit" — 다르면 다른 기기일 수 있다
FULL = 16 * 1024 * 1024                  # 탭5 의 플래시(ESP32-P4NRW32 — 16MB). 백업 · 되돌리기는 이만큼 통째로


class ReleaseError(ValueError):
    """release.json 이 없거나 꼴이 틀렸다."""


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    file: str
    offset: int
    size: int
    sha256: str
    chip: str
    rev_min: int              # 칩 판 = 주판 × 100 + 부판(v1.3 → 103). 우리 빌드는 v1.0 ~ v1.99 만(SELECTS_REV_LESS_V3)
    rev_max: int
    flash_size: int           # 바이트
    esptool: str


def size_bytes(text: str) -> int:
    m = re.fullmatch(r"(\d+)\s*(KB|MB)", text.strip(), re.I)
    if not m:
        raise ValueError(f"크기 꼴이 아니다: {text!r}")
    return int(m.group(1)) * (1024 if m.group(2).upper() == "KB" else 1024 * 1024)


def load_release(path: Path | None = None) -> Release:
    path = Path(path or RELEASE_FILE)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        r = Release(version=str(d["version"]), tag=str(d["tag"]), file=str(d["file"]), offset=int(str(d["offset"]), 16),
                    size=int(d["size"]), sha256=str(d["sha256"]).lower(), chip=str(d["chip"]), rev_min=int(d["chip_rev"][0]),
                    rev_max=int(d["chip_rev"][1]), flash_size=size_bytes(str(d["flash_size"])), esptool=str(d["esptool"]))
    except FileNotFoundError:
        raise ReleaseError(f"{path} 가 없다") from None
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ReleaseError(f"{path} 의 꼴이 틀렸다({e})") from None
    if not re.fullmatch(r"[0-9a-f]{64}", r.sha256) or "/" in r.file or not r.file.endswith(".bin") or r.size <= 0:
        raise ReleaseError(f"{path} 의 값이 이상하다")
    return r


def image_url(rel: Release, repo: str | None = None) -> str | None:
    """받을 주소. 저장소 주소가 아직 자리표시자면(작업장) None — 그때는 --image 로 준다."""
    repo = REPO if repo is None else repo
    if not repo.startswith("https://"):
        return None
    return f"{repo.rstrip('/')}/releases/download/{rel.tag}/{rel.file}"


def norm_mac(s: str | None) -> str:
    """'AA:BB:…' · 'aa-bb-…' · 'aabb…' 를 'aa:bb:…' 로. MAC 꼴이 아니면 빈 글자."""
    h = re.sub(r"[^0-9a-f]", "", (s or "").lower())
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)) if len(h) == 12 else ""


@dataclass
class Chip:
    """esptool 출력에서 읽은 것(모르는 것은 None)."""
    name: str | None = None
    rev: int | None = None
    mac: str = ""
    flash: int | None = None
    secure_boot: bool | None = None
    flash_enc: bool | None = None

    def rev_text(self) -> str:
        return "모름" if self.rev is None else f"v{self.rev // 100}.{self.rev % 100}"


_CHIP = re.compile(r"^(?:Chip type:|Chip is)\s*([A-Za-z0-9-]+)(?:\s*\(revision v(\d+)\.(\d+)\))?", re.M)
_MAC = re.compile(r"^MAC:\s*([0-9A-Fa-f:]{17})\s*$", re.M)
_FLASH = re.compile(r"^Detected flash size:\s*(\d+\s*[KM]B)\s*$", re.M)
_ONOFF = {"enabled": True, "disabled": False}


def parse(text: str, into: Chip | None = None) -> Chip:
    """esptool 5.x(`Chip type:`) · 4.x(`Chip is`) 출력에서 칩 · 판 · MAC · 플래시 크기 · 보안 부팅 · 플래시 암호화를 읽어 into 에 더한다."""
    c = into or Chip()
    m = _CHIP.search(text)
    if m:
        c.name = m.group(1).upper()
        if m.group(2) is not None:
            c.rev = int(m.group(2)) * 100 + int(m.group(3))
    macs = {norm_mac(x) for x in _MAC.findall(text)} - {""}
    if len(macs) == 1:
        c.mac = macs.pop()
    m = _FLASH.search(text)
    if m:
        c.flash = size_bytes(m.group(1))
    for key, attr in (("Secure Boot", "secure_boot"), ("Flash Encryption", "flash_enc")):
        m = re.search(rf"^{key}:\s*(Enabled|Disabled)\b", text, re.M | re.I)
        if m:
            setattr(c, attr, _ONOFF[m.group(1).lower()])
    return c


def _bare(name: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def chip_problems(c: Chip, rel: Release, serial: str) -> list[str]:
    """굽기 전에 막을 까닭(FL-D7 ②). 빈 목록이면 굽는다. 모르는 것도 막는다 — 확인한 것만 믿는다."""
    out = []
    if _bare(c.name) != _bare(rel.chip):                 # release.json 은 esp32p4, esptool 은 ESP32-P4 라고 쓴다
        out.append(f"칩이 ESP32-P4 가 아니다({c.name or '읽지 못함'}) — 탭5 가 아닌 기기일 수 있다")
    if c.rev is None or not rel.rev_min <= c.rev <= rel.rev_max:
        out.append(f"칩 판이 {c.rev_text()} 이다 — 이 펌웨어는 v{rel.rev_min // 100}.x 칩에서만 돈다"
                   " (새로 나온 v3 칩 탭5 라면 아직 지원하지 않는다)")
    if not c.mac or c.mac != norm_mac(serial):
        out.append("MAC 이 USB 일련번호와 다르다(또는 읽지 못함) — 고른 기기와 연결된 기기가 다를 수 있다")
    if c.flash is None or c.flash < rel.flash_size:
        out.append(f"플래시가 {rel.flash_size // 1048576}MB 보다 작다(또는 읽지 못함)")
    if c.secure_boot is not False or c.flash_enc is not False:
        out.append("보안 부팅이나 플래시 암호화가 켜져 있다(또는 읽지 못함) — 이 기기에는 굽지 않는다(서명 없는 펌웨어는 켜지지 않는다)")
    return out


def pick(devices: list[dict], wanted: str | None, configured: str | None) -> tuple[dict | None, str]:
    """어느 기기에 굽나(FL-D6) — USB 정보로만, esptool 을 걸기 전에. (기기, 까닭 또는 거절 글).

    --serial 로 준 것 → 설정의 탭5(display.tab5_usb_serial) → Espressif 기기가 딱 하나일 때만 그것. 다른 ESP32 기기
    (스택짱 · UIFlow 기기 …)에 리셋을 걸지 않으려고 넘겨짚지 않는다."""
    by = {norm_mac(d.get("serial")) or (d.get("serial") or "").upper(): d for d in devices}
    listing = ", ".join(f"{d.get('serial')} ({d.get('device')})" for d in devices) or "없음"
    def key(v: str) -> str:
        return norm_mac(v) or v.upper()
    if wanted:
        if key(wanted) in by:
            return by[key(wanted)], "--serial 로 준 탭5"
        return None, f"--serial 로 준 일련번호 {wanted} 인 기기가 USB 에 없다 — 꽂힌 기기: {listing}"
    if configured:
        if key(configured) in by:
            return by[key(configured)], "설정의 탭5"
        if devices:
            return None, (f"설정의 탭5({configured})가 USB 에 없다 — 다른 탭5 에 구우려면 --serial <일련번호> 로 고른다. "
                          f"꽂힌 기기: {listing}")
        return None, f"설정의 탭5({configured})가 USB 에 없다 — 데이터가 되는 USB-C 선으로 꽂고 다시"
    if not devices:
        return None, "USB 로 꽂힌 탭5(Espressif 기기)가 없다 — 데이터가 되는 USB-C 선으로 꽂고 다시"
    if len(devices) > 1:
        return None, f"Espressif 기기가 {len(devices)}대 꽂혀 있다 — --serial <일련번호> 로 하나를 고른다: {listing}"
    return devices[0], GUESS


def backup_stem(serial: str, when: datetime) -> str:
    """백업 파일 이름(확장자 없이) — tab5-<MAC 끝 4자리>-<YYYYmmdd-HHMMSS>."""
    tail = re.sub(r"[^0-9a-f]", "", serial.lower())[-4:] or "x"
    return f"tab5-{tail}-{when.strftime('%Y%m%d-%H%M%S')}"


def sha_line(sha: str, name: str) -> str:
    return f"{sha}  {name}\n"


def read_sha_file(text: str) -> str | None:
    m = re.match(r"\s*([0-9a-fA-F]{64})\b", text)
    return m.group(1).lower() if m else None
