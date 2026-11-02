"""tools/snap.py 의 그림 정리 — 글 · EXIF · XMP 조각을 떼어 낸다(공개판 그림에 맥 이름 같은 메타가 묻지 않게)."""
import struct
import zlib

from tools import snap


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(*extra: tuple[bytes, bytes]) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    parts = [chunk(b"IHDR", ihdr), chunk(b"sRGB", b"\x00")] + [chunk(k, b) for k, b in extra] + [chunk(b"IDAT", idat), chunk(b"IEND", b"")]
    return b"\x89PNG\r\n\x1a\n" + b"".join(parts)


def kinds(data: bytes) -> list[bytes]:
    out, i = [], 8
    while i < len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        out.append(data[i + 4:i + 8])
        i += 12 + n
    return out


def test_strip_removes_text_exif_and_time_but_keeps_image_and_colour_chunks():
    src = png((b"tEXt", b"Author\x00someone"), (b"eXIf", b"MM\x00*"), (b"iTXt", b"XML:com.adobe.xmp\x00x"), (b"zTXt", b"k\x00\x00x"), (b"tIME", b"\x07\xea\x0a\x12\x00\x00\x00"))
    out = snap.strip_png_metadata(src)
    assert kinds(out) == [b"IHDR", b"sRGB", b"IDAT", b"IEND"]
    assert b"someone" not in out and b"xmp" not in out


def test_strip_leaves_a_clean_png_untouched_and_ignores_non_png():
    clean = png()
    assert snap.strip_png_metadata(clean) == clean
    assert snap.strip_png_metadata(b"not a png") == b"not a png"


def test_strip_keeps_the_pixels_identical():
    src = png((b"tEXt", b"a\x00b"))
    out = snap.strip_png_metadata(src)
    idat = lambda d: b"".join(d[i + 8:i + 8 + struct.unpack(">I", d[i:i + 4])[0]] for i in _offsets(d) if d[i + 4:i + 8] == b"IDAT")
    assert idat(out) == idat(src)


def _offsets(d):
    i = 8
    while i < len(d):
        yield i
        i += 12 + struct.unpack(">I", d[i:i + 4])[0]


def test_strip_keeps_only_plain_image_chunks_and_drops_the_colour_profile_and_tail():
    """검토 1 I3: iCCP(색 프로필 — 장치 이름이 든다)와 모르는 조각 · IEND 뒤 꼬리도 뗀다. 그림(IDAT)은 같다."""
    src = png((b"iCCP", b"Capture Device\x00\x00" + zlib.compress(b"p")), (b"abCd", b"x")) + b"tail"
    out = snap.strip_png_metadata(src)
    assert kinds(out) == [b"IHDR", b"sRGB", b"IDAT", b"IEND"] and b"Capture" not in out and not out.endswith(b"tail")
