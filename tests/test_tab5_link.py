"""탭5 연결(tools/tab5_link.py) — 순수 함수와, 펌웨어를 흉내 내는 가짜 포트로 일꾼까지. 실제 포트는 열지 않는다."""
import base64
import json
import threading
import time
import zlib

from tools import tab5_link as tl


class FakePort:
    """탭5 펌웨어 흉내: frame/조각/frameend/screen 에 규약(설계서 2-5)대로 답하고, 받은 그림을 모은다.
    확인 줄 앞에는 STAT 줄의 꼬리가 붙어 온다 — 답을 줄 어디에서든 찾는지 본다."""

    def __init__(self, *, unknown=False, crc_bad_once=False, refuse_begin=False, garble_head_once=False):
        self.out = bytearray(); self.inbuf = b""; self.closed = False
        self.frames = []; self.screens = []; self.rx = None; self.lock = threading.Lock()
        self.unknown, self.crc_bad_once, self.refuse_begin = unknown, crc_bad_once, refuse_begin
        self.garble_head_once = garble_head_once
        self.mute = 0                                           # 이만큼의 명령에는 답하지 않는다(막 켜져 아직 듣지 못하는 탭5)
        self.begins = 0
        self.ages = []                                          # 머리 줄의 나이 칸(ms)
        self.on_screen = None                                   # screen 을 받았을 때 부를 것(답하기 전)
        self.orient = None                                      # 확인 줄 앞 STAT 꼬리에 실을 방향(None = 옛 펌웨어 — 칸이 없다)

    def write(self, b):
        with self.lock:
            self.inbuf += b
            while b"\n" in self.inbuf:
                line, self.inbuf = self.inbuf.split(b"\n", 1)
                self._handle(line.decode())
        return len(b)

    def say(self, s):
        with self.lock:
            self.out += (s + "\n").encode()

    def _say(self, s):
        self.out += (s + "\n").encode()

    def _handle(self, line):
        p = line.split()
        if not p:
            return
        if self.mute > 0 and not line.startswith("+"):
            self.mute -= 1
            return
        if self.unknown and p[0] in ("frame", "screen"):
            return self._say(f"ERR unknown '{line}' (help)")
        if self.garble_head_once and p[0] == "frame":           # 머리 줄의 글자 몇 개가 선에서 사라졌다 → 펌웨어는 모르는 명령으로 본다
            self.garble_head_once = False
            return self._say(f"ERR unknown '{' '.join(p[:2])}' (help)")
        if p[0] == "screen":
            self.screens.append(int(p[2]))
            if self.on_screen is not None:
                self.on_screen(int(p[2]))
            return self._say(f"OK screen {p[1]} {p[2]}")
        if p[0] == "frame":
            self.begins += 1
            if self.refuse_begin:
                return self._say(f"ERR frame {p[1]} busy")
            self.ages.append(int(p[4]))
            self.rx = {"id": int(p[1]), "len": int(p[2]), "crc": int(p[3], 16), "data": bytearray(), "n": 0}
            return self._say(f"OK frame-begin {p[1]}")
        if line.startswith("+") and self.rx:
            self.rx["data"] += base64.b64decode(line[1:]); self.rx["n"] += 1
            if self.rx["n"] % 4 == 0:
                tail = f" orient={self.orient}" if self.orient is not None else ""
                self._say(f"STAT up=1 link=NO_DEVICE temp=40.0 board=live bseq=0 scr=1 bage=1{tail} OK + {self.rx['id']} {len(self.rx['data'])}")
            return
        if p[0] == "frameend" and self.rx:
            rx, self.rx = self.rx, None
            if self.crc_bad_once:
                self.crc_bad_once = False
                return self._say(f"ERR frame {p[1]} crc")
            if zlib.crc32(bytes(rx["data"])) != rx["crc"] or len(rx["data"]) != rx["len"]:
                return self._say(f"ERR frame {p[1]} crc")
            self.frames.append(bytes(rx["data"]))
            return self._say(f"OK frame {p[1]} {len(rx['data'])} crc={rx['crc']:08x}")

    def read(self, n=1):
        with self.lock:
            b, self.out = bytes(self.out[:n]), self.out[n:]
        if not b:
            time.sleep(0.005)
        return b

    @property
    def in_waiting(self):
        return len(self.out)

    def close(self):
        self.closed = True


