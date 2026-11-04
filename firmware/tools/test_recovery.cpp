// main/recovery.h 호스트 시험. 빌드·실행: tools/run_host_tests.sh
#include "../main/recovery.h"
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string_view>
static int fails = 0, checks = 0;
#define CHECK(c) do { ++checks; if (!(c)) { ++fails; printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); } } while (0)
using namespace rec;

// 시간은 64비트여야 한다: 기기의 long 은 32비트다(49일 = 2^32 ms 에서 넘친다). 헤더가 int64_t 를 쓰는지 형으로 못 박는다
static_assert(sizeof(WdIn::now_ms) == 8 && sizeof(WdIn::pipe_beat_ms) == 8 && sizeof(WdIn::sess_beat_ms) == 8 && sizeof(WdIn::ui_beat_ms) == 8, "watchdog times must be 64-bit");

// 건강한 입력(now 에 모두 방금 돌았다). 주사 완료 수는 시험마다 스스로 올린다
static WdIn healthy(int64_t now, uint32_t refresh) { WdIn in; in.now_ms = now; in.pipe_beat_ms = now; in.sess_beat_ms = now; in.ui_beat_ms = now; in.refresh_count = refresh; return in; }

int main()
{
    const WdCfg cfg;
    // 1. 건강하면 아무 일도 없다(1초마다 10분)
    {
        WdState s; bool quiet = true;
        for (int i = 0; i < 600; ++i) quiet = quiet && wd_step(s, cfg, healthy(1000LL * i, (uint32_t)i * 57)).action == Action::NONE;
        CHECK(quiet);
    }
    // 2. 파이프라인이 멈추면 5초 뒤 재시작. 5초까지는 기다린다
    {
        WdState s; WdIn in = healthy(10000, 1); wd_step(s, cfg, in);
        in.now_ms = 15000; in.sess_beat_ms = 15000; in.ui_beat_ms = 15000; in.refresh_count = 2;   // 파이프라인만 10000 에 머문다
        CHECK(wd_step(s, cfg, in).action == Action::NONE);
        in.now_ms = 15001; in.sess_beat_ms = 15001; in.ui_beat_ms = 15001; in.refresh_count = 3;
        const WdOut o = wd_step(s, cfg, in);
        CHECK(o.action == Action::RESTART && o.cause == Cause::PIPELINE_STUCK && o.age_ms == 5001);
    }
    // 3. 오래 걸리는 일(bench — beat 를 60초 뒤로 밀어 둠) 동안에는 재시작하지 않고, 그 한도를 넘기면 한다
    {
        WdState s; WdIn in = healthy(1000, 1); in.pipe_beat_ms = 61000; wd_step(s, cfg, in);
        bool quiet = true;
        for (int64_t t = 2000; t <= 66000; t += 1000) { WdIn x = healthy(t, (uint32_t)t); x.pipe_beat_ms = 61000; quiet = quiet && wd_step(s, cfg, x).action == Action::NONE; }
        CHECK(quiet);
        WdIn x = healthy(66001, 99999); x.pipe_beat_ms = 61000;
        CHECK(wd_step(s, cfg, x).cause == Cause::PIPELINE_STUCK);
    }
    // 4. 세션이 멈추면 30초 뒤 재시작
    {
        WdState s; wd_step(s, cfg, healthy(0, 1));
        WdIn in = healthy(30000, 2); in.sess_beat_ms = 0;
        CHECK(wd_step(s, cfg, in).action == Action::NONE);
        in = healthy(30001, 3); in.sess_beat_ms = 0;
        const WdOut o = wd_step(s, cfg, in);
        CHECK(o.action == Action::RESTART && o.cause == Cause::SESSION_STUCK);
    }
    // 5. 주사 완료 인터럽트가 3초 넘게 오지 않으면 재시작. 다시 오면 시계가 돌아간다
    {
        WdState s; wd_step(s, cfg, healthy(0, 100));
        CHECK(wd_step(s, cfg, healthy(3000, 100)).action == Action::NONE);
        CHECK(wd_step(s, cfg, healthy(3001, 100)).cause == Cause::PANEL_STUCK);
        WdState s2; wd_step(s2, cfg, healthy(0, 100));
        CHECK(wd_step(s2, cfg, healthy(2900, 101)).action == Action::NONE);       // 한 번 왔다
        CHECK(wd_step(s2, cfg, healthy(5800, 101)).action == Action::NONE);       // 그때부터 다시 3초
        CHECK(wd_step(s2, cfg, healthy(5901, 101)).cause == Cause::PANEL_STUCK);
    }
    // 11. 49.7일(2^32 ms)을 넘겨도 판단이 그대로다 — 32비트 시간이라면 여기서 깨진다
    {
        const int64_t base = 4294967296LL + 123456;                               // 2^32 를 넘긴 때
        CHECK(base > 4294967295LL);
        WdState s; wd_step(s, cfg, healthy(base, 1));
        CHECK(wd_step(s, cfg, healthy(base + 1000, 2)).action == Action::NONE);
        WdIn in = healthy(base + 6001, 3); in.pipe_beat_ms = base + 1000;
        CHECK(wd_step(s, cfg, in).cause == Cause::PIPELINE_STUCK);
    }

    // 19. 범위 검사(검토 I-7): 끝값은 들어가고 그 밖은 안 들어간다
    {
        CHECK(in_range(1, 1, 8) && in_range(8, 1, 8) && !in_range(0, 1, 8) && !in_range(9, 1, 8) && !in_range(-1, 1, 8));
        CHECK(!in_range(INT32_MAX, 1, 8) && !in_range(INT32_MIN, 1, 8));
    }

    // 20. 발열: 문턱을 넘으면 바로 올라가고(두 단계를 한 번에도), 문턱보다 5도 식어야 한 단계씩 내려온다
    {
        const HeatCfg h;
        CHECK(heat_step(Heat::NORMAL, 45.0f, h) == Heat::NORMAL);                 // 실측 평소 값
        CHECK(heat_step(Heat::NORMAL, 64.9f, h) == Heat::NORMAL && heat_step(Heat::NORMAL, 65.0f, h) == Heat::WARM);
        CHECK(heat_step(Heat::NORMAL, 76.0f, h) == Heat::HOT && heat_step(Heat::NORMAL, 90.0f, h) == Heat::CRITICAL);
        CHECK(heat_step(Heat::WARM, 61.0f, h) == Heat::WARM);                     // 65 아래지만 60 위 → 그대로(오르내리지 않는다)
        CHECK(heat_step(Heat::WARM, 59.9f, h) == Heat::NORMAL);
        CHECK(heat_step(Heat::CRITICAL, 81.0f, h) == Heat::CRITICAL && heat_step(Heat::CRITICAL, 79.9f, h) == Heat::HOT);
        CHECK(heat_step(Heat::CRITICAL, 30.0f, h) == Heat::HOT);                  // 내려올 때는 한 번에 한 단계
        CHECK(heat_step(Heat::HOT, 30.0f, h) == Heat::WARM && heat_step(Heat::WARM, 30.0f, h) == Heat::NORMAL);
    }
    // 21. 못 읽은 온도(센서 없음 -99 · NaN · 터무니없는 값)는 단계를 바꾸지 않는다
    {
        const HeatCfg h;
        CHECK(heat_step(Heat::NORMAL, -99.0f, h) == Heat::NORMAL && heat_step(Heat::HOT, -99.0f, h) == Heat::HOT);
        CHECK(heat_step(Heat::WARM, std::nanf(""), h) == Heat::WARM && heat_step(Heat::NORMAL, 999.0f, h) == Heat::NORMAL);
    }
    // 22. 단계가 오를수록 밝기 상한이 낮아진다. 치명 단계에서만 그림을 멈춘다
    {
        const HeatLimit n = heat_limit(Heat::NORMAL), w = heat_limit(Heat::WARM), t = heat_limit(Heat::HOT), c = heat_limit(Heat::CRITICAL);
        CHECK(n.bri_max == 255 && n.video);
        CHECK(w.bri_max < n.bri_max && t.bri_max < w.bri_max && c.bri_max < t.bri_max && c.bri_max > 0);
        CHECK(w.video && t.video && !c.video);
    }
    // 25. panel 태스크(터치·밝기·온도 보호)가 10초 넘게 돌지 않으면 재시작. 10초까지는 기다리고, 다시 돌면 아무 일도 없다
    {
        WdState s; wd_step(s, cfg, healthy(0, 1));
        WdIn in = healthy(10000, 2); in.ui_beat_ms = 0;
        CHECK(wd_step(s, cfg, in).action == Action::NONE);
        in = healthy(10001, 3); in.ui_beat_ms = 0;
        const WdOut o = wd_step(s, cfg, in);
        CHECK(o.action == Action::RESTART && o.cause == Cause::UI_STUCK && o.age_ms == 10001);
        CHECK(wd_step(s, cfg, healthy(20000, 4)).action == Action::NONE);
        // 파이프라인도 함께 멈췄으면 파이프라인 쪽 까닭이 먼저다(화면 안내가 달라진다)
        in = healthy(30000, 5); in.ui_beat_ms = 0; in.pipe_beat_ms = 0;
        CHECK(wd_step(s, cfg, in).cause == Cause::PIPELINE_STUCK);
    }
    // 26. 상한에 막힌 재시작: 같은 까닭으로는 다시 청하지 않고, 고장이 풀리면 놓는다(며칠 뒤의 같은 고장에는 다시 해 본다)
    {
        GiveUp g;
        const WdOut stuck = {Action::RESTART, Cause::PIPELINE_STUCK, 6000}, none = {};
        CHECK(g.should_try(stuck));                                               // 처음에는 해 본다
        g.refused(Cause::PIPELINE_STUCK);
        CHECK(!g.should_try(stuck) && !g.should_try(stuck));                      // 막혔으면 1초마다 다시 청하지 않는다
        CHECK(g.should_try({Action::RESTART, Cause::SESSION_STUCK, 31000}));      // 다른 까닭은 따로 본다
        CHECK(!g.should_try(stuck));                                              // 아직 붙들고 있다
        CHECK(!g.should_try(none));                                               // 고장이 풀렸다
        CHECK(g.should_try(stuck));                                               // → 같은 고장이 또 생기면 다시 해 본다
        CHECK(!g.should_try(none));
    }
    // 28. 패닉·워치독으로 다시 켜진 횟수: 2번 잇달아 → 안전 모드, 4번 잇달아 → 선다. 전원을 켠 부팅이 지우고, 소프트 재시작은 그대로 둔다
    {
        CrashBoot c = crash_boot(0, false, true);  CHECK(c.count == 0 && !c.safe_mode && !c.halt);
        c = crash_boot(c.count, true, false);      CHECK(c.count == 1 && !c.safe_mode && !c.halt);
        c = crash_boot(c.count, true, false);      CHECK(c.count == 2 && c.safe_mode && !c.halt);
        c = crash_boot(c.count, true, false);      CHECK(c.count == 3 && c.safe_mode && !c.halt);
        c = crash_boot(c.count, true, false);      CHECK(c.count == 4 && c.safe_mode && c.halt);
        const CrashBoot sw = crash_boot(c.count, false, false);                   // 선 뒤에 시리얼 reboot: 서지 않고(패닉 부팅이 아니다) 안전 모드로 해 본다
        CHECK(sw.count == 4 && sw.safe_mode && !sw.halt);
        const CrashBoot again = crash_boot(sw.count, true, false);                // 그래도 또 죽으면 바로 선다
        CHECK(again.count == 5 && again.halt);
        const CrashBoot pwr = crash_boot(again.count, false, true);               // 전원을 껐다 켜면 처음부터
        CHECK(pwr.count == 0 && !pwr.safe_mode && !pwr.halt);
        // 전원을 켠 부팅만 "우리가 청한 재시작"의 횟수도 지운다. 패닉 부팅·소프트 재시작은 지우지 않는다(되풀이를 세는 중이다)
        CHECK(pwr.fresh_start && !sw.fresh_start && !again.fresh_start && !c.fresh_start && crash_boot(0, false, true).fresh_start);
        CHECK(!crash_boot(3, true, true).fresh_start);                            // 까닭이 둘 다로 읽히면(있을 수 없지만) 지우지 않는 쪽
        CHECK(crash_boot(-7, false, false).count == 0 && crash_boot(2000000000, true, false).count == 2000000000);   // 깨진 저장값 · 넘치지 않는다
    }
    // 28b. RL-D1 — 3분 넘게 돈 실행 뒤의 패닉 · 워치독은 "잇달아"가 아니다(2026-11-03 맥북 덮개 닫기 · 다시 켜기 무렵 띄엄띄엄 6번 → 섰다)
    {
        int32_t n = 0;
        for (int i = 0; i < 10; ++i) {                                            // 몇 시간 간격의 워치독 리셋 열 번 — 늘 1, 서지 않는다
            const CrashBoot c = crash_boot(n, true, false, /*prev_alive=*/true);
            CHECK(c.count == 1 && !c.halt);
            n = c.count;
        }
        CrashBoot c = crash_boot(n, true, false, false);  CHECK(c.count == 2 && !c.halt);   // 켜지자마자 또 죽으면 잇달음으로 센다
        c = crash_boot(c.count, true, false, false);       CHECK(c.count == 3 && !c.halt);
        c = crash_boot(c.count, true, false, false);       CHECK(c.count == 4 && c.halt);    // 진짜 되풀이는 여전히 선다
        const CrashBoot retry = crash_boot(c.count, false, false, false);                    // RL-D2: 선 뒤 스스로 한 소프트 재시작 — 서지 않고 해 본다
        CHECK(retry.count == 4 && !retry.halt);
        CHECK(crash_boot(retry.count, true, false, false).halt);                             // 또 곧바로 죽으면 다시 선다
        const CrashBoot ok = crash_boot(retry.count, false, false, true);                     // 다시 켠 뒤 3분 넘게 돌고 우리가 청해 다시 켰다 → 지운다
        CHECK(ok.count == 0 && !ok.halt && !ok.fresh_start);
        CHECK(crash_boot(5, true, false, true).count == 1);                                  // 쌓인 수가 있어도 오래 돈 뒤의 패닉은 1 부터
        CHECK(crash_boot(3, false, false).count == 3);                                       // 짧게 돈 뒤의 소프트 재시작은 세지도 지우지도 않는다(옛 판과 같다)
    }
    // 28c. RL-D3 — 실행 길이 표지와 부팅 기록 칸
    {
        CHECK(up_mark(0) == 0 && up_mark(CRASH_ALIVE_MS - 1) == 0 && up_mark(CRASH_ALIVE_MS) == 1);
        CHECK(up_mark(30 * 60000LL) == 2 && up_mark(3 * 3600000LL) == 3 && up_mark(24 * 3600000LL) == 4 && up_mark(400LL * 24 * 3600000LL) == 4);
        CHECK(!hist_unpack(0).valid);                                                        // 빈 칸
        const HistEntry e = hist_unpack(hist_pack(0x12, 7, 2));
        CHECK(e.valid && e.hw == 0x12 && e.why == 7 && e.prev_up == 2);
        const HistEntry z = hist_unpack(hist_pack(0, 0, 0));                                 // 모든 칸이 0 이어도 빈 칸과 가려진다
        CHECK(z.valid && z.hw == 0 && z.why == 0 && z.prev_up == 0);
        CHECK(CRASH_RETRY_MS == 15 * 60000 && CRASH_ALIVE_MS < CRASH_RETRY_MS);
        // 비정상 리셋 번호(IDF esp_reset_reason_t): PANIC 4 · INT_WDT 5 · TASK_WDT 6 · WDT 7 · CPU_LOCKUP 15. 전원 1 · 소프트 3 · 브라운아웃 9 · USB 11 은 아니다
        CHECK(crash_reason(4) && crash_reason(5) && crash_reason(6) && crash_reason(7) && crash_reason(15));
        CHECK(!crash_reason(0) && !crash_reason(1) && !crash_reason(3) && !crash_reason(9) && !crash_reason(11) && !crash_reason(14));
        int32_t h[HIST_N] = {hist_pack(0x15, 7, 1), hist_pack(0xc, 3, 2), hist_pack(0x15, 7, 1), 0, hist_pack(1, 1, 0), hist_pack(0x15, 4, 1), 0, 0};
        CHECK(recent_crashes(h, HIST_N) == 3);                                               // 빈 칸 · 소프트 · 전원은 세지 않는다
    }
    // 24. 까닭 이름은 모두 다르고 비어 있지 않다(다음 부팅 줄에 찍힌다)
    {
        bool ok = true;
        CHECK((int)Cause::SYS_INIT == CAUSE_COUNT - 1 && cause_name((Cause)CAUSE_COUNT)[0] == '?');   // 새 까닭은 끝에 더한다(NVS 의 last_rst 는 숫자다)
        for (int a = 0; a < CAUSE_COUNT; ++a) for (int b = a + 1; b < CAUSE_COUNT; ++b) {
            const char *x = cause_name((Cause)a), *y = cause_name((Cause)b);
            ok = ok && x[0] && y[0] && x[0] != '?' && y[0] != '?' && std::string_view(x) != std::string_view(y);
        }
        CHECK(ok);
    }
    // 현황판 모드(MN-D32·D33 — urgent-board 설계 2026-10-15 2-2)
    {
        static_assert(sizeof(BoardIn::now_ms) == 8 && sizeof(BoardIn::drawn_content_ms) == 8 && sizeof(BoardIn::screen_off_ms) == 8, "board times must be 64-bit");
        const BoardCfg c; BoardIn in; in.boot_ms = 0;
        in.now_ms = 1000;                                   CHECK(board_state(c, in) == Board::NONE);
        CHECK(!board_alarm(c, in, Board::NONE) && board_bri_max(c, Board::NONE, false) == 255);
        in.now_ms = 179999;                                 CHECK(!board_alarm(c, in, Board::NONE));
        in.now_ms = 180000;                                 CHECK(board_alarm(c, in, Board::NONE));      // 부팅 뒤 3분 — 알린다
        CHECK(board_bri_max(c, Board::NONE, true) == 60);
        in.drawn_content_ms = 200000; in.now_ms = 379999;   CHECK(board_state(c, in) == Board::LIVE);    // 내용이 179.999초 전
        CHECK(!board_alarm(c, in, Board::LIVE));
        in.now_ms = 380000;                                 CHECK(board_state(c, in) == Board::LOST);    // 180초
        CHECK(board_alarm(c, in, Board::LOST) && board_bri_max(c, Board::LOST, true) == 60);
        in.screen_off_ms = 370000; in.now_ms = 460000;      CHECK(board_state(c, in) == Board::SLEEP);   // screen 0 뒤 90초째 — 내용이 낡아도 잠듦이 먼저
        CHECK(board_bri_max(c, Board::SLEEP, false) == 0 && !board_alarm(c, in, Board::SLEEP));
        in.now_ms = 460001;                                 CHECK(board_state(c, in) == Board::LOST);    // SLEEP 이 끊김(맥이 잠든 채 죽음)
        in.screen_off_ms = 0; in.drawn_content_ms = 455000; CHECK(board_state(c, in) == Board::LIVE);
        CHECK(board_bri_max(c, Board::LIVE, false) == 255);
        CHECK(board_minutes(in) == 0);
        in.now_ms = 455000 + 5 * 60000 + 1;                 CHECK(board_minutes(in) == 5);
        BoardIn z; z.now_ms = 3 * 60000; z.boot_ms = 0;     CHECK(board_minutes(z) == 3);               // NONE: 부팅부터 센다
        BoardCfg short_c; short_c.stale_ms = 20000;         // `board stale 20`
        BoardIn s2; s2.now_ms = 50000; s2.drawn_content_ms = 30001; CHECK(board_state(short_c, s2) == Board::LIVE);
        s2.drawn_content_ms = 30000;                        CHECK(board_state(short_c, s2) == Board::LOST);
        // 깨어난 직후 유예(검토 5): 맥 화면이 오래 잠들었다 깨면 screen 1 이 그림보다 먼저 온다 — 그 사이 "현황판이 오지 않습니다"가 번쩍이지 않게
        BoardIn w; w.now_ms = 1000000; w.drawn_content_ms = 100000; w.wake_ms = 990001;   // 내용은 15분 전, 깬 지 9.999초
        CHECK(board_state(c, w) == Board::LIVE);
        w.wake_ms = 980000;                                 CHECK(board_state(c, w) == Board::LOST);    // 깬 지 20초 — 유예 끝
        w.wake_ms = 0;                                      CHECK(board_state(c, w) == Board::LOST);
        BoardIn w0; w0.now_ms = 1000000; w0.wake_ms = 995000; w0.boot_ms = 0; CHECK(board_state(c, w0) == Board::NONE);   // 그린 적이 없으면 유예도 없다
        static_assert(sizeof(BoardIn::wake_ms) == 8, "board times must be 64-bit");
        CHECK(std::string_view(board_name(Board::LIVE)) == "live" && std::string_view(board_name(Board::SLEEP)) == "sleep" &&
              std::string_view(board_name(Board::LOST)) == "lost" && std::string_view(board_name(Board::NONE)) == "none");
    }
    // SLEEP 이 끊기면(맥이 잠긴 채 창이 죽음 · 포트를 놓아 둠) LOST — 그림 내용이 싱싱해도 LIVE 로 켜지지 않는다(설계 2-6 · MN-D32 · 검토 초점 4).
    //   맥은 잠긴 동안에도 그림을 보내고 탭5 는 SLEEP 중에도 그린다 → 끊긴 순간의 내용은 늘 새것이다. LIVE 로 보면 잠긴 맥 옆에 메시지가 최대 밝기로 켜진다
    {
        const BoardCfg c;
        BoardIn s; s.boot_ms = 0; s.screen_off_ms = 1060000; s.drawn_content_ms = 1055000;
        s.now_ms = 1150000;                                 CHECK(board_state(c, s) == Board::SLEEP);    // screen 0 뒤 90초째
        s.now_ms = 1150001;                                 CHECK(board_state(c, s) == Board::LOST);     // 끊김 — 내용은 95초(싱싱)
        CHECK(board_alarm(c, s, Board::LOST) && board_bri_max(c, Board::LOST, true) == 60);
        s.drawn_content_ms = 1149000;                       CHECK(board_state(c, s) == Board::LOST);     // 끊기기 1초 전에 그린 그림이어도
        s.wake_ms = 1149500;                                CHECK(board_state(c, s) == Board::LOST);     // 깬 때의 유예도 screen 1 이 와야(screen_off 0)
        BoardIn n; n.boot_ms = 0; n.screen_off_ms = 10000; n.now_ms = 100001;
        CHECK(board_state(c, n) == Board::NONE);            // 그린 적이 없으면 NONE(부팅 뒤 3분이면 알림)
        s.screen_off_ms = 0; s.now_ms = 1150001;            CHECK(board_state(c, s) == Board::LIVE);     // screen 1 이 오면 다시 내용으로
    }
    // screen 신호(검토 4): 맥은 30초마다 같은 값을 보낸다 — 바뀔 때만 다시 그린다. 0 → 1 이면 깬 때를 적는다
    {
        ScreenSig s;                                        // 부팅: 켜짐
        CHECK(!screen_signal(s, true, 5000) && s.off_ms == 0 && s.wake_ms == 0);      // 켜짐 → 켜짐: 바뀜 없음 · 깬 것도 아니다
        CHECK(screen_signal(s, false, 10000) && s.off_ms == 10000);                    // 꺼짐
        CHECK(!screen_signal(s, false, 40000) && s.off_ms == 40000 && s.wake_ms == 0); // 30초 뒤 같은 값: 때만 새로(SLEEP 이 이어진다)
        CHECK(screen_signal(s, true, 50000) && s.off_ms == 0 && s.wake_ms == 50000);   // 깸
        CHECK(!screen_signal(s, true, 80000) && s.wake_ms == 50000);                   // 켜짐이 이어져도 깬 때는 그대로(유예 20초가 늘지 않게)
        ScreenSig z; CHECK(screen_signal(z, false, 0) && z.off_ms != 0);              // 0 ms 에 와도 "꺼짐"으로 남는다(0 = 켜짐)
    }
    // 현황판 화면 한 바퀴(MN-D35 · 펌웨어 검토 2026-10-15 의 1~3 · 검토 뒤 찾은 것): 무엇을 그릴지
    {
        auto turn = [](Board st, uint32_t gen) { BoardTurn k; k.state = st; k.gen = gen; return k; };
        // 새 그림: 세대가 바뀌면 한 번만 해 본다 · 낡았으면 그리지 않고 다시 보지 않는다 · 세대 0 은 그림이 없다
        BoardView v;
        CHECK(!board_try_new(v, 0, 0, 1000, 180000));
        CHECK(board_try_new(v, 1, 900, 1000, 180000) && !board_try_new(v, 1, 900, 1000, 180000));
        CHECK(!board_try_new(v, 2, 1000 - 180000, 1000, 180000) && v.tried_gen == 2);   // 내용이 180초 — 낡음
        // 처음: 아무것도 모른다 → 기다림 안내를 한 번
        BoardView a;
        board_begin(a, false, false, false, 0);
        CHECK(board_pick(a, turn(Board::NONE, 0)) == BoardDraw::WAIT);
        board_drawn(a, BoardDraw::WAIT, true, 0, 0);
        board_begin(a, false, false, false, 0);
        CHECK(board_pick(a, turn(Board::NONE, 0)) == BoardDraw::NOTHING);               // 그대로면 다시 그리지 않는다
        // 새 그림을 그렸다 → LIVE 에서 다시 그리지 않는다
        board_begin(a, false, false, false, 0);
        CHECK(board_try_new(a, 1, 1000, 1000, 180000));
        board_drawn(a, BoardDraw::PICTURE, true, 1, 0);
        CHECK(a.ok_gen == 1 && a.on_screen == BoardDraw::PICTURE);
        CHECK(board_pick(a, turn(Board::LIVE, 1)) == BoardDraw::NOTHING);
        // 다시 그리라는 요청(조작 띠 · 방향) → 같은 그림을 다시
        board_begin(a, true, false, false, 0);
        CHECK(board_pick(a, turn(Board::LIVE, 1)) == BoardDraw::PICTURE);
        board_drawn(a, BoardDraw::PICTURE, true, 1, 0);
        board_begin(a, false, false, false, 0);
        CHECK(board_pick(a, turn(Board::LIVE, 1)) == BoardDraw::NOTHING);
        // 검토 2: 발열 알림이 사라지면 다시 그린다(알림을 지우려면)
        board_begin(a, false, true, false, 0);  CHECK(board_pick(a, turn(Board::LIVE, 1)) == BoardDraw::NOTHING);   // 알림이 뜨는 것은 요청(force)이 맡는다
        board_begin(a, false, true, false, 0);  CHECK(board_pick(a, turn(Board::LIVE, 1)) == BoardDraw::NOTHING);
        board_begin(a, false, false, false, 0); CHECK(board_pick(a, turn(Board::LIVE, 1)) == BoardDraw::PICTURE);
        board_drawn(a, BoardDraw::PICTURE, true, 1, 0);
        // 검토 3: 자가 시험 그림이 덮었으면 다시 그린다(요청이 없어도)
        board_begin(a, false, false, true, 0);  CHECK(board_pick(a, turn(Board::LIVE, 1)) == BoardDraw::PICTURE);
        board_drawn(a, BoardDraw::PICTURE, true, 1, 0);
        // 검토 뒤 찾은 것: 보이는 칸이 마지막으로 그려 낸 그림이 아니면(새 그림이 풀리지 않았다) 다시 그리지 않는다 — 실패를 되풀이하지 않게
        board_begin(a, false, false, false, 0);
        CHECK(board_try_new(a, 2, 2000, 2000, 180000));
        board_drawn(a, BoardDraw::PICTURE, false, 2, 0);                                  // 풀지 못함
        CHECK(a.ok_gen == 1 && a.on_screen == BoardDraw::PICTURE);                        // 화면은 앞 그림 그대로
        board_begin(a, true, false, false, 0);
        CHECK(board_pick(a, turn(Board::LIVE, 2)) == BoardDraw::NOTHING);
        CHECK(!board_try_new(a, 2, 2000, 2000, 180000));                                  // 같은 그림을 다시 풀지 않는다
        // 다시 그리기가 실패해도 다음 바퀴에 되풀이하지 않는다(검토 뒤 찾은 것 — 일시 실패면 최대 다음 그림까지 낡음, 미룬 사소 7)
        BoardView r; r.on_screen = BoardDraw::PICTURE; r.ok_gen = 3;
        board_begin(r, true, false, false, 0);  CHECK(board_pick(r, turn(Board::LIVE, 3)) == BoardDraw::PICTURE);
        board_drawn(r, BoardDraw::PICTURE, false, 3, 0);
        board_begin(r, false, false, false, 0); CHECK(board_pick(r, turn(Board::LIVE, 3)) == BoardDraw::NOTHING);
        // 새 그림이 풀리지 않아도 걸려 있던 요청(조작 띠)은 남는다 — 안내를 조작 띠와 함께 다시(검토 1)
        BoardView c2; c2.on_screen = BoardDraw::LOST; c2.minute = 3; c2.ok_gen = 1; c2.tried_gen = 1;
        board_begin(c2, true, false, false, 0);
        CHECK(board_try_new(c2, 2, 1000, 1000, 180000)); board_drawn(c2, BoardDraw::PICTURE, false, 2, 0);
        BoardTurn l2 = turn(Board::LOST, 2); l2.alarm = true; l2.minute = 3;
        CHECK(board_pick(c2, l2) == BoardDraw::LOST);
        // 안내는 그리지 못했으면(앞의 그리기가 안 끝남) 다음 바퀴에 다시
        BoardView n;
        board_begin(n, false, false, false, 0); CHECK(board_pick(n, turn(Board::NONE, 0)) == BoardDraw::WAIT);
        board_drawn(n, BoardDraw::WAIT, false, 0, 0);
        board_begin(n, false, false, false, 0); CHECK(board_pick(n, turn(Board::NONE, 0)) == BoardDraw::WAIT);
        // LOST: 안내로 바뀌고, 분이 바뀔 때마다 다시
        BoardTurn lost = turn(Board::LOST, 1); lost.alarm = true; lost.minute = 3;
        board_begin(a, false, false, false, 0); CHECK(board_pick(a, lost) == BoardDraw::LOST);
        board_drawn(a, BoardDraw::LOST, true, 1, 3);
        board_begin(a, false, false, false, 0); CHECK(board_pick(a, lost) == BoardDraw::NOTHING);
        lost.minute = 4;
        board_begin(a, false, false, false, 0); CHECK(board_pick(a, lost) == BoardDraw::LOST);
        board_drawn(a, BoardDraw::LOST, true, 1, 4);
        BoardTurn none_alarm = turn(Board::NONE, 0); none_alarm.alarm = true; none_alarm.minute = 4;   // 부팅 뒤 3분 넘게 한 장도 없음 → LOST 와 같은 안내
        BoardView q; q.on_screen = BoardDraw::WAIT;
        board_begin(q, false, false, false, 0); CHECK(board_pick(q, none_alarm) == BoardDraw::LOST);
        // 깬 직후 유예로 LIVE(낡은 그림): 안내가 떠 있었으면 그 그림을 다시 그린다
        board_begin(a, false, false, false, 0); CHECK(board_pick(a, turn(Board::LIVE, 2)) == BoardDraw::NOTHING);   // a: ok_gen 1, 보이는 칸 세대 2(못 푼 것) — 다시 그릴 그림이 없다
        BoardView w; w.on_screen = BoardDraw::LOST; w.ok_gen = 5; w.tried_gen = 5;
        board_begin(w, false, false, false, 0); CHECK(board_pick(w, turn(Board::LIVE, 5)) == BoardDraw::PICTURE);
        // SLEEP: 아무것도 그리지 않고, 요청은 깰 때까지 남는다
        BoardView s; s.on_screen = BoardDraw::PICTURE; s.ok_gen = 7;
        board_begin(s, true, false, false, 0);  CHECK(board_pick(s, turn(Board::SLEEP, 7)) == BoardDraw::NOTHING);
        board_begin(s, false, false, false, 0); CHECK(board_pick(s, turn(Board::SLEEP, 7)) == BoardDraw::NOTHING);
        board_begin(s, false, false, false, 0); CHECK(board_pick(s, turn(Board::LIVE, 7)) == BoardDraw::PICTURE);
        // 잠든 동안 새 그림을 그렸으면 그 요청은 채워졌다(그림에 그때의 덧그림이 들었다)
        BoardView s2v; s2v.on_screen = BoardDraw::PICTURE; s2v.ok_gen = 7;
        board_begin(s2v, true, false, false, 0);
        CHECK(board_try_new(s2v, 8, 9000, 9000, 180000)); board_drawn(s2v, BoardDraw::PICTURE, true, 8, 0);
        CHECK(board_pick(s2v, turn(Board::SLEEP, 8)) == BoardDraw::NOTHING);
        board_begin(s2v, false, false, false, 0); CHECK(board_pick(s2v, turn(Board::LIVE, 8)) == BoardDraw::NOTHING);
        // 발열 치명이 맨 앞: 어느 상태에서든 안내를 한 번, 그 뒤로는 요청이 있을 때만
        BoardTurn hot = turn(Board::LIVE, 8); hot.hot = true;
        board_begin(s2v, false, false, false, 0); CHECK(board_pick(s2v, hot) == BoardDraw::HOT);
        board_drawn(s2v, BoardDraw::HOT, true, 8, 0);
        board_begin(s2v, false, false, false, 0); CHECK(board_pick(s2v, hot) == BoardDraw::NOTHING);
        hot.state = Board::SLEEP;
        board_begin(s2v, true, false, false, 0); CHECK(board_pick(s2v, hot) == BoardDraw::HOT);     // 잠든 동안에도(백라이트는 panel 이 끈다)
        board_drawn(s2v, BoardDraw::HOT, true, 8, 0);
        // 발열이 풀리면 그림으로 돌아간다
        board_begin(s2v, false, false, false, 0); CHECK(board_pick(s2v, turn(Board::LIVE, 8)) == BoardDraw::PICTURE);
        // 방향이 바뀌면 요청이 없어도 다시 그린다 — 요청과 방향을 읽는 때가 어긋나도 옛 방향의 그림이 남지 않게(기기 시험 2026-10-15)
        BoardView o; o.on_screen = BoardDraw::WAIT;
        board_begin(o, false, false, false, 0);  // 처음 본 방향
        CHECK(board_pick(o, turn(Board::NONE, 0)) == BoardDraw::WAIT);    // 앞에 그린 방향을 모른다 → 한 번 그린다
        board_drawn(o, BoardDraw::WAIT, true, 0, 0);
        board_begin(o, false, false, false, 0);  CHECK(board_pick(o, turn(Board::NONE, 0)) == BoardDraw::NOTHING);
        board_begin(o, false, false, false, 1);  CHECK(board_pick(o, turn(Board::NONE, 0)) == BoardDraw::WAIT);
        board_drawn(o, BoardDraw::WAIT, true, 0, 0);
        board_begin(o, false, false, false, 1);  CHECK(board_pick(o, turn(Board::NONE, 0)) == BoardDraw::NOTHING);
    }
    // 현황판의 방향(MN-D41 — 사용자 2026-10-18 "90도 회전이 가능하게 하자" · "화면터치하면 90도 회전도 나오도록"): 네 방향 모두.
    //   세로(1 · 3)면 맥이 세로 화면을 90° 눕혀 보내므로 그림 길은 그대로다(urgent-board 설계 2026-10-18 D1 · D2). 그 전(MN-D38)은 가로 둘뿐
    {
        CHECK(rotate_next(0) == 1 && rotate_next(1) == 2 && rotate_next(2) == 3 && rotate_next(3) == 0);   // 회전 단추 = 90°씩
        CHECK(orient_for(1) == 1 && orient_for(3) == 3 && orient_for(7) == 3 && orient_for(-1) == 3);
    }
    printf("recovery: %d checks, %d fail\n", checks, fails);
    return fails ? 1 : 0;
}
