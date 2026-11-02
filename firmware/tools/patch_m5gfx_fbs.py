#!/usr/bin/env python3
"""M5GFX 의 Tab5 패널(Panel_DSI)이 잡는 프레임버퍼를 2장 → 3장으로 늘린다 (멱등, CMake 가 구성 때마다 실행).

이유(MN-D10): 버퍼가 2장이면 뒤 버퍼를 화면으로 넘긴 뒤 그것이 실제 주사에 반영될 때까지(최대 17.5ms) 옛 버퍼에 그릴 수 없다.
  한 장을 그리는 데 18.6ms 가 걸려 주사 한 주기(17.45ms)를 살짝 넘으므로, 2장으로는 프레임마다 두 주기 = 약 29fps 에 묶인다.
  3장이면 기다리지 않고 남는 한 장에 바로 그린다 → 그리는 속도만큼(50fps 안팎) 나온다.
비용: PSRAM 1.84MB. M5GFX 는 첫 장만 쓰므로 M5GFX 쪽 동작은 그대로다.
되돌리기: 이 스크립트 호출을 CMakeLists.txt 에서 빼고 managed_components/m5stack__m5gfx 를 지우면 원본이 다시 받아진다
  (펌웨어는 버퍼가 2장이어도 그대로 돈다 — video.cpp 가 장수를 물어본다).
CHECKSUMS.json 의 해당 파일 해시도 갱신해 컴포넌트 매니저 무결성 검사를 통과시킨다. 다시 받아져도 이 스크립트가 다시 적용한다.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMP = ROOT / "managed_components/m5stack__m5gfx"
TARGET = COMP / "src/lgfx/v1/platforms/esp32p4/Panel_DSI.inl"
ORIG = "    dpi_config.num_fbs = 2;"
MARK = "PATCHED (tab5_monitor)"
PATCHED = "    dpi_config.num_fbs = 3;  /* PATCHED (tab5_monitor): 2 -> 3, tools/patch_m5gfx_fbs.py */"


def update_checksum(path: Path) -> None:
    cj = COMP / "CHECKSUMS.json"
    if not cj.exists():
        return
    j = json.loads(cj.read_text(encoding="utf-8"))
    rel = str(path.relative_to(COMP))
    data = path.read_bytes()
    for f in j.get("files", []):
        if f.get("path", "").lstrip("/") == rel:
            f["size"] = len(data)
            f["hash"] = hashlib.sha256(data).hexdigest()
    cj.write_text(json.dumps(j, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if not TARGET.exists():
        # CMake 는 project() 뒤에 이 스크립트를 부른다 — 그때는 부품 관리자가 부품을 이미 받아 놓았다(2026-10-12 빈 폴더의 첫 구성에서
        # 확인: 바로 "applied"). 그런데도 없으면 M5GFX 의 파일 배치가 바뀐 것이다. 조용히 넘어가면 버퍼 2장(29fps)짜리가 만들어진다 → 멈춘다.
        print(f"[patch_m5gfx_fbs] ERROR: {TARGET} not found - M5GFX layout changed or the component was not fetched", file=sys.stderr)
        return 1
    src = TARGET.read_text(encoding="utf-8")
    if MARK in src:
        print("[patch_m5gfx_fbs] already applied")
        return 0
    if src.count(ORIG) != 1:
        print("[patch_m5gfx_fbs] ERROR: 'dpi_config.num_fbs = 2;' not found exactly once — M5GFX layout changed", file=sys.stderr)
        return 1
    TARGET.write_text(src.replace(ORIG, PATCHED), encoding="utf-8")
    update_checksum(TARGET)
    print("[patch_m5gfx_fbs] applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
