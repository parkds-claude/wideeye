// mon.h — WideEye 탭5 펌웨어 공용 상수 · 통계.
#pragma once
#include <cstddef>
#include <cstdint>

#include "esp_timer.h"

#define PANEL_W     720          // 물리 패널(세로)
#define PANEL_H     1280
#define SCREEN_W    1280         // 가로로 볼 때의 화면(맥이 보내는 현황판 그림의 크기)
#define SCREEN_H    720
#define MAX_SRC_W   1920         // 풀 수 있는 가장 큰 그림(자가 시험 `put` 포함)
#define MAX_SRC_H   1088         // 1080 을 16 의 배수로 올린 값(4:2:0 MCU)

inline int64_t now_ms() { return esp_timer_get_time() / 1000; }       // 부팅 뒤 흐른 시간(64비트 — 되감기지 않는다)

namespace mon {

struct Stats {                   // 5초 창 누적(파이프라인 태스크만 쓴다. 읽는 쪽은 스냅샷)
    uint32_t in = 0;             // 그려 보려고 집은 그림
    uint32_t out = 0;            // 화면에 낸 그림
    uint32_t dec_fail = 0;
    uint64_t dec_us = 0, rot_us = 0, flip_us = 0, busy_us = 0;   // 풀기 · 돌리기 · 교체 대기 · 한 장에 파이프라인이 붙잡힌 시간
    uint64_t bytes = 0; uint32_t bytes_max = 0;
};

}  // namespace mon
