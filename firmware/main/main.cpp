/*
 * WideEye 탭5 펌웨어 — M5Stack Tab5 를 맥의 현황판(WideEye)을 보여 주는 5" 화면으로.
 *   맥(urgent-board tools/tab5_link.py) → USB-C 직렬로 1280×720 JPEG → 하드웨어로 풀어 → 90° 돌려 → 5" 720p 화면.
 *
 * 설계 · 결정: urgent-board docs/exec-plans/2026-10-15-tab5-board-mode.md(현황판 모드) · 2026-10-18-tab5-portrait-design.md(세로),
 *   이 펌웨어의 앞 판(HDMI 모니터 겸용 — 2026-11-02 에 HDMI 기능을 지웠다)은 앞 판 저장소의 태그 tab5_monitor-hdmi-last. 쓰는 법은 ../README.md.
 *
 * 결정(앞 판에서 이어 온 것):
 *   MN-D1  M5GFX 로 패널을 켜고 그 프레임버퍼를 직접 다룬다. 비용: M5GFX 의 감춘 멤버 이름에 기댄다(바뀌면 컴파일이 깨진다).
 *   MN-D2  Wi-Fi(ESP32-C6)는 켜지 않는다 — 그림은 USB-C 로 온다.
 *   MN-D3  NVS 를 지우지 않는다(이름 공간 "tab5mon" 만 쓴다) — 다른 펌웨어가 저장한 값(Wi-Fi 목록 등)이 남는다.
 *   MN-D12 멈추면 재시작한다 — 판단은 recovery.h, 실행은 감시 태스크. MN-D17 재시작은 까닭 묶음마다 NVS 에 횟수를 세어 상한을 둔다.
 *   MN-D18 칩 온도로 밝기를 낮추고, 아주 뜨거우면 그림을 멈추고 안내만 띄운다.
 *   MN-D21 JPEG 은 머리를 엄격히 검사한 것만 푼다(video.cpp · 부품 패치 J6).  MN-D31~41 현황판(받기 · 상태 · 잠듦 · 방향).
 *
 * 태스크(우선순위/코어 · 스택이 놓이는 곳) — 서로를 기다리지 않게 일을 나눴다:
 *   watchdog 20/0   TCM     1초마다 건강을 본다. 판단에는 아무 잠금도 잡지 않는다(다른 태스크가 멈춰도 돈다). 재시작을 실행할 때만 NVS 에 횟수를 쓴다
 *   flip 6/1                PPA 돌리기가 끝나면 덧그림·교체      pipeline 5/1      현황판 → 화면(그리는 것은 이 태스크뿐)
 *   panel 4/0               터치·밝기·방향·온도(M5.Display 와 내부 I2C 는 이 태스크만 만진다. 예외: 시리얼 pwr 와 USB-A 전원은 g_i2c_mu 아래에서)
 *   sercmd 3/0              시리얼 명령 · 현황판 그림 받기        session(app_main) 1/0   박동 · 횟수 지우기(board_session.cpp)
 *
 * 파일: 이 파일은 부팅(app_main) · panel 태스크 · 감시 태스크. 나머지는 settings.cpp(NVS·재시작 횟수) · board_session.cpp(USB-A 전원 · 세션) ·
 *   pipeline.cpp(현황판 → 화면) · sercmd.cpp(시리얼 명령 · 그림 받기) · video.cpp(풀기·돌리기·버퍼 교체) · ui.cpp(글자·안내 화면).
 *   파일들이 함께 쓰는 상태와 약속은 app.h, 복구·발열·현황판의 판단은 recovery.h(순수 로직 — 호스트 시험).
 */
#include "app.h"

#include <M5Unified.h>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "driver/temperature_sensor.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_system.h"
#include "freertos/idf_additions.h"

#include "video.h"

static const char *TAG = "monitor";

constexpr int HEAT_NOTE_MS        = 8000;        // 발열로 낮췄다는 알림이 떠 있는 시간
constexpr int OSD_MS              = 6000;        // 조작 띠가 떠 있는 시간(마지막으로 만진 뒤)
constexpr int TEMP_PERIOD_MS      = 2000;

static char                  s_boot_line[384] = "";                // 부팅 줄(MON BOOT 뒤의 본문) — 시리얼 `boot` 가 다시 찍는다
const char *boot_line() { return s_boot_line; }
static temperature_sensor_handle_t s_tsens = nullptr;

