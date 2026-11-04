// settings.cpp — NVS 설정과 재시작 횟수. 이름 공간 "tab5mon" 만 쓴다(MN-D3 — 다른 펌웨어가 저장한 것은 건드리지 않는다. 앞 판과 같은 이름이라 설정이 이어진다).
#include "app.h"

#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_system.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "monitor";

constexpr int NO_NVS_MIN_UP_MS    = 60000;       // NVS 를 못 쓸 때는 부팅 뒤 이만큼 지난 다음에만 재시작한다(고리가 돼도 1분에 한 번)
constexpr char NVS_NS[] = "tab5mon";

// ── 설정 저장 ────────────────────────────────────────────────────────────────
static bool s_nvs_ok = false;
bool cfg_init()
{
    s_nvs_ok = nvs_flash_init() == ESP_OK;
    return s_nvs_ok;
}
int cfg_get(const char *key, int def)
{
    if (!s_nvs_ok) return def;
    nvs_handle_t h; int32_t v = def;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) return def;
    nvs_get_i32(h, key, &v); nvs_close(h);
    return (int)v;
}
void cfg_set(const char *key, int v)
{
    if (!s_nvs_ok) return;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_i32(h, key, v); nvs_commit(h); nvs_close(h);
}
// 저장된 값이 범위 밖이면(옛 펌웨어가 검사 없이 저장했을 수 있다) 기본값을 쓴다
int cfg_get_in(const char *key, int def, int lo, int hi)
{
    const int v = cfg_get(key, def);
    if (rec::in_range(v, lo, hi)) return v;
    ESP_LOGW(TAG, "saved %s=%d is outside %d..%d -> using %d", key, v, lo, hi, def);
    return def;
}

// ── 재시작으로 복구(MN-D9 · MN-D12 · MN-D17) ───────────────────────────────────
// 까닭 묶음마다 NVS 한 칸에 횟수를 센다. 상한을 넘으면 재시작하지 않는다(재부팅 고리 방지). 횟수를 적지 못해도 하지 않는다.
//   rst_panel 3  패널이 감지되지 않음(소프트 리셋 뒤 가끔 실패한다 — 메모리 tab5-arduino-pitfalls). 패널이 잡히면 지운다
//   rst_video 3  부팅 때 꼭 있어야 하는 것을 못 만듦(디코더 · PPA · 버퍼 · RTOS 객체 · 태스크). 다 만들어지면 지운다
//   rst_stuck 4  멈춤(파이프라인 · 세션 · panel · 주사).        현황판이 1분 이어지면 지운다(board_session)
//   rst_panic    패닉 · 워치독으로 다시 켜진 횟수 — 우리가 청한 재시작이 아니라 부팅 때 센다(rec::crash_boot). 켜지자마자 4번 잇달아면 서고
//                15분 뒤 스스로 다시 해 본다(RL-D2). 전원을 켠 부팅과 "켜진 뒤 3분"(RL-D1 — board_session)이 지운다
//   up_mark      이번 실행이 넘은 시간 문턱(1: 3분 · 2: 30분 · 3: 3시간 · 4: 하루) — 다음 부팅이 읽고 0 으로(RL-D1 · RL-D3)
//   hist_n · h0~h7  부팅 기록(rec::hist_pack — 하드웨어 리셋 까닭 · IDF 까닭 · 그 전 실행 길이). 시리얼 `boot` 의 BOOTHIST(RL-D3)
//   (앞 판의 rst_lost — HDMI 동글 쪽 — 는 2026-11-02 에 지웠다. 기기의 NVS 에 남아 있어도 읽지 않는다)
RstSlot rst_slot(rec::Cause c)
{
    switch (c) {
    case rec::Cause::PANEL_INIT: return {"rst_panel", 3};
    case rec::Cause::VIDEO_INIT: case rec::Cause::SYS_INIT: return {"rst_video", 3};
    default: return {"rst_stuck", 4};
    }
}
// 돌아오면(false) 재시작하지 않은 것이다. 알림은 esp_rom_printf 로 낸다 — printf 의 잠금을 멈춘 태스크가 쥐고 있을 수 있다.
//   다음 부팅 줄의 last_rst= 가 같은 내용을 다시 알린다(이 줄을 놓쳐도 된다).
bool guarded_restart(rec::Cause cause)
{
    const RstSlot slot = rst_slot(cause);
    int n = 0;
    if (s_nvs_ok) {
        n = cfg_get(slot.key, 0);
        if (n >= slot.max) return false;
        cfg_set(slot.key, n + 1);
        if (cfg_get(slot.key, 0) != n + 1) return false;
        cfg_set("last_rst", (int)cause);
    } else if (now_ms() < NO_NVS_MIN_UP_MS) return false;
    // 부른 태스크의 스택이 가장 적게 남았던 양도 찍는다: 여기까지 오는 길(NVS 에 쓰기)이 그 태스크에서 가장 깊다 — 감시 태스크는 4KB 뿐이다
    esp_rom_printf("MON RECOVER %s -> restart %d/%d (stack free %u)\n", rec::cause_name(cause), n + 1, slot.max, (unsigned)uxTaskGetStackHighWaterMark(nullptr));
    vTaskDelay(pdMS_TO_TICKS(200));
    esp_restart();
    return true;
}