class Port:
    def __init__(self, device, serial_number):
        self.device, self.serial_number = device, serial_number


def _link(tmp_path, port, logs=None):
    return tl.Tab5Link("SER", tmp_path, log=(logs.append if logs is not None else (lambda m: None)),
                       opener=lambda dev: port, lister=lambda: [Port("/dev/x", "SER")])


def _wait(cond, sec=5.0):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


BIG = bytes(range(256)) * 82            # 20,992 바이트 → 조각 14개 → 확인 3번(4·8·12)


def test_frame_lines_split_and_crc():
    jpeg = bytes(range(256)) * 20       # 5,120 바이트 → 조각 4개(1500 · 1500 · 1500 · 620)
    head, chunks, end = tl.frame_lines(17, jpeg, 1234)
    assert head == f"frame 17 5120 {zlib.crc32(jpeg):08x} 1234\n".encode()
    assert len(chunks) == 4 and all(c.startswith(b"+") and c.endswith(b"\n") for c in chunks)
    assert b"".join(base64.b64decode(c[1:-1]) for c in chunks) == jpeg
    assert max(len(c) for c in chunks) == 2002 and end == b"frameend 17\n"
    assert tl.frame_lines(1, b"x", -5)[0].endswith(b" 0\n")          # 나이는 0 아래로 가지 않는다


def test_find_reply_anywhere_in_line():
    assert tl.find_reply("STAT up=3 ... OK frame-begin 5", "frame-begin", 5) == (True, "")
    assert tl.find_reply("OK + 5 6000", "+", 5) == (True, "6000")
    assert tl.find_reply("ERR frame 5 crc", "frame", 5) == (False, "crc")
    assert tl.find_reply("OK frame 6 100 crc=1", "frame", 5) is None          # 다른 번호(늦게 온 옛 답)
    assert tl.find_reply("OK frame 55 100 crc=1", "frame", 5) is None         # 번호의 앞부분만 같은 것
    assert tl.find_reply("OK frame-begin 5", "frame", 5) is None              # 다른 종류
    # "모른다"는 보낸 줄이 그대로 되돌아왔을 때만 옛 펌웨어다. 글자가 빠져 되돌아오면 줄이 깨진 것 — 5분 쉴 까닭이 아니다
    assert tl.classify_unknown("ERR unknown 'frame 1 2 3 4' (help)", "frame 1 2 3 4") == "old"
    assert tl.classify_unknown("STAT up=1 ERR unknown 'screen 9 1' (help)", "screen 9 1") == "old"
    assert tl.classify_unknown("ERR unknown 'frme 1 2 3 4' (help)", "frame 1 2 3 4") == "garbled"
    assert tl.classify_unknown("ERR unknown 'frameend ' (help)", "frameend 7") == "garbled"
    assert tl.classify_unknown("ERR unknown 'QUJD' (help)", "frameend 7") == "garbled"      # 조각 줄이 깨져 '+' 를 잃었다
    assert tl.classify_unknown("ERR frame 1 crc", "frame 1 2 3 4") is None


def test_parse_stat_and_pick_port():
    s = tl.parse_stat("STAT up=10 link=NO_DEVICE fmt=- temp=41.5 tbrd=40 board=live bseq=12 scr=1 bage=3")
    assert s == {"board": "live", "bseq": 12, "scr": 1, "bage": 3, "temp": 41.5, "orient": None}     # 방향 칸이 없는 펌웨어
    assert tl.parse_stat("MON BOOT video=1") is None
    assert tl.parse_stat("STAT up=1 link=STREAMING temp=40") is None            # 옛 펌웨어(현황판 꼬리 없음)
    assert tl.pick_port([Port("/dev/a", "X"), Port("/dev/b", "aa:bb:cc:00:00:02")], "AA:BB:CC:00:00:02") == "/dev/b"
    assert tl.pick_port([Port("/dev/a", None)], "E8") is None
    assert tl.pick_port([Port("/dev/a", "E8")], "") is None                      # 일련번호를 모르면 아무 포트도 고르지 않는다


