// video.cpp — 풀기(하드웨어 JPEG) → 90° 돌리기(PPA) → 패널 버퍼 → 주사에 맞춰 교체.
//
// 들여온 것과 참고한 것:
//   - 풀기: Hiroki-Kawakami/Tab5-UVC-Display 의 부품 jpeg_decode_enhanced(MIT, components/ 에 복사 — 고친 곳은 그 폴더의 PATCHES.md)의 통째 풀기.
//   - 버퍼를 주사에 맞춰 교체: 같은 프로젝트의 3장 돌려쓰기 방식을 M5GFX 가 켠 패널에 얹은 것.
//   - 화면 주사에 메모리 버스 우선권: Hiroki-Kawakami/Tab5-Screen-Streamer bsp_tab5.c(MIT)의 값.
//   - 참고한 프로젝트: nnn112358/Tab5_UVC_Camera(Tab5 에서 하드웨어 JPEG 와 PPA 로 화면을 그리는 길).
//
// 패널 버퍼 규칙(MN-D10 — 찢어짐 없이):
//   버퍼는 2장 또는 3장이다(M5GFX 원본은 2장, tools/patch_m5gfx_fbs.py 를 거치면 3장). 버퍼마다 상태는 셋 중 하나:
//     주사 중(s_scan) · 다음 주사부터 쓰기로 청해 둠(s_req) · 비어 있음.  "비어 있는" 버퍼에만 그린다.
//   교체를 청하면(flip_request) 한 장의 주사가 끝나는 순간 드라이버가 그 버퍼로 넘어가고, 같은 인터럽트에서 s_scan 을 맞춘다.
//   3장이면 빈 버퍼가 늘 하나 있어 기다리지 않는다. 2장이면 청한 교체가 반영될 때까지(최대 한 주기) 기다린다.
//   그리는 사람은 한 번에 하나다(s_tok): 파이프라인 태스크가 직접 그리거나(안내 화면), PPA 비동기 돌리기 + 교체 태스크가 한 조로 그린다.
//   토큰을 제때 못 잡으면 그 프레임을 포기한다 — 잡지 못한 채 그리지 않는다(검토 1차 I-4).
//
// 캐시 규칙(PSRAM 버퍼를 CPU 와 DMA 가 번갈아 쓴다):
//   - 디코더·PPA(DMA)가 쓴 범위는 드라이버가 캐시를 버린다(M2C) → 그 뒤 CPU 가 읽어도 새 값.
//   - CPU 가 패널 버퍼에 쓴 것(안내 화면·덧그림)은 교체를 청할 때 캐시를 PSRAM 으로 내보낸다(C2M) — 주사는 PSRAM 을 읽는다.
#include "video.h"
#include "mon.h"
#include "recovery.h"

#include <M5Unified.h>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#include "esp_attr.h"
#include "esp_cache.h"
#include "esp_heap_caps.h"
#include "esp_ipc.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "driver/jpeg_decode.h"
#include "driver/jpeg_encode.h"
#include "driver/ppa.h"
#include "hal/axi_icm_ll.h"
#include "lgfx/v1/platforms/esp32p4/Panel_DSI.hpp"

#include "jpeg_decode_enhanced.h"
#include "jpeg_header_check.h"

