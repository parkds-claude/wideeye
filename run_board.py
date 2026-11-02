#!/usr/bin/env python3
"""진입점. main() 에서만 스레드를 띄운다(import 만으로는 아무것도 하지 않는다).

흐름: 설정 → 저장소 → 수집기 스레드(각자 주기) → Flask 127.0.0.1:<port>. 수집기 하나가 실패해도 나머지와 화면은 계속 돈다.
"""
from __future__ import annotations
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from urgent_board.collectors import claude_sessions            # noqa: E402
from urgent_board.collectors.base import Collector, run_loop   # noqa: E402
from urgent_board.config import Settings                       # noqa: E402
from urgent_board import board, config, paths, settings_doc, summarize   # noqa: E402
from urgent_board.store import Store                           # noqa: E402
from urgent_board.triage.pipeline import judge                 # noqa: E402
from urgent_board.web import create_app                        # noqa: E402

log = logging.getLogger("run")
MESSAGES_DB = Path.home() / "Library" / "Messages" / "chat.db"


def wait_for_network(stop: threading.Event, host: str = "api.telegram.org", tries: int = 30, delay: float = 10.0,
                     resolve=socket.getaddrinfo) -> bool:
    """부팅 직후 DNS 가 불안정하다(홈 CLAUDE.md Gotcha). 이름이 풀릴 때까지 기다린다 — 네트워크 수집기만."""
    for _ in range(tries):
        if stop.is_set():
            return False
        try:
            resolve(host, 443)
            return True
        except socket.gaierror:
            stop.wait(delay)
    return False


class MissingCollector(Collector):
    """켜져 있는데 연결 정보가 없는 출처 — collect 마다 까닭을 올려 "수집 중단"으로 보이게 한다(조용히 빠지지 않게)."""

    def __init__(self, name: str, reason: str):
        self.name, self.reason = name, reason

    def collect(self, since):
        raise RuntimeError(self.reason)


def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def collectors(s: Settings) -> list[tuple[Collector, bool]]:
    """(수집기, 네트워크 필요 여부) 목록 — 설정의 accounts 로. 만들기만 하고 연결하지 않는다(실패는 run_loop 가 '수집 중단'으로 적는다)."""
    from urgent_board.collectors.contacts import AddressBook
    from urgent_board.collectors.imessage import IMessageCollector
    from urgent_board.collectors.kakao import KakaoCollector, configured_helper
    a, auth = s.accounts or {}, paths.auth_dir(s.home)
    out: list[tuple[Collector, bool]] = []
    if (a.get("imessage") or {}).get("enabled"):
        out.append((IMessageCollector(MESSAGES_DB, s.my_phone_tails, names=AddressBook().name_for), False))
    if (a.get("kakao") or {}).get("enabled"):
        out.append((KakaoCollector(helper_dir=configured_helper(a)), False))
    if (a.get("whatsapp") or {}).get("enabled"):                 # 맥 왓츠앱 앱의 DB(2026-10-28 — 설계 D2)
        from urgent_board.collectors.whatsapp import WhatsAppCollector
        out.append((WhatsAppCollector(my_phone_tails=s.my_phone_tails, names=AddressBook().name_for), False))
    if (a.get("telegram") or {}).get("enabled"):
        tg = _read_json(auth / "telegram.json")
        if tg.get("api_id") and tg.get("api_hash"):
            from urgent_board.collectors.telegram import TelegramCollector
            out.append((TelegramCollector(str(auth / "telethon.session"), int(tg["api_id"]), str(tg["api_hash"])), True))
        else:
            out.append((MissingCollector("telegram", "텔레그램 연결 정보(auth/telegram.json)가 없다 — 설정 > 연결에서 로그인"), True))
    if (a.get("calls") or {}).get("enabled"):                    # 부재중 전화 — 탭5 알림에만(2026-10-17 D6 · D7)
        from urgent_board.collectors.calls import CallsCollector
        out.append((CallsCollector(names=AddressBook().name_for), False))
    if (a.get("teams") or {}).get("enabled"):                    # 팀즈 알림(맥 알림 센터) — 탭5 알림에만(D8)
        from urgent_board.collectors.teams import TeamsCollector
        out.append((TeamsCollector(), False))
    if a.get("gmail"):
        from urgent_board.collectors.gmail import GmailCollector
        for g in a["gmail"]:
            out.append((GmailCollector(g["alias"], g["email"], g["token"], s.my_addresses, auth), True))
    for c, _ in out:
        c.poll_seconds = s.poll_seconds.get(c.name.split(":")[0], c.poll_seconds)
    return out