def test_backoff_and_pause(tmp_path):
    assert [tl.backoff(i) for i in range(7)] == [2, 4, 8, 16, 30, 30, 30]
    p = tmp_path / "tab5-link.pause"
    assert tl.read_pause(p, 100.0) is None
    p.write_text("200\n")
    assert tl.read_pause(p, 100.0) == 200.0
    assert tl.read_pause(p, 300.0) is None and not p.exists()                    # 만료 → 지운다
    p.write_text("엉망")
    assert tl.read_pause(p, 100.0) is None


def test_import_and_constructor_start_nothing(tmp_path):
    before = threading.active_count()
    tl.Tab5Link("SER", tmp_path, opener=lambda d: (_ for _ in ()).throw(AssertionError("열면 안 된다")), lister=lambda: [])
    assert threading.active_count() == before and not (tmp_path / "tab5-link.json").exists()


def test_worker_sends_screen_then_newest_frame(tmp_path):
    port = FakePort(); link = _link(tmp_path, port)
    link.set_screen(True)
    link.offer(b"old" * 1000, tl.mono()); link.offer(BIG, tl.mono())   # 칸 하나 — 새 것이 덮는다
    link.start()
    try:
        assert _wait(lambda: port.frames)
        assert port.screens[0] == 1 and port.frames == [BIG]
        assert _wait(lambda: (tmp_path / "tab5-link.json").exists())
        st = json.loads((tmp_path / "tab5-link.json").read_text())
        assert st["connected"] is True and st["port"] == "/dev/x"
        assert _wait(lambda: link.status()["frames_ok"] == 1)           # 가짜 포트가 받은 직후에는 일꾼이 아직 답을 안 읽었을 수 있다
        assert link.connected() and link.alive()
    finally:
        link.stop()


def test_worker_drops_too_old_frame(tmp_path):
    port = FakePort(); link = _link(tmp_path, port)
    link.offer(b"x" * 100, tl.mono() - 91)                    # 내용이 91초 전 — 보내지 않는다(C1)
    link.start()
    try:
        assert _wait(lambda: link.status()["connected"])
        assert not _wait(lambda: port.begins, 0.6)
    finally:
        link.stop()


def test_worker_retries_once_on_crc_error(tmp_path):
    port = FakePort(crc_bad_once=True); link = _link(tmp_path, port)
    link.offer(BIG, tl.mono()); link.start()
    try:
        assert _wait(lambda: port.frames == [BIG])
        assert link.status()["frames_err"] == 1
    finally:
        link.stop()


def test_begin_refusal_is_seen_without_waiting_for_timeout(tmp_path):
    port = FakePort(refuse_begin=True); logs = []; link = _link(tmp_path, port, logs)
    link.offer(BIG, tl.mono()); link.start()
    try:
        t0 = time.monotonic()
        assert _wait(lambda: any("busy" in m for m in logs), 2.5)       # 답 제한(3초)을 기다리지 않는다
        assert time.monotonic() - t0 < 2.5
    finally:
        link.stop()


def test_worker_rests_when_firmware_does_not_know_frame(tmp_path):
    port = FakePort(unknown=True); logs = []; link = _link(tmp_path, port, logs)
    link.offer(b"z" * 10, tl.mono()); link.start()
    try:
        assert _wait(lambda: any("모른다" in m for m in logs))
        assert not port.frames
    finally:
        link.stop()