namespace video {

static const char *TAG = "video";

constexpr size_t FB_BYTES   = (size_t)PANEL_W * PANEL_H * 2;
constexpr size_t ENC_BYTES  = 1024 * 1024;
constexpr int    TOKEN_WAIT_MS = 300;                // 앞의 그리기가 끝나기를 기다리는 한도(한 장 55ms 남짓의 몇 배)
constexpr int    BENCH_TOKEN_WAIT_MS = 2000;

// M5GFX 는 esp_lcd 패널 핸들을 protected 로 감춘다. 멤버 포인터로 읽는다(파생 타입으로의 캐스트가 아니라서 정의된 동작).
//   M5GFX 판을 올릴 때 이 멤버 이름이 바뀌면 여기서 컴파일이 깨진다 — 조용히 틀리지 않는다.
struct PanelPeek : public lgfx::Panel_DSI {
    static esp_lcd_panel_handle_t handle(lgfx::Panel_DSI *p) { return p->*(&PanelPeek::_disp_panel_handle); }
};
static lgfx::Panel_DSI *panel_dsi()
{
    if (!(M5.getBoard() == m5::board_t::board_M5Tab5 || M5.getBoard() == m5::board_t::board_M5Tab5X)) return nullptr;
    return static_cast<lgfx::Panel_DSI *>(M5.Display.getPanel());
}

static jpeg_enh_strip_decoder_handle_t s_wdec = nullptr;   // 통째로 풀기(부품의 띠 고리 없이)
static jpeg_encoder_handle_t s_enc = nullptr;
static uint8_t *s_enc_out = nullptr; static size_t s_enc_cap = 0;
static uint8_t *s_land[2] = {nullptr, nullptr}; static size_t s_land_size = 0;   // 통째로 풀 때의 가로 버퍼(PSRAM)
static int      s_cur = 0;                          // 다음에 풀어 넣을 가로 버퍼

static esp_lcd_panel_handle_t s_panel = nullptr;
static uint8_t *s_fb[3] = {nullptr, nullptr, nullptr};
static int      s_nfb = 0;                          // 패널 버퍼 장수(2 또는 3)
static portMUX_TYPE s_fb_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile int s_scan = 0;                     // 지금 주사 중인 버퍼                              ┐ s_fb_mux 아래에서만 바꾼다
static volatile int s_req = 0;                      // 다음 주사부터 쓰기로 청한 버퍼(없으면 s_scan 과 같다) ┘
static int      s_draw = 0;                         // 지금 그리는 버퍼(s_tok 을 쥔 쪽만 본다)
// 버퍼마다 "지금 무엇이 덮고 있나"(s_tok 을 쥔 쪽만 본다): 0 = 모른다(안내 화면 · 영상 밖에 그린 덧그림), COVER_FULL = 영상이 전체를 덮었다,
//   그 밖 = 그 크기의 영상이 가운데에 있고 테두리는 검다. 화면을 다 덮지 않는 영상(720p 가 아닌 것)을 그리기 전에, 그 버퍼가 같은 꼴이
//   아니면 통째로 검게 지운다 → 안내 화면의 바탕색이 테두리에 남지 않는다(검토 1차 M-4). 주사 중인 버퍼는 건드리지 않는다.
constexpr uint32_t COVER_FULL = 0xFFFFFFFFu;
static uint32_t s_fb_cover[3] = {0, 0, 0};
static SemaphoreHandle_t s_tok = nullptr;           // 그리는 사람은 하나
static SemaphoreHandle_t s_boundary = nullptr;      // 교체가 주사에 반영될 때마다 한 번
// 시각은 32비트 마이크로초로 든다(71분마다 한 바퀴 — 빼기는 한 바퀴 안에서 맞다). 인터럽트와 태스크가 함께 읽고 쓰는데
//   이 칩(RV32)에서 64비트 값은 한 번에 읽고 쓸 수 없다(검토 1차 M-7).
static volatile uint32_t s_flip_t0_us = 0, s_flip_us = 0;
static volatile uint32_t s_refresh_count = 0;       // 주사 완료 인터럽트 누적
static volatile int      s_isr_core = -1;           // 그 인터럽트가 도는 코어

static ppa_client_handle_t s_ppa = nullptr;         // 비동기(완료 콜백) — 화면용
static ppa_client_handle_t s_ppa_blk = nullptr;     // 블로킹 — bench
static std::atomic<uint32_t> s_hdr_rejects{0};      // 머리 검사에서 거른 프레임 수(부팅 뒤 누적)
static std::atomic<int>  s_hdr_err{0};              // 마지막으로 거른 까닭(jpeg_hdr_err_t)
static std::atomic<bool> s_frozen{false};           // 고장 안내가 화면을 차지했다 — 더는 그리지 않는다(freeze)
static std::atomic<uint32_t> s_token_timeouts{0};

static std::atomic<bool> s_full_range{true}, s_mono{false};
static volatile uint32_t s_t0_us = 0, s_render_us = 0, s_decode_us = 0;
static const char *volatile s_last_path = "-";
static TaskHandle_t      s_flipper = nullptr;
static overlay_fn_t      s_overlay_fn = nullptr;
static Overlay           s_ov;                      // PPA 돌리기가 끝난 뒤 교체 태스크가 그릴 것

static inline uint32_t now_us32() { return (uint32_t)esp_timer_get_time(); }

// ── 교체 ─────────────────────────────────────────────────────────────────────
// rev 3 미만 칩에는 진짜 vsync 인터럽트가 없다 — 이 콜백은 "한 장의 주사(DMA)가 끝나 다음 장을 걸었다"는 뜻이다(esp_lcd_panel_dpi.c).
//   드라이버는 이 콜백을 부르기 직전에, 그때의 "청한 버퍼"로 다음 주사를 건다 → 여기서 s_scan 을 s_req 로 맞추면 실제와 같다.
//   (같으려면 드라이버가 번호를 읽는 것과 이 콜백 사이에 번호가 바뀌지 않아야 한다 — flip_request 가 그것을 지킨다.)
static bool IRAM_ATTR on_refresh_done(esp_lcd_panel_handle_t, esp_lcd_dpi_panel_event_data_t *, void *)
{
    BaseType_t hp = pdFALSE;
    portENTER_CRITICAL_ISR(&s_fb_mux);
    const bool changed = s_scan != s_req;
    if (changed) { s_scan = s_req; s_flip_us = now_us32() - s_flip_t0_us; }
    portEXIT_CRITICAL_ISR(&s_fb_mux);
    s_refresh_count = s_refresh_count + 1;
    s_isr_core = (int)xPortGetCoreID();
    if (changed) xSemaphoreGiveFromISR(s_boundary, &hp);
    return hp == pdTRUE;
}
// MN-D16 "다음 주사부터 이 버퍼"를 드라이버에 알리는 일은 주사 완료 인터럽트와 같은 코어에서, 인터럽트를 막고 한다.
//   왜(검토 1차 I-8 — IDF esp_lcd_panel_dpi.c 를 읽어 확인): 그 인터럽트는 드라이버의 버퍼 번호를 잠금 없이 읽어 다음 주사를
//   건 다음에 on_refresh_done 을 부른다. 다른 코어에서 번호를 바꾸면 "읽기"와 "콜백" 사이에 끼어들 수 있고, 그러면 콜백은
//   새 번호가 주사 중이라고 적는데 실제로는 옛 버퍼가 한 주기 더 주사된다 → 그 옛 버퍼를 빈 것으로 알고 그리면 한 장이 찢어진다.
//   같은 코어에서 인터럽트를 막고 바꾸면 그 사이에 낄 수 없다: 인터럽트가 통째로 앞서거나 통째로 뒤선다.
//   비용: 교체마다 코어 간 호출 한 번(수십 μs). 버퍼는 그대로 3장을 다 쓴다.
//   버린 대안: 직전에 주사되던 버퍼를 한 경계 더 "사용 중"으로 두기(검토 제안) — 틈은 막히지만 3장이 사실상 2장이 되어
//   50fps 가 38fps 쯤으로 떨어진다(한 장 18.6ms, 주사 17.45ms 로 따져 본 값 — 계획서 MN-D16).
//   탈출구: 아래 esp_ipc_call_blocking 을 flip_on_isr_core(&r) 직접 호출로 바꾸면 예전 동작이다.
struct FlipReq { int idx; esp_err_t err; };
static void flip_on_isr_core(void *arg)
{
    auto *r = (FlipReq *)arg;
    portENTER_CRITICAL(&s_fb_mux);
    // 드라이버 자신의 버퍼 주소를 주면 복사하지 않고 "다음 주사부터 이 버퍼"로만 바꾼다(줄 범위는 캐시를 내보낼 범위 — 부른 쪽이 했으니 한 줄만)
    r->err = esp_lcd_panel_draw_bitmap(s_panel, 0, 0, PANEL_W, 1, s_fb[r->idx]);
    if (r->err == ESP_OK) s_req = r->idx;
    portEXIT_CRITICAL(&s_fb_mux);
}
// 버퍼 idx 를 화면으로(다음 주사부터). cpu_drew = CPU 가 그 버퍼에 쓴 것이 있다 → 캐시를 PSRAM 으로 내보낸다(주사는 PSRAM 을 읽는다).
static void flip_request(int idx, bool cpu_drew)
{
    if (cpu_drew) esp_cache_msync(s_fb[idx], FB_BYTES, ESP_CACHE_MSYNC_FLAG_DIR_C2M);
    s_flip_t0_us = now_us32();
    FlipReq r = {idx, ESP_FAIL};
    const int core = s_isr_core;                    // 아직 한 번도 안 왔으면 패널을 만든 코어(app_main = 0)
    if (esp_ipc_call_blocking(core < 0 ? 0 : (uint32_t)core, flip_on_isr_core, &r) != ESP_OK) r.err = ESP_ERR_INVALID_STATE;
    if (r.err != ESP_OK) ESP_LOGW(TAG, "flip failed: %s", esp_err_to_name(r.err));
}
// 주사 중도 아니고 청해 두지도 않은 버퍼를 고른다. 2장뿐이고 교체가 아직 반영되지 않았으면 한 주기 기다린다.
//   -1 = 주사 완료가 오지 않는다(패널이 멈췄다). 주사 중일 수 있는 버퍼에는 그리지 않는다 — 멈춤 감시(PANEL_STUCK)가 재시작한다.
static int acquire_back()
{
    for (int tries = 0; tries < 8; ++tries) {
        portENTER_CRITICAL(&s_fb_mux);
        const int scan = s_scan, req = s_req;
        portEXIT_CRITICAL(&s_fb_mux);
        for (int i = 0; i < s_nfb; ++i) if (i != scan && i != req) return i;
        xSemaphoreTake(s_boundary, pdMS_TO_TICKS(40));
    }
    return -1;
}
// 그리기 토큰. false = 앞의 그리기가 끝나지 않았다 → 부른 쪽은 그 일을 포기한다(토큰을 돌려주지도 않는다).
//   PPA 가 끝나지 않는 경우가 IDF 5.5.4 에 있다(esp-idf #19096) — 그때 토큰은 영영 돌아오지 않고, 프레임이 줄곧 포기되는 것을
//   멈춤 감시가 "진행 없음"으로 잡아 재시작한다.
static bool take_token(int wait_ms)
{
    if (xSemaphoreTake(s_tok, pdMS_TO_TICKS(wait_ms)) == pdTRUE) return true;
    static std::atomic<uint32_t> next_log_s{0};                     // 여러 태스크가 부른다(파이프라인 · 시리얼의 shot·fault tok) → 한 번에 읽고 쓰는 32비트로
    const uint32_t n = s_token_timeouts.fetch_add(1) + 1, now_s = (uint32_t)(now_ms() / 1000);
    if (now_s >= next_log_s.load()) { next_log_s.store(now_s + 2); ESP_LOGW(TAG, "draw token timeout (%u so far) - frame dropped", (unsigned)n); }
    return false;
}
static bool draw_overlay(int fb_idx, const Overlay &ov)
{
    return s_overlay_fn && ov.any() ? s_overlay_fn(fb_idx, ov) : false;
}
// PPA 돌리기가 끝나면 깨어나 덧그림을 얹고 교체를 청한 뒤 토큰을 돌려준다.
static void flipper_task(void *)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        const bool drew = draw_overlay(s_draw, s_ov);
        if (drew && s_fb_cover[s_draw] != COVER_FULL) s_fb_cover[s_draw] = 0;   // 덧그림이 테두리에 걸쳤을 수 있다
        flip_request(s_draw, drew);
        xSemaphoreGive(s_tok);
    }
}
static bool IRAM_ATTR ppa_done_cb(ppa_client_handle_t, ppa_event_data_t *, void *)
{
    BaseType_t hp = pdFALSE;
    s_render_us = now_us32() - s_t0_us;
    vTaskNotifyGiveFromISR(s_flipper, &hp);
    return hp == pdTRUE;
}

