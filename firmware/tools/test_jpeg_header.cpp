// components/jpeg_decode_enhanced/include/jpeg_header_check.h 호스트 시험. 빌드·실행: tools/run_host_tests.sh
// 인자로 파일을 주면 시험 대신 그 파일들의 판정만 찍는다:  test_jpeg_header a.jpg b.jpg
//
// 지키려는 성질은 하나다: **검사를 통과한 것은 ESP-IDF v5.5.4 의 마커 파서에 넣어도 배열 밖을 건드리지 않고, SOS 까지 가며,
// 크기가 0 이 아니고, 2D-DMA 의 블록 폭(IDF 의 dec_hb_tbl)도 0 이 아니다.** 그래서 IDF 파서가 무엇을 색인으로 쓰는지를 그대로
// 옮긴 모형(IdfModel)을 두고, 통과한 입력마다 돌려 본다. 모형이 보는 범위는 머리(마커 파서 + 블록 폭 표)까지다 — 스캔 데이터는 보지 않는다.
#include "../components/jpeg_decode_enhanced/include/jpeg_header_check.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string_view>
#include <vector>
#include <sys/mman.h>
#include <unistd.h>
static int fails = 0, checks = 0;
#define CHECK(c) do { ++checks; if (!(c)) { ++fails; printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); } } while (0)
using Bytes = std::vector<uint8_t>;

// ── IDF v5.5.4 esp_driver_jpeg/jpeg_parse_marker.c + 부품의 s_parse_jpeg() 고리를 색인·소비 바이트 수 그대로 옮긴 모형 ────────────
// 배열 크기는 hal/jpeg_types.h: 성분 4 · 양자화 표 64 · 허프만 길이 16 · 값 256. 표 밖을 쓰려 하면 oob, 끝을 넘겨 읽으려 하면 underflow.
struct IdfModel {
    const uint8_t *p = nullptr; uint32_t left = 0, header_size = 0, total = 0;
    bool oob = false, underflow = false, error = false, sos = false;
    uint32_t process_h = 0, process_v = 0, nf = 0, qt_tbl_num = 0, mcux = 0, mcuy = 0, hv0 = 0;
    uint8_t get1() { if (left == 0) { underflow = true; return 0; } const uint8_t c = *p++; ++header_size; --left; return c; }
    uint32_t get(int n) { uint32_t r = 0; for (int i = 0; i < n; ++i) r |= (uint32_t)get1() << ((n - i - 1) * 8); return r; }
    bool skip_seg() {                                              // jpeg_parse_appn_marker / jpeg_parse_com_marker
        const uint16_t skip = (uint16_t)get(2); if (skip < 2) return false;
        const uint32_t n = (uint32_t)skip - 2; if (left < n) return false;
        p += n; header_size += n; left -= n; return true;
    }
    bool dqt() {                                                   // jpeg_parse_dqt_marker
        uint16_t length = (uint16_t)get(2); if (length < 2) return false; length -= 2;
        while (length) {
            uint32_t n = get(1); const uint32_t prec = n >> 4; n &= 0x0F;
            if (length < 1) return false; length -= 1;
            for (int i = 0; i < 64; ++i) {
                get(1); if (length < 1) return false; length -= 1;
                if (prec) { get(1); if (length < 1) return false; length -= 1; }
                if (n >= 4) oob = true;                            // qt_tbl[n][...]
            }
            if (++qt_tbl_num > 4) oob = true;                      // dqt_func[i], i < qt_tbl_num
        }
        return true;
    }
    bool sof() {                                                   // jpeg_parse_sof_marker
        get(2); if (get(1) != 8) return false;
        const uint16_t height = (uint16_t)get(2), width = (uint16_t)get(2);
        process_v = height; process_h = width;
        if (((uint32_t)width * height % 8) != 0) return false;
        const uint8_t n = (uint8_t)get(1); if (n >= 4 || n == 0) return false;
        nf = n; uint32_t hi0 = 0, vi0 = 0;
        for (uint32_t i = 0; i < nf; ++i) { get(1); const uint32_t hv = get(1); get(1); if (i == 0) { hv0 = hv; vi0 = hv & 0x0F; hi0 = hv >> 4; } }
        mcux = hi0 * 8; mcuy = vi0 * 8;
        if (mcux == 0 || mcuy == 0) { oob = true; return true; }   // 0 으로 나눈다(기기에서는 죽지 않지만 값이 쓰레기)
        if (process_v % mcuy) process_v = (process_v / mcuy + 1) * mcuy;
        if (process_h % mcux) process_h = (process_h / mcux + 1) * mcux;
        return true;
    }
    bool dht() {                                                   // jpeg_parse_dht_marker
        const uint16_t raw = (uint16_t)get(2); if (raw < 2) return false; uint16_t num_left = raw - 2;
        while (num_left) {
            const uint8_t info = (uint8_t)get(1); const uint32_t id = info & 0x0F, type = info >> 4; uint32_t np = 0;
            if (type >= 2 || id >= 2) oob = true;                  // huffbits[type][id][...]
            for (int i = 0; i < 16; ++i) np += get(1);
            if (np > 256) oob = true;                              // huffcode[type][id][i], i < np · tmp_huff[256]
            for (uint32_t i = 0; i < np; ++i) get(1);
            if (num_left < 16 + np + 1) return false;
            num_left -= (uint16_t)(1 + 16 + np);
        }
        return true;
    }
    void run(const uint8_t *buf, uint32_t len) {                   // 부품의 s_parse_jpeg()
        p = buf; left = len; total = len;
        while (left) {
            if (left > len || p < buf || p >= buf + len) { error = true; return; }
            const uint8_t a = get1(), b = get1(); const uint32_t marker = ((uint32_t)a << 8) | b;
            if (marker == 0xFFD8) {}
            else if ((marker >= 0xFFE0 && marker <= 0xFFEF) || marker == 0xFFFE) { if (!skip_seg()) error = true; }   // 부품은 돌려받은 값을 버린다
            else if (marker == 0xFFDB) { if (!dqt()) error = true; }
            else if (marker == 0xFFC0) { if (!sof()) { error = true; return; } }
            else if (marker >= 0xFFC1 && marker <= 0xFFCF && marker != 0xFFC4 && marker != 0xFFC8 && marker != 0xFFCC) { error = true; return; }
            else if (marker == 0xFFDD) { if (get(2) != 4) error = true; else get(2); }
            else if (marker == 0xFFC4) { if (!dht()) error = true; }
            else if (marker == 0xFFDA) { p -= 2; header_size -= 2; left += 2; sos = true; break; }
            else if (marker == 0xFFFF) { --p; --header_size; ++left; }
        }
    }
    // IDF jpeg_param.c 의 dec_hb_tbl[표본][출력] 에서 우리가 쓰는 출력 두 칸(RGB888 · RGB565)만. 행: 4:4:4 · 4:2:2 · 4:2:0 · 흑백.
    //   0 인 칸은 "그 조합은 하지 않는다"는 뜻이고, 그 값이 디스크립터의 블록 폭이 된다 → 0 이면 크기 0 인 전송(사전 검토 2a I-1).
    uint32_t dma_block() const {
        static const uint32_t HB[4][2] = {{40, 32}, {32, 64}, {32, 48}, {0, 0}};
        const int row = nf == 1 ? 3 : hv0 == 0x11 ? 0 : hv0 == 0x21 ? 1 : hv0 == 0x22 ? 2 : -1;
        return row < 0 ? 0 : (HB[row][0] < HB[row][1] ? HB[row][0] : HB[row][1]);   // 둘 중 작은 쪽이 0 이 아니어야 한다
    }
    bool safe() const { return !oob && !underflow && !error && sos && process_h && process_v && nf == 3 && qt_tbl_num <= 4 && dma_block() != 0; }
};