def test_worker_resends_when_tab5_reports_it_lost_the_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "RESEND_GAP", 0.0)
    port = FakePort(); link = _link(tmp_path, port)
    link.offer(b"q" * 2000, tl.mono()); link.start()
    try:
        assert _wait(lambda: len(port.frames) == 1)
        port.say("STAT up=2 link=NO_DEVICE temp=39.0 board=none bseq=0 scr=1 bage=-1")   # 탭5 가 다시 켜졌다(MON BOOT 를 놓쳤다)
        assert _wait(lambda: len(port.frames) == 2)
    finally:
        link.stop()


def test_screen_off_is_sent_and_repeated(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "SCREEN_EVERY", 0.3)
    port = FakePort(); link = _link(tmp_path, port); link.start()
    try:
        assert _wait(lambda: port.screens[:1] == [1])
        link.set_screen(False)
        assert _wait(lambda: port.screens.count(0) >= 2)                # 바뀌면 곧바로, 그 뒤로도 되풀이(탭5 는 90초 끊기면 SLEEP 을 푼다)
    finally:
        link.stop()


def test_pause_closes_port_and_resume_reopens(tmp_path):
    port = FakePort(); link = _link(tmp_path, port); link.start()
    try:
        assert _wait(lambda: link.status()["connected"])
        (tmp_path / "tab5-link.pause").write_text(f"{time.time() + 60}\n")
        assert _wait(lambda: json.loads((tmp_path / "tab5-link.json").read_text()).get("paused") is True)
        assert port.closed and not link.connected()
        (tmp_path / "tab5-link.pause").unlink()
        assert _wait(lambda: link.status()["connected"] and not link.status()["paused"])
    finally:
        link.stop()


def test_port_that_vanishes_is_found_again(tmp_path):
    ports = [FakePort(), FakePort()]; opened = []

    def opener(dev):
        p = ports[len(opened)]; opened.append(p); return p

    link = tl.Tab5Link("SER", tmp_path, log=lambda m: None, opener=opener, lister=lambda: [Port("/dev/x", "SER")])
    link.offer(b"v" * 3000, tl.mono()); link.start()
    try:
        assert _wait(lambda: ports[0].frames)
        def broken(_b):
            raise OSError("device disconnected")
        ports[0].write = broken                                          # 선이 빠졌다
        link.set_screen(False)                                           # 다음에 쓸 일을 만든다
        assert _wait(lambda: ports[1].frames, 8.0)                       # 다시 찾아 가장 새 그림을 보낸다
    finally:
        link.stop()


def test_worker_rests_when_tab5_is_in_hdmi_mode(tmp_path):
    # 탭5 가 `board off`(HDMI 모니터)면 STAT 에 board=off — 그림을 보내 봐야 거절된다. 끝없이 거절당하는 고리를 돌지 않고 쉰다
    port = FakePort(); logs = []; link = _link(tmp_path, port, logs)
    port.say("STAT up=5 link=STREAMING temp=40.0 board=off bseq=0 scr=1 bage=-1")
    link.offer(BIG, tl.mono()); link.start()
    try:
        assert _wait(lambda: any("HDMI" in m for m in logs))
        assert not _wait(lambda: port.begins, 0.6)
    finally:
        link.stop()


def test_garbled_head_is_a_retry_not_a_five_minute_rest(tmp_path):
    # 머리 줄이 깨져 `ERR unknown 'frame 5'` 로 돌아왔다 — 옛 펌웨어가 아니다. 쉬지 않고 곧바로 실패로 보고 다시 보낸다(검토 — 2026-10-15 기기에서 줄 손실을 봄)
    port = FakePort(garble_head_once=True); logs = []; link = _link(tmp_path, port, logs)
    link.offer(BIG, tl.mono()); link.start()
    try:
        assert _wait(lambda: port.frames == [BIG], 6.0)
        assert not any("모른다" in m for m in logs)
    finally:
        link.stop()


