#!/usr/bin/env python3
"""탭5 연결 — 맥이 뜬 현황판 그림을 USB-C 직렬로 탭5 에 보낸다(설계 docs/exec-plans/2026-10-15-tab5-usb-frames-design.md 2-3 · 2-5).

일꾼 스레드 하나가 포트를 독점(TIOCEXCL)으로 쥔다. 보낼 그림 칸은 하나 — 새 것이 앞의 것을 덮는다. 보낼 때 나이가 90초를 넘은 그림은
버린다(다시 연결했을 때 옛 그림을 살아 있는 것처럼 보내지 않게 — 독립 검토 C1). 탭5 의 STAT 줄(5초마다)로 탭5 상태를 맞춘다
(다시 켜졌는가 · 마지막 그림을 그렸는가 · 화면 켜짐). screen 은 바뀌면 곧바로, 그 뒤로도 30초마다 보낸다(탭5 는 90초 끊기면 SLEEP 을 푼다).
import · 생성자는 아무것도 띄우지 않는다 — start() 에서만. 일꾼은 AppKit 을 부르지 않는다.
다른 도구가 포트를 쓰려면 `tools/kiosk.sh tab5 pause` — 놓기 파일(state/tab5-link.pause, 첫 줄 = 만료 epoch 초)을 보고 포트를 닫는다.
"""
from __future__ import annotations
import base64
import json
import os
import re
import threading
import time
import zlib
from pathlib import Path

CHUNK = 1500                  # 원본 바이트 → base64 2,000자 한 줄(펌웨어 줄 버퍼 2,400)
ACK_EVERY = 4                 # 펌웨어 brx::ACK_EVERY 와 같아야 한다
MAX_AGE_MS = 90_000           # 보낼 때 이보다 나이 든 그림은 버린다
SCREEN_EVERY = 30.0           # screen 신호 간격
REPLY_TIMEOUT = 3.0           # 답을 기다리는 한도
WRITE_TIMEOUT = 2.0           # 쓰기가 막히는 한도(탭5 가 멈춰 USB 가 NAK 만 낼 때)
UNKNOWN_REST = 300.0          # 펌웨어가 frame 을 모르면 이만큼 쉰다
RESEND_GAP = 30.0             # 탭5 가 그린 그림이 없다고(STAT bseq=0) 할 때 같은 그림을 다시 보내는 최소 간격(MON BOOT 에는 곧바로)
STATE_EVERY = 30.0            # 상태 파일을 적는 간격(바뀔 때도 적는다)
BACKOFF = (2, 4, 8, 16, 30)   # 붙어 있는데 열기 · 주고받기가 잇달아 실패할 때 다시 하는 간격(초)
ABSENT_POLL = 5.0             # 탭5 가 USB 목록에 없을 때 다시 찾는 간격 — 늘리지 않는다(다시 붙으면 30초 안에 그림이 가게 · 검토 초점 1)
LINE_MAX = 16_384             # 받은 글을 쌓아 두는 한도(줄바꿈 없는 잡음이 이어져도 끝없이 늘지 않게)
TIOCEXCL = 0x2000740D         # macOS <sys/ttycom.h> — 다른 프로세스의 열기를 막는다(termios.TIOCEXCL 과 같은 값)
PAUSE_FILE, STATE_FILE = "tab5-link.pause", "tab5-link.json"


def mono() -> float:
    """잠잔 시간까지 세는 단조 시계(초). time.monotonic 은 맥이 잠자는 동안 멈춘다 — 8시간 재웠다 깨우면 8시간 묵은 그림을
    "몇 초 전 것"으로 보내게 된다(독립 검토 8). macOS 의 CLOCK_MONOTONIC 은 잠자는 동안에도 간다. 창의 내용 시각도 이 시계로 잰다."""
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def frame_lines(rid: int, jpeg: bytes, age_ms: int) -> tuple[bytes, list[bytes], bytes]:
    """그림 한 장을 규약의 줄로: 머리(frame) · 조각들(+base64) · 끝(frameend)."""
    head = f"frame {rid} {len(jpeg)} {zlib.crc32(jpeg) & 0xFFFFFFFF:08x} {max(0, int(age_ms))}\n".encode()
    chunks = [b"+" + base64.b64encode(jpeg[i:i + CHUNK]) + b"\n" for i in range(0, len(jpeg), CHUNK)]
    return head, chunks, f"frameend {rid}\n".encode()