// ── 색 설정을 디코더에 ─────────────────────────────────────────────────────────
// 풀기를 부르는 바로 그 태스크(파이프라인)가 풀기 직전에 부른다 → 부품의 "process 도중 호출 금지"(J3·J4)가 구조로 지켜진다.
static void sync_color()
{
    const bool fr = s_full_range.load(), mono = s_mono.load();
    if (s_wdec) { jpeg_enh_strip_decoder_set_full_range(s_wdec, fr); jpeg_enh_strip_decoder_set_mono(s_wdec, mono); }
}

// ── PPA(통째로 푼 가로 그림을 돌린다) ─────────────────────────────────────────
static ppa_srm_rotation_angle_t angle_of(bool flip) { return flip ? PPA_SRM_ROTATION_ANGLE_270 : PPA_SRM_ROTATION_ANGLE_90; }
// 화면에 꽉 차게 맞추는 배율(PPA 는 1/16 단위)
static float fit_scale(int w, int h)
{
    if (w == SCREEN_W && h == SCREEN_H) return 1.0f;
    float s = (float)SCREEN_W / w, sy = (float)SCREEN_H / h;
    if (sy < s) s = sy;
    int q = (int)(s * 16.0f); if (q < 1) q = 1;
    return q / 16.0f;
}
// 돌린 뒤의 폭·높이(화면을 다 덮으면 720×1280)
static void fit_size(const Frame &f, int *ow, int *oh)
{
    const float s = fit_scale(f.w, f.h);
    *ow = (int)(f.h * s); *oh = (int)(f.w * s);
}
// 가로 그림 전체를 dst(720×1280) 가운데에. 여백은 4의 배수로 내린다 — IDF 5.5.4 의 PPA 는 90°·270° RGB565 회전에서 출력의 x 위치를
//   4로 나눈 나머지가 2 이고 그 버퍼가 화면에 나가는 중이면 끝나지 않는다(esp-idf #19096 — 2026-10-12 직접 열어 확인: 열려 있고,
//   master 에서는 고쳐졌으며 5.5 로의 반영은 예정).
static esp_err_t ppa_rotate(ppa_client_handle_t cl, const Frame &f, uint8_t *dst, ppa_srm_rotation_angle_t ang, ppa_trans_mode_t mode)
{
    const float s = fit_scale(f.w, f.h);
    int ow, oh; fit_size(f, &ow, &oh);
    ppa_srm_oper_config_t op = {};
    op.in.buffer = s_land[f.idx]; op.in.pic_w = f.stride; op.in.pic_h = f.h;
    op.in.block_w = f.w; op.in.block_h = f.h;
    op.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
    op.out.buffer = dst; op.out.buffer_size = FB_BYTES; op.out.pic_w = PANEL_W; op.out.pic_h = PANEL_H;
    op.out.block_offset_x = ((PANEL_W - ow) / 2) & ~3; op.out.block_offset_y = ((PANEL_H - oh) / 2) & ~3;
    op.out.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
    op.rotation_angle = ang;
    op.scale_x = s; op.scale_y = s;
    op.alpha_update_mode = PPA_ALPHA_NO_CHANGE;
    op.mode = mode;
    return ppa_do_scale_rotate_mirror(cl, &op);
}

