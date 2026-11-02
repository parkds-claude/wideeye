// ui.h — 현황판 그림이 없을 때의 안내 화면과, 그림 위에 덧그리는 작은 표시.
// 패널 버퍼(720×1280, 세로)에 직접 그린다 — 그리개를 버퍼에 묶고 회전을 걸어 보는 방향의 좌표로 쓴다.
// 그 버퍼의 주인(video 의 토큰을 쥔 쪽)만 부른다.
//
// 방향(orient, MN-D15): 0 가로(삼각대 나사가 아래) · 1 세로 · 2 가로 뒤집힘 · 3 세로 뒤집힘. 가로일 때 좌표는 1280×720, 세로일 때 720×1280.
//   이 모듈은 방향을 기억하지 않는다 — 부르는 쪽이 프레임마다 한 번 읽은 값을 넘긴다. 그래야 한 프레임 안에서 영상과 글자의 방향이
//   어긋나지 않는다(검토 1차 M-6: 예전에는 전역 값이라 그리는 도중에 바뀔 수 있었다).
#pragma once
#include "mon.h"
#include "video.h"

namespace ui {

inline int m5_rotation(int orient) { static const int R[4] = {3, 0, 1, 2}; return R[orient & 3]; }   // M5GFX 회전 번호(터치 좌표도 이것을 따른다)
inline int width(int orient)  { return (orient & 1) ? PANEL_W : SCREEN_W; }
inline int height(int orient) { return (orient & 1) ? PANEL_H : SCREEN_H; }

void init();                                                        // 그리개를 패널 버퍼에 묶는다(video::init 뒤에)

// 안내 화면 한 장(버퍼 전체를 덮는다). detail = 아래에 작게 적을 영문·숫자(없으면 "")
enum class Screen : uint8_t {
    TOO_HOT,        // 뜨거워 쉬는 중
    BOARD_WAIT,     // 켜진 뒤 아직 그림이 없다(MN-D32)
    BOARD_LOST,     // 그림이 끊겼다 — detail 에 "N min"
};
void draw_status(int fb_idx, int orient, Screen scr, const char *detail);
bool draw_overlay(int fb_idx, const video::Overlay &ov);            // 발열 알림 · 진단 한 줄 · 조작 띠. 그린 것이 있으면 true

// 조작 띠의 단추(화면을 한 번 치면 뜬다). 좌표는 그 방향의 화면 기준(width × height)
enum class Btn : uint8_t { NONE, DARKER, BRIGHTER, ROTATE, MONO, CLOSE };
Btn hit(int orient, int x, int y);

// 고장 안내. 그림 경로(video::init · 토큰 · 버퍼 교체)에 기대지 않는다: M5GFX 가 잡은 0번 버퍼(primary_fb)에 바로 그린다.
//   그린 뒤 video::present_primary() 를 부른다.
enum class Fault : uint8_t { STUCK, INIT };
void draw_fault(void *primary_fb, int orient, Fault f);

}  // namespace ui