def find_reply(text: str, kind: str, rid: int) -> tuple[bool, str] | None:
    """줄 어디에 있든 `OK|ERR <kind> <rid>` 를 찾는다(앞선 출력의 꼬리가 줄 머리에 붙어 올 수 있다). (OK 인가, 뒤의 나머지)."""
    m = re.search(rf"(?<![\w-])(OK|ERR) {re.escape(kind)} {rid}(?!\d)\s*(.*)$", text)
    return (m.group(1) == "OK", m.group(2).strip()) if m else None


def classify_unknown(text: str, sent: str) -> str | None:
    """`ERR unknown '<받은 줄>' (help)` 을 가른다: 보낸 줄이 그대로 되돌아왔으면 "old"(옛 펌웨어 — 명령을 모른다),
    다르게 되돌아왔으면 "garbled"(선에서 글자가 빠져 줄이 깨졌다 — 다시 보내면 된다), 그런 답이 아니면 None.
    깨진 머리 줄 하나로 5분을 쉬지 않게(2026-10-15 기기에서 받는 쪽의 글자 손실을 봤다)."""
    m = re.search(r"ERR unknown '(.*)' \(help\)", text)
    if m is None:
        return None
    return "old" if sent and m.group(1) == sent.strip() else "garbled"


def parse_stat(line: str) -> dict | None:
    """탭5 의 STAT 줄에서 현황판 꼬리를 읽는다. 꼬리가 없으면(옛 펌웨어 · 다른 줄) None.
    orient = 탭5 의 방향(0 가로 · 1 세로 · 2 가로 뒤집힘 · 3 세로 뒤집힘 — 탭5 펌웨어 MN-D41), 칸이 없는 펌웨어면 None(가로)."""
    if "STAT " not in line or "board=" not in line:
        return None
    f = dict(re.findall(r"(\w+)=(\S+)", line[line.index("STAT "):]))
    try:
        st = {"board": f["board"], "bseq": int(f["bseq"]), "scr": int(f["scr"]), "bage": int(f["bage"]),
              "temp": float(f.get("temp", "nan"))}
    except (KeyError, ValueError):
        return None
    o = f.get("orient", "")
    st["orient"] = int(o) if o.isdigit() and int(o) <= 3 else None
    return st


def orient_mode(orient: int | None) -> str:
    """탭5 의 방향 → 맥이 그릴 꼴. 세로(1 · 3)만 "portrait" — 뒤집힘(180°)은 탭5 가 돌려 그린다(설계 D2). 모르면 가로."""
    return "portrait" if orient in (1, 3) else "landscape"


def remembered_orient(state_dir: Path) -> int | None:
    """앞서 적어 둔 상태 파일의 방향 — 창이 다시 떠 첫 STAT 이 오기 전까지만 쓴다(설계 D3). 못 읽으면 None."""
    try:
        o = json.loads((Path(state_dir) / STATE_FILE).read_text(encoding="utf-8")).get("orient")
    except (OSError, ValueError, AttributeError):
        return None
    return o if isinstance(o, int) and 0 <= o <= 3 else None


def pick_port(ports, serial: str) -> str | None:
    """USB 일련번호가 같은 포트. 일련번호를 모르면 아무것도 고르지 않는다(남의 기기에 그림을 보내지 않게)."""
    want = (serial or "").strip().upper()
    if not want:
        return None
    for p in ports:
        if (getattr(p, "serial_number", None) or "").upper() == want:
            return p.device
    return None


def backoff(n: int) -> float:
    return float(BACKOFF[min(max(n, 0), len(BACKOFF) - 1)])