// ── 통째로 풀기 ──────────────────────────────────────────────────────────────
// 크기 검사(가로 1920 · 세로 1088 이하, 버퍼에 들어가는가)는 부품이 한다. 버퍼 전체 크기를 넘겨도 캐시는 쓴 만큼만 버린다.
static bool decode(const uint8_t *jpg, size_t len, int idx, Frame &out)
{
    jpeg_enh_frame_info_t fi = {};
    if (jpeg_enh_decoder_process(s_wdec, jpg, (uint32_t)len, s_land[idx & 1], s_land_size, &fi) != ESP_OK) return false;
    if (fi.origin_w == 0 || fi.origin_h == 0) return false;
    out = Frame(); out.w = (int)fi.origin_w; out.h = (int)fi.origin_h; out.idx = idx & 1; out.stride = (int)fi.pic_w;
    return true;
}

// ── 공개 ─────────────────────────────────────────────────────────────────────
void set_overlay_fn(overlay_fn_t fn) { s_overlay_fn = fn; }
int  panel_count() { return s_nfb; }
uint16_t *panel(int idx) { return idx >= 0 && idx < s_nfb ? (uint16_t *)s_fb[idx] : nullptr; }
int  begin()
{
    if (s_frozen.load()) return -1;
    if (!take_token(TOKEN_WAIT_MS)) return -1;
    const int b = acquire_back();
    if (b < 0) { xSemaphoreGive(s_tok); return -1; }
    s_draw = b; s_fb_cover[b] = 0;                                  // 부른 쪽이 무엇을 그릴지 모른다
    return b;
}
void end(bool show) { if (show) flip_request(s_draw, true); xSemaphoreGive(s_tok); }
uint32_t last_decode_us() { return s_decode_us; }
uint32_t last_render_us() { return s_render_us; }
uint32_t last_flip_us() { return s_flip_us; }
uint32_t refresh_count() { return s_refresh_count; }
int  isr_core() { return s_isr_core; }
uint32_t token_timeouts() { return s_token_timeouts.load(); }
uint32_t header_rejects() { return s_hdr_rejects.load(); }
const char *last_header_error() { return jpeg_hdr_err_name((jpeg_hdr_err_t)s_hdr_err.load()); }
const char *last_path() { return s_last_path; }
bool full_range() { return s_full_range.load(); }
void set_full_range(bool on) { s_full_range.store(on); }
bool mono() { return s_mono.load(); }
void set_mono(bool on) { s_mono.store(on); }
bool hold_token(int ms)
{
    if (!s_tok || !take_token(TOKEN_WAIT_MS)) return false;
    vTaskDelay(pdMS_TO_TICKS(ms));
    xSemaphoreGive(s_tok);
    return true;
}

