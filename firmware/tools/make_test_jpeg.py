#!/usr/bin/env python3
"""make_test_jpeg.py — 자가 시험용 1280×720 시험 그림(JPEG, baseline, 4:2:2)을 만든다.

  python3 tools/make_test_jpeg.py <출력.jpg> [품질=90] [--420 | --gray]
     --gray = 성분이 하나뿐인 흑백 JPEG(이 펌웨어는 받지 않아야 한다 — 부품 패치 J6)

그림에 든 것(화면 찍기로 무엇을 확인하나):
  - 네 귀퉁이 글자 TL/TR/BL/BR · 화살표  → 방향(뒤집힘·좌우 반전)
  - 색 막대 · 회색 계단 0~255(16단) · 매끈한 기울기 → 색 순서·색 범위(양 끝이 뭉개지는지)
  - 1픽셀 줄무늬 · 작은 글자 → 1:1 로 나오는지(늘리거나 줄이면 무늬가 뭉개진다)
  - 테두리 1픽셀 흰 줄 → 가장자리가 잘리지 않는지
"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720


def font(size):
    for path in ("/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make():
    im = Image.new("RGB", (W, H), (24, 24, 26))
    d = ImageDraw.Draw(im)
    # 색 막대(위)
    bars = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
    bw = W // len(bars)
    for i, c in enumerate(bars):
        d.rectangle([i * bw, 60, (i + 1) * bw - 1, 200], fill=c)
    # 회색 계단 16단 + 숫자
    sw = W // 16
    for i in range(16):
        v = round(i * 255 / 15)
        d.rectangle([i * sw, 210, (i + 1) * sw - 1, 290], fill=(v, v, v))
        d.text((i * sw + 6, 214), str(v), fill=(255, 64, 64) if v < 128 else (128, 0, 0), font=font(18))
    # 매끈한 기울기(회색 · 빨강 · 초록 · 파랑)
    for x in range(W):
        v = x * 255 // (W - 1)
        d.line([x, 300, x, 329], fill=(v, v, v))
        d.line([x, 330, x, 349], fill=(v, 0, 0))
        d.line([x, 350, x, 369], fill=(0, v, 0))
        d.line([x, 370, x, 389], fill=(0, 0, v))
    # 1픽셀 줄무늬(세로 · 가로 · 바둑판)
    for x in range(40, 340):
        if x % 2 == 0:
            d.line([x, 410, x, 500], fill=(255, 255, 255))
    for y in range(410, 500):
        if y % 2 == 0:
            d.line([380, y, 680, y], fill=(255, 255, 255))
    px = im.load()
    for y in range(410, 500):
        for x in range(720, 1020):
            if (x + y) % 2 == 0:
                px[x, y] = (255, 255, 255)
    d.text((40, 504), "1px 세로줄", fill=(200, 200, 200), font=font(16))
    d.text((380, 504), "1px 가로줄", fill=(200, 200, 200), font=font(16))
    d.text((720, 504), "1px 바둑판", fill=(200, 200, 200), font=font(16))
    # 글자 크기별
    y = 536
    for size in (11, 13, 16, 20, 28):
        d.text((40, y), f"{size}px  가나다라마바사 ABCDEFG abcdefg 0123456789  WideEye 자가 시험", fill=(235, 235, 230), font=font(size))
        y += size + 8
    # 귀퉁이 표시 + 테두리
    d.rectangle([0, 0, W - 1, H - 1], outline=(255, 255, 255))
    f = font(34)
    d.text((14, 8), "TL ↖ 왼쪽 위", fill=(255, 220, 0), font=f)
    d.text((W - 250, 8), "오른쪽 위 ↗ TR", fill=(255, 220, 0), font=f)
    d.text((14, H - 52), "BL ↙ 왼쪽 아래", fill=(255, 220, 0), font=f)
    d.text((W - 290, H - 52), "오른쪽 아래 ↘ BR", fill=(255, 220, 0), font=f)
    d.text((W // 2 - 150, 12), "1280 × 720  시험 그림", fill=(255, 255, 255), font=font(30))
    return im


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    q = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 90
    sub = 2 if "--420" in sys.argv else 1                  # PIL: 0 = 4:4:4, 1 = 4:2:2, 2 = 4:2:0
    im = make()
    if "--gray" in sys.argv:
        im.convert("L").save(sys.argv[1], "JPEG", quality=q, progressive=False, optimize=False)
        print(f"{sys.argv[1]}: {W}x{H} q={q} gray {os.path.getsize(sys.argv[1])} B")
        return
    im.save(sys.argv[1], "JPEG", quality=q, subsampling=sub, progressive=False, optimize=False)
    print(f"{sys.argv[1]}: {W}x{H} q={q} {'4:2:0' if sub == 2 else '4:2:2'} {os.path.getsize(sys.argv[1])} B")


if __name__ == "__main__":
    main()