// 그림 없이 안내만 띄운 채 선다(시리얼은 듣는다). 돌아오지 않는다. 고장 안내는 video::init 과 무관하게 그려진다(M5GFX 가 켠 패널의 0번 버퍼에 직접).
[[noreturn]] void halt_with_notice(const char *what, ui::Fault fault, int orient)
{
    printf("MON FAIL %s\n", what); fflush(stdout);
    video::freeze(0);
    ui::draw_fault(video::primary_buffer(), orient, fault);
    video::present_primary();
    for (;;) vTaskDelay(pdMS_TO_TICKS(1000));
}
// 부팅 때 꼭 있어야 하는 것을 만들지 못했다 → 재시작해 본다(상한까지). 다 썼으면 화면에 알리고 선다. 돌아오지 않는다.
//   ESP_ERROR_CHECK·assert 로 죽게 두면 횟수 제한 없이 몇 초마다 재부팅하는 고리가 된다 — MN-D17 이 막으려는 바로 그것이다.
//   고장 안내는 video::init 이 실패했어도(또는 아직 안 불렸어도) 그려진다: M5GFX 가 켠 패널의 0번 버퍼에 직접 그린다.
[[noreturn]] static void init_failed(rec::Cause cause, const char *what, int orient)
{
    guarded_restart(cause);
    halt_with_notice(what, ui::Fault::INIT, orient);
}

// ── 재시작 직전: 그리기 쪽 태스크를 세운다 ────────────────────────────────────
// IDF 의 재시작(esp_restart_noos)은 다른 코어를 그 자리에서 리셋한 뒤 이 코어에서 ROM 의 Cache_WriteBack_All 을 부른다. 그 다른 코어가
// 마침 캐시 동기화(ROM 의 캐시 함수 — esp_cache_msync · PPA · JPEG 입력이 그림마다 부른다)를 하던 중이었으면, 이어지는 그 호출이
// 캐시 동기화 레지스터에 쓰다가 접근 오류로 죽는다. 2026-10-13 기기에서: 영상이 흐르는 중 `reboot` 20번에 1번, 40번에 1번 —
//   `Guru Meditation … (Store access fault)`, MEPC 는 ROM, MTVAL = 0x3ff100a0(CACHE_SYNC_ADDR_REG), 돌아갈 곳은 esp_restart_noos 의 캐시 끄는 자리.
//   죽어도 곧바로 다시 켜지긴 하지만(패닉 재시작) 패닉 횟수로 세어진다(MN-D22). 왜 그 쓰기가 오류가 되는지는 추정이다(동기화가 도는 중이라 본다).
// → 재시작 직전에 코어 1 에서 캐시를 만지는 우리 태스크 둘(pipeline · flip)을 세우고, 하던 캐시 작업이 끝날 시간을 준다(그 작업은 인터럽트를 막고 한다 — 길어야 몇 ms).
//   세워 둔 채로 재시작한다. 파이프라인이 멈춰서 하는 재시작이어도 된다(멈춘 자리에서 세워진다). 한계: 패닉으로 재시작할 때는 이 훅이 불리지 않는다.
static void park_video_before_reset()
{
    const TaskHandle_t me = xTaskGetCurrentTaskHandle();
    for (const char *name : {"pipeline", "flip"}) {
        const TaskHandle_t h = xTaskGetHandle(name);
        if (h && h != me) vTaskSuspend(h);
    }
    vTaskDelay(pdMS_TO_TICKS(20));
}

// ── 태스크 띄우기 ────────────────────────────────────────────────────────────
// stack_caps 로 스택이 놓일 내부 RAM 구역을 고른다(MN-D19 — 감시 태스크는 DMA 로 쓸 수 없는 TCM 에. DMA 가 쓸 수 있는 구역을 남긴다).
//   RTC 램은 느리다(일반 내부 RAM 의 100배쯤) — 바쁜 태스크 · 시리얼 받기는 거기 두지 않는다(MN-D39: 받는 바이트를 잃었다).
//   탈출구: caps 를 0 으로 주면 일반 내부 RAM 에 잡힌다. 고른 구역에 못 잡으면 스스로 그렇게 한다.
bool spawn(TaskFunction_t fn, const char *name, uint32_t stack, UBaseType_t prio, BaseType_t core, uint32_t stack_caps)
{
    if (stack_caps && xTaskCreatePinnedToCoreWithCaps(fn, name, stack, nullptr, prio, nullptr, core, stack_caps) == pdPASS) return true;
    if (stack_caps) ESP_LOGW(TAG, "task %s: stack did not fit the chosen region -> general internal RAM", name);
    // 여기서도 못 띄우면 부른 쪽이 초기화 실패로 넘긴다 — 감시 태스크 없이, 파이프라인 없이 조용히 도는 일이 없게(2차 검토 I-1)
    return xTaskCreatePinnedToCore(fn, name, stack, nullptr, prio, nullptr, core) == pdPASS;
}

