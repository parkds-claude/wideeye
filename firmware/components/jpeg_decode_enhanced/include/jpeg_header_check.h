/*
 * jpeg_header_check.h — [tab5_monitor J6] strict check of a baseline JPEG header, before any of it reaches the hardware.
 *
 * Why this exists. ESP-IDF v5.5.4's marker parser (esp_driver_jpeg/jpeg_parse_marker.c) trusts the stream:
 *   - DQT : table id is `n & 0x0F` and indexes qt_tbl[4]; every table bumps qt_tbl_num, which later indexes dqt_func[4].
 *   - DHT : class/id are 4 bits each and index huffbits[2][2] / huffcode[2][2][256]; the 16 counts are summed (up to 4080)
 *           and that many bytes are copied into a 256-byte row, then jpeg_hal_create_minicode_tbl() writes as many words
 *           into tmp_huff[256].
 *   - A stream with no SOF0/SOS at all parses "successfully" with a 0 x 0 picture; starting a 2D-DMA transaction for
 *     0 x 0 trips `assert(dma2d_ll_rx_is_fsm_idle(...))` in the DMA interrupt handler -> panic.
 *     (Reproduced on the device 2026-10-13 with the two bytes FF D8 followed by zeros.)
 * A capture dongle on a marginal cable, or a transfer spliced across two frames, is exactly such a stream. So everything
 * that the IDF parser would index with, and everything the DMA needs to be non-zero, is checked here first.
 *
 * What passes: SOI, then only APPn / COM / DQT / SOF0 / DHT / DRI segments laid out back to back (no fill bytes — the IDF
 * parser walks marker pairs and would lose sync on them), exactly one SOF0, then SOS. 8-bit baseline, 3 components (YCbCr),
 * luma sampling 1x1 / 2x1 / 2x2 with 1x1 chroma. What the scan data contains is the hardware's business: it reports
 * bitstream errors by itself and those paths do not touch memory. One thing about the scan is checked because it is free:
 * the data must contain the EOI marker near its end (last 4 KB) — a frame cut off in transit is refused here instead of timing
 * out in the decoder. Whatever follows the last FF D9 (padding, a capture dongle's trailer) is cut off: the decoder is handed
 * exactly total_len bytes (MN-D27: fail-open — review 3a C-1).
 *
 * Gray (1 component) is refused although it is a valid baseline JPEG: this decoder path only ever produces RGB, and for
 * gray-in/RGB-out IDF's block-size table (dec_hb_tbl) holds 0 — a zero-width 2D-DMA block, the same family as the 0 x 0
 * transfer above. IDF's own decoder refuses the combination too ("gray style picture, but your output format is wrong").
 * (Independent pre-review 2a, I-1. The decoder additionally refuses any frame whose DMA block size comes out as 0.)
 *
 * Scope of the guarantee: an accepted header is safe for the IDF *marker parser* (indices, lengths, non-zero size) and
 * selects a non-zero DMA block for RGB565/RGB888 output. It says nothing about the scan data.
 *
 * Pure C, no allocation, fixed-width integers (the firmware is 32-bit, the host tests are 64-bit). Host test:
 * tools/test_jpeg_header.cpp, which also replays the IDF parser's indexing on everything this accepts.
 */
#pragma once

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    JPEG_HDR_OK = 0,
    JPEG_HDR_NO_SOI,        // empty, shorter than a header can be, or not starting with FF D8
    JPEG_HDR_BAD_MARKER,    // no FF xx where a marker must be, or a marker that may not appear before the scan
    JPEG_HDR_BAD_LENGTH,    // a segment length below 2, or running past the end of the data
    JPEG_HDR_BAD_DQT,
    JPEG_HDR_BAD_SOF,
    JPEG_HDR_BAD_DHT,
    JPEG_HDR_BAD_DRI,
    JPEG_HDR_BAD_SOS,
    JPEG_HDR_NO_SCAN,       // header is fine but nothing follows SOS
    JPEG_HDR_NO_EOI,        // no FF D9 in the last JPEG_HDR_TAIL_WINDOW bytes of the scan: the frame was cut off on the way
    JPEG_HDR_UNSUPPORTED,   // well-formed, but not what this decoder path handles (progressive, 12/16-bit, odd sampling ...)
    JPEG_HDR_ERR_COUNT
} jpeg_hdr_err_t;

typedef struct {
    uint16_t width, height;     // as written in SOF0 (both non-zero)
    uint8_t  nf;                // 3 (YCbCr) — gray is refused, see above
    uint8_t  hv0;               // luma sampling byte: 0x11, 0x21 or 0x22
    uint32_t scan_offset;       // offset of the SOS marker — the hardware is fed from here
    uint32_t scan_len;          // bytes from there up to and including the EOI marker (what the hardware is given)
    uint32_t total_len;         // scan_offset + scan_len: the length to hand to the decoder — anything after it is a trailer and is dropped
} jpeg_hdr_info_t;