def announce(store: Store, cs: list[Collector], now: datetime) -> None:
    """수집기마다 '시작' 기록을 남긴다. 첫 수집이 끝나기 전에 걸려 버린 수집기도 기록이 오래되면
    화면에 '수집 중단'으로 보이게 하려는 것(기록이 아예 없으면 화면은 그 수집기를 모른다)."""
    store.forget_sources({c.name for c in cs})
    for c in cs:
        store.set_source_status(c.name, True, "시작", now)


SUMMARY_EVERY = 15            # 초 — 화면에 뜬 것 가운데 요약이 없는 것을 이 간격으로 채운다
SUMMARY_PER_CYCLE = 6
SUMMARY_CONCURRENCY = 3       # 동시에 부르는 수(다른 봇들과 같은 값)
SUMMARY_BACKOFF = 300         # 초 — 요약이 실패하면 이만큼 쉬고, 계속 실패하면 두 배씩 늘린다
SUMMARY_BACKOFF_MAX = 3600


def next_wait(failed: bool, backoff: int) -> tuple[int, int]:
    """(다음 회차까지 쉴 초, 새 물러서기 값). 실패하면 5분 → 10분 → … 최대 1시간, 성공하면 평소 간격으로."""
    if not failed:
        return SUMMARY_EVERY, 0
    back = min(SUMMARY_BACKOFF_MAX, backoff * 2) if backoff else SUMMARY_BACKOFF
    return back, back


def make_summarizer(s: Settings):
    """요약기(Claude Haiku). 설정에서 끄면 None — 그러면 화면에는 원문 앞부분이 그대로 나온다."""
    if not s.summary.get("enabled"):
        return None
    return summarize.ClaudeSummarizer(model=s.summary["model"], max_chars=int(s.summary["max_chars"]), skip_when=summarize.skip_rule(s.summary),
                                      what_chars=summarize.what_chars(s.summary), boxes=summarize.boxes_for(s))


def summary_cycle(store: Store, s: Settings, summarizer, now: datetime) -> summarize.Outcome:
    """지금 화면에 뜨는 항목 가운데 요약이 없는 것을 몇 건 요약해 저장한다(할 일 문구 · 기한도 같은 호출에서 — 2026-10-16).
    failed 면 부르는 쪽이 물러선다."""
    j = judge(store.recent_events(days=7), s, now, store.dismissed_keys(now))
    items = j.items + j.awaiting                                # 답 기다림도(내 글 — "상대가 할 일"을 받아 줄로 쓰고, 할 일이 없으면 뺀다)
    allowed = s.summary.get("sources")                          # 글을 Claude 로 보내도 되는 출처(설정 summary.sources)
    return summarize.summarize_pending([i for i in items if board.summary_allowed(i, allowed)], store, summarizer, now, limit=SUMMARY_PER_CYCLE,
                                       max_per_day=int(s.summary["max_per_day"]), concurrency=SUMMARY_CONCURRENCY,
                                       recipe=summarize.recipe_for(s))


def summarizer_key(s: Settings) -> tuple:
    """요약기를 새로 만들어야 하는가 — 켬/끔 · 모델 · 길이 · 판단 기준이 바뀌었을 때만(요약기는 제 빈 작업 폴더를 하나 쥔다)."""
    return (bool(s.summary.get("enabled")), s.summary.get("model"), int(s.summary.get("max_chars") or 0), summarize.skip_rule(s.summary),
            summarize.what_chars(s.summary), summarize.boxes_for(s))