// ── panel 태스크: 터치 · 방향 · 밝기 · 온도 · 고장 안내 ───────────────────────────
// 화면을 치면 조작 띠가 뜨고, 띠의 단추로 밝기·방향·흑백을 바꾼다. 값만 바꾼다 — 그리는 일은 파이프라인이 다음 프레임에 한다.
// 돌려주는 것 = 그 터치가 한 일(진단 줄 MON TOUCH 에 찍는다)
static const char *osd_touch(int orient, int x, int y)
{
    const int64_t n = now_ms();
    if (n >= g_osd_until_ms.load()) { g_osd_until_ms.store(n + OSD_MS); g_force_redraw.store(true); return "show"; }   // 꺼져 있었으면 띄우기만
    int bri = g_bri.load();
    const char *did = "none";                                       // 띠 밖을 쳤다 — 시간만 늘린다
    switch (ui::hit(orient, x, y)) {
    case ui::Btn::DARKER:   bri = bri > 40 ? bri - 32 : 8; did = "darker"; break;
    case ui::Btn::BRIGHTER: bri = bri < 223 ? bri + 32 : 255; did = "brighter"; break;
    case ui::Btn::ROTATE:   { const int o = rec::rotate_next(orient); g_orient.store(o); cfg_set("orient", o); g_req_stat.store(true); did = "rotate"; break; }   // 90°씩(MN-D41) — 맥이 곧바로 알게 STAT 한 줄
    case ui::Btn::MONO:     video::set_mono(!video::mono()); cfg_set("mono", video::mono()); did = "mono"; break;
    case ui::Btn::CLOSE:    g_osd_until_ms.store(0); g_force_redraw.store(true); return "close";
    case ui::Btn::NONE:     break;
    }
    if (bri != g_bri.load()) { g_bri.store(bri); cfg_set("bri", bri); }
    g_osd_until_ms.store(n + OSD_MS);
    g_force_redraw.store(true);
    return did;
}
static float read_chip_temp()
{
    const int inj = g_inj_temp_x10.load();
    if (inj != INT_MIN) return inj / 10.0f;
    float c = NAN;
    if (!s_tsens || temperature_sensor_get_celsius(s_tsens, &c) != ESP_OK) return NAN;
    return c;
}
static void panel_task(void *)
{
    int orient_applied = g_orient.load() & 3, bri_applied = g_bri_eff.load();
    bool osd_was_visible = false, dimmed = false;
    int64_t next_temp_ms = 0;
    float ema = NAN;                                                // 고르게 한 칩 온도(센서 값은 ±1°C 쯤 흔들린다)
    rec::Heat heat = rec::Heat::NORMAL;
    const rec::HeatCfg heat_cfg;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(30));
        while (g_inj_ui_stuck.load()) vTaskDelay(pdMS_TO_TICKS(1000));  // 시험용: 돌아오지 않는 I2C 호출 흉내
        const int64_t n = now_ms();
        g_ui_beat_ms.store(n);                                      // 살아 있다(멈춤 감시가 본다 — 이 태스크가 멈추면 과열 보호도 멈춘다)
        const int o = g_orient.load() & 3;
        if (o != orient_applied) { M5.Display.setRotation(ui::m5_rotation(o)); orient_applied = o; }   // 터치 좌표계가 이것을 따른다

        const bool temp_due = n >= next_temp_ms;
        int x = -1, y = -1; float board = NAN;
        xSemaphoreTake(g_i2c_mu, portMAX_DELAY);                    // 다른 쥐는 쪽(시리얼 pwr · usb5v)은 레지스터 몇 개만 읽고 놓는다
        M5.update();
        const auto t = M5.Touch.getDetail();
        if (t.wasPressed()) { x = t.x; y = t.y; }
        if (temp_due && M5.Imu.isEnabled()) { float v = NAN; if (M5.Imu.getTemp(&v)) board = v; }
        xSemaphoreGive(g_i2c_mu);
        const int ix = g_inj_x.exchange(-1);
        if (ix >= 0) { x = ix; y = g_inj_y.load(); }
        if (x >= 0) {
            g_touch_ms.store(n);
            const char *did = "wake";
            if (dimmed) g_force_redraw.store(true);                 // 꺼 두었던 화면 — 이번 터치는 깨우기만 한다
            else did = osd_touch(o, x, y);
            // 진단 줄: 손가락이 친 자리와 그것이 한 일. 실제 터치(panel)인지 시리얼로 넣은 것(serial)인지 — 좌표가 화면 방향과 맞는지를 한 번 쳐서 볼 수 있다
            printf("MON TOUCH src=%s x=%d y=%d orient=%d -> %s\n", ix >= 0 ? "serial" : "panel", x, y, o, did);
        }
        const bool osd_visible = n < g_osd_until_ms.load();
        if (osd_was_visible && !osd_visible) g_force_redraw.store(true);   // 띠가 시간이 다 돼 사라질 때 한 장을 다시 그린다
        osd_was_visible = osd_visible;

        // 온도 → 단계(MN-D18). 단계가 바뀌면 밝기 상한 · 그림 멈춤을 바꾼다
        if (temp_due) {
            next_temp_ms = n + TEMP_PERIOD_MS;
            const float c = read_chip_temp();
            if (rec::temp_valid(c)) ema = (std::isnan(ema) || g_inj_temp_x10.load() != INT_MIN) ? c : ema + 0.3f * (c - ema);
            g_temp_x10.store(rec::temp_valid(ema) ? (int)lroundf(ema * 10) : -990);
            g_board_x10.store(rec::temp_valid(board) ? (int)lroundf(board * 10) : -990);
            const rec::Heat h2 = rec::heat_step(heat, ema, heat_cfg);
            if (h2 != heat) {
                const rec::HeatLimit l = rec::heat_limit(h2);
                if (h2 > heat && h2 != rec::Heat::CRITICAL) g_heat_note_until_ms.store(n + HEAT_NOTE_MS);   // 낮췄다는 것을 화면에 잠깐 알린다
                heat = h2;
                g_heat.store((int)h2); g_video_paused.store(!l.video);
                g_force_redraw.store(true);
                printf("MON HEAT %s temp=%.1f -> bri<=%d video=%d\n", rec::heat_name(h2), ema, (int)l.bri_max, (int)l.video);
            }
        }

        // 밝기 = 사용자가 고른 값, 발열 상한, 현황판 상태 상한 가운데 가장 낮은 것(MN-D32 — 밤은 SLEEP(맥 화면 꺼짐)이 맡고, 알림은 꺼지지 않게)
        int bri = g_bri.load();
        const int cap_heat = rec::heat_limit(heat).bri_max;
        if (bri > cap_heat) bri = cap_heat;
        const rec::Board bs = (rec::Board)g_board_state.load();
        const int cap_board = rec::board_bri_max(rec::BoardCfg(), bs, g_board_alarm.load());
        if (bri > cap_board) bri = cap_board;
        dimmed = bs == rec::Board::SLEEP;                           // 꺼진 동안의 터치는 깨우기만 한다 — 잠긴 맥의 메시지가 보이지 않게
        if (bri != bri_applied) { M5.Display.setBrightness((uint8_t)bri); bri_applied = bri; g_bri_eff.store(bri); }

        // 고장 안내(감시 태스크가 청한다): 그림 경로와 무관한 길로 한 번 그린다
        if (const int f = g_fault.exchange(0)) {
            video::freeze(500);                                     // 그리던 프레임이 끝난 뒤에(멈춘 채 쥐고 있으면 0.5초만 기다리고) 그린다
            ui::draw_fault(video::primary_buffer(), o, (ui::Fault)(f - 1));
            video::present_primary();
        }
    }
}