enum {
    JPEG_HDR_MAX_QT        = 4,     // qt_tbl[] / dqt_func[] in IDF
    JPEG_HDR_MAX_DC_VALUES = 16,    // JPEG_HUFFMAN_DC_VALUE_TABLE_LEN
    JPEG_HDR_MAX_AC_VALUES = 256,   // JPEG_HUFFMAN_AC_VALUE_TABLE_LEN == huffcode row == tmp_huff
};

static inline const char *jpeg_hdr_err_name(jpeg_hdr_err_t e)
{
    switch (e) {
    case JPEG_HDR_OK:          return "ok";
    case JPEG_HDR_NO_SOI:      return "no-soi";
    case JPEG_HDR_BAD_MARKER:  return "marker";
    case JPEG_HDR_BAD_LENGTH:  return "length";
    case JPEG_HDR_BAD_DQT:     return "dqt";
    case JPEG_HDR_BAD_SOF:     return "sof";
    case JPEG_HDR_BAD_DHT:     return "dht";
    case JPEG_HDR_BAD_DRI:     return "dri";
    case JPEG_HDR_BAD_SOS:     return "sos";
    case JPEG_HDR_NO_SCAN:     return "no-scan";
    case JPEG_HDR_NO_EOI:      return "no-eoi";
    case JPEG_HDR_UNSUPPORTED: return "unsupported";
    default:                   return "?";
    }
}

static inline uint32_t jpeg_hdr_be16(const uint8_t *p) { return ((uint32_t)p[0] << 8) | (uint32_t)p[1]; }

// One Huffman table spec inside a DHT segment: [Tc|Th][16 counts][values...]. Returns its size, or 0 if it is not usable.
static inline uint32_t jpeg_hdr_dht_table_size(const uint8_t *t, uint32_t avail)
{
    if (avail < 17) return 0;
    const uint32_t tc = t[0] >> 4, th = t[0] & 0x0F;
    if (tc > 1 || th > 1) return 0;
    uint32_t np = 0, code = 0;
    for (uint32_t l = 1; l <= 16; l++) {            // counts must form a prefix code: no more than 2^l codes of length l
        const uint32_t n = t[l];
        np += n;
        code += n;
        if (code > (1u << l)) return 0;
        code <<= 1;
    }
    if (np == 0 || np > (tc ? (uint32_t)JPEG_HDR_MAX_AC_VALUES : (uint32_t)JPEG_HDR_MAX_DC_VALUES)) return 0;
    if (avail < 17 + np) return 0;
    return 17 + np;
}

// Where does the picture end? Returns the offset just past the LAST FF D9 found in p[from, len) while looking back at most
// JPEG_HDR_TAIL_WINDOW bytes from the end, or 0 if there is none. Inside entropy-coded data FF is always followed by 00 or
// RSTn (D0-D7), so an FF D9 there can only be the EOI marker — the search is exact, not heuristic (review 3a C-1).
// Why not "the data must end with FF D9 (+ a little padding)": that was closed-fail — a capture dongle that appends
// anything else (a trailer, more than 64 bytes of padding) would have had every frame refused and the screen stay black
// (MN-D27). A frame cut off in transit has no EOI anywhere — without this the hardware decoder only finds out by waiting
// for data that never comes (200-500 ms per frame on this board, review 2 M-2).
enum { JPEG_HDR_TAIL_WINDOW = 4096 };
static inline uint32_t jpeg_hdr_eoi_end(const uint8_t *p, uint32_t from, uint32_t len)
{
    if (len < 2 || from > len - 2) return 0;
    const uint32_t stop = len - from > (uint32_t)JPEG_HDR_TAIL_WINDOW ? len - (uint32_t)JPEG_HDR_TAIL_WINDOW : from;   // lowest marker start we look at
    for (uint32_t e = len - 2; ; e--) {                              // e = candidate marker start, walking back
        if (p[e] == 0xFF && p[e + 1] == 0xD9) return e + 2;
        if (e == stop) return 0;
    }
}

