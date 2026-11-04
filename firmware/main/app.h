// app.h — WideEye 탭5 펌웨어의 파일들이 함께 쓰는 상태와 서로에게 내놓는 함수.
//
//   settings.cpp       NVS 설정 · 재시작 횟수(guarded_restart)      board_session.cpp  USB-A 전원 · 세션 박동 · 횟수 지우기
//   pipeline.cpp       현황판 그림 → 풀기·돌리기 → 화면(그리는 것은 여기뿐)   sercmd.cpp   시리얼 명령 · 현황판 그림 받기 · 고장 넣기
//   main.cpp           부팅 · panel 태스크 · 감시 태스크              recovery.h         복구·발열·현황판의 판단(순수 로직, 호스트 시험)
//
// 이름: g_ 는 여러 파일(=여러 태스크)이 함께 쓰는 것, s_ 는 한 파일 안의 것. 함께 쓰는 값은 원자 변수이거나, 옆에 적힌 잠금 아래에서만 만진다.
// 태스크 사이의 약속: 값은 원자 변수로 넘기고, 그리기는 파이프라인만.
#pragma once

#include <atomic>
#include <climits>
#include <cstddef>
#include <cstdint>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "mon.h"
#include "recovery.h"
#include "ui.h"

// 저장되는 값의 범위(검토 1차 I-7). 시리얼로 받을 때와 NVS 에서 읽을 때 같은 범위로 본다 — 범위 밖 값은 저장하지 않고, 읽혔으면 기본값을 쓴다.
namespace lim {
constexpr int BRI_LO = 5, BRI_HI = 255;
}

inline SemaphoreHandle_t  g_i2c_mu = nullptr;                       // 내부 I2C(터치 · IMU · USB 전원 확장칩)는 한 번에 한 태스크만

// 설정(NVS) · 요청
inline std::atomic<bool> g_osd{false};
inline std::atomic<int>  g_bri{200};                                // 사용자가 고른 밝기
inline std::atomic<int>  g_bri_eff{0};                              // 지금 실제로 낸 밝기(발열 · 현황판 상태로 낮췄을 수 있다)
inline std::atomic<int>  g_orient{0};                               // 화면 방향: 0 가로 · 1 세로 · 2 가로 뒤집힘 · 3 세로 뒤집힘 (MN-D15 · D41)
inline std::atomic<bool> g_req_shot{false}, g_req_bench{false}, g_req_stat{false}, g_force_redraw{false};
inline std::atomic<int64_t> g_osd_until_ms{0};                       // 조작 띠를 이때까지 보인다(0 = 안 보임)
inline std::atomic<int>  g_inj_x{-1}, g_inj_y{-1};                   // 시리얼 `t x y` 로 넣은 터치(시험용)

// 건강 — 감시 태스크가 읽는다(잠금 없이)
inline std::atomic<int64_t>  g_pipe_beat_ms{0};                      // 파이프라인이 마지막으로 돈 때(미래 값 = 그때까지는 오래 걸리는 일을 하는 중)
inline std::atomic<int64_t>  g_sess_beat_ms{0};                      // 세션(board_session)이 마지막으로 돈 때
inline std::atomic<int64_t>  g_ui_beat_ms{0};                        // panel 태스크가 마지막으로 돈 때
inline std::atomic<bool>     g_halt{false};                          // 고장 안내를 띄웠다 → 파이프라인은 더 그리지 않는다
inline std::atomic<int>      g_fault{0};                             // 띄울 고장 안내(ui::Fault + 1, 0 = 없음) — panel 태스크가 한 번 그린다

// 발열(MN-D18) — panel 태스크가 정하고 파이프라인이 따른다
inline std::atomic<int>     g_temp_x10{-990};                        // 칩 온도 ×10(고르게 한 값). -990 = 읽지 못했다
inline std::atomic<int>     g_board_x10{-990};                       // 기판 온도 ×10(IMU 의 온도계 — 참고용)
inline std::atomic<int>     g_heat{0};                               // rec::Heat
inline std::atomic<bool>    g_video_paused{false};                   // 치명 단계 — 풀지 않는다
inline std::atomic<int64_t> g_heat_note_until_ms{0};
inline std::atomic<int64_t> g_touch_ms{0};                           // 마지막으로 화면을 만진 때

// 자가 시험(MN-D11): 시리얼로 올린 JPEG 한 장을 되풀이해 낸다 — 맥 없이 풀기 · 돌리기 · 교체를 확인하고 속도를 잰다
inline constexpr size_t TEST_JPG_CAP = 640 * 1024;
inline uint8_t *g_tj = nullptr; inline size_t g_tj_len = 0, g_tj_expect = 0;
inline std::atomic<bool>    g_tj_ready{false}, g_tj_busy{false};    // busy = 파이프라인이 지금 그 버퍼를 읽고 있다
inline std::atomic<int64_t> g_play_until_ms{0};
inline std::atomic<int>     g_play_fps{0};                          // 0 = 낼 수 있는 만큼