// ── 감시 태스크(MN-D12 — 검토 1차 I-1·I-2) ─────────────────────────────────────
// 다른 태스크가 무엇을 기다리든 이 태스크는 돈다: 잠금을 잡지 않고 원자 값만 읽는다. 우선순위가 가장 높아 우리 태스크가 굶길 수 없고,
// 하는 일은 1초에 한 번 몇 μs 다. 판단은 recovery.h(호스트 시험), 여기는 그 결과를 실행만 한다.
static void watchdog_task(void *)
{
    rec::WdState st; const rec::WdCfg cfg;
    rec::GiveUp gave_up;                                            // 상한에 막힌 재시작을 1초마다 다시 청하지 않는다(고장이 풀리면 놓는다)
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        rec::WdIn in;
        in.now_ms = now_ms(); in.pipe_beat_ms = g_pipe_beat_ms.load(); in.sess_beat_ms = g_sess_beat_ms.load(); in.ui_beat_ms = g_ui_beat_ms.load();
        in.refresh_count = video::refresh_count();
        const rec::WdOut o = rec::wd_step(st, cfg, in);
        if (gave_up.should_try(o) && !guarded_restart(o.cause)) {
            gave_up.refused(o.cause);
            esp_rom_printf("MON FAIL %s %d ms - restart budget used up (%s)\n", rec::cause_name(o.cause), (int)o.age_ms, rst_slot(o.cause).key);
            // 화면이 더는 바뀌지 않을 까닭이면 그 사실을 화면에 적는다
            if (o.cause == rec::Cause::PIPELINE_STUCK || o.cause == rec::Cause::PANEL_STUCK) raise_fault(ui::Fault::STUCK);
        }
    }
}

