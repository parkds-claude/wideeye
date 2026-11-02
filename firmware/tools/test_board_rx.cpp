// main/board_rx.h 호스트 시험. 빌드·실행: tools/run_host_tests.sh
#include "../main/board_rx.h"
#include <cstdio>
static int fails = 0, checks = 0;
#define CHECK(c) do { ++checks; if (!(c)) { ++fails; printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); } } while (0)
using namespace brx;
static_assert(sizeof(Rx::last_ms) == 8 && sizeof(Rx::age_ms) == 8, "times must be 64-bit (the device's long is 32-bit)");

int main()
{
    {   // 1. 정상: 시작 → 조각 → 4조각마다 확인 → 끝
        Rx r; CHECK(begin(r, 7, 6000, 0xABCD, 1200, 1000) == Begin::OK);
        CHECK(r.st == St::RECEIVING && r.age_ms == 1200);
        CHECK(chunk(r, true, 1500, 1001) == Chunk::OK);
        CHECK(chunk(r, true, 1500, 1002) == Chunk::OK);
        CHECK(chunk(r, true, 1500, 1003) == Chunk::OK);
        CHECK(chunk(r, true, 1500, 1004) == Chunk::ACK);
        CHECK(r.got == 6000);
        CHECK(end(r, 7, 0xABCD) == End::OK && r.st == St::IDLE);
        CHECK(r.got == 6000 && r.age_ms == 1200);                 // OK 뒤에도 부르는 쪽이 길이·나이를 읽을 수 있다
    }
    {   // 2. 크기 · 인자: 0 · 음수 · 상한 초과 · id 0 · 나이 음수
        Rx r;
        CHECK(begin(r, 1, 0, 0, 0, 0) == Begin::BAD_ARGS);
        CHECK(begin(r, 1, -5, 0, 0, 0) == Begin::BAD_ARGS);
        CHECK(begin(r, 0, 10, 0, 0, 0) == Begin::BAD_ARGS);
        CHECK(begin(r, 1, 10, 0, -1, 0) == Begin::BAD_ARGS);
        CHECK(begin(r, 1, (long long)CAP + 1, 0, 0, 0) == Begin::TOO_BIG && r.st == St::IDLE);
        CHECK(begin(r, 1, (long long)CAP, 0, 0, 0) == Begin::OK);
    }
    {   // 3. 중간에 끊김: 마지막 조각 뒤 10초는 기다리고, 넘으면 버린다
        Rx r; begin(r, 9, 3000, 1, 0, 0); chunk(r, true, 1500, 100);
        CHECK(expire(r, 100 + CHUNK_TIMEOUT_MS) == 0 && r.st == St::RECEIVING);
        CHECK(expire(r, 101 + CHUNK_TIMEOUT_MS) == 9 && r.st == St::IDLE);
        CHECK(chunk(r, true, 1500, 20000) == Chunk::IGNORED);
        CHECK(end(r, 9, 1) == End::NOT_RECEIVING);
        Rx q; CHECK(expire(q, 1000000) == 0);                      // 받는 중이 아니면 아무것도 안 한다
    }
    {   // 4. 새 frame 이 앞의 것을 버린다
        Rx r; begin(r, 1, 3000, 1, 0, 0); chunk(r, true, 1500, 1);
        CHECK(begin(r, 2, 1500, 2, 0, 2) == Begin::OK && r.id == 2 && r.got == 0);
        CHECK(chunk(r, true, 1500, 3) == Chunk::OK && end(r, 2, 2) == End::OK);
    }
    {   // 5. 깨진 조각 · 넘치는 조각 → BROKEN, 받기를 버린다
        Rx r; begin(r, 3, 3000, 1, 0, 0);
        CHECK(chunk(r, false, 0, 1) == Chunk::BROKEN && r.st == St::IDLE);
        begin(r, 4, 1000, 1, 0, 0);
        CHECK(chunk(r, true, 1500, 1) == Chunk::BROKEN && r.st == St::IDLE);
    }
    {   // 6. 끝: 다른 id(늦게 온 옛 줄 — 받기는 그대로) · 모자람 · CRC
        Rx r; begin(r, 5, 3000, 0x1111, 0, 0); chunk(r, true, 1500, 1);
        CHECK(end(r, 6, 0x1111) == End::WRONG_ID && r.st == St::RECEIVING);
        CHECK(end(r, 5, 0x1111) == End::SHORT && r.st == St::IDLE);
        begin(r, 8, 1500, 0x1111, 0, 0); chunk(r, true, 1500, 1);
        CHECK(end(r, 8, 0x2222) == End::CRC && r.st == St::IDLE);
    }
    {   // 7. 확인은 4조각마다 — 150KB(조각 100개)면 25번
        Rx r; begin(r, 11, 150000, 0, 0, 0); int acks = 0;
        for (int i = 0; i < 100; ++i) acks += chunk(r, true, 1500, i) == Chunk::ACK;
        CHECK(acks == 25 && r.got == 150000);
    }
    printf("board_rx: %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