// 현황판(MN-D31~36 — urgent-board 설계 2026-10-15). 그림은 받는 칸에 쓰고 다 받으면 보이는 칸과 바꾼다(sercmd).
//   파이프라인이 보이는 칸을 읽는 동안 g_board_busy. 번호(g_board_seq)는 맨 나중에 바뀐다 — 파이프라인은 번호가 바뀐 것을 보고 그린다
inline constexpr size_t BOARD_CAP = 512 * 1024;                     // brx::CAP 과 같다
inline uint8_t              *g_board_buf[2] = {nullptr, nullptr};   // PSRAM(128바이트 정렬), 부팅 때 잡는다
inline size_t                g_board_len[2] = {0, 0};
inline std::atomic<int>      g_board_show{-1};                       // 보이는 칸(-1 = 아직 없음)
inline std::atomic<uint32_t> g_board_seq{0};                         // 보이는 칸의 그림 번호(맥이 붙인 id — STAT bseq 로 맥에 돌려준다)
inline std::atomic<uint32_t> g_board_gen{0};                         // 보이는 칸이 바뀔 때마다 +1(sercmd 가 맨 나중에 쓴다). 새 그림은 이것으로 안다 —
                                                                    //   맥이 다시 떠서 번호가 겹쳐도(검토 6). 0 = 받은 그림이 없다
inline std::atomic<int64_t>  g_board_content_ms{0};                  // 보이는 칸 그림의 내용 시각(받은 때 − 맥이 적은 나이)
inline std::atomic<bool>     g_board_busy{false};
inline std::atomic<uint32_t> g_board_drawn_seq{0};                   // 마지막으로 화면에 그린 번호(STAT bseq)
inline std::atomic<int64_t>  g_board_drawn_content_ms{0};            // 화면에 그려진 그림의 내용 시각 — LIVE 판단은 이 값으로(MN-D33)
inline std::atomic<int64_t>  g_screen_off_ms{0};                     // 마지막 screen 0 (0 = screen 1). 저장하지 않는다
inline std::atomic<int64_t>  g_screen_on_ms{0};                      // 마지막으로 screen 0 → 1 로 바뀐 때(0 = 없음) — 깬 직후 유예(rec::BoardIn.wake_ms)
inline std::atomic<int64_t>  g_board_stale_ms{180000};               // 시험용으로 줄인다(`board stale`) — 저장하지 않는다
inline std::atomic<int>      g_board_state{0};                       // rec::Board — 파이프라인이 정하고 panel 태스크·STAT 이 읽는다
inline std::atomic<bool>     g_board_alarm{false};                   // "현황판이 오지 않습니다"를 띄우는 중
inline std::atomic<int64_t>  g_board_live_since_ms{0};               // LIVE 가 시작된 때(0 = LIVE 아님) — 횟수 지우기(MN-D36)
inline std::atomic<int64_t>  g_boot_ms{0};
inline std::atomic<int>      g_recent_crashes{0};        // 부팅 기록 8칸 가운데 비정상 리셋 수 — STAT crashes=(RL-D3)
inline std::atomic<uint32_t> g_board_decfail{0};                     // 풀지 못한 현황판 그림(부팅 뒤 누적 — STAT bdec)

// 고장 넣기(시리얼 `fault …` — 복구 경로를 기기에서 확인하는 용도. 평소에는 모두 0)
inline std::atomic<bool> g_inj_pipe_stuck{false}, g_inj_sess_stuck{false}, g_inj_ui_stuck{false};
inline std::atomic<int>  g_inj_dec{0};                               // 남은 횟수(-1 = 끌 때까지)
inline std::atomic<int>  g_inj_temp_x10{INT_MIN};                    // 온도를 이 값으로 친다(INT_MIN = 실제 값)
inline bool take_inject(std::atomic<int> &a)
{
    const int v = a.load();
    if (v == 0) return false;
    if (v > 0) a.fetch_sub(1);
    return true;
}

inline void raise_fault(ui::Fault f) { g_halt.store(true); g_fault.store((int)f + 1); }

// ── settings.cpp ─────────────────────────────────────────────────────────────
bool cfg_init();                                                   // NVS 를 연다(못 열어도 지우지 않는다 — MN-D3). false = 설정 저장 없이 돈다
int  cfg_get(const char *key, int def);
void cfg_set(const char *key, int v);
int  cfg_get_in(const char *key, int def, int lo, int hi);          // 저장된 값이 범위 밖이면 기본값
struct RstSlot { const char *key; int max; };
RstSlot rst_slot(rec::Cause c);                                     // 그 까닭의 재시작 횟수가 적히는 NVS 칸과 상한
bool guarded_restart(rec::Cause cause);                             // 상한 안이면 횟수를 적고 재시작한다(돌아오지 않는다). false = 하지 않았다

// ── board_session.cpp ────────────────────────────────────────────────────────
void board_session_install();                                       // USB-A 5V 를 끈다(현황판은 쓰지 않는다)
[[noreturn]] void board_session_run();                              // 세션 박동 · 횟수 지우기 — app_main 의 태스크에서

// ── pipeline.cpp · sercmd.cpp ────────────────────────────────────────────────
void pipeline_task(void *);
void serial_task(void *);

// ── main.cpp ─────────────────────────────────────────────────────────────────
bool spawn(TaskFunction_t fn, const char *name, uint32_t stack, UBaseType_t prio, BaseType_t core, uint32_t stack_caps);   // false = 태스크를 띄우지 못했다
const char *boot_line();                                            // 부팅 줄의 본문(MON BOOT 뒤) — 시리얼 `boot` 가 BOOTINFO 로 다시 찍는다
[[noreturn]] void halt_with_notice(const char *what, ui::Fault fault, int orient);
