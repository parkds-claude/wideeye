// board_session.cpp — 현황판 세션: USB-A 전원을 끄고, 살아 있다는 박동을 내고, 현황판이 오래 이어지면 재시작 · 패닉 횟수를 지운다.
// 그림을 받는 일은 sercmd.cpp(USB-C 직렬), 그리는 일은 pipeline.cpp 가 한다. 이 파일은 app_main 의 태스크에서 돈다.
// (HDMI 모니터 기능 — USB 호스트 · UVC 캡처 동글 — 은 2026-11-02 에 지웠다. 그 판은 앞 판 저장소의 태그 tab5_monitor-hdmi-last)
#include "app.h"

#include <M5Unified.h>
#include <cstdio>

#include "esp_log.h"

static const char *TAG = "board";

constexpr int STABLE_MS           = 60000;       // 현황판이 이만큼 이어지면 "안정"으로 보고 멈춤 재시작 횟수를 지운다
constexpr int PANIC_STABLE_MS     = 30 * 60000;  // 패닉 횟수는 이만큼 이어져야 지운다(MN-D30 — 주기가 1분보다 긴 결정적 패닉도 "잇달아"로 세게)

// USB-A 5V(확장칩 0x44 의 P3). 현황판은 USB-A 를 쓰지 않는다 — 부팅 때 끈다(꽂힌 것이 있어도 데워지지 않게)
static void usb5v(bool on)
{
    const uint8_t BIT = 1u << 3;
    xSemaphoreTake(g_i2c_mu, portMAX_DELAY);
    bool ok = on ? M5.In_I2C.bitOn(0x44, 0x05, BIT, 400000) : M5.In_I2C.bitOff(0x44, 0x05, BIT, 400000);
    ok = M5.In_I2C.bitOff(0x44, 0x07, BIT, 400000) && ok;          // Hi-Z 해제
    ok = M5.In_I2C.bitOn(0x44, 0x03, BIT, 400000) && ok;           // 출력
    xSemaphoreGive(g_i2c_mu);
    if (!ok) ESP_LOGE(TAG, "usb5v(%d): i2c write failed", (int)on);
}

void board_session_install() { usb5v(false); }

// 세션 루프(MN-D31 · D36): 박동과 "LIVE 가 이어지면 재시작 · 패닉 횟수 지우기". 이것이 없으면 몇 주의 띄엄띄엄한 패닉 · 멈춤이 쌓여
//   정지 · 안전 모드가 된다(독립 검토 I4). 박동은 감시 태스크가 본다(SESSION_STUCK)
[[noreturn]] void board_session_run()
{
    printf("MON BOARD ready (usb-a 5v off)\n"); fflush(stdout);
    bool counted_stable = false, counted_panic = false;
    for (;;) {
        g_sess_beat_ms.store(now_ms());
        while (g_inj_sess_stuck.load()) vTaskDelay(pdMS_TO_TICKS(1000));   // 시험용(`fault sess`): 돌아오지 않는 호출 흉내 → 30초 뒤 재시작
        vTaskDelay(pdMS_TO_TICKS(500));
        const int64_t since = g_board_live_since_ms.load(), n = now_ms();
        if (since == 0) { counted_stable = counted_panic = false; continue; }   // LIVE 가 끊기면 다음 LIVE 에서 다시 센다
        if (!counted_stable && n - since > STABLE_MS) {
            counted_stable = true;
            if (cfg_get("rst_stuck", 0) != 0) cfg_set("rst_stuck", 0);
        }
        if (!counted_panic && n - since > PANIC_STABLE_MS) {
            counted_panic = true;
            if (cfg_get("rst_panic", 0) != 0) cfg_set("rst_panic", 0);
        }
    }
}
