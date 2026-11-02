// board_rx.h — 현황판 그림 받기(맥 → 탭5, 직렬 텍스트 줄)의 상태 기계. 순수 로직(호스트 시험: tools/test_board_rx.cpp).
//   바이트를 풀고(base64) 버퍼에 쓰고 CRC 를 재는 일은 부르는 쪽(sercmd.cpp)이 한다 — 여기는 "지금 받는 중인가 · 몇 바이트 · 언제 확인 ·
//   언제 버리나"만 정한다. 시간은 ms(int64_t — 기기의 long 은 32비트). 규약: MN-D34, urgent-board 설계 2026-10-15 2-5
#pragma once
#include <cstddef>
#include <cstdint>

namespace brx {

constexpr size_t  CAP = 512 * 1024;            // 그림 한 장의 상한(현황판 한 장 150KB 의 서너 배)
constexpr int32_t ACK_EVERY = 4;               // 조각 4개(8KB)마다 확인 — 받는 고리(16KB)가 넘치지 않게(IDF 는 넘치면 조용히 버린다)
constexpr int64_t CHUNK_TIMEOUT_MS = 10000;    // 마지막 조각 뒤 이만큼이면 버린다(맥이 보내다 죽음)

enum class St : uint8_t { IDLE, RECEIVING };
struct Rx {
    St       st = St::IDLE;
    uint32_t id = 0;
    size_t   expect = 0, got = 0;
    uint32_t crc = 0;
    int64_t  age_ms = 0;                       // 맥이 적은 그림의 나이(받은 때 − 이것 = 내용 시각)
    int32_t  chunks = 0;
    int64_t  last_ms = 0;                      // 마지막으로 무엇이든 받은 때
};

enum class Begin : uint8_t { OK, TOO_BIG, BAD_ARGS };
// frame <id> <len> <crc> <age>. 받는 중이었으면 그것을 버리고 새로 시작한다(맥이 다시 보낸다)
inline Begin begin(Rx &r, uint32_t id, long long len, uint32_t crc, long long age_ms, int64_t now)
{
    r = Rx();
    if (id == 0 || len <= 0 || age_ms < 0) return Begin::BAD_ARGS;
    if ((unsigned long long)len > CAP) return Begin::TOO_BIG;
    r.st = St::RECEIVING; r.id = id; r.expect = (size_t)len; r.crc = crc; r.age_ms = age_ms; r.last_ms = now;
    return Begin::OK;
}

// 받는 중인데 마지막 조각에서 너무 오래 지났으면 버린다. 버린 id(0 = 버리지 않음)
inline uint32_t expire(Rx &r, int64_t now)
{
    if (r.st != St::RECEIVING || now - r.last_ms <= CHUNK_TIMEOUT_MS) return 0;
    const uint32_t id = r.id;
    r = Rx();
    return id;
}

enum class Chunk : uint8_t { IGNORED, OK, ACK, BROKEN };
// 조각 하나를 풀었다(decoded_ok = base64 가 맞고 버퍼에 들어갔다, n = 바이트). BROKEN 이면 받기를 버렸다(맥은 처음부터 다시)
inline Chunk chunk(Rx &r, bool decoded_ok, size_t n, int64_t now)
{
    if (r.st != St::RECEIVING) return Chunk::IGNORED;
    if (!decoded_ok || n > r.expect - r.got) { r = Rx(); return Chunk::BROKEN; }
    r.got += n; ++r.chunks; r.last_ms = now;
    return r.chunks % ACK_EVERY == 0 ? Chunk::ACK : Chunk::OK;
}

enum class End : uint8_t { OK, NOT_RECEIVING, WRONG_ID, SHORT, CRC };
// frameend <id>. crc_of_data = 받은 바이트의 CRC(부르는 쪽이 잰다). WRONG_ID 면 받기를 그대로 둔다(늦게 온 옛 줄).
//   OK 면 IDLE 로 돌아가되 길이·나이는 남겨 둔다 — 부르는 쪽이 읽는다(다음 begin 이 지운다)
inline End end(Rx &r, uint32_t id, uint32_t crc_of_data)
{
    if (r.st != St::RECEIVING) return End::NOT_RECEIVING;
    if (id != r.id) return End::WRONG_ID;
    const End e = r.got != r.expect ? End::SHORT : crc_of_data != r.crc ? End::CRC : End::OK;
    if (e != End::OK) r = Rx();
    else r.st = St::IDLE;
    return e;
}

}  // namespace brx
