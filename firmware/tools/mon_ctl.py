#!/usr/bin/env python3
"""mon_ctl.py — WideEye 탭5 펌웨어의 시리얼 도구. ESP-IDF export 를 하지 않은 셸에서 실행(pyserial·Pillow 필요).

  python3 tools/mon_ctl.py port                         기기 포트 경로만 출력(포트를 열지 않는다)
  python3 tools/mon_ctl.py listen 20                    20초 동안 로그 보기
  python3 tools/mon_ctl.py run <단계> <단계> ...
      wait:<정규식>:<초>     로그에 정규식이 나올 때까지(안 나오면 실패로 끝)
      sleep:<초>            로그를 보며 기다림
      shot:<파일.png>        화면 찍기 — 패널 프레임버퍼(720×1280)를 받아 기기의 화면 방향대로 돌려 저장(돌리기까지 거친 실제 출력)
      put:<파일.jpg>         시험 JPEG 올리기(자가 시험 — 그 뒤 `play <fps> <초>` 로 되풀이해 그리기, `bench` 로 속도 재기)
      frame:<파일.jpg>[:<나이ms>]  현황판 그림 보내기(맥과 같은 규약 — 4조각마다 확인. 나이 = 맥이 적는 그림의 나이, 기본 0)
      screen:<0|1>          현황판 화면 끄기·켜기(맥 화면 잠듦 흉내)
      그 밖                  그대로 한 줄 명령(stat, bench, bri 120, orient 1 …  기기에서 `help`)

기기 고르기: `--serial <USB 일련번호>`(맥의 시스템 정보 > USB 에 보이는 값 — WideEye 설정의 display.tab5_usb_serial) 또는
  `--port /dev/cu.usbmodemXXXX`. 기기를 여는 명령(listen · run)은 둘 중 하나가 있어야 한다 — 다른 ESP32 기기(전화기 · 스택짱 등)를
  탭5 로 알고 열지 않게(독립 검토 2026-11-02). 둘 다 없을 때의 `port` 는 꽂힌 Espressif 기기(USB VID 303A)가 하나면 그 경로를 보여 주기만 한다(TB-D5).
포트를 열면 기기가 한 번 리셋될 수 있다 → 한 시나리오는 한 번의 실행 안에서 끝낸다.
"""
import base64
import io
import re
import sys
import time

ESPRESSIF_VID = 0x303A


def find_port(serial=None):
    """일련번호가 있으면 그 기기, 없으면 꽂힌 Espressif 기기가 하나일 때만 그것. 못 고르면 까닭을 적고 끝낸다."""
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    if serial:
        for p in ports:
            if (p.serial_number or "").upper() == serial.upper():
                return p.device
        sys.exit(f"일련번호 {serial} 인 기기가 USB 에 보이지 않음 — 전원 · 데이터 케이블 확인")
    esp = [p for p in ports if p.vid == ESPRESSIF_VID]
    if len(esp) == 1:
        return esp[0].device
    if not esp:
        sys.exit("탭5 가 USB 에 보이지 않음 — 전원 · 데이터 케이블 확인")
    sys.exit(f"Espressif 기기가 {len(esp)}대 꽂혀 있다 — --serial <일련번호> 로 하나를 고른다: "
             + ", ".join(p.serial_number or "?" for p in esp))


