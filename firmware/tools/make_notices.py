#!/usr/bin/env python3
"""미리 빌드한 탭5 이미지에 실을 고지 파일(THIRD_PARTY_NOTICES.txt)을 원문 라이선스 파일들에서 모아 쓴다 — 계획 FL-D10.

사용(ESP-IDF 환경을 연 셸에서, 이 폴더에서 한 번 빌드한 뒤 — managed_components 의 라이선스를 읽는다):
    python3 tools/make_notices.py <LovyanGFX license.txt> [--check]
LovyanGFX 의 license.txt 는 M5GFX 부품에 들어 있지 않아 따로 받아 준다
(https://raw.githubusercontent.com/lovyan03/LovyanGFX/master/license.txt).

무엇이 이미지에 들어가는지는 링크 지도(build/wideeye_tab5.map · bootloader.map)로 확인했다(2026-11-03): ESP-IDF 부품 · FreeRTOS ·
Mbed TLS(암호 부분) · TLSF · newlib · libgcc/libstdc++ · M5GFX(글꼴은 Font0 = Adafruit glcdfont 하나) · M5Unified ·
jpeg_decode_enhanced · Pretendard 로 구운 글자 그림. Wi-Fi · 블루투스 · lwIP 는 들어가지 않는다. 부품 판을 올리면 다시 확인하고 다시 돌린다.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FW = Path(__file__).resolve().parents[1]
OUT = FW / "THIRD_PARTY_NOTICES.txt"
VERSIONS = {"idf": "5.5.4", "freertos": "10.5.1", "mbedtls": "3.6.5", "newlib": "4.3.0", "gcc": "14.2.0", "m5gfx": "0.2.30",
            "m5unified": "0.2.23", "pretendard": "1.3.9"}
# FreeRTOS 의 LICENSE.md 에는 저작권 줄이 없다 — MIT 는 저작권 줄을 함께 실으라고 하므로 소스(tasks.c 3번째 줄)의 줄을 붙인다
FREERTOS_COPYRIGHT = "Copyright (C) 2021 Amazon.com, Inc. or its affiliates.  All Rights Reserved."
STREAMER_COPYRIGHT = "Copyright (c) 2025 Hiroki Kawakami"      # Tab5-Screen-Streamer 의 LICENSE(MIT) 첫 저작권 줄 — 2026-11-03 원문 확인
TLSF_BSD3 = """Copyright (c) 2006-2016, Matthew Conte
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the copyright holder nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL MATTHEW CONTE BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


def read(p: str | Path) -> str:
    return Path(p).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").rstrip() + "\n"