def test_lost_frame_is_resent_at_most_every_resend_gap(tmp_path, monkeypatch):
    # 탭5 가 bseq=0 에 머문다(그 그림을 못 푼다) — 5초마다의 STAT 마다 같은 그림을 다시 보내지 않는다(펌웨어 검토 8)
    monkeypatch.setattr(tl, "RESEND_GAP", 1.0)
    port = FakePort(); link = _link(tmp_path, port)
    link.offer(b"q" * 2000, tl.mono()); link.start()
    try:
        assert _wait(lambda: len(port.frames) == 1)
        port.say("STAT up=2 link=NO_DEVICE temp=39.0 board=none bseq=0 scr=1 bage=-1")
        assert not _wait(lambda: len(port.frames) == 2, 0.5)            # 방금 보냈다 — 아직 다시 보내지 않는다
        time.sleep(0.6)
        port.say("STAT up=7 link=NO_DEVICE temp=39.0 board=none bseq=0 scr=1 bage=-1")
        assert _wait(lambda: len(port.frames) == 2)                      # 간격이 지났다 — 한 번 다시
        port.say("STAT up=12 link=NO_DEVICE temp=39.0 board=none bseq=0 scr=1 bage=-1")
        assert not _wait(lambda: len(port.frames) == 3, 0.5)
        port.say("MON BOOT video=1 board=1")                             # 다시 켜졌다는 분명한 신호에는 곧바로
        assert _wait(lambda: len(port.frames) == 3)
    finally:
        link.stop()


class ChattyPort(FakePort):
    """기기가 줄을 쉬지 않고 낸다(부팅 기록 · 진단 출력이 쏟아짐) — 읽을 것이 늘 있다."""

    def __init__(self, noise=b"MON noise line\n", **kw):
        super().__init__(**kw); self.noise = noise

    def read(self, n=1):
        with self.lock:
            if self.out:
                b, self.out = bytes(self.out[:n]), self.out[n:]
                return b
        time.sleep(0.004)
        return self.noise

    @property
    def in_waiting(self):
        return len(self.out) or len(self.noise)


def test_worker_keeps_sending_while_the_device_chatters(tmp_path):
    # 리뷰 2: _pump 가 "읽을 줄이 없을 때"만 마감을 봐서, 줄이 쉬지 않고 오면 일꾼이 영원히 읽기만 했다
    port = ChattyPort(); link = _link(tmp_path, port)
    link.offer(BIG, tl.mono()); link.start()
    try:
        assert _wait(lambda: port.frames == [BIG], 6.0)
        assert link.alive()
    finally:
        link.stop()


def test_bytes_without_newline_do_not_grow_the_buffer_or_stall(tmp_path):
    port = ChattyPort(noise=b"\xfe" * 64); link = _link(tmp_path, port)       # 줄바꿈 없는 바이트가 끝없이(선의 잡음 — 글자가 아닌 바이트)
    link.offer(BIG, tl.mono()); link.start()
    try:
        assert _wait(lambda: port.frames == [BIG], 6.0)
        assert len(link._buf) <= tl.LINE_MAX
    finally:
        link.stop()


def test_a_port_lister_that_raises_does_not_kill_the_worker(tmp_path):
    port = FakePort(); calls = []

    def lister():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("IOKit 이 잠깐 답하지 않음")
        return [Port("/dev/x", "SER")]
    link = tl.Tab5Link("SER", tmp_path, log=lambda m: None, opener=lambda d: port, lister=lister)
    link.offer(b"k" * 3000, tl.mono()); link.start()
    try:
        assert _wait(lambda: port.frames, 8.0)
        assert link.alive()
    finally:
        link.stop()


def test_screen_off_that_arrives_while_screen_on_is_in_flight_is_not_lost(tmp_path):
    # 리뷰 9: 일꾼이 screen 1 을 보내는 사이 맥이 잠기면, 메인이 세운 "다시 보낼 것"을 일꾼이 지워 탭5 가 STAT 까지 켜져 있었다
    port = FakePort(); link = _link(tmp_path, port)
    port.on_screen = lambda v: link.set_screen(False) if v == 1 else None
    link.start()
    try:
        assert _wait(lambda: 0 in port.screens, 2.0)
    finally:
        link.stop()


