#!/usr/bin/env python3
"""gen_ui_assets.py — 화면 글자를 미리 구워 둔다(알파 8bit). ESP-IDF export 를 하지 않은 셸에서 실행(Pillow 필요).

왜: M5GFX 내장 한글 비트맵 글꼴(efontKR 24px)은 키우면 계단이 진다. 맥에서 좋은 글꼴로 한 번 그려 알파 마스크로 굽고,
    기기는 그 마스크에 색을 입혀 화면에 섞기만 한다.
무엇을: ① 고정 문구(안내 화면·단추·이름표) — 문구 하나가 그림 하나  ② 영문·숫자·기호(ASCII 0x20~0x7E) 낱자 — 바뀌는 글(밝기·온도·진단 숫자)용
출력: main/ui_assets.dat(알파 바이트를 이어 붙인 것, 펌웨어에 통째로 들어간다) + main/ui_assets.h(위치·크기 표)
글꼴: Pretendard Bold(SIL OFL 1.1 — 구운 글자 그림을 펌웨어에 넣어도 된다). 아래 두 자리에서 찾는다.
      둘 다 없으면 멈춘다 — 다른 글꼴로 조용히 구워지면 글자 폭이 달라져 화면 배치가 틀어진다(독립 검토 1차 M-8).
      Noto Sans CJK KR Bold(OFL)로라도 구우려면 `--allow-fallback`(헤더 머리말에 글꼴 이름이 남는다).
문구를 바꾸면: 아래 LABELS 를 고치고 `python3 tools/gen_ui_assets.py --preview /tmp/ui.png` → 미리보기로 글자가 깨지지 않았는지 본 뒤 빌드.
같은 글꼴 파일·같은 Pillow 판이면 결과가 바이트까지 같다(2026-11-02 확인: Pillow 12.3.0 + raqm — 맥북 brew 파이썬).
  raqm 이 없는 Pillow(맥 기본 파이썬의 10.4.0)로 구우면 낱자는 같지만 한글 문구의 글자 사이가 달라진다 — 같은 조건으로 구울 것.
"""
import hashlib
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT_DAT = ROOT / "main" / "ui_assets.dat"
OUT_H = ROOT / "main" / "ui_assets.h"
# Pretendard 1.3.9 묶음(github.com/orioncactus/pretendard 릴리스)의 public/static/Pretendard-Bold.otf — sha256 2e91915fab54…
#   맥에 깔려 있거나, 그 파일을 fonts/ 에 두면 된다(fonts/*.otf 는 git 에서 뺐다)
FONTS = [Path.home() / "Library/Fonts/Pretendard-Bold.otf",
         ROOT / "fonts" / "Pretendard-Bold.otf"]
FALLBACK_FONTS = [Path.home() / "Library/Fonts/NotoSansCJKkr-Bold.otf"]      # --allow-fallback 일 때만
MAX_LABEL_W = 672                  # 세로로 세웠을 때의 가운데 판(720 - 24) 안에 들어가야 한다

# (C 이름, 글, 크기 px)
LABELS = [
    ("H_TOO_HOT",   "기기가 뜨거워 잠시 쉽니다", 46),
    ("H_FAULT",     "화면이 멈췄습니다", 46),
    ("H_BOARD_WAIT", "현황판을 기다리는 중", 46),          # 현황판 모드(MN-D32)
    ("H_BOARD_LOST", "현황판이 오지 않습니다", 46),
    ("S_BOARD_WAIT", "맥이 화면을 보내면 나옵니다", 28),
    ("S_BOARD_LOST", "맥과 USB-C 선을 확인해 주세요", 28),
    ("H_FAULT_INIT", "화면을 시작하지 못했습니다", 46),
    ("S_TOO_HOT",   "식으면 현황판이 다시 나옵니다", 28),
    ("S_FAULT",     "Tab5 전원을 껐다 켜 주세요", 28),
    ("N_HEAT",      "뜨거워져 밝기를 낮췄습니다", 28),
    ("F_NAME",      "WideEye", 22),
    ("B_DARKER",    "어둡게", 34),
    ("B_BRIGHTER",  "밝게", 34),
    ("B_ROTATE",    "방향", 34),
    ("B_MONO",      "흑백", 34),
    ("B_COLOR",     "컬러", 34),
    ("B_CLOSE",     "닫기", 34),
    ("L_BRI",       "밝기", 26),
    ("L_SCREEN",    "화면", 26),
    ("L_MONO",      "흑백", 26),
    ("L_COLOR",     "컬러", 26),
    ("L_TEMP",      "온도", 26),
    ("L_DEG",       "도", 26),
]
GLYPH_PX = 26                      # 바뀌는 글의 크기(이름표 L_* 와 같아야 한 줄에 나란히 놓인다)
GLYPH_FIRST, GLYPH_LAST = 0x20, 0x7E


def load_font(px):
    cands = FONTS + (FALLBACK_FONTS if "--allow-fallback" in sys.argv else [])
    for p in cands:
        if p.exists():
            return ImageFont.truetype(str(p), px), p
    sys.exit("글꼴이 없습니다(다른 글꼴로 구우려면 --allow-fallback): " + ", ".join(str(p) for p in cands))