def main(argv: list[str]) -> int:
    if not argv or not Path(argv[0]).is_file():
        print(__doc__)
        return 2
    idf = Path(os.environ.get("IDF_PATH", ""))
    if not (idf / "LICENSE").is_file():
        print("IDF_PATH 가 없다 — . $IDF_PATH/export.sh")
        return 2
    gcc = shutil.which("riscv32-esp-elf-gcc")                 # 빌드에 쓰는 툴체인(IDF 환경이 PATH 에 둔다) — 여러 판이 깔려 있어도 그것 하나
    lic = Path(gcc).resolve().parents[1] / "share" / "licenses" if gcc else None     # …/riscv32-esp-elf/bin/gcc → …/riscv32-esp-elf/share/licenses
    if lic is None or not (lic / "newlib" / "COPYING.NEWLIB").is_file():
        print("빌드에 쓰는 툴체인의 라이선스 폴더를 못 찾았다 — ESP-IDF 환경을 연 셸에서(riscv32-esp-elf-gcc 가 PATH 에)")
        return 2
    mc = FW / "managed_components"
    mit_streamer = read(FW / "components" / "jpeg_decode_enhanced" / "LICENSE").replace("Copyright (c) 2025 Hiroki Kawakami",
                                                                                       STREAMER_COPYRIGHT)
    v = VERSIONS
    parts = [f"""WideEye Tab5 firmware — third-party notices
===========================================

This file goes with the prebuilt Tab5 image of WideEye (wideeye-tab5-v<version>.bin in the GitHub releases) and with the
firmware source in this folder. The WideEye firmware source itself is under the MIT License (LICENSE at the top of the
repository). The prebuilt image contains code from the projects below (checked against the linker map of the release build).

1. ESP-IDF v{v['idf']} — Espressif Systems (Shanghai) Co., Ltd. — Apache License 2.0 (text A).
   Linked parts: bootloader, bootloader_support, efuse, esp_hw_support, esp_system, esp_lcd, esp_driver_gpio · i2c · i2s ·
   jpeg · ledc · ppa · rmt · spi · tsens · usb_serial_jtag, esp_mm, esp_psram, esp_timer, esp_ringbuf, esp_partition,
   esp_security, esp_app_format, app_update, hal, soc, spi_flash, nvs_flash, nvs_sec_provider, heap, vfs, log, pthread, cxx,
   newlib (ESP-IDF port), riscv, esp_rom, esp_common, esp_pm, esp_vfs_console.
   Third-party code inside ESP-IDF that is linked:
   - FreeRTOS Kernel {v['freertos']} — Amazon.com, Inc. or its affiliates — MIT License (text B).
   - Mbed TLS {v['mbedtls']} (crypto library) — The Mbed TLS Contributors — Apache License 2.0 (text A).
   - TLSF memory allocator — Matthew Conte — BSD 3-Clause License (text C).
2. Toolchain runtime libraries (Espressif GCC {v['gcc']} toolchain):
   - newlib C and math library {v['newlib']} — many authors — the licenses collected in newlib's COPYING.NEWLIB (text D).
   - libgcc and libstdc++ — Free Software Foundation — GPL-3.0 with the GCC Runtime Library Exception 3.1 (text E). The
     exception allows the compiled image to be distributed under terms of our choice.
3. M5GFX {v['m5gfx']} — M5Stack — MIT License (text F). The build patches it to use three frame buffers (tools/patch_m5gfx_fbs.py).
   M5GFX contains LovyanGFX — lovyan03 and contributors — FreeBSD License, with the Adafruit GFX (BSD) and other notices in
   LovyanGFX's license.txt (text G). The image keeps one built-in M5GFX font: the 5x7 "glcdfont" from Adafruit GFX (BSD, text G).
4. M5Unified {v['m5unified']} — M5Stack — MIT License (text H).
5. jpeg_decode_enhanced (from Tab5-UVC-Display) — Hiroki Kawakami — MIT License (text I). Modified: components/jpeg_decode_enhanced/PATCHES.md.
6. One setting value (memory bus priority of the display) from Tab5-Screen-Streamer — Hiroki Kawakami — MIT License (text J).
7. Pretendard {v['pretendard']} Bold — Kil Hyung-jin — SIL Open Font License 1.1 (text K). The image holds pre-rendered glyph
   pictures (main/ui_assets.dat), not the font file.

Not in the image: esptool (the flashing tool — Espressif Systems, GPL-2.0-or-later). The WideEye installer downloads it from
PyPI, and WideEye runs it as a separate program.
"""]
    texts = [("A", "Apache License 2.0 (ESP-IDF, Mbed TLS)", read(idf / "LICENSE")),
             ("B", "MIT License (FreeRTOS Kernel)", FREERTOS_COPYRIGHT + "\n\n" + read(idf / "components/freertos/FreeRTOS-Kernel/LICENSE.md")),
             ("C", "BSD 3-Clause License (TLSF)", TLSF_BSD3),
             ("D", "newlib licenses (COPYING.NEWLIB)", read(lic / "newlib" / "COPYING.NEWLIB")),
             ("E", "GCC Runtime Library Exception 3.1 (libgcc, libstdc++)", read(lic / "gcc" / "COPYING.RUNTIME")),
             ("F", "MIT License (M5GFX)", read(mc / "m5stack__m5gfx" / "LICENSE")),
             ("G", "LovyanGFX license.txt (inside M5GFX)", read(argv[0])),
             ("H", "MIT License (M5Unified)", read(mc / "m5stack__m5unified" / "LICENSE")),
             ("I", "MIT License (jpeg_decode_enhanced)", read(FW / "components" / "jpeg_decode_enhanced" / "LICENSE")),
             ("J", "MIT License (Tab5-Screen-Streamer)", mit_streamer),
             ("K", "SIL Open Font License 1.1 (Pretendard)", read(FW / "fonts" / "OFL-Pretendard.txt"))]
    for key, title, body in texts:
        parts.append(f"\n{'=' * 78}\nText {key}. {title}\n{'=' * 78}\n\n{body}")
    text = "".join(parts)
    if "--check" in argv[1:]:
        same = OUT.is_file() and OUT.read_text(encoding="utf-8") == text
        print("같다" if same else "다르다 — 다시 돌려 쓸 것")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"썼다: {OUT} ({len(text):,} 글자)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