def summary_loop(store: Store, live, stop: threading.Event) -> None:
    """요약을 뒤에서 채운다. 실패해도 죽지 않고 물러선다(요약이 없으면 화면은 원문 앞부분을 보인다).
    늘 돌며 회차마다 지금 설정을 본다 — 꺼져 있으면 쉬고, 켜면 다시 시작하지 않아도 시작한다(설계 4-3)."""
    backoff = 0
    summarizer, made_for = None, None
    while not stop.is_set():
        s = live.get()
        key = summarizer_key(s)
        if key != made_for:
            summarizer, made_for = make_summarizer(s), key
        if summarizer is None:
            stop.wait(SUMMARY_EVERY)
            continue
        failed = False
        try:
            failed = summary_cycle(store, s, summarizer, datetime.now(timezone.utc)).failed
        except Exception:                                        # noqa: BLE001
            log.exception("summary cycle failed")
            failed = True
        wait, backoff = next_wait(failed, backoff)
        if failed:
            log.warning("요약이 실패해 %d초 쉰다", wait)
        stop.wait(wait)


HANDLED_EVERY = 60            # 초 — 다른 Claude 세션의 기록에서 늘어난 만큼을 이 간격으로 읽는다


def board_marks(s: Settings) -> tuple[str, ...]:
    """명령에 이 가운데 하나가 들어 있으면 메일을 다룬 것이 아니라 현황판 자체를 들여다본 것이다:
    폴더 이름 · 패키지 이름(`python -c "from urgent_board…"` — 검수가 재현한 구멍) · 저장소 파일 이름 · 포트."""
    marks = (s.root.name, claude_sessions.__name__.split(".")[0], s.db_path.name, f":{s.port}")
    return tuple(m for m in marks if len(m) >= 4)                # 빈 글자나 너무 짧은 표식은 어느 명령에나 들어 있다 — 전부 "치지 않음"이 된다


def handled_cycle(store: Store, s: Settings, now: datetime) -> int:
    """다른 Claude 세션이 도구로 다룬 메일 id 를 저장소에 적는다(기록은 읽기만 한다)."""
    return claude_sessions.scan(Path(s.handled["sessions_dir"]).expanduser(), store, now, days=int(s.handled["lookback_days"]),
                                ignore_dirs=(str(s.root),),                # 이 현황판을 고치는 세션은 치지 않는다
                                ignore_marks=board_marks(s))               # 다른 폴더에서 현황판을 들여다본 명령도


def handled_loop(store: Store, live, stop: threading.Event) -> None:
    """늘 돌며 회차마다 지금 설정을 본다 — handled.claude_sessions 가 꺼져 있으면 그 회차는 건너뛴다."""
    while not stop.is_set():
        s = live.get()
        if s.handled.get("claude_sessions"):
            try:
                handled_cycle(store, s, datetime.now(timezone.utc))
            except Exception:                                    # noqa: BLE001
                log.exception("claude session scan failed")
        stop.wait(HANDLED_EVERY)


HEALTH_EVERY = 60             # 초 — 제 웹(/healthz)에 스스로 물어보는 간격
HEALTH_FAILS = 3              # 연달아 이만큼 답이 없으면 스스로 끝낸다


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 제 서버에 묻는 요청은 프록시 설정을 타지 않는다