def test_the_age_on_the_wire_is_measured_from_the_content_time(tmp_path):
    port = FakePort(); link = _link(tmp_path, port)
    link.offer(BIG, tl.mono() - 30.0); link.start()                         # 내용은 30초 전에 살아 있었다
    try:
        assert _wait(lambda: port.ages, 3.0)
        assert 29_500 <= port.ages[0] <= 32_000, port.ages
    finally:
        link.stop()


def test_the_clock_counts_time_the_mac_sleeps():
    # 리뷰 8: time.monotonic 은 맥이 잠자는 동안 멈춘다 — 8시간 재웠다 깨우면 8시간 묵은 그림을 "몇 초"로 보낸다
    import inspect
    assert "CLOCK_MONOTONIC" in inspect.getsource(tl.mono) and "monotonic()" not in inspect.getsource(tl.mono)
    a = tl.mono(); time.sleep(0.01); assert tl.mono() > a
    assert tl.Tab5Link("SER", "/nonexistent").clock is tl.mono


def test_open_serial_refuses_a_port_another_tool_holds_with_flock(tmp_path):
    # 리뷰 1: esptool · idf monitor 는 flock(exclusive) 으로만 연다 — 우리가 flock 을 걸지 않으면 그 둘이 쓰는 중에도 열고 끼어든다
    import fcntl, os, pty
    master, slave = pty.openpty()
    name = os.ttyname(slave)
    holder = os.open(name, os.O_RDWR | os.O_NOCTTY)
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        import pytest
        with pytest.raises(Exception):
            tl._open_serial(name).close()
    finally:
        os.close(holder); os.close(slave); os.close(master)


def test_wait_screen_reports_when_the_tab5_has_been_told(tmp_path):
    # 마지막 검토 중요 2: 창이 끝날 때(로그아웃 · 재시작) 탭5 를 끄고 나서 끝나야 한다 — 일꾼이 보냈는지 기다릴 수 있어야 한다
    port = FakePort(); link = _link(tmp_path, port); link.start()
    try:
        assert _wait(lambda: port.screens[:1] == [1])
        link.set_screen(False)
        assert link.wait_screen(False, 2.0) and port.screens[-1] == 0
    finally:
        link.stop()
    gone = tl.Tab5Link("SER", tmp_path, log=lambda m: None, opener=lambda d: port, lister=lambda: [])   # 탭5 가 없다
    gone.start()
    try:
        t0 = time.monotonic(); gone.set_screen(False)
        assert gone.wait_screen(False, 0.5) is False and time.monotonic() - t0 < 1.0
    finally:
        gone.stop()


def test_a_replugged_tab5_gets_a_picture_without_the_long_backoff(tmp_path, monkeypatch):
    # 마지막 검토 사소 3(검토 초점 1 — 30초 안): 오래 빠져 있던 동안 쌓인 쉼 간격이, 다시 붙은 뒤 막 켜진 탭5 의 첫 무응답에 그대로 쓰였다
    monkeypatch.setattr(tl, "BACKOFF", (0.05, 0.1, 0.2, 0.4, 3.0)); monkeypatch.setattr(tl, "REPLY_TIMEOUT", 0.4)
    monkeypatch.setattr(tl, "ABSENT_POLL", 0.3)                           # 운영 5초 — 빠져 있는 동안의 찾기 간격은 늘지 않는다
    port = FakePort(); port.mute = 1; present = {"on": False}
    link = tl.Tab5Link("SER", tmp_path, log=lambda m: None, opener=lambda d: port,
                       lister=lambda: [Port("/dev/x", "SER")] if present["on"] else [])
    link.offer(b"r" * 3000, tl.mono()); link.start()
    try:
        time.sleep(1.2)                                                   # 빠져 있는 동안 쉼 간격이 끝까지 늘어난다
        present["on"] = True; t0 = time.monotonic()
        assert _wait(lambda: port.frames, 5.0)
        assert time.monotonic() - t0 < 2.5, time.monotonic() - t0         # 막 붙은 탭5 의 첫 무응답 뒤에는 짧게 다시
    finally:
        link.stop()


