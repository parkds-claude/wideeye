// sercmd.cpp — 시리얼 명령(USB-Serial/JTAG, 줄 단위): 상태 보기 · 설정 바꾸기 · 자가 시험 JPEG 올리기 · 고장 넣기(시험용).
// 값만 바꾼다 — 그리기는 요청을 남겨 그 일을 맡은 태스크(파이프라인)가 하게 한다.
#include "app.h"

#include <M5Unified.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_heap_caps.h"
#include "esp_rom_crc.h"
#include "esp_system.h"
#include "mbedtls/base64.h"

#include "board_rx.h"
#include "video.h"

// ── 시리얼 명령(USB-Serial/JTAG, 줄 단위) ─────────────────────────────────────
// 내부 RAM 의 힙을 구역별로(여유 · 가장 큰 덩어리 · 가장 적었던 때)
static void print_heap()
{
    printf("HEAP internal free=%u largest=%u min_ever=%u | dma largest=%u | psram free=%u\n",
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL), (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    heap_caps_print_heap_info(MALLOC_CAP_INTERNAL);
}
static void print_tasks()
{
    static const char *const names[] = {"pipeline", "panel", "sercmd", "watchdog", "main", "flip", "ipc0", "ipc1"};
    printf("TASKS (free stack, bytes) isr_core=%d", video::isr_core());
    for (const char *nm : names) { const TaskHandle_t h = xTaskGetHandle(nm); if (h) printf(" %s=%u", nm, (unsigned)uxTaskGetStackHighWaterMark(h)); }
    printf("\n");
}
static void fault_cmd(const char *what, int a, int b)
{
    if (!strcmp(what, "stuck")) g_inj_pipe_stuck.store(true);           // 파이프라인이 돌아오지 않는다 → 5초 뒤 재시작이 기대값
    else if (!strcmp(what, "sess")) g_inj_sess_stuck.store(true);       // 세션이 돌아오지 않는다 → 30초 뒤 재시작
    else if (!strcmp(what, "ui")) g_inj_ui_stuck.store(true);           // panel 태스크가 돌아오지 않는다 → 10초 뒤 재시작
    else if (!strcmp(what, "dec")) g_inj_dec.store(a);                  // 다음 a 장(-1 = 끌 때까지)을 못 푼 것으로(현황판은 bdec 가 는다 — 재시작하지 않는다)
    else if (!strcmp(what, "tok")) {                                    // 토큰을 a ms(5초까지) 붙든다 → 그동안의 프레임은 포기
        a = a < 0 ? 0 : a > 5000 ? 5000 : a;
        printf("fault tok %d: %s\n", a, video::hold_token(a) ? "held" : "busy");
        return;
    }
    else if (!strcmp(what, "temp")) g_inj_temp_x10.store(a <= -100 ? INT_MIN : a * 10);   // 온도를 a °C 로 친다(-100 이하 = 실제 값으로)
    else if (!strcmp(what, "budget")) { cfg_set("rst_stuck", a); cfg_set("rst_panic", a); }   // 재시작·패닉 횟수를 a 로(상한 · 정지 시험)
    else if (!strcmp(what, "panic")) { printf("OK fault panic (aborting)\n"); fflush(stdout); vTaskDelay(pdMS_TO_TICKS(100)); abort(); }   // 패닉으로 다시 켜지는 길(횟수 세기 · USB 멈추기)
    else if (!strcmp(what, "boot")) {                                   // 다음 b 번의 부팅에서 초기화가 실패한 것으로(a: 1 = 그리기 장치 · 3 = panel 태스크 생성) → 재시작 3번 뒤 고장 안내
        if (!(a == 1 || a == 3) || !rec::in_range(b, 1, 8)) { printf("ERR fault boot: <1 video | 3 task> <boots 1..8>\n"); return; }
        cfg_set("inj_boot", a); cfg_set("inj_boot_n", b);               // NVS 에 둔다 — 재시작을 건너가야 한다. 부팅마다 하나씩 준다
    }
    else if (!strcmp(what, "notice")) raise_fault(a ? ui::Fault::INIT : ui::Fault::STUCK);   // 고장 안내 화면을 띄운다(0 = 멈춤 · 1 = 시작 실패. 그리기가 멈춘다 — `reboot` 으로 푼다)
    else if (!strcmp(what, "clear")) {
        g_inj_dec.store(0); g_inj_temp_x10.store(INT_MIN);
        if (cfg_get("inj_boot_n", 0)) cfg_set("inj_boot_n", 0);
    }
    else { printf("ERR fault: stuck | sess | ui | dec <n> | tok <ms> | temp <c> | budget <n> | boot <1|3> <n> | panic | notice | clear (stuck/sess/ui/notice are undone by reboot only)\n"); return; }
    printf("OK fault %s %d\n", what, a);
}
// ── 현황판 그림 받기(MN-D34) — 이 태스크만 만진다. 규약: urgent-board 설계 2026-10-15 2-5 ──
static brx::Rx s_brx;
static int s_brx_idx = -1;                                          // 지금 받는 칸
static void board_frame_begin(uint32_t id, long long len, uint32_t crc, long long age)
{
    for (int i = 0; i < 100 && g_board_busy.load(); ++i) vTaskDelay(pdMS_TO_TICKS(10));   // 파이프라인이 그 칸을 다 읽을 때까지(1초 한도)
    const int show = g_board_show.load();
    s_brx_idx = show == 0 ? 1 : 0;                                  // 보이는 칸이 아닌 쪽에 받는다
    if (!g_board_buf[s_brx_idx] || g_board_busy.load()) { s_brx = brx::Rx(); printf("ERR frame %u busy\n", (unsigned)id); return; }
    const brx::Begin r = brx::begin(s_brx, id, len, crc, age, now_ms());
    if (r == brx::Begin::OK) printf("OK frame-begin %u\n", (unsigned)id);
    else printf("ERR frame %u %s\n", (unsigned)id, r == brx::Begin::TOO_BIG ? "too-big" : "bad-args");
}
static void board_frame_chunk(const char *b64, size_t n)
{
    size_t olen = 0;
    const bool ok = s_brx.st == brx::St::RECEIVING &&
                    mbedtls_base64_decode(g_board_buf[s_brx_idx] + s_brx.got, s_brx.expect - s_brx.got, &olen, (const unsigned char *)b64, n) == 0;
    const uint32_t id = s_brx.id;
    switch (brx::chunk(s_brx, ok, olen, now_ms())) {
    case brx::Chunk::ACK:    printf("OK + %u %u\n", (unsigned)id, (unsigned)s_brx.got); fflush(stdout); break;   // 곧바로 — 맥은 이 답을 기다린다
    case brx::Chunk::BROKEN: printf("ERR + %u broken\n", (unsigned)id); fflush(stdout); break;
    default: break;
    }
}
static void board_frame_end(uint32_t id)
{
    const int idx = s_brx_idx;
    const bool mine = s_brx.st == brx::St::RECEIVING && id == s_brx.id;
    const uint32_t crc = mine ? esp_rom_crc32_le(0, g_board_buf[idx], s_brx.got) : 0;
    const brx::End e = brx::end(s_brx, id, crc);
    if (e == brx::End::OK) {
        // 쓰는 차례: 길이 → 내용 시각 → 번호 → 보이는 칸 → 세대(검토 덧붙임). 파이프라인은 세대·번호·시각을 먼저 읽고 g_board_busy 를 건 다음 칸을 읽는다 —
        //   그사이 새 그림이 와도 더 새 그림을 더 옛 시각으로 칠 뿐이고(안전한 쪽), 다음 바퀴에 세대가 달라 다시 그린다
        g_board_len[idx] = s_brx.got;
        g_board_content_ms.store(now_ms() - s_brx.age_ms);
        g_board_seq.store(id);
        g_board_show.store(idx);
        g_board_gen.fetch_add(1);                                   // 맨 나중에 — 파이프라인은 세대가 바뀐 것을 보고 그린다
        printf("OK frame %u %u crc=%08x\n", (unsigned)id, (unsigned)g_board_len[idx], (unsigned)crc);
    } else {
        const char *why = e == brx::End::NOT_RECEIVING ? "not-receiving" : e == brx::End::WRONG_ID ? "wrong-id" : e == brx::End::SHORT ? "short" : "crc";
        printf("ERR frame %u %s\n", (unsigned)id, why);
    }
}
// 현황판 명령이면 처리하고 true
static bool board_cmd(const std::string &cmd)
{
    unsigned id = 0, crc = 0; long long len = 0, age = 0; int a = 0;
    if (sscanf(cmd.c_str(), "frameend %u", &id) == 1) board_frame_end(id);
    else if (sscanf(cmd.c_str(), "frame %u %lld %x %lld", &id, &len, &crc, &age) == 4) board_frame_begin(id, len, crc, age);
    else if (sscanf(cmd.c_str(), "screen %u %d", &id, &a) == 2) {
        // 30초마다 같은 값이 온다 — screen 0 은 마지막 때를 새로 적고(90초 끊기면 SLEEP 이 풀린다), 다시 그리기는 바뀔 때만(검토 4)
        rec::ScreenSig sig; sig.off_ms = g_screen_off_ms.load(); sig.wake_ms = g_screen_on_ms.load();
        const bool changed = rec::screen_signal(sig, a != 0, now_ms());
        g_screen_on_ms.store(sig.wake_ms);                          // 깬 때를 먼저 — 파이프라인이 켜짐을 보는 때에는 이미 적혀 있게
        g_screen_off_ms.store(sig.off_ms);
        if (changed) g_force_redraw.store(true);
        printf("OK screen %u %d\n", id, a != 0);
    }
    else if (sscanf(cmd.c_str(), "board stale %d", &a) == 1) {
        if (!rec::in_range(a, 5, 3600)) printf("ERR board stale: 5..3600 sec (not saved)\n");
        else { g_board_stale_ms.store((int64_t)a * 1000); printf("OK board stale=%d\n", a); }
    }
    else return false;
    return true;
}
static void serial_cmd(const std::string &cmd)
{
    int a = 0, b = 0; char w[16] = "";
    if (board_cmd(cmd)) {}
    else if (cmd == "stat") g_req_stat.store(true);
    else if (cmd == "shot") g_req_shot.store(true);
    else if (cmd == "tasks") print_tasks();
    else if (cmd == "heap") print_heap();
    else if (cmd == "boot") {                                       // 부팅 줄을 다시(재부팅 직후의 MON BOOT 는 포트가 다시 붙기 전이면 못 받는다)
        printf("BOOTINFO %s\n", boot_line());
        // RL-D3 최근 부팅 기록 — 새것부터 "hw=하드웨어 까닭(16진) why=IDF 까닭 prev=그 전 실행 길이(0 3분 안 · 1 3분 · 2 30분 · 3 3시간 · 4 하루)"
        const int total = cfg_get("hist_n", 0);
        printf("BOOTHIST n=%d", total);
        for (int k = 0; k < rec::HIST_N && k < total; ++k) {
            char key[8];
            snprintf(key, sizeof key, "h%d", (total - 1 - k) % rec::HIST_N);
            const rec::HistEntry e = rec::hist_unpack(cfg_get(key, 0));
            if (e.valid) printf(" [hw=0x%x why=%d prev=%d]", e.hw, e.why, e.prev_up);
        }
        printf("\n");
    }
    else if (cmd == "cfg")                                           // 지금 설정(기기 시험이 시작할 때 적어 두고 끝나면 되돌린다 — 사전 검토 3a T-1)
        printf("CFG bri=%d orient=%d mono=%d range=%d osd=%d\n", g_bri.load(), g_orient.load(), (int)video::mono(), (int)video::full_range(), (int)g_osd.load());
    else if (sscanf(cmd.c_str(), "t %d %d", &a, &b) == 2) {          // 터치 넣기(시험용, 지금 방향의 화면 좌표)
        if (!rec::in_range(a, 0, SCREEN_W - 1) || !rec::in_range(b, 0, PANEL_H - 1)) printf("ERR t: 0..%d 0..%d\n", SCREEN_W - 1, PANEL_H - 1);
        else { g_inj_y.store(b); g_inj_x.store(a); printf("OK t %d %d\n", a, b); }
    }
    else if (cmd == "bench") g_req_bench.store(true);
    else if (sscanf(cmd.c_str(), "orient %d", &a) == 1 || cmd == "flip") {
        const int want = cmd == "flip" ? (g_orient.load() ^ 2) : a;     // flip = 지금 방향의 180° 뒤집힘
        const int o = rec::orient_for(want);                            // 네 방향(MN-D41)
        if (!rec::in_range(want, 0, 3)) printf("ERR orient: 0..3\n");
        else {
            g_orient.store(o); cfg_set("orient", o); g_force_redraw.store(true); g_req_stat.store(true);   // 맥이 곧바로 알게(MN-D41)
            printf("OK orient=%d (%s)\n", o, o == 0 ? "landscape" : o == 1 ? "portrait" : o == 2 ? "landscape-flipped" : "portrait-flipped");
        }
    }
    else if (sscanf(cmd.c_str(), "mono %d", &a) == 1) { video::set_mono(a != 0); cfg_set("mono", a != 0); g_force_redraw.store(true); printf("OK mono=%d\n", a != 0); }
    else if (sscanf(cmd.c_str(), "range %d", &a) == 1) { video::set_full_range(a != 0); cfg_set("range", a != 0); g_force_redraw.store(true); printf("OK range=%s\n", a ? "full" : "limited"); }
    else if (sscanf(cmd.c_str(), "bri %d", &a) == 1) {
        if (a < lim::BRI_LO) a = lim::BRI_LO;
        if (a > lim::BRI_HI) a = lim::BRI_HI;
        g_bri.store(a); cfg_set("bri", a); g_touch_ms.store(now_ms()); printf("OK bri=%d\n", a);   // 실제로 내는 일은 panel 태스크가 한다
    }
    else if (sscanf(cmd.c_str(), "osd %d", &a) == 1) { g_osd.store(a != 0); cfg_set("osd", a != 0); g_force_redraw.store(true); printf("OK osd=%d\n", a != 0); }
    else if (sscanf(cmd.c_str(), "put %d", &a) == 1) {              // 시험 JPEG 올리기 시작: 이어서 '+<base64>' 줄들, 끝에 putend
        g_tj_ready.store(false); g_play_until_ms.store(0);
        for (int i = 0; i < 100 && g_tj_busy.load(); ++i) vTaskDelay(pdMS_TO_TICKS(10));   // 파이프라인이 그 버퍼를 다 읽을 때까지
        if (!g_tj) g_tj = (uint8_t *)heap_caps_aligned_alloc(128, TEST_JPG_CAP, MALLOC_CAP_SPIRAM);
        if (!g_tj || g_tj_busy.load() || a <= 0 || (size_t)a > TEST_JPG_CAP) { g_tj_expect = 0; printf("ERR put size (max %u)\n", (unsigned)TEST_JPG_CAP); }
        else { g_tj_len = 0; g_tj_expect = (size_t)a; printf("OK put-begin %d\n", a); }
    }
    else if (cmd == "putend") {
        if (g_tj && g_tj_expect && g_tj_len == g_tj_expect) { g_tj_ready.store(true); printf("OK put %u crc=%08x\n", (unsigned)g_tj_len, (unsigned)esp_rom_crc32_le(0, g_tj, g_tj_len)); }
        else printf("ERR put got %u of %u\n", (unsigned)g_tj_len, (unsigned)g_tj_expect);
        g_tj_expect = 0;
    }
    else if (sscanf(cmd.c_str(), "play %d %d", &a, &b) == 2) {       // play <fps, 0=최대> <초>
        if (!g_tj_ready.load()) printf("ERR play: no test jpeg (put first)\n");
        else if (!rec::in_range(a, 0, 60) || !rec::in_range(b, 1, 3600)) printf("ERR play: fps 0..60 sec 1..3600\n");
        else { g_play_fps.store(a); g_play_until_ms.store(now_ms() + (int64_t)b * 1000); printf("OK play fps=%d sec=%d\n", a, b); }
    }
    else if (cmd == "pwr") {
        // 전원 진단: 확장칩 0x44(E2) 의 방향·출력·입력 레지스터(P3 = USB-A 5V — 현황판은 끈다)와 배터리 전압·전류
        uint8_t dir = 0, out = 0, hiz = 0, in = 0;
        xSemaphoreTake(g_i2c_mu, portMAX_DELAY);
        const bool ok = M5.In_I2C.readRegister(0x44, 0x03, &dir, 1, 400000) && M5.In_I2C.readRegister(0x44, 0x05, &out, 1, 400000) &&
                        M5.In_I2C.readRegister(0x44, 0x07, &hiz, 1, 400000) && M5.In_I2C.readRegister(0x44, 0x0F, &in, 1, 400000);
        const int bat_mv = (int)M5.Power.getBatteryVoltage(), bat_ma = (int)M5.Power.getBatteryCurrent();
        xSemaphoreGive(g_i2c_mu);
        printf("PWR e2_ok=%d dir=0x%02x out=0x%02x hiz=0x%02x in=0x%02x usb5v_en=%d bat_mv=%d bat_ma=%d\n", (int)ok, dir, out, hiz, in,
               (int)((out >> 3) & 1), bat_mv, bat_ma);
    }
    else if (sscanf(cmd.c_str(), "fault %15s %d %d", w, &a, &b) >= 1) fault_cmd(w, a, b);
    else if (cmd == "reboot") { printf("OK reboot\n"); fflush(stdout); vTaskDelay(pdMS_TO_TICKS(200)); esp_restart(); }
    else if (cmd == "help") printf("cmds: stat tasks heap pwr boot cfg put <n>/+b64/putend play <fps> <sec> shot `t <x> <y>` bench orient 0..3 flip mono 0|1 range 0|1 "
                                   "bri <5..255> osd 0|1 reboot "
                                   "| board: frame <id> <len> <crc> <age_ms>/+b64/frameend <id> screen <id> 0|1 board stale <sec> | test only: fault stuck|sess|ui|dec|tok|temp|budget|boot|panic|notice|clear [n] [m]\n");
    else printf("ERR unknown '%s' (help)\n", cmd.c_str());
    fflush(stdout);
}
void serial_task(void *)
{
    usb_serial_jtag_driver_config_t jcfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    jcfg.rx_buffer_size = 16384;                                    // 긴 줄(조각 2,000자)이 4개씩 몰려 온다 — 넘치면 IDF 가 조용히 버린다(MN-D34)
    // 보내는 쪽 고리도 키운다(기본 256): 플래시에 쓰는 동안(설정 저장 — 수십 ms) USB 로 내보내지 못하는데, 그사이 고리가 차서 50ms 를 넘기면
    //   IDF 의 콘솔이 글자를 버린다. STAT 한 줄이 560바이트다 — 2026-10-13 설정을 120번 저장하는 시험에서 그 순간의 응답 한 줄이 사라졌다.
    jcfg.tx_buffer_size = 2048;
    if (usb_serial_jtag_driver_install(&jcfg) == ESP_OK) usb_serial_jtag_vfs_use_driver();   // 인터럽트로 받는다(폴링하면 글자를 잃는다)
    static char line[2400]; size_t n = 0; bool too_long = false;
    static uint8_t rx[1024];
    for (;;) {
        const int got = usb_serial_jtag_read_bytes(rx, sizeof rx, pdMS_TO_TICKS(100));
        if (got < 0) { vTaskDelay(pdMS_TO_TICKS(200)); continue; }   // 드라이버가 없으면 곧바로 -1 이 온다 — 쉬지 않고 돌면 아래 우선순위의 세션이 굶는다(사전 검토 2a M-2)
        if (const uint32_t gone = brx::expire(s_brx, now_ms())) { printf("ERR frame %u timeout\n", (unsigned)gone); fflush(stdout); }   // 맥이 보내다 멈췄다(MN-D34) — 줄이 안 와도 100ms 마다 본다
        for (int i = 0; i < got; ++i) {
            const char ch = (char)rx[i];
            if (ch == '\r') continue;
            if (ch != '\n') { if (n < sizeof line - 1) line[n++] = ch; else too_long = true; continue; }
            line[n] = 0;
            if (too_long) printf("ERR line too long\n");
            else if (line[0] == '+' && s_brx.st == brx::St::RECEIVING) board_frame_chunk(line + 1, n - 1);   // 현황판 그림 조각
            else if (line[0] == '+') {                              // 시험 JPEG 조각
                size_t olen = 0;
                if (g_tj && g_tj_expect && mbedtls_base64_decode(g_tj + g_tj_len, TEST_JPG_CAP - g_tj_len, &olen, (const unsigned char *)line + 1, n - 1) == 0) g_tj_len += olen;
                else g_tj_expect = 0;                               // 깨진 조각 → putend 가 실패를 알린다
            }
            else if (n) serial_cmd(std::string(line, n));
            n = 0; too_long = false;
        }
    }
}
