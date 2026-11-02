// video.h — 현황판 JPEG 한 장을 화면으로: 풀기(하드웨어 JPEG) → 90° 돌리기(PPA) → 패널 버퍼 → 주사에 맞춰 교체.
//
// 패널 버퍼는 2장 또는 3장이다. 화면에 나가고 있지 않은 버퍼에 다 그린 다음 교체를 청한다
// → 그리는 도중의 그림이 화면에 나가지 않는다(MN-D10, 자세한 규칙은 video.cpp 머리말).
//
// 누가 부르나 — 이 규칙이 잠금을 대신한다:
//   "그리는 함수"(show_jpeg · begin/end · shot · bench)는 한 태스크(파이프라인)만 부른다.
//   다른 태스크가 불러도 되는 것은 설정(set_*)과 읽기뿐이다. 설정은 값만 적어 두고, 그 값을 디코더에 넣는 일은
//   다음 프레임을 풀기 직전에 파이프라인 태스크가 한다 → 풀고 있는 도중에 색 행렬이 바뀌는 일이 없다(검토 1차 I-4).
#pragma once
#include <cstddef>
#include <cstdint>

namespace video {

struct Frame {                  // 화면에 낸 그림 한 장의 정보
    int w = 0, h = 0;           // 그림 크기
    int idx = 0;                // (PPA) 가로 버퍼 번호
    int stride = 0;             // (PPA) 가로 버퍼에서 한 줄의 픽셀 수 — 디코더가 MCU 폭의 배수로 올린다
};

struct Overlay {                // 영상 위에 덧그릴 것(빈 문자열·false = 없음)
    int  orient = 0;            // 글자·단추의 방향(0 가로 · 1 세로 · 2 가로 뒤집힘 · 3 세로 뒤집힘) — 그 프레임의 영상과 같은 값이어야 한다
    char stats[160] = "";       // 왼쪽 아래 한 줄(진단, 영문·숫자만)
    bool heat_note = false;     // "뜨거워져 밝기와 속도를 낮췄습니다" 알림
    bool controls = false;      // 아래쪽 조작 띠(밝기·방향·흑백)와 그 위의 한 줄 요약 ↓
    int  bri_pct = 0;           //   밝기(%)
    bool mono = false;          //   흑백인가
    int  temp_c = -100;         //   칩 온도(°C). -100 = 모른다(적지 않는다)
    bool any() const { return stats[0] || controls || heat_note; }
};
// 그리는 중인 패널 버퍼(fb_idx)에 덧그린다. 무엇이든 그렸으면 true(그 버퍼의 캐시를 내보내야 한다). 버퍼의 주인이 부른다(파이프라인 태스크 또는 교체 태스크)
typedef bool (*overlay_fn_t)(int fb_idx, const Overlay &ov);

bool      init();                                   // 디코더 · PPA · 버퍼 · 패널. 실패하면 false
void      set_overlay_fn(overlay_fn_t fn);
int       panel_count();                            // 패널 버퍼 장수(2 또는 3)
uint16_t *panel(int idx);                           // 패널 버퍼(720×1280 RGB565). 없는 번호면 nullptr

// ── 그리는 함수(파이프라인 태스크만) ──
// 그림 한 장을 화면으로. flip = 180° 뒤집힌 방향. false = 화면에 내지 못했다(못 풀었다 · 앞의 그리기가 끝나지 않았다) — 화면은 그대로.
//   화면을 다 덮지 않는 그림(1280×720 이 아닌 것)의 테두리는 검게 둔다(앞서 그 버퍼에 안내 화면이 있었어도)
bool      show_jpeg(const uint8_t *jpg, size_t len, bool flip, const Overlay &ov, Frame &out);
int       begin();                                  // 직접 그릴 때: 그릴 버퍼의 번호. -1 = 지금은 그릴 수 없다(그러면 end 를 부르지 않는다)
void      end(bool show);                           // begin 이 성공한 뒤에만. true = 그 버퍼를 화면으로, false = 버린다
uint32_t  shot(const uint8_t **out);                // 지금 화면에 나가는 패널 버퍼(720×1280)를 JPEG 로. 반환 0 = 실패
void      bench(const uint8_t *jpg, size_t len);    // 이 1280×720 JPEG 한 장으로 풀기 · 돌리기 시간을 잰다(시리얼 BENCH 줄). 화면은 건드리지 않는다

// ── 설정(어느 태스크든) ──
void      set_full_range(bool on);                  // 색 범위: true = JPEG 전체 범위(0~255), false = 제한 범위(16~235)
bool      full_range();
void      set_mono(bool on);                        // 흑백(밝기만)
bool      mono();

// ── 읽기(어느 태스크든) ──
const char *last_path();                            // 가장 최근 그림이 실제로 간 길: ppa · -
uint32_t  last_decode_us();                         // 가장 최근 그림의 풀기 시간
uint32_t  last_render_us();                         // 가장 최근에 끝난 돌리기 시간
uint32_t  last_flip_us();                           // 교체를 청한 뒤 주사에 반영되기까지
uint32_t  refresh_count();                          // 주사 완료 인터럽트 누적(멈춤 감시가 본다)
int       isr_core();                               // 그 인터럽트가 도는 코어(-1 = 아직 한 번도 오지 않았다)
uint32_t  token_timeouts();                         // 앞의 그리기가 끝나지 않아 프레임을 포기한 횟수(부팅 뒤 누적)
uint32_t  header_rejects();                         // JPEG 머리가 깨져 하드웨어에 넣지 않고 버린 프레임 수(부팅 뒤 누적)
const char *last_header_error();                    // 그 마지막 까닭("ok" = 아직 없음)

// ── 고장 안내(영상 경로가 죽었을 때도 쓸 수 있는 길) ──
bool      freeze(int wait_ms);                      // 화면을 얼린다: 그리던 프레임이 끝나기를 기다리고 그 뒤의 그리기를 모두 막는다(되돌리는 길은 재시작뿐)
void     *primary_buffer();                         // M5GFX 가 잡은 0번 패널 버퍼. video::init 이 실패했어도 패널만 켜졌으면 있다
void      present_primary();                        // 0번 버퍼를 화면으로(캐시를 내보내고 교체). freeze 뒤에 부른다

// ── 시험용(시리얼 `fault …`) ──
bool      hold_token(int ms);                       // 그리기 토큰을 ms 동안 붙든다(앞의 그리기가 끝나지 않는 상황). false = 못 잡았다

}  // namespace video