bool show_jpeg(const uint8_t *jpg, size_t len, bool flip, const Overlay &ov, Frame &out)
{
    // 머리부터 엄격히 본다(부품 패치 J6 — 이유는 jpeg_header_check.h). IDF 의 jpeg_decoder_get_info 는 쓰지 않는다: SOF 가 없어도
    // "0×0, 성공"을 돌려주고, 프레임마다 내부 RAM 을 잡았다 놓으며, 표본 비를 못 알아보면 잡은 것을 놓지 않고 돌아간다.
    jpeg_hdr_info_t info;
    const jpeg_hdr_err_t he = jpeg_hdr_check(jpg, len > UINT32_MAX ? UINT32_MAX : (uint32_t)len, &info);
    if (he != JPEG_HDR_OK) {
        s_hdr_err.store((int)he);
        const uint32_t n = s_hdr_rejects.fetch_add(1) + 1;
        // "끝 표시 없음"은 처음 셋과 1000장마다 끝 16바이트를 남긴다 — `rejwhy=no-eoi` 만 보고 끝나지 않게(MN-D27)
        if (he == JPEG_HDR_NO_EOI && (n <= 3 || n % 1000 == 0)) {
            char hex[16 * 3 + 1] = ""; const size_t k = len < 16 ? len : 16;
            for (size_t i = 0; i < k; i++) snprintf(hex + i * 3, 4, "%02x ", jpg[len - k + i]);
            ESP_LOGW(TAG, "frame %u rejected (no-eoi): len=%u tail: %s", (unsigned)n, (unsigned)len, hex);
        }
        return false;
    }
    len = info.total_len;                                           // 끝 표시 뒤의 꼬리는 하드웨어에 주지 않는다(MN-D27)
    sync_color();
    const ppa_srm_rotation_angle_t ang = angle_of(flip);

    // 통째로 푼다: 앞 그림이 아직 돌아가는 동안 다른 가로 버퍼에 푼다(둘이 겹친다)
    Frame f;
    const uint32_t t0 = now_us32();
    if (!decode(jpg, len, s_cur, f)) return false;
    s_decode_us = now_us32() - t0;
    const int b = begin();                                          // 앞 프레임의 돌리기가 끝난 뒤(토큰은 교체 태스크가 돌려준다)
    if (b < 0) return false;
    int ow, oh; fit_size(f, &ow, &oh);
    const uint32_t cover = (ow >= PANEL_W && oh >= PANEL_H) ? COVER_FULL : ((uint32_t)f.w << 16 | (uint32_t)f.h);
    if (cover != COVER_FULL && s_fb_cover[b] != cover) {            // 테두리가 검지 않을 수 있다 → 지우고(캐시를 내보낸 뒤) 그린다. PPA 는 자기 사각형만 쓴다
        memset(s_fb[b], 0, FB_BYTES); esp_cache_msync(s_fb[b], FB_BYTES, ESP_CACHE_MSYNC_FLAG_DIR_C2M);
    }
    s_fb_cover[b] = cover;
    s_ov = ov;
    s_t0_us = now_us32();
    const esp_err_t e = ppa_rotate(s_ppa, f, s_fb[b], ang, PPA_TRANS_MODE_NON_BLOCKING);
    if (e != ESP_OK) { ESP_LOGW(TAG, "ppa failed: %s", esp_err_to_name(e)); end(false); return false; }
    s_cur ^= 1; s_last_path = "ppa";
    out = f;
    return true;
}