class Session:
    def __init__(self, port):
        self.port = port
        self.s = None
        self.buf = b""
        self.log = []
        self.open()

    def open(self):
        import serial
        self.s = serial.Serial(self.port, 115200, timeout=0.05)  # DTR 은 기본값(켜짐) — 꺼서 열면 출력이 안 나온다

    def read(self):
        """받은 만큼 돌려준다. 기기가 다시 켜지며 포트가 잠깐 사라지면(재부팅·패닉) 기다렸다가 다시 연다."""
        import serial
        try:
            # 들어와 있는 만큼 바로 읽는다. 큰 크기를 청하면 pyserial 이 제한 시간 내내 기다리고, 그사이 맥 쪽 버퍼가 차면
            # 기기는 50ms 뒤부터 글자를 버린다(IDF 의 USB 콘솔) — 화면 찍기처럼 긴 출력이 깨져 오던 까닭이다
            n = self.s.in_waiting
            return self.s.read(n if n else 1)
        except (serial.SerialException, OSError):
            print("(port lost - reopening)")
            for _ in range(50):
                time.sleep(0.2)
                try:
                    self.s.close()
                except Exception:
                    pass
                try:
                    self.open()
                    return b""
                except (serial.SerialException, OSError):
                    continue
            raise

    def lines(self, secs):
        t0 = time.time()
        while time.time() - t0 < secs:
            d = self.read()
            if d:
                self.buf += d
            while b"\n" in self.buf:
                ln, self.buf = self.buf.split(b"\n", 1)
                yield ln.decode("utf-8", "replace").rstrip("\r")

    def pump(self, secs, pattern=None):
        rx = re.compile(pattern) if pattern else None
        for ln in self.lines(secs):
            if not ln.startswith("B64:"):
                print(ln)
            self.log.append(ln)
            if rx and rx.search(ln):
                return ln
        return None

    def send(self, cmd):
        self.s.write((cmd + "\n").encode())
        self.s.flush()

    def frame(self, path, age_ms=0):
        """현황판 그림 한 장을 규약(MN-D34)대로 보낸다: frame → +조각(4개마다 확인) → frameend."""
        import zlib
        data = open(path, "rb").read()
        self.rid = getattr(self, "rid", int(time.time()) % 900000) + 1
        rid, t0 = self.rid, time.time()
        self.send(f"frame {rid} {len(data)} {zlib.crc32(data) & 0xffffffff:08x} {int(age_ms)}")
        ln = self.pump(3, rf"(OK frame-begin|ERR frame) {rid}\b")
        if ln is None or "ERR" in ln:
            print(f"FRAME begin failed: {ln}")
            return False
        parts = [data[i:i + 1500] for i in range(0, len(data), 1500)]
        for i, part in enumerate(parts, 1):
            self.s.write(b"+" + base64.b64encode(part) + b"\n")
            if i % 4 == 0 and i != len(parts):
                self.s.flush()
                if self.pump(3, rf"(OK|ERR) \+ {rid}\b") is None:
                    print(f"FRAME ack {i} missing")
                    return False
        self.s.flush()
        self.send(f"frameend {rid}")
        ln = self.pump(5, rf"(OK|ERR) frame {rid}\b")
        ok = bool(ln) and ln.find(f"OK frame {rid}") >= 0
        print(f"frame {path}: {len(data)} B id={rid} {'OK' if ok else 'FAIL ' + str(ln)} {time.time() - t0:.2f}s")
        return ok

    def screen(self, on):
        self.rid = getattr(self, "rid", int(time.time()) % 900000) + 1
        self.send(f"screen {self.rid} {1 if on else 0}")
        return self.pump(3, rf"OK screen {self.rid}\b") is not None

    def put(self, path):
        import zlib
        data = open(path, "rb").read()
        self.send(f"put {len(data)}")
        ln = self.pump(5, r"OK put-begin|ERR put")                 # 줄 머리에 앞선 출력의 꼬리가 붙어 올 수 있다 → 줄 어디에 있든 찾는다
        if ln is None or "ERR put" in ln:
            print("PUT begin failed")
            return False
        for off in range(0, len(data), 1500):                     # 1500 B → base64 2000자 한 줄
            self.s.write(b"+" + base64.b64encode(data[off:off + 1500]) + b"\n")
            if off % 30000 == 0:
                self.s.flush()
                time.sleep(0.02)                                  # 기기가 받아 풀 틈
        self.s.flush()
        self.send("putend")
        ln = self.pump(10, r"OK put \d|ERR put")
        want = f"crc={zlib.crc32(data) & 0xffffffff:08x}"
        ok = bool(ln) and "OK put" in ln and want in ln
        print(f"put {path}: {len(data)} B {'OK' if ok else 'FAIL (want ' + want + ')'}")
        return ok

    def shot(self, path, cmd="shot", timeout=40):
        self.send(cmd)
        parts, size, kind, t0 = [], None, "", time.time()
        for ln in self.lines(timeout):
            if "SHOT BEGIN" in ln:                                # 줄 머리에 앞선 출력의 꼬리가 붙어 올 수 있다 → 줄 어디에 있든 찾는다
                f = ln[ln.index("SHOT BEGIN"):].split()
                size, kind, parts = int(f[2]), (f[3] if len(f) > 3 else ""), []
            elif ln.startswith("B64:") and size is not None:
                parts.append(ln[4:])
            elif ln.startswith("SHOT END") and size is not None:
                try:
                    data = base64.b64decode("".join(parts))
                except Exception as e:                            # 로그 줄이 끼어 깨진 경우
                    print(f"SHOT decode error {e}")
                    return False
                if len(data) != size:
                    print(f"SHOT size mismatch {len(data)}/{size}")
                    return False
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                img.load()
                # 패널 버퍼(세로 720×1280)를 기기의 화면 방향대로 돌려 저장한다: 0 가로 · 1 세로 · 2 가로 뒤집힘 · 3 세로 뒤집힘
                turn = {"panel-o0": Image.ROTATE_270, "panel-o1": None, "panel-o2": Image.ROTATE_90, "panel-o3": Image.ROTATE_180}.get(kind)
                if turn is not None:
                    img = img.transpose(turn)
                img.save(path)
                print(f"saved {path} {img.size[0]}x{img.size[1]} ({size} B jpeg, {kind}, {time.time() - t0:.1f}s)")
                return True
            elif ln.startswith("SHOT FAIL"):
                print("SHOT FAIL")
                return False
            elif not ln.startswith("B64:"):
                print(ln)
        print("SHOT timeout")
        return False