def read_pause(path: Path, now: float) -> float | None:
    """놓기 파일이 살아 있으면 만료 시각. 만료됐으면 지우고 None. 없거나 못 읽으면 None."""
    try:
        until = float(Path(path).read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if until <= now:
        try:
            Path(path).unlink()
        except OSError:
            pass
        return None
    return until


def _open_serial(device: str):
    """탭5 포트를 연다: DTR 켬 · RTS 끔(열 때 칩이 리셋되지 않게), 연 뒤 TIOCEXCL 로 독점."""
    import fcntl
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = device, 115200, 0.05, WRITE_TIMEOUT
    s.dtr, s.rts = True, False
    s.exclusive = True                                   # flock — esptool · idf monitor 가 쥔 동안에는 열리지 않고, 우리가 쥔 동안에는 그들이 못 연다
    s.open()
    try:
        fcntl.ioctl(s.fileno(), TIOCEXCL)
    except OSError:
        s.close()
        raise
    return s


def _list_ports():
    from serial.tools import list_ports
    return list_ports.comports()


class _Unknown(Exception):
    """펌웨어가 명령을 모른다(옛 펌웨어)."""


class Tab5Link:
    def __init__(self, serial_number: str, state_dir: Path, *, log=print, opener=None, lister=None,
                 clock=mono, wall=time.time):
        self.serial_number, self.state_dir, self.log = serial_number, Path(state_dir), log
        self.opener, self.lister, self.clock, self.wall = opener or _open_serial, lister or _list_ports, clock, wall
        self._lock = threading.Lock()
        self._pending: tuple[bytes, float, str] | None = None   # (그림, 내용 시각 — monotonic, 꼴 landscape|portrait) — 아직 일꾼이 안 가져간 것
        self._newest: tuple[bytes, float, str] | None = None    # 일꾼이 가진 가장 새 그림 — 탭5 가 잃으면 다시 보낸다
        self._skip_note = ""                                     # 방향이 달라 보내지 않은 까닭(같은 것을 되풀이해 적지 않는다)
        self._screen = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._port = None
        self._buf = ""
        self._rid = 0
        self._beat = clock()
        self._need_frame = False
        self._need_screen = True
        self._screen_sent_at = float("-inf")
        self._sent_at = float("-inf")                            # 마지막으로 그림을 보내 성공한 때
        self._rest_until = float("-inf")
        self._fails = 0
        self._absent = False                                     # 앞서 탭5 가 USB 목록에 없었다(다시 붙으면 쉼 간격을 처음부터)
        self._st = {"connected": False, "paused": False, "port": None, "frames_ok": 0, "frames_err": 0, "reconnects": 0,
                    "last_ok_at": None, "last_error": None, "tab5": None, "sent_seq": 0, "screen": True,
                    "orient": remembered_orient(self.state_dir)}       # 탭5 의 방향 — 첫 STAT 이 오면 그 값으로(칸이 없으면 None = 가로)

    # ── 메인 스레드가 부르는 것 ──
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="tab5-link", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._close()

    def offer(self, jpeg: bytes, content_mono: float, mode: str = "landscape") -> None:
        """보낼 그림을 맡긴다(앞에 맡긴 것을 덮는다). content_mono = 그 그림의 내용이 살아 있던 때(time.monotonic).
        mode = 그 그림의 꼴(landscape | portrait) — 탭5 의 지금 방향과 다른 그림은 보내지 않는다(설계 D7)."""
        with self._lock:
            self._pending = (bytes(jpeg), float(content_mono), mode)

    def set_screen(self, on: bool) -> None:
        with self._lock:
            if bool(on) != self._screen:
                self._screen, self._need_screen = bool(on), True

    def connected(self) -> bool:
        return bool(self._st["connected"])

    def wait_screen(self, on: bool, timeout: float) -> bool:
        """탭5 에 screen on 이 닿을 때까지 기다린다(창이 끝나기 전에 탭5 를 끄려고). 닿았으면 True, 연결이 없거나 한도를 넘으면 False."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                done = self._screen == bool(on) and not self._need_screen
            if done and self._st["screen"] == bool(on) and self._st["connected"]:
                return True
            time.sleep(0.05)
        return False

    def alive(self) -> bool:
        """일꾼이 살아 돌고 있는가(1분 안에 한 바퀴 돌았다)."""
        return self._thread is not None and self._thread.is_alive() and self.clock() - self._beat < 60

    def status(self) -> dict:
        return dict(self._st)

    # ── 일꾼 ──
    def _run(self) -> None:
        last_state = float("-inf")
        while not self._stop.is_set():
            self._beat = self.clock()
            if self.clock() - last_state >= STATE_EVERY:
                self._write_state(); last_state = self.clock()
            if read_pause(self.state_dir / PAUSE_FILE, self.wall()) is not None:
                if not self._st["paused"]:
                    self._close(); self._st["paused"] = True; self._write_state()
                    self.log("탭5 포트를 잠시 놓는다(pause)")
                self._stop.wait(0.5)
                continue
            if self._st["paused"]:
                self._st["paused"] = False; self._fails = 0
                self._write_state()                      # 곧바로 — kiosk.sh 의 다음 pause 가 낡은 "paused" 를 믿지 않게
                self.log("탭5 포트를 다시 잡는다(resume)")
            if self._port is None and not self._connect():
                continue
            try:
                self._step()
            except Exception as e:                       # noqa: BLE001 — 직렬 오류 · 시간 초과 · 거절 · 끊김 모두 다시 연결로
                self._fail(f"{type(e).__name__}: {e}")

    def _connect(self) -> bool:
        try:
            dev = pick_port(self.lister(), self.serial_number)
        except Exception as e:                           # noqa: BLE001 — 포트 목록을 못 읽은 것은 일꾼이 죽을 까닭이 아니다
            self._note_error(f"포트 목록을 읽지 못함: {e}")
            return False
        if dev is None:
            self._absent = True
            if self._st["last_error"] != "탭5 가 USB 에 없다":
                self.log("탭5 연결 — 탭5 가 USB 에 없다")
            self._st["last_error"] = "탭5 가 USB 에 없다"
            self._stop.wait(ABSENT_POLL)
            return False
        try:
            self._port = self.opener(dev)
        except Exception as e:                           # noqa: BLE001
            self._note_error(f"열기 실패: {e}")
            return False
        self._buf = ""
        if self._absent:                                 # 빠졌다 다시 붙었다 — 막 켜진 탭5 의 첫 무응답에 긴 쉼을 쓰지 않는다(검토 초점 1: 30초 안)
            self._absent, self._fails = False, 0
        self._st.update(connected=True, port=dev, last_error=None)
        self._st["reconnects"] += 1
        self._need_frame = self._need_screen = True
        self.log(f"탭5 연결: {dev}")
        self._write_state()
        return True

    def _note_error(self, why: str) -> None:
        if self._st["last_error"] != why:
            self.log(f"탭5 연결 — {why}")
        self._st["last_error"] = why
        self._stop.wait(backoff(self._fails)); self._fails += 1

    def _fail(self, why: str) -> None:
        self.log(f"탭5 연결 끊김 — {why}")
        self._st["last_error"] = why
        self._close(); self._write_state()
        self._stop.wait(backoff(self._fails)); self._fails += 1

    def _close(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            except Exception:                            # noqa: BLE001
                pass
        self._port = None
        self._st["connected"] = False

    def _step(self) -> None:
        self._pump(0.1)
        now = self.clock()
        if now < self._rest_until:
            return
        with self._lock:
            screen = self._screen
            if self._pending is not None:
                self._newest, self._pending, self._need_frame = self._pending, None, True
        if self._need_screen or now - self._screen_sent_at >= SCREEN_EVERY:
            self._send_screen(screen)
        if self._need_frame and self._newest is not None:
            jpeg, content, mode = self._newest
            want = orient_mode(self._st["orient"])
            if mode != want:                             # 바꾸기 전에 뜬 그림 · 다시 켜진 탭5 에 다시 보낼 옛 그림 — 보내면 눕는다(설계 D7)
                self._need_frame = False                 #   창이 새 방향으로 뜬 그림을 맡길 때까지 기다린다
                note = f"탭5 방향은 {want} 인데 그림은 {mode} — 보내지 않고 새 그림을 기다린다"
                if note != self._skip_note:
                    self._skip_note = note; self.log(note)
            else:
                self._skip_note = ""
                self._send_frame(jpeg, content)
        self._fails = 0

    def _readline(self) -> str | None:
        """줄 하나. 쌓인 것에 줄이 없으면 한 번만 읽어 보고, 그래도 없으면 None — 부르는 쪽이 마감을 본다
        (줄바꿈 없는 바이트가 이어져도 여기서 돌지 않는다 · 쌓아 두는 글은 LINE_MAX 까지 — 답은 늘 끝에 붙어 온다)."""
        if "\n" not in self._buf:
            b = self._port.read(max(1, getattr(self._port, "in_waiting", 0) or 1))
            if not b:
                return None
            self._buf = (self._buf + b.decode("utf-8", "replace"))[-LINE_MAX:]
            if "\n" not in self._buf:
                return None
        line, self._buf = self._buf.split("\n", 1)
        return line.rstrip("\r")

    def _pump(self, sec: float) -> None:
        """sec 동안 들어온 줄을 읽어 STAT · MON BOOT 를 처리한다. 줄이 쉬지 않고 와도 sec 에 끝난다."""
        end = self.clock() + sec
        while self.clock() < end:
            line = self._readline()
            if line is not None:
                self._watch(line)

    def _watch(self, line: str) -> None:
        st = parse_stat(line)
        if st is not None and st["board"] == "off":
            if self.clock() >= self._rest_until:         # 탭5 가 HDMI 모니터(`board off`)다 — 보내 봐야 거절된다
                self._st["tab5"] = st
                self._rest("탭5 가 HDMI 모드다(`board off`) — 현황판을 보내려면 `board on`")
            return
        if st is not None:
            self._st["tab5"] = st
            if st["orient"] != self._st["orient"]:       # 현황판 꼬리가 있으면 그 방향이 사실이다 — 칸이 없으면 가로(기억을 지키지 않는다, 코덱스 치명 1)
                self._st["orient"] = st["orient"]
                self._write_state()                      # 곧바로 — 창이 지금 다시 떠도 이 방향으로 그리게
            if self._newest is not None and st["bseq"] == 0 and self.clock() - self._sent_at >= RESEND_GAP:
                self._need_frame = True                  # 탭5 에 그려진 그림이 없다 = 다시 켜졌다(MON BOOT 를 놓쳐도 여기서 안다).
                #   번호가 다르다는 것만으로는 다시 보내지 않는다 — 그 STAT 이 그림 답보다 먼저 찍혔거나(그리기 직전) 그림을 못 풀었을 뿐이다.
                #   못 푼 그림은 다시 보내도 같다: 탭5 가 3분 뒤 LOST 로 보인다(MN-D33). 그래서 bseq=0 이 이어져도 RESEND_GAP 에 한 번만
                #   (보낸 직후 그리기 전에 찍힌 STAT 에 다시 보내지도 않는다)
            if st["scr"] != int(self._screen):
                self._need_screen = True
        if "MON BOOT" in line:
            self._need_frame = self._need_screen = True

    def _wait(self, rid: int, *kinds: str, sent: str = "") -> tuple[str, bool, str]:
        """kinds 가운데 하나의 답을 기다린다 → (종류, OK 인가, 나머지). sent = 방금 보낸 줄(펌웨어가 모른다는 답과 견준다).
        펌웨어가 모르면 _Unknown, 우리 줄이 깨져 닿았으면 곧바로 RuntimeError(답 한도를 기다리지 않는다), 한도를 넘으면 TimeoutError."""
        end = self.clock() + REPLY_TIMEOUT
        while self.clock() < end:
            line = self._readline()                      # 한 줄씩 — 답을 찾으면 곧바로 멈춘다. 답 뒤의 줄(STAT 등)은 다음 차례에 읽는다
            if line is None:                             #   (뒤의 줄까지 읽어 버리면 "다시 켜졌음"이 곧이어 오는 "보냄 성공"에 지워진다)
                continue
            self._watch(line)
            kind = classify_unknown(line, sent)
            if kind == "old":
                raise _Unknown()
            if kind == "garbled":
                raise RuntimeError(f"보낸 줄이 깨져 닿았다: {line[:60]}")
            for k in kinds:
                r = find_reply(line, k, rid)
                if r is not None:
                    return (k, *r)
        raise TimeoutError(f"{'/'.join(kinds)} {rid} 답 없음")

    def _next_id(self) -> int:
        self._rid = self._rid % 999_999 + 1
        return self._rid

    def _send_screen(self, on: bool) -> None:
        rid = self._next_id()
        cmd = f"screen {rid} {int(on)}"
        self._port.write((cmd + "\n").encode())
        try:
            _, ok, rest = self._wait(rid, "screen", sent=cmd)
        except _Unknown:
            return self._rest("탭5 펌웨어가 screen 을 모른다(펌웨어를 올려야 한다)")
        if not ok:
            raise RuntimeError(f"screen 거절: {rest}")
        with self._lock:                                 # 보내는 사이 메인이 값을 바꿨으면 "다시 보낼 것"을 지우지 않는다(검토 9)
            if self._screen == bool(on):
                self._need_screen = False
        self._screen_sent_at = self.clock()
        self._st["screen"] = bool(on)

    def _send_frame(self, jpeg: bytes, content_mono: float) -> None:
        age_ms = int((self.clock() - content_mono) * 1000)
        if age_ms > MAX_AGE_MS:
            self._need_frame = False                     # 옛 그림 — 새로 뜬 것을 기다린다
            return
        for attempt in (1, 2):                           # CRC 오류면 한 번 곧바로 다시
            rid = self._next_id()
            head, chunks, end = frame_lines(rid, jpeg, age_ms)
            try:
                self._port.write(head)
                kind, ok, rest = self._wait(rid, "frame-begin", "frame", sent=head.decode())   # 거절은 `ERR frame <id> …` 로 온다
                if not (ok and kind == "frame-begin"):
                    raise RuntimeError(f"frame 거절: {rest}")
                for i, c in enumerate(chunks, 1):
                    self._port.write(c)
                    if i % ACK_EVERY == 0 and i != len(chunks):
                        _, ok, rest = self._wait(rid, "+")
                        if not ok:
                            raise RuntimeError(f"조각 거절: {rest}")
                self._port.write(end)
                _, ok, rest = self._wait(rid, "frame", sent=end.decode())
            except _Unknown:
                return self._rest("탭5 펌웨어가 frame 을 모른다(펌웨어를 올려야 한다)")
            if ok:
                self._st["frames_ok"] += 1; self._st["sent_seq"] = rid; self._st["last_ok_at"] = self.wall()
                self._need_frame, self._sent_at = False, self.clock()
                if self._st["frames_ok"] == 1:
                    self.log(f"탭5 에 첫 그림을 보냈다({len(jpeg)} B)")
                return
            self._st["frames_err"] += 1
            if "crc" not in rest or attempt == 2:
                raise RuntimeError(f"그림 실패: {rest}")

    def _rest(self, why: str) -> None:
        """보내 봐야 안 되는 까닭이 있다 — 한동안 보내지 않는다(끝없이 거절당하는 고리를 돌지 않게)."""
        self.log(f"{why} — {UNKNOWN_REST:.0f}초 쉰다")
        self._st["last_error"] = why
        self._rest_until = self.clock() + UNKNOWN_REST
        self._need_frame = self._need_screen = False

    def _write_state(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.state_dir / (STATE_FILE + ".tmp")
            tmp.write_text(json.dumps({**self._st, "updated_at": self.wall(), "pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.state_dir / STATE_FILE)
        except OSError:
            pass