static inline jpeg_hdr_err_t jpeg_hdr_check(const uint8_t *p, uint32_t len, jpeg_hdr_info_t *out)
{
    jpeg_hdr_info_t info;
    info.width = 0; info.height = 0; info.nf = 0; info.hv0 = 0; info.scan_offset = 0; info.scan_len = 0; info.total_len = 0;
    if (out) *out = info;
    if (!p || len < 4 || p[0] != 0xFF || p[1] != 0xD8) return JPEG_HDR_NO_SOI;

    uint32_t pos = 2;                                // always <= len
    uint32_t n_qt = 0;
    bool have_sof = false;
    for (;;) {
        if (len - pos < 4) return JPEG_HDR_BAD_LENGTH;
        if (p[pos] != 0xFF) return JPEG_HDR_BAD_MARKER;
        const uint8_t  m = p[pos + 1];
        const uint32_t L = jpeg_hdr_be16(p + pos + 2);
        if (L < 2 || L > len - pos - 2) return JPEG_HDR_BAD_LENGTH;
        const uint8_t *seg = p + pos + 4;            // segment payload, n bytes
        uint32_t n = L - 2;

        if ((m >= 0xE0 && m <= 0xEF) || m == 0xFE) {             // APPn, COM: skipped
        } else if (m == 0xDB) {                                  // DQT: [Pq|Tq][64 bytes] ...
            if (n == 0) return JPEG_HDR_BAD_DQT;
            while (n) {
                if (n < 65) return JPEG_HDR_BAD_DQT;
                if (seg[0] >> 4) return JPEG_HDR_UNSUPPORTED;    // 16-bit tables are not baseline
                if ((seg[0] & 0x0F) >= JPEG_HDR_MAX_QT) return JPEG_HDR_BAD_DQT;
                if (++n_qt > JPEG_HDR_MAX_QT) return JPEG_HDR_BAD_DQT;
                seg += 65; n -= 65;
            }
        } else if (m == 0xC0) {                                  // SOF0: P, Y, X, Nf, then (C, HV, Tq) per component
            if (have_sof || n < 6) return JPEG_HDR_BAD_SOF;
            if (seg[0] != 8) return JPEG_HDR_UNSUPPORTED;
            info.height = (uint16_t)jpeg_hdr_be16(seg + 1);
            info.width  = (uint16_t)jpeg_hdr_be16(seg + 3);
            info.nf     = seg[5];
            if (info.nf != 3) return JPEG_HDR_UNSUPPORTED;      // gray, CMYK, 2 components
            if (n != 6u + 3u * info.nf) return JPEG_HDR_BAD_SOF;
            if (info.width == 0 || info.height == 0) return JPEG_HDR_BAD_SOF;
            if (((uint32_t)info.width * info.height) % 8 != 0) return JPEG_HDR_UNSUPPORTED;   // the IDF parser's own rule
            for (uint32_t i = 0; i < info.nf; i++) {
                const uint8_t hv = seg[6 + 3 * i + 1], tq = seg[6 + 3 * i + 2];
                if (tq >= JPEG_HDR_MAX_QT) return JPEG_HDR_BAD_SOF;
                if (i == 0) {
                    if (!(hv == 0x11 || hv == 0x21 || hv == 0x22)) return JPEG_HDR_UNSUPPORTED;
                    info.hv0 = hv;
                } else if (hv != 0x11) {
                    return JPEG_HDR_UNSUPPORTED;
                }
            }
            have_sof = true;
        } else if (m == 0xC4) {                                  // DHT: one or more table specs
            if (n == 0) return JPEG_HDR_BAD_DHT;
            while (n) {
                const uint32_t sz = jpeg_hdr_dht_table_size(seg, n);
                if (sz == 0) return JPEG_HDR_BAD_DHT;
                seg += sz; n -= sz;
            }
        } else if (m == 0xDD) {                                  // DRI
            if (L != 4) return JPEG_HDR_BAD_DRI;
        } else if (m == 0xDA) {                                  // SOS: Ns, (Cs, Td|Ta) per component, Ss, Se, Ah|Al
            if (!have_sof || n < 1) return JPEG_HDR_BAD_SOS;
            if (seg[0] != info.nf || n != 1u + 2u * info.nf + 3u) return JPEG_HDR_BAD_SOS;
            for (uint32_t i = 0; i < info.nf; i++) {
                const uint8_t tdta = seg[1 + 2 * i + 1];
                if ((tdta >> 4) > 1 || (tdta & 0x0F) > 1) return JPEG_HDR_BAD_SOS;
            }
            const uint8_t *tail = seg + 1 + 2 * info.nf;
            if (tail[0] != 0 || tail[1] != 63 || tail[2] != 0) return JPEG_HDR_UNSUPPORTED;   // not a single baseline scan
            if (len - (pos + 2 + L) < 2) return JPEG_HDR_NO_SCAN;
            const uint32_t end = jpeg_hdr_eoi_end(p, pos + 2 + L, len);   // only after the SOS segment: a table could hold FF D9 by chance
            if (!end) return JPEG_HDR_NO_EOI;
            info.scan_offset = pos;
            info.scan_len = end - pos;
            info.total_len = end;
            if (out) *out = info;
            return JPEG_HDR_OK;
        } else if (m >= 0xC1 && m <= 0xCF) {                     // other SOFn, JPG, DAC: real JPEG, not baseline Huffman
            return JPEG_HDR_UNSUPPORTED;
        } else {                                                 // SOI again, EOI, RSTn, fill bytes, anything else
            return JPEG_HDR_BAD_MARKER;
        }
        pos += 2 + L;
    }
}

#ifdef __cplusplus
}
#endif
