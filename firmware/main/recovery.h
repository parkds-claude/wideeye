// recovery.h — 고장에서 돌아오는 판단과 발열 정책. 순수 로직이다(기기 없이 시험: tools/test_recovery.cpp).
//   FreeRTOS·드라이버·시계가 없다. 시간은 부르는 쪽이 ms 로 넘긴다 — int64_t 로만 다룬다(기기의 long 은 32비트라
//   맥의 호스트 시험이 넘침을 못 잡는다: 옛 HDMI 판의 형식 고르기에서 실제로 겪었다).
//
// 왜 따로 뗐나(독립 검토 1차 I-1~I-6): "멈췄는가"의 판단이 세션 루프 안에 흩어져 있어 진행이 아니라 생존만 봤고,
//   그 판단을 하는 태스크가 함께 멈출 수 있었다. 판단을 한곳에 모아 시험하고, 실행은 독립된 감시 태스크가 한다(main.cpp).
#pragma once
#include <cstdint>

namespace rec {

inline bool in_range(int32_t v, int32_t lo, int32_t hi) { return v >= lo && v <= hi; }

// 재시작의 까닭. 값은 NVS(last_rst)에 적혀 다음 부팅 줄에 찍힌다 — 뒤에만 보탤 것.
//   (HDMI) 표시는 2026-11-02 에 지운 HDMI 모니터 기능의 까닭이다 — 이제 생기지 않지만 번호를 지키려고 남긴다(TB-D6).
enum class Cause : uint8_t {
    NONE = 0,
    PIPELINE_STUCK = 1,   // 파이프라인 태스크가 돌지 않는다(끝나지 않는 호출)
    SESSION_STUCK = 2,    // 세션 태스크(board_session)가 돌지 않는다
    PANEL_STUCK = 3,      // 화면 주사 완료 인터럽트가 오지 않는다
    NO_PROGRESS = 4,      // (HDMI) 프레임은 오는데 한 장도 화면에 내지 못한다
    REOPEN_STUCK = 5,     // (HDMI) 스트림 다시 열기를 청했는데 일어나지 않는다
    VIDEO_FATAL = 6,      // (HDMI) 띠 부품의 일꾼이 돌아오지 않는다
    CLOSE_FAILED = 7,     // (HDMI) 스트림을 닫지 못했다
    OPEN_FAILED = 8,      // (HDMI) 붙어 있는 동글을 여러 번 열어도 안 열린다
    DONGLE_LOST = 9,      // (HDMI) 붙어 있던 동글이 버스에서 사라졌다
    VIDEO_INIT = 10,      // 디코더·PPA·패널 버퍼를 잡지 못했다
    PANEL_INIT = 11,      // 패널이 감지되지 않았다
    UI_STUCK = 12,        // panel 태스크가 돌지 않는다(터치·밝기·온도 보호가 멈춘다 — I2C 가 돌아오지 않는 꼴)
    SYS_INIT = 13,        // 부팅 때 RTOS 객체 · 현황판 버퍼 · 태스크를 만들지 못했다
};
constexpr int CAUSE_COUNT = 14;
inline const char *cause_name(Cause c)
{
    switch (c) {
    case Cause::NONE: return "none";
    case Cause::PIPELINE_STUCK: return "pipeline-stuck";
    case Cause::SESSION_STUCK: return "session-stuck";
    case Cause::PANEL_STUCK: return "panel-stuck";
    case Cause::NO_PROGRESS: return "no-progress";
    case Cause::REOPEN_STUCK: return "reopen-stuck";
    case Cause::VIDEO_FATAL: return "video-fatal";
    case Cause::CLOSE_FAILED: return "close-failed";
    case Cause::OPEN_FAILED: return "open-failed";
    case Cause::DONGLE_LOST: return "dongle-lost";
    case Cause::VIDEO_INIT: return "video-init";
    case Cause::PANEL_INIT: return "panel-init";
    case Cause::UI_STUCK: return "ui-stuck";
    case Cause::SYS_INIT: return "sys-init";
    }
    return "?";
}

// ── 멈춤 감시(MN-D12) ───────────────────────────────────────────────────────
// 보는 것 넷: ① 파이프라인이 도는가 ② 세션(board_session)이 도는가 ③ panel 태스크가 도는가 ④ 화면 주사가 도는가.
//   현황판 그림을 못 푼 것은 여기 넣지 않는다(MN-D35 — 풀리지 않는 그림은 LIVE 를 늘리지 않을 뿐, 다음 그림을 기다린다).
enum class Action : uint8_t { NONE, RESTART };

struct WdCfg {
    int32_t pipe_stuck_ms  = 5000;    // 한 장 55ms 남짓에 견주어 넉넉하다. 오래 걸리는 일(bench · shot)은 beat 를 미래로 민다
    int32_t sess_stuck_ms  = 30000;   // 세션은 0.5초마다 돈다. 설정 저장(NVS)이 몇 번 겹쳐도 넉넉하다
    int32_t panel_stuck_ms = 3000;    // 주사 한 주기는 17.5ms
    int32_t ui_stuck_ms    = 10000;   // panel 태스크는 30ms 마다 돈다. 가장 긴 일(고장 안내 그리기)이 1초 안쪽
};
struct WdIn {
    int64_t  now_ms = 0;
    int64_t  pipe_beat_ms = 0;        // 파이프라인이 마지막으로 돈 때. 미래 값 = 그때까지는 오래 걸리는 일을 하는 중
    int64_t  sess_beat_ms = 0;        // 세션이 마지막으로 돈 때
    int64_t  ui_beat_ms = 0;          // panel 태스크가 마지막으로 돈 때
    uint32_t refresh_count = 0;       // 주사 완료 인터럽트 누적
};
struct WdState {
    bool     primed = false;
    uint32_t refresh_seen = 0;
    int64_t  refresh_ms = 0;          // 주사 완료 수가 마지막으로 바뀐 때
};
struct WdOut {
    Action  action = Action::NONE;
    Cause   cause = Cause::NONE;
    int64_t age_ms = 0;               // 얼마나 그 상태였나(기록용)
};

inline WdOut wd_step(WdState &s, const WdCfg &c, const WdIn &in)
{
    if (!s.primed) { s.primed = true; s.refresh_seen = in.refresh_count; s.refresh_ms = in.now_ms; }
    const int64_t pipe_age = in.now_ms - in.pipe_beat_ms, sess_age = in.now_ms - in.sess_beat_ms;
    if (pipe_age > c.pipe_stuck_ms) return {Action::RESTART, Cause::PIPELINE_STUCK, pipe_age};
    if (sess_age > c.sess_stuck_ms) return {Action::RESTART, Cause::SESSION_STUCK, sess_age};
    // 그림은 멀쩡히 나가도 이 태스크가 멈추면 과열 보호(MN-D18)가 조용히 사라진다 → 그림보다 보호가 먼저다
    if (in.now_ms - in.ui_beat_ms > c.ui_stuck_ms) return {Action::RESTART, Cause::UI_STUCK, in.now_ms - in.ui_beat_ms};

    if (in.refresh_count != s.refresh_seen) { s.refresh_seen = in.refresh_count; s.refresh_ms = in.now_ms; }
    else if (in.now_ms - s.refresh_ms > c.panel_stuck_ms) return {Action::RESTART, Cause::PANEL_STUCK, in.now_ms - s.refresh_ms};
    return {};
}

// ── 상한에 막힌 재시작을 되풀이해 청하지 않기 ─────────────────────────────────
// 감시가 재시작을 청했는데 횟수 상한에 막혔다 → 같은 까닭으로 1초마다 다시 청하지 않는다(NVS 를 읽고 같은 줄을 찍는 일의 되풀이).
// 그 고장이 풀리면(감시가 아무 조치도 청하지 않으면) 놓는다 — 횟수가 지워진 뒤 같은 고장이 또 생기면 다시 재시작해 본다.
//   (사전 검토 2a I-4: 예전에는 한 번 붙들면 그 부팅 내내 놓지 않았다 — 며칠 켜 둔 기기는 두 번째 고장에서 재시작하지 않았다)
struct GiveUp {
    Cause held = Cause::NONE;
    bool should_try(const WdOut &o)                   // 이 조치를 실행해 볼 것인가(RESTART 에만 뜻이 있다)
    {
        if (o.action == Action::NONE) held = Cause::NONE;
        return o.action == Action::RESTART && o.cause != held;
    }
    void refused(Cause c) { held = c; }               // 재시작이 상한에 막혔다
};

// ── 패닉·워치독으로 다시 켜진 횟수(사전 검토 2a I-2) ────────────────────────────
// 우리가 청한 재시작(guarded_restart)은 횟수를 세지만, 패닉은 IDF 가 곧장 재부팅한다 — 같은 입력이 같은 패닉을 내면 끝없이 되풀이된다
// (2026-10-13 의 깨진 JPEG 이 그런 입력이었다). 부팅 때 "이번이 패닉·워치독 재시작인가"를 보고 센다:
//   SAFE_AFTER 번 잇달아 → safe_mode(옛 판의 안전 모드 — 지금 펌웨어는 쓰지 않는다) · HALT_AFTER 번 잇달아 → 그리기를 시작하지 않고 안내를 띄운 채 선다.
// 전원을 켠 부팅과 "켜진 뒤 3분 넘게 돈 실행"(RL-D1)이 횟수를 지운다. 시리얼 `reboot`(소프트 재시작)은 세지 않는다 — 선 뒤에 `reboot` 하면
// 한 번 더 해 본다. 선 상태는 RETRY_MS 뒤 스스로 그 `reboot` 을 한다(RL-D2).
// RL-D1(2026-11-03): 전에는 "현황판 LIVE 가 30분 이어짐"만 횟수를 지웠다. 맥북은 자주 잠기고 닫혀(SLEEP — LIVE 아님) 30분이 좀처럼 이어지지 않아,
//   몇 시간 · 며칠 띄엄띄엄 난 워치독 리셋(맥북 덮개 닫기 · 다시 켜기 무렵 · 굽기 도구의 watchdog-reset)이 "잇달아"로 쌓여 4 를 넘자
//   탭5 가 사람이 손댈 때까지 섰다. 되풀이 재시작은 "켜지자마자 다시 죽는 것"이다 — 그 전 실행이 ALIVE_MS 를 넘겼으면(prev_alive)
//   이번 패닉은 잇달음의 첫 번째(1)로 센다. 비용: 3분보다 긴 주기로 되풀이되는 패닉은 서지 않고 계속 다시 켜진다(화면은 2초 끊기고 돌아온다 —
//   영영 서는 것보다 낫다고 본다). 되돌리기: prev_alive 를 늘 false 로 넘긴다(옛 판과 같다).
// fresh_start = 전원을 켠 부팅이다 → 우리가 청한 재시작의 횟수들(rst_stuck · rst_video · rst_panel)도 처음부터 센다.
//   상한을 다 쓰면 화면이 "전원을 껐다 켜 주세요"라고 한다. 그렇게 했는데 횟수가 남아 있으면 다음 고장에서 스스로 재시작하지 못한다
//   (2차 검토 I-5). 횟수는 "사람 없이 되풀이되는 재시작"을 막으려는 것이고, 전원은 사람이 켠다.
struct CrashBoot { int32_t count; bool safe_mode; bool halt; bool fresh_start; };
constexpr int32_t CRASH_SAFE_AFTER = 2, CRASH_HALT_AFTER = 4;
constexpr int64_t CRASH_ALIVE_MS = 3 * 60000;          // RL-D1 이만큼 돈 실행은 "켜지자마자 죽음"이 아니다
constexpr int64_t CRASH_RETRY_MS = 15 * 60000;         // RL-D2 선 뒤 이만큼 지나면 스스로 한 번 더 해 본다(소프트 재시작 — 세지 않는다)
inline CrashBoot crash_boot(int32_t stored, bool was_crash, bool power_on, bool prev_alive = false)
{
    int32_t n = stored < 0 ? 0 : stored;
    if (was_crash) { n = prev_alive ? 1 : (n < 1000 ? n + 1 : n); }
    else if (power_on || prev_alive) n = 0;            // 전원을 켰거나, 그 전 실행이 3분 넘게 돌고 우리가 청해 다시 켰다
    return {n, n >= CRASH_SAFE_AFTER, was_crash && n >= CRASH_HALT_AFTER, power_on && !was_crash};
}

// RL-D3 실행이 얼마나 이어졌나 — 부팅 기록의 "그 전 실행" 칸. 0: 3분 안 · 1: 3분 · 2: 30분 · 3: 3시간 · 4: 하루(문턱마다 한 번 NVS 에 적는다)
constexpr int64_t UP_MARK_MS[5] = {0, CRASH_ALIVE_MS, 30 * 60000LL, 3 * 3600000LL, 24 * 3600000LL};
inline int up_mark(int64_t uptime_ms)
{
    int m = 0;
    for (int i = 1; i < 5; ++i) if (uptime_ms >= UP_MARK_MS[i]) m = i;
    return m;
}
// 부팅 기록 한 칸(NVS 정수 하나): 하드웨어 리셋 까닭(soc_reset_reason — 원래 번호) · IDF 까닭(esp_reset_reason) · 그 전 실행의 up_mark
inline int32_t hist_pack(int hw, int why, int prev_up) { return (hw & 0xff) | ((why & 0xff) << 8) | ((prev_up & 0xf) << 16) | (1 << 20); }
struct HistEntry { bool valid; int hw, why, prev_up; };
inline HistEntry hist_unpack(int32_t v)
{
    if (!(v & (1 << 20))) return {false, 0, 0, 0};    // 빈 칸(0)과 가르는 표지
    return {true, v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xf};
}
constexpr int HIST_N = 8;
// 비정상 리셋인가 — IDF esp_reset_reason_t 의 번호(esp_system.h: PANIC 4 · INT_WDT 5 · TASK_WDT 6 · WDT 7 · CPU_LOCKUP 15). main.cpp 가 static_assert 로 맞춘다
inline bool crash_reason(int why) { return why == 4 || why == 5 || why == 6 || why == 7 || why == 15; }
// 부팅 기록 가운데 비정상 리셋 수 — STAT 의 crashes=. 3분보다 긴 주기로 되풀이되는 리셋은 RL-D1 로 서지 않으므로(독립 검토 2026-11-03)
//   맥이 이 수를 보고 알린다(kiosk.sh doctor — 3 이상)
inline int recent_crashes(const int32_t *hist, int n)
{
    int c = 0;
    for (int i = 0; i < n; ++i) { const HistEntry e = hist_unpack(hist[i]); if (e.valid && crash_reason(e.why)) ++c; }
    return c;
}

// ── 발열(MN-D18) ────────────────────────────────────────────────────────────
// 칩 온도(P4 내장 센서)로 네 단계. 올라갈 때는 문턱을 넘는 즉시, 내려올 때는 그 문턱보다 hyst 만큼 식은 뒤 한 단계씩
//   (문턱 근처에서 밝기가 오르내리지 않게). 읽지 못한 값(센서 없음 · 범위 밖)은 단계를 바꾸지 않는다.
enum class Heat : uint8_t { NORMAL = 0, WARM = 1, HOT = 2, CRITICAL = 3 };
inline const char *heat_name(Heat h) { return h == Heat::NORMAL ? "normal" : h == Heat::WARM ? "warm" : h == Heat::HOT ? "hot" : "critical"; }

struct HeatCfg {
    float warm_c = 65.0f, hot_c = 75.0f, crit_c = 85.0f, hyst_c = 5.0f;
};
inline bool temp_valid(float t) { return t > -40.0f && t < 150.0f; }       // NaN 은 두 비교 모두 거짓
inline Heat heat_step(Heat cur, float temp_c, const HeatCfg &c)
{
    if (!temp_valid(temp_c)) return cur;
    const Heat up = temp_c >= c.crit_c ? Heat::CRITICAL : temp_c >= c.hot_c ? Heat::HOT : temp_c >= c.warm_c ? Heat::WARM : Heat::NORMAL;
    if (up > cur) return up;
    if (cur == Heat::NORMAL) return cur;
    const float enter = cur == Heat::CRITICAL ? c.crit_c : cur == Heat::HOT ? c.hot_c : c.warm_c;
    return temp_c < enter - c.hyst_c ? (Heat)((uint8_t)cur - 1) : cur;
}
struct HeatLimit {
    int32_t bri_max;     // 백라이트 상한(0~255) — 사용자가 고른 밝기와 이것 중 낮은 쪽
    bool    video;       // false = 그림을 풀지 않고 안내 화면만
};
inline HeatLimit heat_limit(Heat h)
{
    switch (h) {
    case Heat::NORMAL:   return {255, true};
    case Heat::WARM:     return {150, true};
    case Heat::HOT:      return {70, true};
    case Heat::CRITICAL: return {20, false};
    }
    return {255, true};
}

// ── 현황판 모드(MN-D32·D33 · urgent-board 설계 2026-10-15 2-2) ──────────────────
// 살아 있음 = 화면에 그려진 그림의 "내용 시각"(받은 때 − 맥이 적은 나이)이 stale_ms 안이다. 줄이 왔다는 것이 아니다(독립 검토 C1).
// SLEEP = 맥이 screen 0 을 sleep_hold_ms 안에 보냈다(맥 화면 잠듦 · 잠김). 맥이 잠든 채 죽으면 SLEEP 이 끊겨 LOST 가 된다.
enum class Board : uint8_t { NONE = 0, LIVE = 1, LOST = 2, SLEEP = 3 };
inline const char *board_name(Board b) { return b == Board::NONE ? "none" : b == Board::LIVE ? "live" : b == Board::LOST ? "lost" : "sleep"; }
struct BoardCfg { int64_t stale_ms = 180000; int64_t sleep_hold_ms = 90000; int64_t wait_ms = 180000; int32_t lost_bri = 60;
                  int64_t wake_grace_ms = 20000; };   // 깨어난 뒤 이만큼은 낡은 그림을 LOST 로 치지 않는다(screen 1 이 그림보다 먼저 온다 — 검토 5)
struct BoardIn {
    int64_t now_ms = 0;
    int64_t boot_ms = 0;              // 현황판 모드가 시작된 때
    int64_t drawn_content_ms = 0;     // 화면에 그려진 그림의 내용 시각. 0 = 아직 그린 것이 없다
    int64_t screen_off_ms = 0;        // 마지막으로 screen 0 을 받은 때. 0 = 없음(screen 1 이 오면 0)
    int64_t wake_ms = 0;              // 마지막으로 screen 0 → 1 로 바뀐 때(0 = 없음)
};
inline Board board_state(const BoardCfg &c, const BoardIn &in)
{
    if (in.screen_off_ms != 0) {                                    // 맥이 끄라고 했다(화면 잠듦 · 잠김)
        if (in.now_ms - in.screen_off_ms <= c.sleep_hold_ms) return Board::SLEEP;
        return in.drawn_content_ms != 0 ? Board::LOST : Board::NONE;   // SLEEP 이 끊김 — screen 1 없이는 그림을 켜지 않는다(검토 초점 4):
    }                                                               //   잠긴 맥도 그림은 보내므로 내용은 싱싱하다 → LIVE 로 보면 메시지가 켜진다
    if (in.drawn_content_ms != 0 && in.now_ms - in.drawn_content_ms < c.stale_ms) return Board::LIVE;
    if (in.drawn_content_ms != 0 && in.wake_ms != 0 && in.now_ms - in.wake_ms < c.wake_grace_ms) return Board::LIVE;   // 깬 직후 — 새 그림이 오는 중
    return in.drawn_content_ms != 0 ? Board::LOST : Board::NONE;
}
// "현황판이 오지 않습니다"를 띄울 때인가 — LOST 이거나, 부팅 뒤 wait_ms 가 지나도 한 장도 없을 때
inline bool board_alarm(const BoardCfg &c, const BoardIn &in, Board b)
{
    return b == Board::LOST || (b == Board::NONE && in.now_ms - in.boot_ms >= c.wait_ms);
}
// 백라이트 상한. SLEEP 이면 끄고, 알림 중에는 조금 낮춘다(꺼진 탭5 가 잠듦과 고장을 가리지 못하게 · 검토 I12)
inline int32_t board_bri_max(const BoardCfg &c, Board b, bool alarm)
{
    if (b == Board::SLEEP) return 0;
    return alarm ? c.lost_bri : 255;
}
// 안내 화면에 적을 분: 그려진 내용에서(없으면 부팅에서) 지난 분
inline int32_t board_minutes(const BoardIn &in)
{
    const int64_t from = in.drawn_content_ms != 0 ? in.drawn_content_ms : in.boot_ms;
    const int64_t m = (in.now_ms - from) / 60000;
    return m < 0 ? 0 : m > 99999 ? 99999 : (int32_t)m;
}

// 방향(MN-D41 · 사용자 2026-10-18 "90도 회전이 가능하게 하자" · "화면터치하면 90도 회전도 나오도록"): 네 방향.
//   세로(1 · 3)면 맥이 세로 화면(540×960)을 시계 방향 90° 눕혀 1280×720 으로 보내므로 그림 길은 그대로이고
//   (뒤집힘 orient >= 2 만 본다), 글자 · 띠 · 터치가 방향을 따른다. 맥은 STAT 의 orient= 로 방향을 안다(urgent-board 설계 2026-10-18 D1 · D2 · D3).
inline int orient_for(int o) { return o & 3; }
// 조작 띠의 회전 단추: 90°씩(가로 → 세로 → 가로 뒤집힘 → 세로 뒤집힘)
inline int rotate_next(int o) { return (o + 1) & 3; }

// screen 신호(MN-D32 · 펌웨어 검토 2026-10-15 의 4): 맥은 30초마다 같은 값을 보낸다 — 화면을 다시 그리는 것은 값이 바뀔 때만.
//   0 → 1 이면 깬 때를 적는다(BoardIn.wake_ms — 깬 직후 유예). 돌려주는 값 = 꺼짐 ↔ 켜짐이 바뀌었다
struct ScreenSig { int64_t off_ms = 0; int64_t wake_ms = 0; };   // off_ms: 마지막 screen 0 의 때(0 = 켜짐)
inline bool screen_signal(ScreenSig &s, bool on, int64_t now_ms)
{
    const bool was_on = s.off_ms == 0;
    if (on) { if (!was_on) s.wake_ms = now_ms; s.off_ms = 0; }
    else s.off_ms = now_ms != 0 ? now_ms : 1;                       // 0 은 "켜짐"이라는 뜻이다
    return on != was_on;
}

// 현황판 화면 한 바퀴(MN-D35 · 펌웨어 검토 2026-10-15 의 1~3 · 검토 뒤 찾은 것): 무엇을 그릴지만 여기서 고르고, 그리기와 결과 알리기는 파이프라인이.
//   한 바퀴 = board_begin(요청 모으기) → board_try_new(새 그림이면 그려 보고 board_drawn) → board_pick(그릴 것) → board_drawn
enum class BoardDraw : uint8_t { NOTHING, PICTURE, WAIT, LOST, HOT };
struct BoardView {                    // 파이프라인이 바퀴를 넘겨 들고 있는 것
    BoardDraw on_screen = BoardDraw::NOTHING;   // 지금 화면에 있는 것(NOTHING = 모른다 — 부팅 · 자가 시험 그림이 덮음)
    uint32_t tried_gen = 0;           // 마지막으로 그려 본 그림의 세대 — 실패해도 다시 풀지 않는다(풀리지 않는 그림은 LIVE 를 늘리지 않는다)
    uint32_t ok_gen = 0;              // 마지막으로 그려 낸 세대 — 같은 그림을 다시 그리는 것은 보이는 칸이 이 세대일 때만(검토 뒤 찾은 것)
    int32_t minute = -1;              // 화면의 안내에 적힌 분
    bool want = false;                // 다시 그릴 것이 남았다(요청 · 안내를 그리지 못함 · 잠든 동안 온 요청)
    bool was_note = false;            // 앞 바퀴에 발열 알림을 띄울 때였다
    int orient = -1;                  // 앞 바퀴의 방향(-1 = 아직 모른다)
};
struct BoardTurn {
    bool hot = false;                 // 발열 치명 — 그림을 풀지 않는다(안내가 맨 앞)
    Board state = Board::NONE;
    bool alarm = false;               // board_alarm
    int32_t minute = 0;               // board_minutes
    uint32_t gen = 0;                 // 보이는 칸의 세대(0 = 받은 그림이 없다)
};
// 바퀴를 시작한다: 다시 그릴 까닭을 모은다 — 요청(조작 띠 · screen 바뀜) · 발열 알림이 사라짐(검토 2) · 자가 시험 그림이 덮음(검토 3) ·
//   방향이 바뀜(요청에 기대지 않는다 — 요청을 받은 바퀴가 옛 방향을 들고 있으면 옛 방향으로 그리고 끝났다. 2026-10-15 기기 시험)
inline void board_begin(BoardView &v, bool force, bool heat_note, bool played, int orient)
{
    if (force || (v.was_note && !heat_note) || orient != v.orient) v.want = true;
    v.was_note = heat_note; v.orient = orient;
    if (played) v.on_screen = BoardDraw::NOTHING;
}
// 새 그림을 그려 볼 때인가: 세대가 바뀌었고 내용이 싱싱하다. 낡았으면 해 본 것으로 친다(그 그림은 다시 보지 않는다)
inline bool board_try_new(BoardView &v, uint32_t gen, int64_t content_ms, int64_t now_ms, int64_t stale_ms)
{
    if (gen == 0 || gen == v.tried_gen) return false;
    v.tried_gen = gen;
    return now_ms - content_ms < stale_ms;
}
// 이번 바퀴에 그릴 것(새 그림은 board_try_new 가 따로 맡는다)
inline BoardDraw board_pick(const BoardView &v, const BoardTurn &k)
{
    if (k.hot) return v.want || v.on_screen != BoardDraw::HOT ? BoardDraw::HOT : BoardDraw::NOTHING;
    switch (k.state) {
    case Board::SLEEP: return BoardDraw::NOTHING;                   // 백라이트는 panel 태스크가 끈다. 요청(want)은 깰 때까지 남는다
    case Board::LIVE:
        return (v.want || v.on_screen != BoardDraw::PICTURE) && k.gen != 0 && v.ok_gen == k.gen ? BoardDraw::PICTURE : BoardDraw::NOTHING;
    case Board::LOST: case Board::NONE: break;
    }
    const BoardDraw d = k.alarm ? BoardDraw::LOST : BoardDraw::WAIT;
    return v.want || v.on_screen != d || (k.alarm && k.minute != v.minute) ? d : BoardDraw::NOTHING;
}
// 그리기 결과를 적는다. 안내는 그릴 수 없었던 것뿐이라(앞의 그리기가 안 끝남) 다음 바퀴에 다시. 그려 낸 적 있는 그림을 다시 그리다
//   실패하면 그 요청은 버린다 — 40ms 마다 실패를 되풀이하지 않게(검토 뒤 찾은 것 · 일시 실패면 다음 그림까지 낡음 — 미룬 사소 7).
//   새 그림이 실패한 것은 요청과 상관없다(요청은 남아 이번 바퀴의 안내 · 그림에 쓰인다)
inline void board_drawn(BoardView &v, BoardDraw what, bool ok, uint32_t gen, int32_t minute)
{
    if (!ok) {
        if (what != BoardDraw::PICTURE) v.want = true;
        else if (gen == v.ok_gen) v.want = false;
        return;
    }
    v.on_screen = what; v.want = false; v.minute = minute;
    if (what == BoardDraw::PICTURE) v.ok_gen = gen;
}

}  // namespace rec