def render_label(text, px):
    """글 전체를 꼭 맞는 상자에(위아래는 글꼴의 줄 높이 — 같은 크기의 문구끼리 밑줄이 맞는다).
    글자의 잉크가 글 폭 밖으로 나가는 글꼴에서도 잘리지 않게, 상자는 잉크 범위와 글 폭 중 넓은 쪽에 맞춘다(양옆 1픽셀 여백)."""
    f, _ = load_font(px)
    asc, desc = f.getmetrics()
    left, _, right, _ = f.getbbox(text)
    pad = 1 + max(0, -left)
    w = pad + max(int(f.getlength(text) + 0.999), right) + 1
    img = Image.new("L", (w, asc + desc), 0)
    ImageDraw.Draw(img).text((pad, 0), text, font=f, fill=255)
    missing = any(f.getmask(ch).getbbox() is None for ch in text if not ch.isspace())
    if missing:
        sys.exit(f"글꼴에 없는 글자가 있습니다: {text!r}")
    return img


def render_glyphs(px):
    """낱자마다 한 칸. 칸의 폭은 보통 그 글자의 나아감(advance)이지만, 잉크가 그 밖으로 나가는 글자(이 글꼴의 26px 에서는
    '#'·'_'·'j' 가 1픽셀씩)는 칸을 그만큼 넓혀 잘리지 않게 한다 — 예전에는 (0, 0)에 그려 그 1픽셀이 잘렸다."""
    f, _ = load_font(px)
    asc, desc = f.getmetrics()
    out = []
    for cp in range(GLYPH_FIRST, GLYPH_LAST + 1):
        ch = chr(cp)
        adv = max(1, int(f.getlength(ch) + 0.5))
        left, _, right, _ = f.getbbox(ch) if not ch.isspace() else (0, 0, 0, 0)
        pad = max(0, -left)
        img = Image.new("L", (pad + max(adv, right), asc + desc), 0)
        ImageDraw.Draw(img).text((pad, 0), ch, font=f, fill=255)
        out.append(img)
    return out


def main():
    _, used = load_font(20)
    blob = bytearray()
    rows = []                                    # (이름, offset, w, h)
    images = []
    for name, text, px in LABELS:
        img = render_label(text, px)
        if name[:2] in ("H_", "S_", "N_") and img.width > MAX_LABEL_W:
            sys.exit(f"{name} 이 너무 넓습니다({img.width} > {MAX_LABEL_W}px) — 세로 화면의 판에 들어가지 않습니다: {text!r}")
        rows.append((name, len(blob), img.width, img.height)); blob += img.tobytes(); images.append((name, img))
    glyphs = render_glyphs(GLYPH_PX)
    gh = glyphs[0].height
    grows = []
    for g in glyphs:
        grows.append((len(blob), g.width)); blob += g.tobytes()
    OUT_DAT.write_bytes(bytes(blob))

    font_sha = hashlib.sha256(used.read_bytes()).hexdigest()[:12]
    h = ["// ui_assets.h — tools/gen_ui_assets.py 가 만든 파일. 손으로 고치지 말 것(문구는 그 스크립트의 LABELS).",
         f"// 글꼴: {used.name}(sha256 {font_sha}…) · 알파 8bit · 자료는 ui_assets.dat({len(blob)} B, 펌웨어에 통째로 들어간다)",
         "#pragma once", "#include <cstdint>", "", "namespace uia {", "",
         "struct Asset { uint32_t off; uint16_t w, h; };          // ui_assets.dat 안의 위치와 크기(알파, 줄마다 w 바이트)",
         f"constexpr uint32_t DATA_BYTES = {len(blob)};            // ui_assets.dat 의 크기 — ui::init 이 실제 크기와 견준다(헤더와 자료가 어긋난 빌드를 막는다)", ""]
    for name, off, w, hh in rows:
        h.append(f"constexpr Asset {name} = {{{off}, {w}, {hh}}};")
    h += ["", f"constexpr int GLYPH_FIRST = {GLYPH_FIRST}, GLYPH_LAST = {GLYPH_LAST}, GLYPH_H = {gh};",
          "constexpr Asset GLYPH[] = {"]
    for i in range(0, len(grows), 6):
        h.append("    " + " ".join(f"{{{off}, {w}, {gh}}}," for off, w in grows[i:i + 6]))
    h += ["};", "", "}  // namespace uia", ""]
    OUT_H.write_text("\n".join(h), encoding="utf-8")
    print(f"{OUT_DAT.name}: {len(blob)} B, {len(rows)} labels + {len(grows)} glyphs, font {used.name}")

    if "--preview" in sys.argv:
        path = sys.argv[sys.argv.index("--preview") + 1]
        W = max(i.width for _, i in images) + 40
        H = sum(i.height + 10 for _, i in images) + gh + 40
        pv = Image.new("RGB", (W, H), (22, 22, 21)); y = 10
        for _, img in images:
            pv.paste((242, 241, 238), (20, y), img); y += img.height + 10
        x = 20
        for g in glyphs:
            if x + g.width > W - 20:
                break
            pv.paste((166, 214, 170), (x, y), g); x += g.width
        pv.save(path); print("preview:", path)


if __name__ == "__main__":
    main()