def health_ok(port: int, timeout: float = 5.0) -> bool:
    try:
        with _DIRECT.open(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as r:
            r.read()                 # 본문까지 읽고 닫는다 — 안 읽고 끊으면 Flask 개발 서버가 요청마다 kqueue 하나를 못 닫는다(홈 CLAUDE.md 함정)
            return r.status == 200
    except Exception:                                            # noqa: BLE001
        return False


def health_loop(port: int, stop, check=health_ok, die=os._exit, every: float = HEALTH_EVERY, on_healthy=None) -> None:
    """제 웹이 답하는지 스스로 본다. 살아 있는데 답을 못 하면(열린 파일 한도 · 교착) 끝낸다 — 바깥 감시 프로그램은
    프로세스가 있는지만 보므로, 끝내야 다시 띄워 준다. 그동안 탭5 는 "연결 끊김"을 보인다."""
    fails = 0
    while not stop.wait(every):
        if check(port):
            fails = 0
            if on_healthy is not None:                           # 첫 성공 때 한 번 — 이 설정으로 잘 돌았다(last-good 으로 올린다, 설계 4-4)
                try:
                    on_healthy()
                except Exception:                                # noqa: BLE001
                    log.exception("last-good 올리기 실패")
                on_healthy = None
            continue
        fails += 1
        log.error("웹이 답하지 않는다(%d번째)", fails)
        if fails >= HEALTH_FAILS:
            log.error("스스로 끝낸다 — 감시가 다시 띄운다")
            die(1)
            return


def engine_lock(home: Path) -> int | None:
    """state/engine.lock 을 쥔다 — 감시 둘(바깥 감시 프로그램 · launchd)이 겹쳐 띄워도 엔진은 하나(설계 4-4).
    파일 번호는 프로세스가 끝나거나 exec 하면 닫혀 잠금이 풀린다(파이썬은 상속하지 않는 파일로 연다)."""
    import fcntl
    paths.state_dir(home).mkdir(parents=True, exist_ok=True)
    fd = os.open(paths.state_dir(home) / "engine.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


WATCH_EVERY = 5.0             # 초 — 설정 파일의 판을 보는 간격
FD_SCAN = 1024                # 다시 시작할 때 살펴볼 파일 번호의 끝(launchd 가 띄운 프로세스의 한도는 256)


def watch_loop(live, stop, restart, every: float = WATCH_EVERY) -> None:
    """설정 파일이 바뀌면 바꿔 끼우고, 연결이 바뀌었으면 다시 시작한다(설계 4-3). 이 고리가 죽지 않게 모든 실패를 삼킨다."""
    while not stop.wait(every):
        try:
            what = live.check()
        except Exception:                                        # noqa: BLE001
            log.exception("설정 감시 실패")
            continue
        if what == "swapped":
            log.info("설정을 다시 읽었다(판 %s)", live.get().version)
        elif what == "error":
            log.warning("%s", live.get().settings_error)
        elif what == "restart":
            restart("연결 설정이 바뀌었다")
            return


def restart(reason: str, execv=os.execv) -> None:
    """엔진을 같은 프로세스 번호로 다시 띄운다 — 돌고 있는 claude 를 끝내고 기록을 비운 뒤 exec(바깥 감시 프로그램의 pgrep 감시도 그대로)."""
    log.warning("엔진을 다시 시작한다: %s", reason)
    summarize.stop_running()
    for h in logging.getLogger().handlers:
        h.flush()
    # werkzeug 는 듣는 소켓을 늘 물려줌으로 두고 그 번호를 환경에 적는다 — 그대로 exec 하면 새 엔진이 같은 포트를 못 연다(실측 2026-10-15).
    # 열린 파일을 모두 '물려주지 않음'으로 바꿔 exec 때 닫히게 한다(엔진 잠금도 그때 풀려 새 엔진이 다시 쥔다)
    os.environ.pop("WERKZEUG_SERVER_FD", None)
    for fd in range(3, FD_SCAN):
        try:
            if os.get_inheritable(fd):
                os.set_inheritable(fd, False)
        except OSError:
            pass
    execv(sys.executable, [sys.executable, *sys.argv])


def _run(collector: Collector, store: Store, stop: threading.Event, needs_network: bool) -> None:
    if needs_network:
        wait_for_network(stop)
    run_loop(collector, store, stop)


def boot_record(home: Path, s: Settings) -> bool:
    """이 설정 판으로 시작한다고 적는다(시작 시도 기록). 물러서 도는 중(거절한 판이 있다)이면 적지 않는다 —
    적으면 나쁜 판의 시작 횟수가 지워져 다음 시작에 그 판으로 다시 돈다(검토 C1)."""
    if not s.version or s.rejected_version:
        return False
    settings_doc.boot_begin(home, s.version)
    return True


def bind(app, port: int):
    """웹의 듣는 소켓을 먼저 연다 — 포트를 이미 누가 쥐고 있으면(옛 엔진이 살아 있다) None. 이것이 끝나기 전에는 수집기를 띄우지 않는다:
    옛 엔진은 엔진 잠금을 모르므로, 둘이 같은 텔레그램 세션으로 접속하면 텔레그램이 세션을 폐기한다(검토 I3).
    werkzeug 는 포트가 쓰이고 있으면 SystemExit 을 올린다."""
    from werkzeug.serving import make_server
    try:
        return make_server("127.0.0.1", port, app, threaded=True)
    except (OSError, SystemExit):
        return None


def log_handlers(home, tty: bool) -> list:
    """기록은 돌려 쓰는 board.log(5MB × 3) 하나 — 터미널에서 띄웠을 때만 화면에도. launchd 의 표준 출력 파일은 돌려 쓰지 않아
    줄마다 거기에도 쓰면 끝없이 자란다(묶음 3 검토)."""
    hs = [RotatingFileHandler(paths.logs_dir(home) / "board.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")]
    if tty:
        hs.append(logging.StreamHandler())
    return hs


def main() -> int:
    home = paths.ensure()
    if engine_lock(home) is None:                                 # 기록 파일을 열기 전에 — 둘째 엔진이 같은 기록에 섞이지 않게
        print(f"다른 엔진이 이미 돈다({paths.state_dir(home) / 'engine.lock'}) — 끝낸다", file=sys.stderr)
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
                        handlers=log_handlers(home, tty=sys.stderr.isatty()))
    logging.getLogger("werkzeug").setLevel(logging.WARNING)       # 10초마다 오는 화면 요청을 기록하지 않는다
    for noisy in ("telethon", "googleapiclient.discovery_cache", "google_auth_httplib2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    s = config.load_for_engine(home)
    if s.settings_error:
        log.warning("%s", s.settings_error)
    if s.legacy_config:
        log.warning("자료 폴더(%s)에 설정이 없고 코드 폴더에 옛 config/ 가 있다 — tools/migrate.py 로 옮긴다", home)
    boot_record(home, s)
    live = config.Live(home, s)
    store = Store(s.db_path)
    token = paths.api_token(home, create=True)                 # 접속 열쇠(설계 9) — 화면 · API 는 이것이 있어야 열린다
    srv = bind(create_app(live, store, token=token, restart=restart), s.port)
    if srv is None:
        log.error("포트 %s 를 이미 다른 것이 쓰고 있다 — 수집기를 띄우지 않고 끝낸다(옛 엔진이 살아 있을 수 있다)", s.port)
        return 1
    from urgent_board.connect import telegram_login                # 연결 화면이 맡겨 둔 세션 바꾸기 · 지우기 — 옛 엔진이 없음을 안 뒤 · 수집기 전에
    done = telegram_login.adopt(paths.auth_dir(home))
    if done:
        log.info("텔레그램 세션: %s", {"swapped": "새로 로그인한 세션으로 바꿨다", "removed": "지웠다"}[done])
    stop = threading.Event()
    cs = collectors(s)
    announce(store, [c for c, _ in cs], datetime.now(timezone.utc))
    for c, net in cs:
        threading.Thread(target=_run, args=(c, store, stop, net), name=f"col-{c.name}", daemon=True).start()
    threading.Thread(target=summary_loop, args=(store, live, stop), name="summary", daemon=True).start()
    threading.Thread(target=handled_loop, args=(store, live, stop), name="handled", daemon=True).start()
    ver = s.version if not s.rejected_version else ""
    promote = (lambda: settings_doc.boot_ok(home, ver)) if ver else None
    threading.Thread(target=health_loop, args=(s.port, stop), kwargs={"on_healthy": promote}, name="health", daemon=True).start()
    threading.Thread(target=watch_loop, args=(live, stop, restart), name="settings", daemon=True).start()
    log.info("urgent-board 시작: 127.0.0.1:%s, 자료 폴더 %s, 수집기 %s, 요약 %s", s.port, home, [c.name for c, _ in cs],
             "켬" if s.summary.get("enabled") else "끔")
    try:
        srv.serve_forever()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