// ── 화면 찍기 ────────────────────────────────────────────────────────────────
static bool enc_ready()
{
    if (s_enc) return true;
    jpeg_encode_engine_cfg_t ecfg = {}; ecfg.intr_priority = 0; ecfg.timeout_ms = 2000;
    if (jpeg_new_encoder_engine(&ecfg, &s_enc) != ESP_OK) { ESP_LOGE(TAG, "jpeg encoder engine failed"); return false; }
    jpeg_encode_memory_alloc_cfg_t mcfg = {}; mcfg.buffer_direction = JPEG_ENC_ALLOC_OUTPUT_BUFFER;
    s_enc_out = (uint8_t *)jpeg_alloc_encoder_mem(ENC_BYTES, &mcfg, &s_enc_cap);
    if (!s_enc_out) { ESP_LOGE(TAG, "jpeg encoder out buf failed"); return false; }
    return true;
}
uint32_t shot(const uint8_t **out)
{
    if (!s_nfb || !enc_ready()) return 0;
    const bool frozen = s_frozen.load();                            // 얼려 둔 화면(고장 안내)은 토큰 없이 찍는다 — 그리는 쪽이 없다
    if (!frozen && !take_token(TOKEN_WAIT_MS)) return 0;            // 그리기가 끝난 뒤의 화면을 찍는다(찍는 동안 다른 그리기도 없다)
    jpeg_encode_cfg_t cfg = {};
    cfg.width = PANEL_W; cfg.height = PANEL_H;
    cfg.src_type = JPEG_ENCODE_IN_FORMAT_RGB565;
    cfg.sub_sample = JPEG_DOWN_SAMPLING_YUV420;
    cfg.image_quality = 90;
    uint32_t len = 0;
    const esp_err_t e = jpeg_encoder_process(s_enc, &cfg, s_fb[s_req], FB_BYTES, s_enc_out, s_enc_cap, &len);   // 가장 최근에 화면으로 넘긴 버퍼
    if (!frozen) xSemaphoreGive(s_tok);
    if (e != ESP_OK) { ESP_LOGE(TAG, "jpeg encode failed"); return 0; }
    *out = s_enc_out;
    return len;
}