def main():
    args = sys.argv[1:]
    serial, port = None, None
    while args and args[0].startswith("--"):
        if args[0] == "--serial" and len(args) > 1:
            serial = args[1]; args = args[2:]
        elif args[0] == "--port" and len(args) > 1:
            port = args[1]; args = args[2:]
        else:
            sys.exit(__doc__)
    if not args:
        sys.exit(__doc__)
    if port is None:
        if serial is None and args[0] != "port":
            sys.exit("어느 기기인지 모른다 — --serial <탭5 의 USB 일련번호> 또는 --port <경로> 를 준다(WideEye 설정의 display.tab5_usb_serial)")
        port = find_port(serial)
    if args[0] == "port":
        print(port)
        return
    ses = Session(port)
    if args[0] == "listen":
        ses.pump(float(args[1]) if len(args) > 1 else 10)
        return
    if args[0] != "run":
        sys.exit(__doc__)
    ok = True
    for step in args[1:]:
        if step.startswith("wait:"):
            rx, secs = step[5:].rsplit(":", 1)
            if ses.pump(float(secs), rx) is None:
                print(f"WAIT FAIL /{rx}/ {secs}s")
                ok = False
                break
        elif step.startswith("sleep:"):
            ses.pump(float(step[6:]))
        elif step.startswith("frame:"):
            path, _, age = step[6:].partition(":")
            ok = ses.frame(path, int(age or 0)) and ok
        elif step.startswith("screen:"):
            ok = ses.screen(step[7:] == "1") and ok
        elif step.startswith("put:"):
            ok = ses.put(step[4:]) and ok
        elif step.startswith("shot:"):
            # 기기는 받는 쪽이 50ms 넘게 밀리면 글자를 버린다(IDF 의 USB 콘솔: 받는 쪽이 없을 때 멈추지 않으려는 장치) → 깨져 오면 두 번까지 다시 찍는다
            ok = any(ses.shot(step[5:]) for _ in range(3)) and ok
        else:
            ses.send(step)
            ses.pump(0.3)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
