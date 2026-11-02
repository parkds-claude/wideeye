#!/usr/bin/env python3
"""make_bad_jpegs.py — 멀쩡한 JPEG 하나에서 깨진 것 여덟 가지를 만든다(기기의 자가 시험 `put`/`play` 에 넣을 것).

  python3 tools/make_bad_jpegs.py <좋은.jpg> <출력 폴더>

출력 폴더는 저장소 밖에 둔다(시험 산출물). 만드는 것:
  good.jpg          그대로
  trunc_half.jpg    앞 절반만                         — 스캔 데이터가 중간에 끊긴다(머리는 멀쩡)
  trunc_tail.jpg    끝 3000바이트를 뺌                 — 끝 표시(EOI) 없음
  trunc_header.jpg  SOS 마커 40바이트 앞에서 자름       — 머리가 끝나기 전에 끊긴다
  noise_scan.jpg    스캔 데이터의 200바이트를 무작위로   — 머리는 멀쩡, 그림 내용이 깨짐
  early_eoi.jpg     스캔 데이터 186바이트째에 FF D9 00 00 — 그림이 다 나오기 전에 끝 표시
  lying_size.jpg    SOF 의 크기를 4096×4096 으로         — 머리는 꼴이 맞지만 내용과 다른 크기
  garbage.jpg       무작위 20000바이트                   — JPEG 이 아니다
  soi_only.jpg      FF D8 뒤에 0 을 1000개               — 2026-10-13 기기를 죽인 입력(부품 패치 J6 이전)
무작위는 씨앗을 고정해 매번 같은 파일이 나온다.
"""
import os
import random
import sys


def find_segments(b):
    """SOI 뒤의 조각을 차례로 따라가 SOF0 과 SOS 의 자리를 돌려준다(검색이 아니라 길이를 따라간다)."""
    if b[:2] != b"\xff\xd8":
        sys.exit("JPEG 이 아니다(FF D8 로 시작하지 않는다)")
    pos, sof, sos = 2, None, None
    while pos + 4 <= len(b):
        if b[pos] != 0xFF:
            sys.exit(f"마커 자리({pos})에 FF 가 없다")
        marker, length = b[pos + 1], int.from_bytes(b[pos + 2:pos + 4], "big")
        if marker == 0xC0:
            sof = pos
        if marker == 0xDA:
            sos = pos
            return sof, sos, pos + 2 + length
        pos += 2 + length
    sys.exit("SOS 를 찾지 못했다")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    good = open(sys.argv[1], "rb").read()
    out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    sof, sos, scan = find_segments(good)
    if sof is None or len(good) < scan + 4000:
        sys.exit("SOF0 이 없거나 그림이 너무 작다")
    rng = random.Random(20261013)

    noise = bytearray(good)
    for _ in range(200):
        noise[rng.randrange(scan + 400, len(good) - 1500)] = rng.randrange(256)
    eoi = bytearray(good)
    eoi[scan + 186:scan + 190] = b"\xff\xd9\x00\x00"
    lying = bytearray(good)
    lying[sof + 5:sof + 9] = b"\x10\x00\x10\x00"       # SOF0: FF C0, 길이 2, 정밀도 1, 세로 2, 가로 2

    files = {
        "good.jpg": good,
        "trunc_half.jpg": good[:len(good) // 2],
        "trunc_tail.jpg": good[:-3000],
        "trunc_header.jpg": good[:sos - 40],
        "noise_scan.jpg": bytes(noise),
        "early_eoi.jpg": bytes(eoi),
        "lying_size.jpg": bytes(lying),
        "garbage.jpg": bytes(rng.randrange(256) for _ in range(20000)),
        "soi_only.jpg": b"\xff\xd8" + bytes(1000),
    }
    for name, data in files.items():
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
        print(f"{name}: {len(data)} B")


if __name__ == "__main__":
    main()
