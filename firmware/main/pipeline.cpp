// pipeline.cpp — 현황판 그림 → 풀기·돌리기 → (덧그림) → 화면. 화면에 그리는 태스크는 이것 하나다(video.h 의 규칙).
// 안내 화면 · 자가 시험 재생 · 화면 찍기 · 벤치 · STAT 줄도 이 태스크가 낸다.
#include "app.h"

#include <cstdio>
#include <cstring>

#include "esp_heap_caps.h"
#include "mbedtls/base64.h"

#include "video.h"

constexpr int STAT_PERIOD_MS      = 5000;

// ── 화면 찍기 · STAT 줄 · 오래 걸리는 일 ──────────────────────────────────────
static void send_shot()
{
    const uint8_t *out = nullptr;
    const uint32_t len = video::shot(&out);
    if (!len) { printf("SHOT FAIL\n"); fflush(stdout); return; }
    printf("SHOT BEGIN %u panel-o%d\n", (unsigned)len, g_orient.load() & 3);     // 도구가 방향에 맞춰 돌려 저장한다
    static unsigned char line[4200];
    for (uint32_t off = 0; off < len; off += 3072) {
        const size_t n = len - off < 3072 ? len - off : 3072; size_t olen = 0;
        mbedtls_base64_encode(line, sizeof line, &olen, out + off, n); line[olen] = 0;
        printf("B64:%s\n", (const char *)line);
        if ((off / 3072) % 8 == 7) vTaskDelay(1);                   // 긴 출력 동안 같은 코어의 idle 이 굶지 않게
    }
    printf("SHOT END\n"); fflush(stdout);
}
// 맥(urgent-board tools/tab5_link.py)은 board= bseq= scr= bage= 를 꼭 읽고 temp= orient= 를 읽는다. 나머지는 진단용이다
static void print_stat(const mon::Stats &st, int64_t win_ms)
{
    const float sec = win_ms > 0 ? win_ms / 1000.0f : 1.0f;
    const uint32_t n = st.out ? st.out : 1, m = st.in ? st.in : 1;
    printf("STAT up=%lld path=%s in=%.1f out=%.1f decfail=%u dec_ms=%.1f rot_ms=%.1f flip_ms=%.1f busy_ms=%.1f kb=%u kbmax=%u "
           "tokto=%u rej=%u rejwhy=%s temp=%.1f tbrd=%.1f heat=%s bri=%d heap_int=%u blk=%u psram=%u "
           "board=%s bseq=%u scr=%d bage=%d bdec=%u orient=%d\n",
           (long long)(now_ms() / 1000), video::last_path(),
           st.in / sec, st.out / sec, (unsigned)st.dec_fail,
           st.dec_us / 1000.0f / n, st.rot_us / 1000.0f / n, st.flip_us / 1000.0f / n, st.busy_us / 1000.0f / n,
           (unsigned)(st.bytes / m / 1024), (unsigned)(st.bytes_max / 1024),
           (unsigned)video::token_timeouts(), (unsigned)video::header_rejects(), video::last_header_error(),
           g_temp_x10.load() / 10.0f, g_board_x10.load() / 10.0f, rec::heat_name((rec::Heat)g_heat.load()), g_bri_eff.load(),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
           rec::board_name((rec::Board)g_board_state.load()), (unsigned)g_board_drawn_seq.load(), (int)(g_screen_off_ms.load() == 0),
           g_board_drawn_content_ms.load() ? (int)((now_ms() - g_board_drawn_content_ms.load()) / 1000) : -1, (unsigned)g_board_decfail.load(),
           g_orient.load());     // 방향 — 맥이 세로면 세로 화면을 눕혀 보낸다(MN-D41). 칸이 없으면 맥은 가로로 본다
    fflush(stdout);
}
// 오래 걸리는 일(bench · shot) 동안 멈춤 감시가 물지 않게 "이때까지는 일하는 중"이라고 적어 둔다
static void run_long(int allow_ms, void (*fn)(void *), void *arg)
{
    g_pipe_beat_ms.store(now_ms() + allow_ms);
    fn(arg);
    fflush(stdout);
    g_pipe_beat_ms.store(now_ms());
}