# ── 방향(세로 화면 — 설계 docs/exec-plans/2026-10-18-tab5-portrait-design.md D3 · D7) ──
STAT_LIVE = "STAT up=2 link=NO_DEVICE temp=39.0 board=live bseq=1 scr=1 bage=1 bdec=0"


def test_parse_stat_reads_orient_or_none():
    assert tl.parse_stat(STAT_LIVE + " orient=1")["orient"] == 1
    assert tl.parse_stat(STAT_LIVE)["orient"] is None                            # 옛 펌웨어 — 칸이 없다
    assert tl.parse_stat(STAT_LIVE + " orient=x")["orient"] is None              # 읽을 수 없는 값은 없는 것으로


def test_orient_mode():
    assert [tl.orient_mode(o) for o in (0, 1, 2, 3, None, 7)] == ["landscape", "portrait", "landscape", "portrait", "landscape", "landscape"]


def test_link_remembers_the_last_orient_across_restarts(tmp_path):
    (tmp_path / "tab5-link.json").write_text(json.dumps({"orient": 3}))
    port = FakePort(); port.orient = 0; link = _link(tmp_path, port)
    assert link.status()["orient"] == 3                                          # 첫 STAT 전에는 앞서 안 방향(창이 다시 떠도 눕지 않게)
    link.start()
    try:
        port.say(STAT_LIVE + " orient=0")
        assert _wait(lambda: link.status()["orient"] == 0)
        assert _wait(lambda: json.loads((tmp_path / "tab5-link.json").read_text()).get("orient") == 0)     # 바뀌면 곧바로 적는다
        assert link.status()["tab5"]["orient"] == 0
    finally:
        link.stop()


def test_old_firmware_stat_forgets_the_remembered_orient(tmp_path):
    # 코덱스 검토 치명 1: 기억을 지키면 펌웨어를 되돌렸을 때 영영 눕는다 — 현황판 꼬리가 있는데 방향 칸이 없으면 가로로 확정
    (tmp_path / "tab5-link.json").write_text(json.dumps({"orient": 3}))
    port = FakePort(); link = _link(tmp_path, port)
    link.start()
    try:
        port.say(STAT_LIVE)
        assert _wait(lambda: link.status()["tab5"] is not None)
        assert link.status()["orient"] is None
    finally:
        link.stop()


def test_link_does_not_send_a_frame_for_the_other_orientation(tmp_path):
    logs = []
    port = FakePort(); port.orient = 1; link = _link(tmp_path, port, logs)
    link.start()
    try:
        port.say(STAT_LIVE + " orient=1")
        assert _wait(lambda: link.status()["orient"] == 1)
        link.offer(BIG, tl.mono(), mode="landscape")                             # 바꾸기 전에 뜬 가로 그림 — 세로로 선 탭5 에 보내면 눕는다
        time.sleep(1.0)
        assert port.frames == [] and any("방향" in m for m in logs), logs
        link.offer(BIG, tl.mono(), mode="portrait")
        assert _wait(lambda: port.frames == [BIG])
    finally:
        link.stop()


def test_resend_after_reboot_skips_a_frame_of_the_old_orientation(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "RESEND_GAP", 0.0)
    port = FakePort(); link = _link(tmp_path, port)
    link.offer(b"q" * 2000, tl.mono()); link.start()                             # 가로 그림(방향을 모르면 가로)
    try:
        assert _wait(lambda: len(port.frames) == 1)
        port.orient = 1
        port.say("STAT up=3 link=NO_DEVICE temp=39.0 board=none bseq=0 scr=1 bage=-1 orient=1")   # 세로로 돌린 채 다시 켜졌다
        assert _wait(lambda: link.status()["orient"] == 1)
        time.sleep(1.0)
        assert len(port.frames) == 1                                             # 옛 가로 그림을 다시 보내지 않는다
    finally:
        link.stop()