// ── app_main: 부팅 → 세션 루프(board_session.cpp) ─────────────────────────────
extern "C" void app_main(void)
{
    auto cfg = M5.config();
    cfg.internal_spk = false;                       // 소리는 쓰지 않는다 — M5Unified 가 I2S·코덱을 잡지 않게
    cfg.internal_mic = false;
    M5.begin(cfg);
    M5.Display.setBrightness(0);                    // 첫 그림을 채우기 전에는 백라이트를 끈다(부팅 때 빈 화면 색이 잠깐 보인다)
    // M5GFX 가 화면을 찾으며 스피커 앰프를 켜 둔다(메모리 tab5-amp-on-at-boot). 확장칩 0x43 출력 레지스터 0x05 의 비트1 을 내려 바로 끈다.
    M5.In_I2C.bitOff(0x43, 0x05, 0b00000010, 400000);

    // MN-D3 NVS 는 열기만 한다. 못 열어도 지우지 않는다(전화기가 저장한 Wi-Fi 목록이 같은 구역에 있다) — 그때는 설정 저장 없이 돈다.
    if (!cfg_init()) ESP_LOGW(TAG, "nvs unavailable -> settings are not saved (not erasing)");

    // 어떻게 켜졌는가. 패닉·워치독이면 센다(우리가 청한 재시작과 달리 IDF 가 곧장 재부팅한다 — 여기서 세지 않으면 끝없이 되풀이될 수 있다: MN-D22).
    //   전원을 켠 부팅이면 재시작 횟수들을 처음부터 센다(화면이 "전원을 껐다 켜 주세요"라고 했으면 그 말대로 다시 해 볼 수 있어야 한다)
    const esp_reset_reason_t why = esp_reset_reason();
    const bool was_crash = why == ESP_RST_PANIC || why == ESP_RST_INT_WDT || why == ESP_RST_TASK_WDT || why == ESP_RST_WDT || why == ESP_RST_CPU_LOCKUP;
    const int stored_panics = cfg_get("rst_panic", 0);
    const rec::CrashBoot crash = rec::crash_boot(stored_panics, was_crash, why == ESP_RST_POWERON);
    if (crash.count != stored_panics) cfg_set("rst_panic", crash.count);
    if (crash.fresh_start) for (const char *key : {"rst_stuck", "rst_video", "rst_panel"}) if (cfg_get(key, 0)) cfg_set(key, 0);

    // 화면이 안 잡히면(소프트 리셋 뒤 패널 감지가 가끔 실패한다) 재시작한다 — 3번까지
    const bool panel_ok = M5.Display.width() != 0;
    if (!panel_ok) { guarded_restart(rec::Cause::PANEL_INIT); printf("MON FAIL panel\n"); }
    else if (cfg_get("rst_panel", 0)) cfg_set("rst_panel", 0);

    const int last_rst = cfg_get("last_rst", 0);    // 지난번에 스스로 재시작한 까닭(한 번 찍고 지운다)
    if (last_rst) cfg_set("last_rst", 0);
    const int n_stuck = cfg_get("rst_stuck", 0);
    // 시험용(`fault boot`): 이번 부팅에서 실패한 것으로 칠 초기화(0 = 없음 · 1 = 그리기 장치 · 3 = 마지막 태스크(panel) 생성). 남은 횟수를 하나 줄인다
    int boot_inj = 0;
    if (const int left = cfg_get("inj_boot_n", 0); left > 0) { cfg_set("inj_boot_n", left - 1); boot_inj = cfg_get("inj_boot", 0); }

    // 옛 설정 flip 은 가로 뒤집힘으로 읽는다. 네 방향(MN-D41 — 그 전 MN-D38 은 가로 둘뿐이었다)
    g_orient.store(rec::orient_for(cfg_get_in("orient", cfg_get("flip", 0) ? 2 : 0, 0, 3)));
    g_boot_ms.store(now_ms());
    video::set_full_range(cfg_get("range", 1) != 0);
    // TB-D4 기본은 컬러(현황판의 색이 뜻을 가진다 — 주황 = 안 읽음). 앞 판의 기본은 흑백이었다(MN-D13, HDMI 모니터용) — 저장된 값이 있으면 그것을 쓴다
    video::set_mono(cfg_get("mono", 0) != 0);
    g_bri.store(cfg_get_in("bri", 200, lim::BRI_LO, lim::BRI_HI));
    g_osd.store(cfg_get("osd", 0) != 0);

    const int orient = g_orient.load();
    M5.Display.setColorDepth(16);
    M5.Display.setSwapBytes(true);
    M5.Display.setRotation(ui::m5_rotation(orient));   // 터치 좌표계. 이 뒤로는 panel 태스크만 바꾼다
    M5.Display.fillScreen((uint32_t)0x161615u);
    M5.Display.setBrightness((uint8_t)g_bri.load()); g_bri_eff.store(g_bri.load());
    // 깨진 프레임은 우리가 세고(STAT decfail) 버린다 — 드라이버의 프레임당 오류 줄은 시리얼을 메워 명령 응답을 가린다.
    //   (dma2d 의 ESP_EARLY_LOGE 줄은 이 설정으로 꺼지지 않는다 — 그런 줄이 찍힐 호출 자체를 하지 않는다: 부품 패치 J7)
    esp_log_level_set("jpeg.decoder", ESP_LOG_NONE);
    esp_log_level_set("jpeg_enh", ESP_LOG_NONE);
    esp_log_level_set("dma2d", ESP_LOG_NONE);

    g_i2c_mu = xSemaphoreCreateMutex();
    if (!g_i2c_mu) init_failed(rec::Cause::SYS_INIT, "sys (rtos objects)", orient);

    // 칩 온도(MN-D18). 20~100°C 범위(오차 2°C 미만). 못 켜도 그림은 나온다 — 그때는 발열 보호 없이(STAT temp=-99)
    temperature_sensor_config_t tc = TEMPERATURE_SENSOR_CONFIG_DEFAULT(20, 100);
    if (temperature_sensor_install(&tc, &s_tsens) != ESP_OK || temperature_sensor_enable(s_tsens) != ESP_OK) { s_tsens = nullptr; ESP_LOGW(TAG, "temperature sensor unavailable -> no thermal protection"); }

    // 패닉이 잇달았다 → 그리기 쪽(디코더)을 아예 시작하지 않고 선다. 시리얼은 듣는다: `reboot` 하면 한 번 더 해 본다
    if (crash.halt) {
        snprintf(s_boot_line, sizeof s_boot_line, "halted: %d crash resets in a row (reset reason %d) reset=%d panics=%d", (int)crash.count, (int)why, (int)why, (int)crash.count);
        printf("MON BOOT %s\n", s_boot_line);                      // 서 있는 동안에도 시리얼 `boot` 가 켜진 까닭을 돌려준다(MN-D29)
        spawn(serial_task, "sercmd", 4 * 1024, 3, 0, 0);           // 못 띄워도 선다(시리얼만 없다)
        halt_with_notice("crash-loop", ui::Fault::STUCK, orient);
    }

    // 현황판 그림 칸 둘(받는 칸 · 보이는 칸). 못 잡으면 초기화 실패(상한 있는 재시작 → 안내)
    for (auto &b : g_board_buf) b = (uint8_t *)heap_caps_aligned_alloc(128, BOARD_CAP, MALLOC_CAP_SPIRAM);
    if (!g_board_buf[0] || !g_board_buf[1]) init_failed(rec::Cause::SYS_INIT, "sys (board buffers)", orient);
    const bool video_ok = video::init() && boot_inj != 1;
    // 부팅 줄은 보관해 둔다: 시리얼 `boot` 가 `BOOTINFO …` 로 다시 찍는다 — 재부팅 직후의 줄은 맥이 포트를 다시 붙이는 동안 잘릴 수 있어(인계 문서 3-16),
    //   기기 시험이 "어떻게 켜졌나(reset=)"를 그 줄에만 기대면 증거 없이 통과한다(사전 검토 3a T-6)
    snprintf(s_boot_line, sizeof s_boot_line, "fw=wideeye video=%d panel=%dx%d fbs=%d range=%s mono=%d orient=%d bri=%d last_rst=%s rst=%d panics=%d reset=%d heap_int=%u blk=%u",
           (int)video_ok, (int)M5.Display.width(), (int)M5.Display.height(),
           video::panel_count(), video::full_range() ? "full" : "limited", (int)video::mono(), orient, g_bri.load(),
           rec::cause_name((rec::Cause)last_rst), n_stuck, (int)crash.count, (int)why,
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
    printf("MON BOOT %s\n", s_boot_line);
    if (video_ok && video::panel_count() < 3) printf("MON WARN fbs=%d - M5GFX frame buffer patch missing (tools/patch_m5gfx_fbs.py)\n", video::panel_count());
    // 꼭 있어야 하는 태스크를 못 띄우면 초기화 실패다(상한 있는 재시작 → 안내). 조용히 반쪽으로 돌지 않는다
    auto must_spawn = [&](TaskFunction_t fn, const char *name, uint32_t stack, UBaseType_t prio, BaseType_t core, uint32_t caps) {
        const bool injected = boot_inj == 3 && !strcmp(name, "panel");   // 시험용: 마지막 태스크를 못 띄운 것으로(앞의 둘은 떠 있다 — 실제 NO_MEM 과 같은 꼴)
        if (!injected && spawn(fn, name, stack, prio, core, caps)) return;
        static char what[40];
        snprintf(what, sizeof what, "sys (task %s)", name);
        init_failed(rec::Cause::SYS_INIT, what, orient);
    };
    // MN-D39 sercmd 스택은 보통 내부 RAM 에: RTC 램(느림)에 두면 받는 바이트를 잃는다 — 2026-10-15 기기 실측,
    //   그림(192KB) 20장 중 성공 17장 → 20장, 300B 조각이면 11 → 20. 잃는 것은 줄 첫머리의 글자 몇 개(USB 묶음 단위가 아님). 까닭은 추정이다
    //   (느린 스택에서 도는 링버퍼의 임계 구역이 길어져 받기 인터럽트가 밀린다)
    must_spawn(serial_task, "sercmd", 4 * 1024, 3, 0, 0);
    // 디코더 · PPA · 패널 버퍼를 못 잡았다 → 재시작해 본다(3번까지). 그래도 안 되면 화면에 알리고 선다(시리얼 `reboot` 은 듣는다)
    if (!video_ok) init_failed(rec::Cause::VIDEO_INIT, "video", orient);
    ui::init();
    video::set_overlay_fn(ui::draw_overlay);

    board_session_install();

    g_pipe_beat_ms.store(now_ms()); g_sess_beat_ms.store(now_ms()); g_ui_beat_ms.store(now_ms());
    must_spawn(watchdog_task, "watchdog", 4 * 1024, 20, 0, MALLOC_CAP_TCM);   // 감시부터 — 나머지가 뜨다 멈춰도 본다
    must_spawn(pipeline_task, "pipeline", 8 * 1024, 5, 1, 0);
    must_spawn(panel_task, "panel", 4 * 1024, 4, 0, 0);
    // 부팅에 필요한 것이 다 만들어졌다 — 태스크 셋까지. 이 줄이 must_spawn 보다 앞에 있으면 태스크를 못 띄운 재시작이 부팅마다 0 → 1 로 세어져
    //   상한(3)에 닿지 않고 되풀이된다(사전 검토 3a I-1 — `fault boot 3` 으로 기기에서 확인)
    if (cfg_get("rst_video", 0)) cfg_set("rst_video", 0);
    if (esp_register_shutdown_handler(park_video_before_reset) != ESP_OK) ESP_LOGE(TAG, "shutdown hook (park video) not registered");

    board_session_run();
}