// ── 시험용 JPEG 머리 만들기 ──────────────────────────────────────────────────
static void seg(Bytes &o, uint8_t marker, const Bytes &payload) { o.push_back(0xFF); o.push_back(marker); const uint32_t L = (uint32_t)payload.size() + 2; o.push_back((uint8_t)(L >> 8)); o.push_back((uint8_t)L); o.insert(o.end(), payload.begin(), payload.end()); }
static Bytes dqt_table(uint8_t pq_tq) { Bytes t{pq_tq}; for (int i = 0; i < 64; ++i) t.push_back((uint8_t)(16 + i)); return t; }
static Bytes cat(Bytes a, const Bytes &b) { a.insert(a.end(), b.begin(), b.end()); return a; }
// 표준 표(JPEG 부록 K)의 길이별 개수. 값은 아무것이나 넣는다(검사는 개수만 본다)
static Bytes dht_table(uint8_t tc_th, const uint8_t counts[16]) { Bytes t{tc_th}; uint32_t np = 0; for (int i = 0; i < 16; ++i) { t.push_back(counts[i]); np += counts[i]; } for (uint32_t i = 0; i < np; ++i) t.push_back((uint8_t)i); return t; }
static const uint8_t DC_L[16] = {0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0};                 // 12
static const uint8_t AC_L[16] = {0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d};              // 162
static Bytes sof_payload(uint16_t w, uint16_t h, const std::vector<uint8_t> &hv, uint8_t precision = 8)
{
    Bytes s{precision, (uint8_t)(h >> 8), (uint8_t)h, (uint8_t)(w >> 8), (uint8_t)w, (uint8_t)hv.size()};
    for (size_t i = 0; i < hv.size(); ++i) { s.push_back((uint8_t)(i + 1)); s.push_back(hv[i]); s.push_back(i ? 1 : 0); }
    return s;
}
static Bytes sos_payload(uint8_t ns) { Bytes s{ns}; for (uint8_t i = 0; i < ns; ++i) { s.push_back((uint8_t)(i + 1)); s.push_back(i ? 0x11 : 0x00); } s.push_back(0); s.push_back(63); s.push_back(0); return s; }
struct Parts { Bytes app0{'J', 'F', 'I', 'F', 0, 1, 1, 0, 0, 1, 0, 1, 0, 0}; Bytes dqt = cat(dqt_table(0x00), dqt_table(0x01)); Bytes sof = sof_payload(1280, 720, {0x21, 0x11, 0x11});
               Bytes dht = cat(cat(dht_table(0x00, DC_L), dht_table(0x10, AC_L)), cat(dht_table(0x01, DC_L), dht_table(0x11, AC_L))); Bytes sos = sos_payload(3); Bytes scan{0x12, 0x34, 0x56, 0x78, 0xFF, 0xD9};
               bool with_dri = false; };
