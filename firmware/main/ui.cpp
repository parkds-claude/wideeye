// ui.cpp — 안내 화면 · 발열 알림 · 조작 띠.
// 글자는 미리 구운 알파 마스크(ui_assets.dat — tools/gen_ui_assets.py, Pretendard Bold)에 색을 입혀 버퍼에 섞는다.
// 고정 문구는 문구 하나가 그림 하나이고, 바뀌는 글(밝기 · 온도 · 진단 숫자)은 영문·숫자 낱자를 이어 붙인다.
#include "ui.h"

#include <M5Unified.h>
#include "esp_log.h"

#include "ui_assets.h"

extern const uint8_t ui_dat_start[] asm("_binary_ui_assets_dat_start");
extern const uint8_t ui_dat_end[]   asm("_binary_ui_assets_dat_end");

namespace ui {

static const char *TAG = "ui";

// 색(RGB888) — 어두운 바탕에 차분한 글자
constexpr uint32_t C_BG = 0x161615u, C_SURFACE = 0x1D1D1Bu, C_LINE = 0x33332Fu, C_INK = 0xF2F1EEu, C_MUTED = 0x9A9893u,
                   C_FAINT = 0x55544Fu, C_WARN = 0xF0A7A5u;

static lgfx::LGFX_Sprite s_sp[3];                   // 도형(채우기·둥근 사각형)은 M5GFX 그리개로
static int s_n = 0;
static bool s_ready = false;

// 그릴 수 있는 조건 둘을 한 번 확인해 둔다(video::init 과 무관 — 고장 안내도 이것만 본다):
//   ① 디코더·PPA 가 쓰는 RGB565 가 CPU 의 바이트 순서 그대로인가(nonswapped). 작은 버퍼에 한 점을 찍어 본다(스냅부스와 같은 방식).
//      아니면 그리지 않는다 — 색이 뒤바뀐 글자를 내느니 안 내는 쪽이 낫고, 이 기기에서는 일어난 적이 없다.
//   ② 구운 글자 자료의 크기가 헤더가 아는 크기와 같은가(헤더만 새로 굽고 자료는 옛것인 빌드를 막는다).
static bool assets_usable()
{
    static int cached = -1;
    if (cached >= 0) return cached == 1;
    static uint16_t probe[2];
    lgfx::LGFX_Sprite t;
    t.setColorDepth(lgfx::color_depth_t::rgb565_nonswapped); t.setBuffer(probe, 2, 1, 0);
    t.fillRect(0, 0, 1, 1, (uint32_t)0xFF0000u);
    const bool order_ok = probe[0] == 0xF800;
    const bool size_ok = (size_t)(ui_dat_end - ui_dat_start) == uia::DATA_BYTES;
    if (!order_ok) ESP_LOGE(TAG, "unexpected RGB565 byte order (raw 0x%04X) -> no text", probe[0]);
    if (!size_ok) ESP_LOGE(TAG, "ui_assets.dat is %u B but ui_assets.h expects %u B -> no text (regenerate both)",
                           (unsigned)(ui_dat_end - ui_dat_start), (unsigned)uia::DATA_BYTES);
    cached = order_ok && size_ok ? 1 : 0;
    return cached == 1;
}

void init()
{
    if (!assets_usable()) return;
    s_n = video::panel_count(); if (s_n > 3) s_n = 3;
    for (int i = 0; i < s_n; ++i) {
        s_sp[i].setColorDepth(lgfx::color_depth_t::rgb565_nonswapped);
        s_sp[i].setBuffer(video::panel(i), PANEL_W, PANEL_H, 0);
    }
    s_ready = s_n >= 2;
    if (!s_ready) ESP_LOGE(TAG, "ui unavailable (buffers=%d)", s_n);
}

// 패널은 세로(720×1280)다. 한 번 그리는 동안 쓰는 방향은 여기 담긴 값 하나다
struct Canvas { lgfx::LGFX_Sprite &sp; int rot, w, h; };            // rot = M5GFX 회전 번호
static Canvas canvas_on(lgfx::LGFX_Sprite &sp, int orient)
{
    const int rot = m5_rotation(orient);
    sp.setRotation(rot);
    return Canvas{sp, rot, width(orient), height(orient)};
}
static Canvas canvas(int fb_idx, int orient) { return canvas_on(s_sp[fb_idx >= 0 && fb_idx < s_n ? fb_idx : 0], orient); }

// 알파 마스크에 색을 입혀 화면 좌표 (x, y)에 섞는다 — 패널 버퍼에 직접 쓴다.
//   M5GFX 의 pushAlphaImage 는 이 버퍼 꼴(회전 + rgb565_nonswapped)에서 자리를 절반으로 잘못 계산한다(2026-10-12 화면 찍기로 확인) → 쓰지 않는다.
//   화면 (x, y) 가 놓이는 패널 자리(px, py):
//     회전 3(가로)       px = y,        py = 1279 - x   ← PPA 90° 와 같은 식(통째 PPA 결과와 픽셀까지 같음을 bench 로 확인)
//     회전 1(가로 뒤집힘)  px = 719 - y,  py = x          ← PPA 270°
//     회전 0(세로)       px = x,        py = y
//     회전 2(세로 뒤집힘)  px = 719 - x,  py = 1279 - y
static void blit(const Canvas &cv, const uia::Asset &a, int x, int y, uint32_t rgb)
{
    uint16_t *fb = (uint16_t *)cv.sp.getBuffer();
    const uint8_t *src = ui_dat_start + a.off;
    const uint32_t fr = (rgb >> 19) & 31, fg = (rgb >> 10) & 63, fb5 = (rgb >> 3) & 31;
    const uint16_t solid = (uint16_t)((fr << 11) | (fg << 5) | fb5);
    for (int r = 0; r < a.h; ++r, src += a.w) {
        const int yy = y + r;
        if (yy < 0 || yy >= cv.h) continue;
        for (int c = 0; c < a.w; ++c) {
            const uint32_t al = src[c];
            const int xx = x + c;
            if (!al || xx < 0 || xx >= cv.w) continue;
            int px, py;
            switch (cv.rot) {
            case 3:  px = yy;               py = PANEL_H - 1 - xx; break;
            case 1:  px = PANEL_W - 1 - yy; py = xx;               break;
            case 0:  px = xx;               py = yy;               break;
            default: px = PANEL_W - 1 - xx; py = PANEL_H - 1 - yy; break;
            }
            uint16_t *p = fb + (size_t)py * PANEL_W + px;
            if (al == 255) { *p = solid; continue; }
            const uint32_t d = *p, ia = 255 - al;
            const uint32_t R = (fr * al + ((d >> 11) & 31) * ia + 127) / 255, G = (fg * al + ((d >> 5) & 63) * ia + 127) / 255, B = (fb5 * al + (d & 31) * ia + 127) / 255;
            *p = (uint16_t)((R << 11) | (G << 5) | B);
        }
    }
}
static void blit_center(const Canvas &cv, const uia::Asset &a, int cx, int cy, uint32_t rgb) { blit(cv, a, cx - a.w / 2, cy - a.h / 2, rgb); }
static const uia::Asset &glyph(char c)
{
    if (c < uia::GLYPH_FIRST || c > uia::GLYPH_LAST) c = '?';
    return uia::GLYPH[c - uia::GLYPH_FIRST];
}
static int text_w(const char *s) { int w = 0; for (; *s; ++s) w += glyph(*s).w; return w; }
// 영문·숫자 한 줄. (x, cy) = 왼쪽 끝과 세로 가운데. 그린 뒤의 x 를 돌려준다
static int text(const Canvas &cv, const char *s, int x, int cy, uint32_t rgb)
{
    for (; *s; ++s) { const uia::Asset &g = glyph(*s); if (*s != ' ') blit(cv, g, x, cy - g.h / 2, rgb); x += g.w; }
    return x;
}
static int label(const Canvas &cv, const uia::Asset &a, int x, int cy, uint32_t rgb) { blit(cv, a, x, cy - a.h / 2, rgb); return x + a.w; }

// 가운데 판 하나에 제목 · 설명 한두 줄 · 작은 글
static void paint_card(const Canvas &cv, const uia::Asset &head, const uia::Asset *sub, const uia::Asset *sub2, uint32_t head_rgb, const char *detail)
{
    cv.sp.fillRect(0, 0, cv.w, cv.h, C_BG);
    // 가로에서는 940, 세로에서는 화면 폭에 맞춘다(문구는 672px 를 넘지 않게 굽는다 — gen_ui_assets.py 의 MAX_LABEL_W)
    const int w = cv.w > 1000 ? 940 : cv.w - 24, h = 320, x = (cv.w - w) / 2, y = (cv.h - h) / 2 - 20, cx = cv.w / 2;
    cv.sp.fillSmoothRoundRect(x, y, w, h, 28, C_SURFACE);
    blit_center(cv, head, cx, y + 104, head_rgb);
    if (sub)  blit_center(cv, *sub,  cx, y + 176, C_MUTED);
    if (sub2) blit_center(cv, *sub2, cx, y + 218, C_MUTED);
    if (detail && detail[0]) text(cv, detail, cx - text_w(detail) / 2, y + 268, C_FAINT);
    blit_center(cv, uia::F_NAME, cx, cv.h - 40, C_FAINT);
}

void draw_status(int fb_idx, int orient, Screen scr, const char *detail)
{
    if (!s_ready) return;
    const uia::Asset *head = &uia::H_BOARD_WAIT, *sub = &uia::S_BOARD_WAIT; uint32_t hc = C_INK;
    switch (scr) {
    case Screen::TOO_HOT:    head = &uia::H_TOO_HOT;   sub = &uia::S_TOO_HOT; hc = C_WARN; break;
    case Screen::BOARD_WAIT: break;
    case Screen::BOARD_LOST: head = &uia::H_BOARD_LOST; sub = &uia::S_BOARD_LOST; hc = C_WARN; break;
    }
    paint_card(canvas(fb_idx, orient), *head, sub, nullptr, hc, detail);
}

void draw_fault(void *primary_fb, int orient, Fault f)
{
    if (!primary_fb || !assets_usable()) return;
    static lgfx::LGFX_Sprite sp;                                     // 0번 패널 버퍼에 묶은 그리개(video::init 이 실패했어도 쓸 수 있다)
    sp.setColorDepth(lgfx::color_depth_t::rgb565_nonswapped);
    sp.setBuffer(primary_fb, PANEL_W, PANEL_H, 0);
    paint_card(canvas_on(sp, orient), f == Fault::INIT ? uia::H_FAULT_INIT : uia::H_FAULT, &uia::S_FAULT, nullptr, C_WARN, "");
}

// 조작 띠: 아래쪽에 단추 다섯 개. 가로에서는 233×92, 세로에서는 132×92 — 손가락으로 누를 만한 크기
constexpr int BAR_H = 92, BAR_INFO_H = 64;
struct Bar { int margin, gap, bw, y, top; int x(int i) const { return margin + i * (bw + gap); } };
static Bar bar(int w, int h)
{
    Bar b; b.margin = w > 1000 ? 24 : 12; b.gap = w > 1000 ? 16 : 8;
    b.bw = (w - 2 * b.margin - 4 * b.gap) / 5; b.y = h - b.margin - BAR_H; b.top = b.y - BAR_INFO_H;
    return b;
}
static const Btn BAR_BTN[5] = {Btn::DARKER, Btn::BRIGHTER, Btn::ROTATE, Btn::MONO, Btn::CLOSE};

Btn hit(int orient, int x, int y)
{
    const Bar b = bar(width(orient), height(orient));
    if (y < b.y - 8 || y >= b.y + BAR_H + 8) return Btn::NONE;
    for (int i = 0; i < 5; ++i) if (x >= b.x(i) - b.gap / 2 && x < b.x(i) + b.bw + b.gap / 2) return BAR_BTN[i];
    return Btn::NONE;
}

bool draw_overlay(int fb_idx, const video::Overlay &ov)
{
    if (!s_ready || !ov.any()) return false;
    const Canvas cv = canvas(fb_idx, ov.orient);
    if (ov.controls) {
        const Bar b = bar(cv.w, cv.h);
        cv.sp.fillRect(0, b.top, cv.w, cv.h - b.top, C_BG);          // 글자가 그림에 묻히지 않게 아래쪽을 덮는다
        cv.sp.drawFastHLine(0, b.top, cv.w, C_LINE);
        // 한 줄 요약: 밝기 78%   화면 컬러   온도 45도
        char num[24]; const int cy = b.top + 32, sep = cv.w > 1000 ? 36 : 18; int x = b.margin + 6;
        x = label(cv, uia::L_BRI, x, cy, C_MUTED);
        snprintf(num, sizeof num, " %d%%", ov.bri_pct); x = text(cv, num, x, cy, C_INK) + sep;
        x = label(cv, uia::L_SCREEN, x, cy, C_MUTED);
        x = text(cv, " ", x, cy, C_INK); x = label(cv, ov.mono ? uia::L_MONO : uia::L_COLOR, x, cy, C_INK) + sep;
        if (ov.temp_c > -100) {
            x = label(cv, uia::L_TEMP, x, cy, C_MUTED);
            snprintf(num, sizeof num, " %d", ov.temp_c); x = text(cv, num, x, cy, C_INK);
            label(cv, uia::L_DEG, x, cy, C_INK);
        }
        // 넷째 단추는 "누르면 바뀔 쪽"을 적는다(지금 흑백이면 '컬러')
        const uia::Asset *const lbl[5] = {&uia::B_DARKER, &uia::B_BRIGHTER, &uia::B_ROTATE, ov.mono ? &uia::B_COLOR : &uia::B_MONO, &uia::B_CLOSE};
        for (int i = 0; i < 5; ++i) {
            cv.sp.fillSmoothRoundRect(b.x(i), b.y, b.bw, BAR_H, 18, C_SURFACE);
            blit_center(cv, *lbl[i], b.x(i) + b.bw / 2, b.y + BAR_H / 2, C_INK);
        }
    }
    const int top = 20;                                              // 왼쪽 위: 발열 알림
    if (ov.heat_note) {
        const int w = uia::N_HEAT.w + 44, h = 52, x = 20;
        cv.sp.fillSmoothRoundRect(x, top, w, h, 16, C_SURFACE);
        label(cv, uia::N_HEAT, x + 22, top + h / 2, C_WARN);
    }
    if (ov.stats[0] && !ov.controls) {                               // 조작 띠가 떠 있으면 자리가 겹친다
        const int w = text_w(ov.stats) + 24, h = uia::GLYPH_H + 8, x = 8, y = cv.h - h - 8;
        cv.sp.fillRect(x, y, w, h, (uint32_t)0x000000u);
        text(cv, ov.stats, x + 12, y + h / 2, C_INK);
    }
    return true;
}

}  // namespace ui
