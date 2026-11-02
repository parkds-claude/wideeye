#!/usr/bin/env python3
"""화면을 그림 파일로 뜬다 — 탭5 창과 같은 엔진(WebKit)으로, 화면에 창을 띄우지 않고.

탭5 가 붙어 있지 않아도, 사용자가 화면을 쓰고 있어도 배치를 확인할 수 있다(헤드리스 Chrome 을 쓰지 않는다 — 실제 창은 WebKit 이다).
쓰기: .venv/bin/python tools/snap.py <주소> <저장할 png> [기다릴 초=3] [너비=960] [높이=540] [찍기 전에 돌릴 스크립트]
    스크립트를 주면 찍기 1초 전에 페이지 안에서 돌린다 — 끊김(`lost()`)처럼 서버 없이 만들기 어려운 상태를 볼 때.
    예) tools/preview.py busy 5093 을 띄워 둔 뒤   tools/snap.py http://127.0.0.1:5093/ /tmp/busy.png
끝나면 0, 시간 안에 뜨지 못하면 1.
"""
from __future__ import annotations
import os
import struct
import sys

TIMEOUT_EXTRA = 20.0          # 페이지를 읽고 그림을 뜨는 데 주는 여유(초). 넘으면 실패로 끝낸다


# 남기는 조각: 그림 · 팔레트 · 투명 · 색 공간 이름(sRGB) · 감마 · 크기 단위 · 색도만. 그 밖(글 · EXIF · XMP · 시각 · iCCP 색 프로필 —
# 장치 · 모니터 이름이 든다 · 모르는 조각)과 IEND 뒤 꼬리는 뗀다(공개판 검사기 scan.PNG_OK 와 같은 목록 — 검토 1 I3).
KEEP_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"sRGB", b"gAMA", b"pHYs", b"cHRM"}


def strip_png_metadata(data: bytes) -> bytes:
    """PNG 에서 그림 조각만 남긴다(픽셀은 그대로). PNG 가 아니거나 깨졌으면 받은 그대로 돌려준다.
    왜: 맥이 저장하는 PNG 에는 EXIF · 색 프로필 등이 붙을 수 있고, 공개판 그림에 그런 꼬리표가 남으면 안 된다."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return data
    out, i = [data[:8]], 8
    while i + 12 <= len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        end = i + 12 + n
        if end > len(data):
            return data
        kind = data[i + 4:i + 8]
        if kind in KEEP_CHUNKS:
            out.append(data[i:end])
        i = end
        if kind == b"IEND":
            return b"".join(out)                       # 뒤의 꼬리는 버린다
    return data


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    url, out = sys.argv[1], sys.argv[2]
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    width = int(sys.argv[4]) if len(sys.argv) > 4 else 960
    height = int(sys.argv[5]) if len(sys.argv) > 5 else 540
    script = sys.argv[6] if len(sys.argv) > 6 else ""
    from AppKit import (NSApplication, NSApplicationActivationPolicyProhibited, NSBackingStoreBuffered, NSBitmapImageRep,
                        NSPNGFileType, NSWindow, NSWindowStyleMaskBorderless)
    from Foundation import NSMakeRect, NSObject, NSTimer, NSURL, NSURLRequest
    from WebKit import WKSnapshotConfiguration, WKWebView, WKWebViewConfiguration, WKWebsiteDataStore

    class Snapper(NSObject):
        def open(self):
            conf = WKWebViewConfiguration.alloc().init()
            conf.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
            rect = NSMakeRect(0, 0, width, height)
            # 화면 밖 좌표에 두고 앞으로 내지 않는다 — 어느 화면에도 보이지 않는다
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(-20000, -20000, width, height), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
            self.window.setReleasedWhenClosed_(False)
            self.web = WKWebView.alloc().initWithFrame_configuration_(rect, conf)
            self.window.setContentView_(self.web)
            self.web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
            if script:
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(max(0.2, wait - 1.0), self, "prepare:", None, False)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(wait, self, "shoot:", None, False)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(wait + TIMEOUT_EXTRA, self, "giveUp:", None, False)

        def prepare_(self, _timer):
            self.web.evaluateJavaScript_completionHandler_(script, None)

        def shoot_(self, _timer):
            conf = WKSnapshotConfiguration.alloc().init()
            conf.setRect_(NSMakeRect(0, 0, width, height))

            def done(image, error):
                ok = False
                if image is not None:
                    rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    data = rep.representationUsingType_properties_(NSPNGFileType, None)
                    with open(out, "wb") as f:
                        f.write(strip_png_metadata(bytes(data)))
                    ok = True
                os._exit(0 if ok else 1)              # 창 하나뿐인 일회용 프로세스 — 바로 끝낸다

            self.web.takeSnapshotWithConfiguration_completionHandler_(conf, done)

        def giveUp_(self, _timer):
            os._exit(1)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)      # Dock·메뉴 없음, 앞으로 나서지 않는다
    snapper = Snapper.alloc().init()
    snapper.open()
    app.run()
    return 1


if __name__ == "__main__":
    sys.exit(main())