static Bytes build(const Parts &x)
{
    Bytes o{0xFF, 0xD8};
    seg(o, 0xE0, x.app0); seg(o, 0xDB, x.dqt); seg(o, 0xC0, x.sof); seg(o, 0xC4, x.dht);
    if (x.with_dri) seg(o, 0xDD, {0, 8});
    seg(o, 0xDA, x.sos);
    o.insert(o.end(), x.scan.begin(), x.scan.end());
    return o;
}
// 끝이 읽기 금지 쪽(page)에 딱 붙은 버퍼: 검사가 한 바이트라도 넘겨 읽으면 그 자리에서 죽는다. (이 맥의 ASan 런타임은 시작 단계에서 멈춰 쓸 수 없다)
struct GuardBuf {
    uint8_t *end = nullptr; size_t cap = 0;
    explicit GuardBuf(size_t max) {
        const size_t page = (size_t)sysconf(_SC_PAGESIZE), pages = (max + page - 1) / page;
        uint8_t *base = (uint8_t *)mmap(nullptr, (pages + 1) * page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        if (base == MAP_FAILED || mprotect(base + pages * page, page, PROT_NONE) != 0) { printf("FAIL guard buffer\n"); _exit(2); }
        end = base + pages * page; cap = pages * page;
    }
    const uint8_t *put(const Bytes &b) const
    {
        if (b.size() > cap) { printf("FAIL guard buffer too small for %u bytes\n", (unsigned)b.size()); fflush(stdout); _exit(2); }
        uint8_t *p = end - b.size(); if (!b.empty()) memcpy(p, b.data(), b.size()); return p;
    }
};
static const GuardBuf G(8u << 20);                                  // 8MB — 파일 판정(인자로 준 JPEG)도 이 버퍼를 지난다
static jpeg_hdr_err_t chk(const Bytes &b, jpeg_hdr_info_t *info = nullptr) { return jpeg_hdr_check(G.put(b), (uint32_t)b.size(), info); }
static bool model_safe(const Bytes &b, const jpeg_hdr_info_t &info)
{
    IdfModel m; m.run(b.data(), info.total_len);                   // 하드웨어가 받는 것은 total_len 바이트다(끝 표시 뒤의 꼬리는 가지 않는다 — MN-D27)
    return m.safe() && m.header_size == info.scan_offset && m.total - m.header_size == info.scan_len && m.nf == info.nf && m.hv0 == info.hv0 &&
           m.process_h >= info.width && m.process_v >= info.height && m.process_h < (uint32_t)info.width + 16 && m.process_v < (uint32_t)info.height + 16;
}
static uint32_t rng_state = 0x1234567u;
static uint32_t rnd() { rng_state ^= rng_state << 13; rng_state ^= rng_state >> 17; rng_state ^= rng_state << 5; return rng_state; }

int main(int argc, char **argv)
{
    if (argc > 1) {                                                // 파일 판정만
        for (int i = 1; i < argc; ++i) {
            FILE *f = fopen(argv[i], "rb"); if (!f) { printf("%s: cannot open\n", argv[i]); continue; }
            Bytes b; uint8_t buf[65536]; size_t n; while ((n = fread(buf, 1, sizeof buf, f)) > 0) b.insert(b.end(), buf, buf + n); fclose(f);
            jpeg_hdr_info_t info; const jpeg_hdr_err_t e = chk(b, &info);
            printf("%s: %u B -> %s", argv[i], (unsigned)b.size(), jpeg_hdr_err_name(e));
            if (e == JPEG_HDR_OK) printf("  %ux%u nf=%u hv=0x%02x scan@%u (%u B)  idf-model=%s", info.width, info.height, info.nf, info.hv0, (unsigned)info.scan_offset, (unsigned)info.scan_len, model_safe(b, info) ? "safe" : "UNSAFE");
            printf("\n");
        }
        return 0;
    }

    const Parts good;
    // 1. 1280×720, 4:2:2(앞 판의 캡처 동글이 내던 꼴): 통과하고, 읽어 낸 값이 맞고, 하드웨어에 넘길 자리가 SOS 마커다
    {
        const Bytes b = build(good); jpeg_hdr_info_t info;
        CHECK(chk(b, &info) == JPEG_HDR_OK);
        CHECK(info.width == 1280 && info.height == 720 && info.nf == 3 && info.hv0 == 0x21);
        CHECK(info.scan_offset < b.size() && b[info.scan_offset] == 0xFF && b[info.scan_offset + 1] == 0xDA && info.scan_len == b.size() - info.scan_offset);
        CHECK(model_safe(b, info));
        CHECK(chk(b, nullptr) == JPEG_HDR_OK);                                    // 결과를 안 받아도 된다
    }
    // 2. 통과해야 하는 다른 꼴: 4:2:0 · 4:4:4 · DRI 있음 · DQT/DHT 가 조각 여러 개로 나뉨 · APPn/COM 여러 개 · 표 없이(기본 허프만 표)
    {
        jpeg_hdr_info_t info;
        Parts a = good; a.sof = sof_payload(1280, 720, {0x22, 0x11, 0x11}); CHECK(chk(build(a), &info) == JPEG_HDR_OK && info.hv0 == 0x22 && model_safe(build(a), info));
        Parts b = good; b.sof = sof_payload(1920, 1080, {0x11, 0x11, 0x11}); CHECK(chk(build(b), &info) == JPEG_HDR_OK && info.width == 1920 && info.height == 1080 && model_safe(build(b), info));
        Parts d = good; d.with_dri = true; CHECK(chk(build(d), &info) == JPEG_HDR_OK && model_safe(build(d), info));
        Bytes s{0xFF, 0xD8}; seg(s, 0xFE, {'h', 'i'}); seg(s, 0xE0, good.app0); seg(s, 0xE1, {}); seg(s, 0xDB, dqt_table(0x00)); seg(s, 0xDB, dqt_table(0x01));
        seg(s, 0xC4, dht_table(0x00, DC_L)); seg(s, 0xC0, good.sof); seg(s, 0xC4, dht_table(0x10, AC_L)); seg(s, 0xDA, good.sos); s.insert(s.end(), good.scan.begin(), good.scan.end());
        CHECK(chk(s, &info) == JPEG_HDR_OK && model_safe(s, info));
        Bytes n{0xFF, 0xD8}; seg(n, 0xDB, good.dqt); seg(n, 0xC0, good.sof); seg(n, 0xDA, good.sos); n.push_back(1); n.push_back(2); n.push_back(0xFF); n.push_back(0xD9);   // MJPEG 은 허프만 표를 빼기도 한다
        CHECK(chk(n, &info) == JPEG_HDR_OK && model_safe(n, info));
        Parts q = good; q.dqt = cat(cat(dqt_table(0), dqt_table(1)), cat(dqt_table(2), dqt_table(3))); CHECK(chk(build(q), &info) == JPEG_HDR_OK && model_safe(build(q), info));   // 표 넷까지
    }
    // 3. 기기를 죽인 입력(2026-10-13): FF D8 뒤에 0 만 — IDF 파서는 이것을 "0×0, 성공"으로 돌려주었다
    {
        Bytes z(1002, 0); z[0] = 0xFF; z[1] = 0xD8;
        CHECK(chk(z) == JPEG_HDR_BAD_MARKER);
        IdfModel m; m.run(z.data(), (uint32_t)z.size());
        CHECK(!m.error && !m.oob && !m.sos && m.process_h == 0 && m.process_v == 0);      // 모형도 같은 잘못을 재현한다(오류 없이 0×0)
    }
    // 4. 비었거나 시작 표시가 없다
    {
        CHECK(jpeg_hdr_check(nullptr, 100, nullptr) == JPEG_HDR_NO_SOI);
        const uint8_t one[4] = {0xFF, 0xD8, 0xFF, 0xDA};
        CHECK(jpeg_hdr_check(one, 0, nullptr) == JPEG_HDR_NO_SOI && jpeg_hdr_check(one, 1, nullptr) == JPEG_HDR_NO_SOI && jpeg_hdr_check(one, 3, nullptr) == JPEG_HDR_NO_SOI);
        CHECK(jpeg_hdr_check(one, 4, nullptr) == JPEG_HDR_BAD_LENGTH);                    // SOI 뒤에 조각 하나도 못 담는다
        Bytes b = build(good); b[0] = 0x00; CHECK(chk(b) == JPEG_HDR_NO_SOI);
        b = build(good); b[1] = 0xD9; CHECK(chk(b) == JPEG_HDR_NO_SOI);
        Bytes junk(20000); for (auto &c : junk) c = (uint8_t)rnd(); junk[0] = 0x12; CHECK(chk(junk) == JPEG_HDR_NO_SOI);
    }
    // 5. 조각 길이: 2 보다 작거나 끝을 넘는다
    {
        Bytes b{0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x00, 1, 2, 3, 4}; CHECK(chk(b) == JPEG_HDR_BAD_LENGTH);
        b[5] = 1; CHECK(chk(b) == JPEG_HDR_BAD_LENGTH);
        b[4] = 0xFF; b[5] = 0xFF; CHECK(chk(b) == JPEG_HDR_BAD_LENGTH);                   // 65535 — 버퍼 밖
        b[4] = 0; b[5] = 7; CHECK(chk(b) == JPEG_HDR_BAD_LENGTH);                         // 한 바이트 넘는다
        b[5] = 6; CHECK(chk(b) == JPEG_HDR_BAD_LENGTH);                                   // 딱 맞지만 다음 조각이 없다
    }
    // 6. 마커 자리에 다른 것: 채움 바이트 · EOI · RST · 다시 SOI · 0
    {
        for (uint8_t m : {(uint8_t)0xFF, (uint8_t)0xD9, (uint8_t)0xD0, (uint8_t)0xD7, (uint8_t)0xD8, (uint8_t)0x00, (uint8_t)0x01, (uint8_t)0xBF}) {
            Bytes b{0xFF, 0xD8, 0xFF, m, 0x00, 0x04, 0, 0}; const Bytes rest = build(good); b.insert(b.end(), rest.begin() + 2, rest.end());
            CHECK(chk(b) == JPEG_HDR_BAD_MARKER);
        }
        Bytes b = build(good); b[2] = 0x7F; CHECK(chk(b) == JPEG_HDR_BAD_MARKER);        // FF 가 아니다
    }
    // 7. 양자화 표: 번호 4~15(IDF 는 qt_tbl[4] 밖에 쓴다) · 다섯 개째 · 16비트 · 길이가 65 의 배수가 아님 · 빈 조각
    {
        for (uint8_t id = 4; id <= 15; ++id) { Parts x = good; x.dqt = dqt_table(id); CHECK(chk(build(x)) == JPEG_HDR_BAD_DQT); }
        Parts five = good; five.dqt = cat(good.dqt, cat(good.dqt, dqt_table(0x02))); CHECK(chk(build(five)) == JPEG_HDR_BAD_DQT);
        Parts wide = good; wide.dqt = dqt_table(0x10); CHECK(chk(build(wide)) == JPEG_HDR_UNSUPPORTED);
        Parts cut = good; cut.dqt.pop_back(); CHECK(chk(build(cut)) == JPEG_HDR_BAD_DQT);
        Parts more = good; more.dqt.push_back(0); CHECK(chk(build(more)) == JPEG_HDR_BAD_DQT);
        Parts none = good; none.dqt.clear(); CHECK(chk(build(none)) == JPEG_HDR_BAD_DQT);
    }
    // 8. 크기·성분: 0 · 성분 0/2/4 · 12비트 · 프로그레시브 · 성분 표 번호 4 · SOF 두 번 · 표본 비
    {
        Parts x = good; x.sof = sof_payload(0, 720, {0x21, 0x11, 0x11}); CHECK(chk(build(x)) == JPEG_HDR_BAD_SOF);
        x.sof = sof_payload(1280, 0, {0x21, 0x11, 0x11}); CHECK(chk(build(x)) == JPEG_HDR_BAD_SOF);
        x.sof = sof_payload(1280, 720, {}); CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED);
        x.sof = sof_payload(1280, 720, {0x11, 0x11}); CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED);
        x.sof = sof_payload(1280, 720, {0x11, 0x11, 0x11, 0x11}); CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED);
        x.sof = sof_payload(1280, 720, {0x21, 0x11, 0x11}, 12); CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED);
        x.sof = sof_payload(3, 3, {0x11, 0x11, 0x11}); CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED);          // 가로×세로가 8 의 배수가 아니면 IDF 가 받지 않는다
        x.sof = good.sof; x.sof.push_back(0); CHECK(chk(build(x)) == JPEG_HDR_BAD_SOF);                       // 길이가 성분 수와 안 맞는다
        x.sof = good.sof; x.sof[8] = 4; CHECK(chk(build(x)) == JPEG_HDR_BAD_SOF);                             // 첫 성분의 양자화 표 번호 4
        x.sof = good.sof; x.sof.resize(5); CHECK(chk(build(x)) == JPEG_HDR_BAD_SOF);
        for (uint8_t hv : {(uint8_t)0x00, (uint8_t)0x12, (uint8_t)0x41, (uint8_t)0x44, (uint8_t)0x10, (uint8_t)0x01}) { x.sof = sof_payload(1280, 720, {hv, 0x11, 0x11}); CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED); }
        x.sof = sof_payload(1280, 720, {0x22, 0x21, 0x11}); CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED);     // 색 성분은 1×1 이어야
        x.sof = sof_payload(640, 480, {0x21}); x.sos = sos_payload(1); CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED);   // 흑백인데 2×1
        for (uint8_t m : {(uint8_t)0xC1, (uint8_t)0xC2, (uint8_t)0xC3, (uint8_t)0xC9, (uint8_t)0xCA, (uint8_t)0xCC, (uint8_t)0xCF}) {
            Bytes b{0xFF, 0xD8}; seg(b, 0xDB, good.dqt); seg(b, m, good.sof); seg(b, 0xDA, good.sos); b.push_back(0); b.push_back(0);
            CHECK(chk(b) == JPEG_HDR_UNSUPPORTED);
        }
        Bytes two{0xFF, 0xD8}; seg(two, 0xDB, good.dqt); seg(two, 0xC0, good.sof); seg(two, 0xC0, good.sof); seg(two, 0xDA, good.sos); two.push_back(0); two.push_back(0);
        CHECK(chk(two) == JPEG_HDR_BAD_SOF);
    }
    // 9. 허프만 표: 종류·번호 2 이상(IDF 는 huffbits[2][2] 밖에 쓴다) · 값이 표보다 많음 · 접두 부호가 될 수 없는 개수 · 잘림
    {
        Parts x = good;
        for (uint8_t t : {(uint8_t)0x20, (uint8_t)0x02, (uint8_t)0xF0, (uint8_t)0x0F, (uint8_t)0x12, (uint8_t)0x21}) { x.dht = dht_table(t, DC_L); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT); }
        uint8_t big[16] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 255}; uint8_t ok256[16] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 255};
        x.dht = dht_table(0x10, ok256); CHECK(chk(build(x)) == JPEG_HDR_OK);                                  // AC 256 개까지
        big[14] = 1; x.dht = dht_table(0x10, big); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT);                  // 257 개 — huffcode[256]·tmp_huff[256] 밖
        uint8_t dc17[16] = {0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 16}; x.dht = dht_table(0x00, dc17); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT);   // DC 17 개
        uint8_t dc16[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 16}; x.dht = dht_table(0x00, dc16); CHECK(chk(build(x)) == JPEG_HDR_OK);
        uint8_t kraft[16] = {3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}; x.dht = dht_table(0x00, kraft); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT);   // 1비트 부호 셋
        uint8_t kraft2[16] = {1, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}; x.dht = dht_table(0x00, kraft2); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT);
        uint8_t zero[16] = {}; x.dht = dht_table(0x00, zero); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT);       // 부호가 하나도 없다
        uint8_t all255[16]; memset(all255, 255, 16); x.dht = Bytes{0x10}; x.dht.insert(x.dht.end(), all255, all255 + 16); x.dht.resize(17 + 200, 7); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT);   // 합 4080
        x.dht = good.dht; x.dht.pop_back(); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT);
        x.dht = good.dht; x.dht.push_back(0); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT);
        x.dht.clear(); CHECK(chk(build(x)) == JPEG_HDR_BAD_DHT);
    }
    // 10. DRI 길이 · SOS(앞에 SOF 없음 · 성분 수 다름 · 표 번호 2 · 한 번에 푸는 baseline 이 아님) · SOS 뒤가 비었다
    {
        Bytes b{0xFF, 0xD8}; seg(b, 0xDB, good.dqt); seg(b, 0xC0, good.sof); seg(b, 0xDD, {0, 8, 0, 0}); seg(b, 0xDA, good.sos); b.push_back(0); b.push_back(0);
        CHECK(chk(b) == JPEG_HDR_BAD_DRI);
        Bytes nosof{0xFF, 0xD8}; seg(nosof, 0xDB, good.dqt); seg(nosof, 0xDA, good.sos); nosof.push_back(0); nosof.push_back(0); CHECK(chk(nosof) == JPEG_HDR_BAD_SOS);
        Parts x = good; x.sos = sos_payload(1); CHECK(chk(build(x)) == JPEG_HDR_BAD_SOS);
        x.sos = good.sos; x.sos[2] = 0x20; CHECK(chk(build(x)) == JPEG_HDR_BAD_SOS);
        x.sos = good.sos; x.sos[4] = 0x02; CHECK(chk(build(x)) == JPEG_HDR_BAD_SOS);
        x.sos = good.sos; x.sos.push_back(0); CHECK(chk(build(x)) == JPEG_HDR_BAD_SOS);
        x.sos.clear(); CHECK(chk(build(x)) == JPEG_HDR_BAD_SOS);
        x.sos = good.sos; x.sos[x.sos.size() - 2] = 5; CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED);          // Se = 5(프로그레시브 스캔)
        x.sos = good.sos; x.sos[x.sos.size() - 1] = 0x10; CHECK(chk(build(x)) == JPEG_HDR_UNSUPPORTED);
        x = good; x.scan.clear(); CHECK(chk(build(x)) == JPEG_HDR_NO_SCAN);
        x.scan = {0x55}; CHECK(chk(build(x)) == JPEG_HDR_NO_SCAN);
        x.scan = {0xFF, 0xD9}; CHECK(chk(build(x)) == JPEG_HDR_OK);                                           // 내용 없는 그림 — 머리는 멀쩡하다(풀다가 하드웨어가 오류를 낸다)
    }
    // 11. 어디서 잘려도 통과하지 않는다 — 끝 표시(FF D9)까지 다 온 것만 통과한다(버퍼 밖을 읽으면 GuardBuf 가 죽인다).
    //     머리가 다 온 뒤에 잘린 것은 "끝 표시 없음"으로 걸러진다: 그대로 두면 하드웨어가 오지 않을 데이터를 0.2~0.5초 기다린다(끝 4KB 안에 FF D9 가 없다 — 스캔 데이터 안에는 FF D9 가 나올 수 없다)
    {
        const Bytes b = build(good); jpeg_hdr_info_t info; chk(b, &info);
        const uint32_t header_end = info.scan_offset + 2 + (uint32_t)good.sos.size() + 2;                     // SOS 조각 끝
        bool ok = true, tail_is_no_eoi = true;
        for (uint32_t n = 0; n <= b.size(); ++n) {
            const jpeg_hdr_err_t e = chk(Bytes(b.begin(), b.begin() + n));
            ok = ok && ((e == JPEG_HDR_OK) == (n == b.size()));
            if (n >= header_end + 2 && n < b.size()) tail_is_no_eoi = tail_is_no_eoi && e == JPEG_HDR_NO_EOI;
        }
        CHECK(ok && tail_is_no_eoi);
    }
    // 16. 끝 표시 뒤에 무엇이 붙어 있어도(채움 · 다른 바이트 · 4KB 가까이) 마지막 FF D9 까지로 길이를 줄여 받는다(total_len) — 열린 쪽(MN-D27).
    //     끝 표시가 끝 4KB 안에 없으면 거른다. 꼬리에 FF D9 가 들어 있으면 거기까지를 그림으로 본다(그 장은 디코더가 오류로 끝낸다 — 기기는 멈추지 않는다)
    {
        const Bytes b = build(good); jpeg_hdr_info_t info;
        CHECK(chk(b, &info) == JPEG_HDR_OK && info.total_len == b.size() && info.scan_offset + info.scan_len == info.total_len);
        for (int pad : {1, 2, 63, 64, 65, 511, 512, 4094}) {
            Bytes z = b; z.insert(z.end(), pad, 0x00); CHECK(chk(z, &info) == JPEG_HDR_OK && info.total_len == b.size());
            Bytes f = b; f.insert(f.end(), pad, 0xFF); CHECK(chk(f, &info) == JPEG_HDR_OK && info.total_len == b.size());
            Bytes j = b; for (int i = 0; i < pad; ++i) j.push_back((uint8_t)(0x12 + i)); CHECK(chk(j, &info) == JPEG_HDR_OK && info.total_len == b.size());
        }
        Bytes far = b; far.insert(far.end(), 4095, 0x00); CHECK(chk(far) == JPEG_HDR_NO_EOI);                  // 끝 표시가 창(4096) 밖
        Bytes two = b; two.insert(two.end(), {0x00, 0xFF, 0xD9, 0x00}); CHECK(chk(two, &info) == JPEG_HDR_OK && info.total_len == b.size() + 3);   // 꼬리 안의 FF D9 가 끝
        Bytes cut = b; cut.pop_back(); CHECK(chk(cut) == JPEG_HDR_NO_EOI);                                    // FF 로 끝남
        Bytes d9 = b; d9[d9.size() - 2] = 0x00; CHECK(chk(d9) == JPEG_HDR_NO_EOI);                            // 00 D9
        const uint8_t tiny[2] = {0xFF, 0xD9};
        CHECK(jpeg_hdr_eoi_end(tiny, 0, 2) == 2 && jpeg_hdr_eoi_end(tiny, 0, 1) == 0 && jpeg_hdr_eoi_end(tiny, 0, 0) == 0 && jpeg_hdr_eoi_end(tiny, 1, 2) == 0);
        const Bytes ffs(200, 0xFF); CHECK(jpeg_hdr_eoi_end(G.put(ffs), 0, 200) == 0);                          // 채움만 있고 끝 표시가 없다(버퍼 앞을 넘겨 읽지 않는다)
        Bytes head_only(70, 0x00); head_only[0] = 0xFF; head_only[1] = 0xD9; CHECK(jpeg_hdr_eoi_end(G.put(head_only), 0, 70) == 2);   // 끝 표시 뒤의 긴 채움도 창 안이면 찾는다
        Bytes big(5000, 0x00); big[0] = 0xFF; big[1] = 0xD9; CHECK(jpeg_hdr_eoi_end(G.put(big), 0, 5000) == 0);   // 창 밖은 보지 않는다(스캔 전체를 훑지 않는다)
        CHECK(jpeg_hdr_eoi_end(G.put(big), 4000, 5000) == 0);
        // SOS 조각 앞(표 안)의 FF D9 는 찾지 않는다: 검사는 스캔 시작부터 본다
        Parts t = good; t.dqt[5] = 0xFF; t.dqt[6] = 0xD9; { Bytes x = build(t); x.resize(x.size() - 2); CHECK(chk(x) == JPEG_HDR_NO_EOI); }
    }
    // 12. 무작위로 망가뜨려도(바이트 바꾸기 · 끼우기 · 빼기 · 자르기): 통과한 것은 모두 IDF 파서 모형에서 안전하다
    {
        Parts with_dri = good; with_dri.with_dri = true;
        const Bytes bases[2] = {build(good), build(with_dri)};                     // DRI 가 있는 것 · 없는 것
        uint32_t accepted = 0, rejected = 0, unsafe = 0, by_err[JPEG_HDR_ERR_COUNT] = {};
        for (int it = 0; it < 400000; ++it) {
            Bytes b = bases[it & 1];
            const uint32_t hdr = (uint32_t)b.size();                              // 머리부터 끝 표시까지 어디든 망가뜨린다(끝이 잘리면 "끝 표시 없음")
            const uint32_t n_mut = 1 + rnd() % 4;
            for (uint32_t k = 0; k < n_mut; ++k) {
                const uint32_t at = rnd() % hdr, kind = rnd() % 8;
                if (at >= b.size()) continue;
                if (kind < 4) b[at] = (uint8_t)rnd();
                else if (kind == 4) b[at] ^= (uint8_t)(1u << (rnd() % 8));
                else if (kind == 5) b.insert(b.begin() + at, (uint8_t)rnd());
                else if (kind == 6) b.erase(b.begin() + at);
                else b.resize(at);
            }
            jpeg_hdr_info_t info; const jpeg_hdr_err_t e = chk(b, &info);
            ++by_err[e];
            if (e != JPEG_HDR_OK) { ++rejected; continue; }
            ++accepted;
            if (!model_safe(b, info)) { if (++unsafe <= 3) { printf("  unsafe accept at iteration %d:", it); for (size_t i = 0; i < b.size() && i < 48; ++i) printf(" %02x", b[i]); printf("\n"); } }
        }
        CHECK(unsafe == 0);
        CHECK(accepted > 1000 && rejected > 100000);                               // 시험이 실제로 두 쪽을 다 밟았다
        bool every_reason = true; for (int e = JPEG_HDR_NO_SOI; e < JPEG_HDR_ERR_COUNT; ++e) every_reason = every_reason && by_err[e] > 0;
        CHECK(every_reason);                                                       // 거부 까닭마다 한 번 이상 나왔다
        printf("  mutation: %u accepted (all safe in the IDF parser model), %u rejected [", (unsigned)accepted, (unsigned)rejected);
        for (int e = JPEG_HDR_NO_SOI; e < JPEG_HDR_ERR_COUNT; ++e) printf("%s%s %u", e > JPEG_HDR_NO_SOI ? ", " : "", jpeg_hdr_err_name((jpeg_hdr_err_t)e), (unsigned)by_err[e]);
        printf("]\n");
    }
    // 13. 모형이 제 구실을 하는가: 검사 없이 IDF 파서에 넣으면 위험한 입력을 모형이 실제로 위험하다고 하는지(검사가 막아 주는 것이 있다는 증거)
    {
        Parts a = good; a.dqt = dqt_table(0x0F); IdfModel m1; { const Bytes b = build(a); m1.run(b.data(), (uint32_t)b.size()); } CHECK(m1.oob);
        Parts c = good; c.dht = dht_table(0x33, DC_L); IdfModel m2; { const Bytes b = build(c); m2.run(b.data(), (uint32_t)b.size()); } CHECK(m2.oob);
        uint8_t all255[16]; memset(all255, 255, 16); Parts d = good; d.dht = Bytes{0x10}; d.dht.insert(d.dht.end(), all255, all255 + 16); d.dht.resize(17 + 4080, 7);
        IdfModel m3; { const Bytes b = build(d); m3.run(b.data(), (uint32_t)b.size()); } CHECK(m3.oob);
        Parts f = good; f.dqt = cat(good.dqt, cat(good.dqt, dqt_table(0x02))); IdfModel m4; { const Bytes b = build(f); m4.run(b.data(), (uint32_t)b.size()); } CHECK(m4.oob);
    }
    // 15. 흑백(성분 하나)은 멀쩡한 JPEG 이지만 거른다: 이 디코더는 RGB 로만 풀고, 그 조합에서 IDF 의 블록 폭 표는 0 이다
    {
        Parts g = good; g.sof = sof_payload(640, 480, {0x11}); g.sos = sos_payload(1); g.dqt = dqt_table(0x00);
        const Bytes b = build(g);
        CHECK(chk(b) == JPEG_HDR_UNSUPPORTED);
        IdfModel m; m.run(b.data(), (uint32_t)b.size());
        CHECK(!m.oob && !m.error && m.sos && m.nf == 1 && m.process_h == 640 && m.dma_block() == 0);   // IDF 파서는 받아들이고, 블록 폭은 0 — 검사가 없으면 여기까지 간다
        IdfModel c; { const Bytes x = build(good); c.run(x.data(), (uint32_t)x.size()); } CHECK(c.dma_block() != 0);
    }
    // 14. 까닭 이름은 모두 다르고 비어 있지 않다(STAT 줄에 찍힌다)
    {
        bool ok = true;
        for (int a = 0; a < JPEG_HDR_ERR_COUNT; ++a) for (int b = a + 1; b < JPEG_HDR_ERR_COUNT; ++b) {
            const char *x = jpeg_hdr_err_name((jpeg_hdr_err_t)a), *y = jpeg_hdr_err_name((jpeg_hdr_err_t)b);
            ok = ok && x[0] && y[0] && x[0] != '?' && y[0] != '?' && std::string_view(x) != std::string_view(y);
        }
        CHECK(ok && jpeg_hdr_err_name(JPEG_HDR_ERR_COUNT)[0] == '?');
    }
    printf("jpeg_header: %d checks, %d fail\n", checks, fails);
    return fails ? 1 : 0;
}