// ── 파이프라인 태스크 ────────────────────────────────────────────────────────
// 한 바퀴: ① 자가 시험 재생(play_test_frame) 또는 현황판 단계(board_step) ② 요청 처리 · 5초마다 STAT(serve_requests)
namespace {

// 한 바퀴 동안 바뀌지 않는 것. 바퀴를 시작할 때 한 번만 읽는다 — 그림의 각도와 글자·단추의 방향이 한 장 안에서 어긋나지 않게(검토 1차 M-6)
struct Turn { int orient; bool flip, controls, heat_note; };
Turn read_turn()
{
    Turn t;
    const int64_t n = now_ms();
    t.orient = g_orient.load() & 3; t.flip = t.orient >= 2;
    t.controls = n < g_osd_until_ms.load(); t.heat_note = n < g_heat_note_until_ms.load();
    return t;
}

// 태스크가 바퀴를 넘겨 가며 들고 있는 것(이 태스크만 만진다)
struct Pipe {
    mon::Stats st;
    int64_t win_t0 = 0, play_next_us = 0;
    char stats_txt[160] = "";
};

void fill_overlay(const Pipe &p, const Turn &t, video::Overlay &ov)
{
    ov.orient = t.orient;
    if (g_osd.load()) snprintf(ov.stats, sizeof ov.stats, "%s", p.stats_txt);
    ov.heat_note = t.heat_note;
    if (t.controls) {
        ov.controls = true; ov.bri_pct = g_bri.load() * 100 / 255; ov.mono = video::mono();
        const int t10 = g_temp_x10.load();
        ov.temp_c = t10 > -400 ? (t10 + 5) / 10 : -100;
    }
}
// 방금 화면에 낸 한 장을 창 누적에 더한다(t0 = 그 장을 풀기 시작한 때)
void count_shown(Pipe &p, int64_t t0_us)
{
    p.st.busy_us += esp_timer_get_time() - t0_us; p.st.dec_us += video::last_decode_us(); p.st.rot_us += video::last_render_us();
    p.st.flip_us += video::last_flip_us(); ++p.st.out;
}

// ① 자가 시험 재생: 올려 둔 JPEG 을 현황판 그림처럼 낸다. 실패는 현황판 상태에 넣지 않는다 — 시험 입력이다
void play_test_frame(Pipe &p, const Turn &t)
{
    g_tj_busy.store(true);
    if (g_tj_ready.load()) {                                        // 그사이 새로 올리기 시작했으면(put) 건드리지 않는다
        if (g_req_bench.exchange(false)) run_long(60000, [](void *) { video::bench(g_tj, g_tj_len); }, nullptr);
        video::Overlay ov;
        fill_overlay(p, t, ov);
        video::Frame f;
        const int64_t t0 = esp_timer_get_time();
        ++p.st.in; p.st.bytes += g_tj_len; p.st.bytes_max = (uint32_t)g_tj_len;
        if (!take_inject(g_inj_dec) && video::show_jpeg(g_tj, g_tj_len, t.flip, ov, f)) count_shown(p, t0);
        else ++p.st.dec_fail;
    }
    g_tj_busy.store(false);
    const int fps = g_play_fps.load();
    if (fps > 0) {                                                  // 정해진 간격으로
        const int64_t period = 1000000 / fps, now_us = esp_timer_get_time();
        if (p.play_next_us < now_us - period) p.play_next_us = now_us;
        p.play_next_us += period;
        if (p.play_next_us > now_us) vTaskDelay(pdMS_TO_TICKS((p.play_next_us - now_us) / 1000));
    } else vTaskDelay(1);                                           // 쉬지 않고 돌면 같은 코어의 idle 이 굶어 워치독이 문다
}

// ② 현황판(MN-D32~35): 무엇을 그릴지는 rec::board_* 가 고르고(호스트 시험), 여기서는 그리고 결과를 알린다.
//   g_force_redraw 는 여기서만 소비한다. 그리기 실패는 감시에 넣지 않는다(MN-D35 — 자가 시험 play 와 같다).
//   풀지 못한 세대는 다시 풀지 않는다 — 풀리지 않는 그림은 LIVE 를 늘리지 않는다(MN-D33)
rec::BoardView s_bv;

// 보이는 칸의 그림을 그린다. g_board_busy 를 건 "다음에" 칸을 읽는다(검토 덧붙임) — sercmd 는 busy 가 아닐 때 보이지 않는 칸에만 쓴다
bool draw_board(Pipe &p, const Turn &t)
{
    g_board_busy.store(true);
    const int idx = g_board_show.load();
    if (idx < 0) { g_board_busy.store(false); return false; }      // 받은 그림이 없다(세대 0 이면 부르지 않는다)
    video::Overlay ov;
    fill_overlay(p, t, ov);
    video::Frame f;
    const int64_t t0 = esp_timer_get_time();
    ++p.st.in; p.st.bytes += g_board_len[idx];
    const bool ok = !take_inject(g_inj_dec) && video::show_jpeg(g_board_buf[idx], g_board_len[idx], t.flip, ov, f);
    g_board_busy.store(false);
    if (ok) count_shown(p, t0);
    else { ++p.st.dec_fail; g_board_decfail.fetch_add(1); }
    return ok;
}
// 안내 화면 한 장 — 조작 띠 · 발열 알림도 함께(검토 1: 띠를 안 그리면 보이지 않는 단추를 누르게 된다). false = 지금 그릴 수 없다(앞의 그리기가 안 끝남)
bool board_screen(Pipe &p, const Turn &t, ui::Screen scr, const char *detail)
{
    const int b = video::begin();
    if (b < 0) return false;
    ui::draw_status(b, t.orient, scr, detail);
    if (t.controls || t.heat_note) { video::Overlay ov; fill_overlay(p, t, ov); ui::draw_overlay(b, ov); }
    video::end(true);
    return true;
}
void board_step(Pipe &p, const Turn &t, int64_t n, bool played)
{
    rec::BoardCfg c; c.stale_ms = g_board_stale_ms.load();
    const bool hot = g_video_paused.load();
    rec::board_begin(s_bv, g_force_redraw.exchange(false), t.heat_note, played, t.orient);
    // 새 그림: 세대 · 번호 · 내용 시각을 칸보다 먼저 읽는다(검토 덧붙임). 내용이 싱싱하면 그린다 — SLEEP 이어도(깨어날 때 지금 그림이 있게).
    //   발열 치명 동안은 풀지 않는다(세대를 해 본 것으로 치지 않아 식으면 가장 새 그림을 그린다)
    const uint32_t gen = g_board_gen.load(), seq = g_board_seq.load();
    const int64_t content = g_board_content_ms.load();
    if (!hot && rec::board_try_new(s_bv, gen, content, n, c.stale_ms)) {
        const bool ok = draw_board(p, t);
        rec::board_drawn(s_bv, rec::BoardDraw::PICTURE, ok, gen, s_bv.minute);
        if (ok) { g_board_drawn_seq.store(seq); g_board_drawn_content_ms.store(content); }
    }
    // 상태는 발열 치명이어도 센다(검토 9 — STAT · 밝기 · 횟수 지우기가 멈추지 않게)
    rec::BoardIn in;
    in.now_ms = n; in.boot_ms = g_boot_ms.load(); in.drawn_content_ms = g_board_drawn_content_ms.load();
    in.screen_off_ms = g_screen_off_ms.load(); in.wake_ms = g_screen_on_ms.load();
    const rec::Board b = rec::board_state(c, in);
    const bool alarm = rec::board_alarm(c, in, b);
    g_board_state.store((int)b); g_board_alarm.store(alarm);
    if (b == rec::Board::LIVE) { if (g_board_live_since_ms.load() == 0) g_board_live_since_ms.store(n); }
    else g_board_live_since_ms.store(0);
    // 그릴 것
    rec::BoardTurn k;
    k.hot = hot; k.state = b; k.alarm = alarm; k.minute = rec::board_minutes(in); k.gen = gen;
    const rec::BoardDraw d = rec::board_pick(s_bv, k);
    bool ok = false;
    switch (d) {
    case rec::BoardDraw::NOTHING: return;
    case rec::BoardDraw::PICTURE: ok = draw_board(p, t); break;     // 같은 그림을 덧그림(조작 띠 · 발열 알림 · 방향)과 함께 다시
    case rec::BoardDraw::HOT: {
        char dt[16]; snprintf(dt, sizeof dt, "%d C", (g_temp_x10.load() + 5) / 10);
        ok = board_screen(p, t, ui::Screen::TOO_HOT, dt);
        break;
    }
    case rec::BoardDraw::WAIT: ok = board_screen(p, t, ui::Screen::BOARD_WAIT, ""); break;
    case rec::BoardDraw::LOST: {
        char dt[24]; snprintf(dt, sizeof dt, "%d min", (int)k.minute);
        ok = board_screen(p, t, ui::Screen::BOARD_LOST, dt);
        break;
    }
    }
    rec::board_drawn(s_bv, d, ok, gen, k.minute);
}

// ③ 시리얼이 남긴 요청(화면 찍기 · 벤치 · STAT)과 5초마다의 STAT 줄. 그림 사이에 한다
void serve_requests(Pipe &p, bool playing, int64_t n)
{
    if (g_req_shot.exchange(false)) run_long(20000, [](void *) { send_shot(); }, nullptr);
    if (!playing && g_req_bench.exchange(false)) {                  // 재생 중이 아닐 때의 벤치(재생 중이면 play_test_frame 이 한다)
        g_tj_busy.store(true);
        if (g_tj_ready.load()) { run_long(60000, [](void *) { video::bench(g_tj, g_tj_len); }, nullptr); g_force_redraw.store(true); }
        else printf("BENCH FAIL no test jpeg (upload one with put)\n");
        g_tj_busy.store(false);
        fflush(stdout);
    }
    if (n - p.win_t0 >= STAT_PERIOD_MS || g_req_stat.exchange(false)) {
        const float sec = (n - p.win_t0 > 0 ? n - p.win_t0 : 1) / 1000.0f;
        const uint32_t o = p.st.out ? p.st.out : 1;
        snprintf(p.stats_txt, sizeof p.stats_txt, "%s  in %.1f  out %.1f  %s %.1f ms  %.0f C", rec::board_name((rec::Board)g_board_state.load()),
                 p.st.in / sec, p.st.out / sec, video::last_path(), (p.st.dec_us + p.st.rot_us) / 1000.0f / o, g_temp_x10.load() / 10.0f);
        print_stat(p.st, n - p.win_t0);
        p.st = mon::Stats(); p.win_t0 = n;
    }
}

}  // namespace