// ── 시간 재기 ────────────────────────────────────────────────────────────────
template <class F> static uint32_t avg_us(int n, F &&fn)
{
    int64_t t = 0;
    for (int i = 0; i < n; ++i) { const int64_t a = esp_timer_get_time(); fn(); t += esp_timer_get_time() - a; vTaskDelay(pdMS_TO_TICKS(2)); }
    return (uint32_t)(t / n);
}

void bench(const uint8_t *jpg, size_t len)
{
    if (!take_token(BENCH_TOKEN_WAIT_MS)) { printf("BENCH FAIL busy (a frame is still being drawn)\n"); return; }   // 화면 쪽 그리기가 없는 상태에서. 결과는 임시 버퍼에만 쓴다
    sync_color();
    Frame f;
    uint8_t *tmp  = (uint8_t *)heap_caps_aligned_alloc(128, FB_BYTES, MALLOC_CAP_SPIRAM);
    if (!tmp || !decode(jpg, len, 0, f) || !(f.w == SCREEN_W && f.h == SCREEN_H && f.stride == SCREEN_W)) {
        printf("BENCH FAIL need a decodable 1280x720 frame and 2 MB of PSRAM\n");
        free(tmp); xSemaphoreGive(s_tok); return;
    }
    const int N = 8;
    const ppa_srm_rotation_angle_t ang = PPA_SRM_ROTATION_ANGLE_90;
    printf("BENCH BEGIN n=%d jpeg=%u range=%s mono=%d heap_int=%u\n", N, (unsigned)len, s_full_range.load() ? "full" : "limited",
           (int)s_mono.load(), (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    printf("BENCH decode_full us=%u\n", (unsigned)avg_us(N, [&] { Frame g; decode(jpg, len, 0, g); }));
    printf("BENCH ppa_rotate us=%u\n", (unsigned)avg_us(N, [&] { ppa_rotate(s_ppa_blk, f, tmp, ang, PPA_TRANS_MODE_BLOCKING); }));
    printf("BENCH END\n");
    free(tmp);
    xSemaphoreGive(s_tok);
}

// ── 고장 안내 ────────────────────────────────────────────────────────────────
// 화면을 얼린다: 지금 그려지고 있는 프레임이 끝나기를 wait_ms 까지 기다리고(토큰을 가져와 돌려주지 않는다), 그 뒤의 그리기는 모두 거절한다.
//   기다리지 않으면 고장 안내를 그리는 동안 그 프레임이 같은 버퍼에 겹쳐 쓴다(2026-10-13 화면 찍기에서 봤다: 안내의 바탕에 영상이 띠로 남았다).
//   false = 토큰을 못 가져왔다(그리던 쪽이 멈춘 채 쥐고 있다) — 그래도 그 뒤의 그리기는 막힌다.
bool freeze(int wait_ms)
{
    s_frozen.store(true);
    return s_tok && xSemaphoreTake(s_tok, pdMS_TO_TICKS(wait_ms)) == pdTRUE;
}
void *primary_buffer()
{
    auto *dsi = panel_dsi();
    return dsi ? dsi->config_detail().buffer : nullptr;
}
// M5GFX 는 0번 버퍼에 그리고 캐시를 내보내지 않는다 → 내보내고, 버퍼를 돌려쓰는 중이었다면 0번으로 돌아간다.
//   freeze() 뒤에 부른다.
void present_primary()
{
    void *buf = primary_buffer();
    if (!buf) return;
    esp_cache_msync(buf, FB_BYTES, ESP_CACHE_MSYNC_FLAG_DIR_C2M);
    if (s_panel && s_nfb && buf == s_fb[0]) flip_request(0, false);
}

bool init()
{
    // 통째 풀기: 부품을 띠 고리 없이(ring_count = 0). 1920×1088 까지(맥이 보내는 현황판은 1280×720)
    jpeg_enh_strip_decoder_cfg_t wc = {};
    wc.decode.output_format = JPEG_DECODE_OUT_FORMAT_RGB565;
    wc.decode.rgb_order = JPEG_DEC_RGB_ELEMENT_ORDER_BGR;
    wc.decode.conv_std = JPEG_YUV_RGB_CONV_STD_BT601;
    wc.decode.yuv_full_range = s_full_range.load();
    wc.max_pic_w = MAX_SRC_W; wc.max_pic_h = MAX_SRC_H; wc.timeout_ms = 500;
    wc.share_engine_with = nullptr;                                 // 이 디코더가 엔진을 만든다(부품 패치 J9 의 나눠 쓰기는 지운 띠 방식용이었다)
    if (jpeg_enh_strip_decoder_new(&wc, &s_wdec) != ESP_OK || !s_wdec) { ESP_LOGE(TAG, "jpeg decoder failed"); s_wdec = nullptr; return false; }
    jpeg_decode_memory_alloc_cfg_t mem_cfg = {}; mem_cfg.buffer_direction = JPEG_DEC_ALLOC_OUTPUT_BUFFER;
    for (int i = 0; i < 2; ++i) {
        s_land[i] = (uint8_t *)jpeg_alloc_decoder_mem((size_t)MAX_SRC_W * MAX_SRC_H * 2, &mem_cfg, &s_land_size);
        if (!s_land[i]) { ESP_LOGE(TAG, "landscape buffer %d alloc failed", i); return false; }
    }
    s_tok = xSemaphoreCreateBinary(); s_boundary = xSemaphoreCreateBinary();
    if (!s_tok || !s_boundary) return false;
    xSemaphoreGive(s_tok);

    ppa_client_config_t pc = {}; pc.oper_type = PPA_OPERATION_SRM; pc.max_pending_trans_num = 1; pc.data_burst_length = PPA_DATA_BURST_LENGTH_128;
    if (ppa_register_client(&pc, &s_ppa) != ESP_OK || ppa_register_client(&pc, &s_ppa_blk) != ESP_OK) { ESP_LOGE(TAG, "ppa client failed"); return false; }
    ppa_event_callbacks_t cbs = {}; cbs.on_trans_done = ppa_done_cb;
    if (ppa_client_register_event_callbacks(s_ppa, &cbs) != ESP_OK) { ESP_LOGE(TAG, "ppa callback failed"); return false; }

    // 패널: M5GFX 가 켠 esp_lcd DPI 패널의 버퍼를 받는다(3장이면 3장, 원본 M5GFX 면 2장)
    auto *dsi = panel_dsi();
    if (!dsi) { ESP_LOGE(TAG, "not a Tab5 (no DSI panel)"); return false; }
    s_panel = PanelPeek::handle(dsi);
    if (!s_panel) { ESP_LOGE(TAG, "no DSI panel handle"); return false; }
    void *fb[3] = {nullptr, nullptr, nullptr};
    esp_log_level_set("lcd.dsi.dpi", ESP_LOG_NONE);                 // 3장을 물어 2장뿐이면 드라이버가 오류 줄을 찍는다 — 정상 경로다
    if (esp_lcd_dpi_panel_get_frame_buffer(s_panel, 3, &fb[0], &fb[1], &fb[2]) == ESP_OK) s_nfb = 3;
    else if (esp_lcd_dpi_panel_get_frame_buffer(s_panel, 2, &fb[0], &fb[1]) == ESP_OK) s_nfb = 2;
    esp_log_level_set("lcd.dsi.dpi", ESP_LOG_WARN);
    if (s_nfb < 2 || fb[0] != dsi->config_detail().buffer) { ESP_LOGE(TAG, "DSI frame buffers unavailable (n=%d)", s_nfb); s_nfb = 0; return false; }
    for (int i = 0; i < s_nfb; ++i) {
        if (!fb[i] || ((uintptr_t)fb[i] & 127)) { ESP_LOGE(TAG, "frame buffer %d unusable: %p", i, fb[i]); s_nfb = 0; return false; }
        s_fb[i] = (uint8_t *)fb[i];
        if (i) { memset(s_fb[i], 0, FB_BYTES); esp_cache_msync(s_fb[i], FB_BYTES, ESP_CACHE_MSYNC_FLAG_DIR_C2M); }   // 0번은 M5GFX 가 이미 채웠다
    }
    esp_lcd_dpi_panel_event_callbacks_t pcb = {}; pcb.on_refresh_done = on_refresh_done;
    if (esp_lcd_dpi_panel_register_event_callbacks(s_panel, &pcb, nullptr) != ESP_OK) { ESP_LOGE(TAG, "refresh callback failed"); return false; }

    // 화면 주사(DSI, DW-GDMA)가 PSRAM 읽기에서 지면 한 장이 통째로 깨져 파랗게 보인다 → 주사의 읽기 우선순위를 가장 높게, 2D-DMA(JPEG·PPA)는 그 아래로.
    //   값은 Hiroki-Kawakami/Tab5-Screen-Streamer bsp_tab5.c 그대로(0~15, 기본은 모두 0).
    axi_icm_ll_set_dw_gdma_qos_arbiter_prio(0, 0x0, 0xF);
    axi_icm_ll_set_dw_gdma_qos_arbiter_prio(1, 0x0, 0xF);
    axi_icm_ll_set_dma2d_qos_arbiter_prio(0x4, 0x4);

    xTaskCreatePinnedToCore(flipper_task, "flip", 4096, nullptr, 6, &s_flipper, 1);
    if (!s_flipper) return false;
    ESP_LOGI(TAG, "panel buffers x%d, landscape %u B x2", s_nfb, (unsigned)s_land_size);
    return true;
}

}  // namespace video