void pipeline_task(void *)
{
    static Pipe p;                                                  // 스택(8KB)에 두지 않는다
    p.win_t0 = now_ms();
    bool board_played = false;                                      // 앞 바퀴에 자가 시험 그림을 냈다
    for (;;) {
        if (now_ms() > g_pipe_beat_ms.load()) g_pipe_beat_ms.store(now_ms());   // 살아 있다(멈춤 감시가 본다)
        if (g_halt.load()) {                                                    // 고장 안내가 떠 있다 — 그 위에 그리지 않는다(화면 찍기만 듣는다)
            if (g_req_shot.exchange(false)) run_long(20000, [](void *) { send_shot(); }, nullptr);
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        while (g_inj_pipe_stuck.load()) vTaskDelay(pdMS_TO_TICKS(1000));        // 시험용: 돌아오지 않는 호출 흉내

        bool playing = g_tj_ready.load() && now_ms() < g_play_until_ms.load();
        vTaskDelay(pdMS_TO_TICKS(playing ? 1 : 40));
        const Turn t = read_turn();                                 // 쉰 "뒤에" 읽는다 — 쉬는 사이 온 요청(방향 · 조작 띠)을 옛 값으로 그리고 소비하지 않게(2026-10-15 기기 시험)
        const int64_t n = now_ms();
        if (playing && !g_video_paused.load()) { play_test_frame(p, t); board_played = true; }   // 자가 시험(put · play)
        else { playing = false; board_step(p, t, n, board_played); board_played = false; }      // 시험 그림이 남았으면 다시 그린다(검토 3)
        serve_requests(p, playing, n);
    }
}
