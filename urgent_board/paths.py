"""자료 폴더 경로 — 엔진 · 메뉴 막대 · 탭5 창 · 관리 명령이 모두 여기서 얻는다(설계 2026-10-15 제품판 3-2).

코드 폴더에는 아무것도 쓰지 않는다(읽기 전용이어도 돈다). 셸(tools/kiosk.sh)은 파이썬을 띄우지 않고 같은 규칙을 직접 푼다
(`${URGENT_BOARD_HOME:-$HOME/Library/Application Support/UrgentBoard}` — 시험이 글자가 같은지 본다).
이 모듈은 표준 라이브러리만 쓴다 — 설정 파일을 가볍게 읽는 read_light 만 PyYAML 을 그 자리에서 불러온다.

셸에서: `python -m urgent_board.paths logs` → 그 경로 한 줄."""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

ENV = "URGENT_BOARD_HOME"
DEFAULT_HOME = Path.home() / "Library" / "Application Support" / "UrgentBoard"
CODE_ROOT = Path(__file__).resolve().parents[1]
PRIVATE = 0o700                          # 자료 폴더 · auth — 메시지와 토큰이 들어 있다
DEFAULT_PORT = 5091


def home() -> Path:
    v = os.environ.get(ENV, "").strip()
    return Path(v).expanduser() if v else DEFAULT_HOME


def _h(h: Path | None) -> Path:
    return Path(h) if h is not None else home()


def settings_file(h: Path | None = None) -> Path:
    return _h(h) / "settings.yaml"


def last_good_file(h: Path | None = None) -> Path:
    return _h(h) / "settings.last-good.yaml"


def backups_dir(h: Path | None = None) -> Path:
    return _h(h) / "backups"


def state_dir(h: Path | None = None) -> Path:
    return _h(h) / "state"


def db_path(h: Path | None = None) -> Path:
    return state_dir(h) / "urgent.db"


def auth_dir(h: Path | None = None) -> Path:
    return _h(h) / "auth"


def logs_dir(h: Path | None = None) -> Path:
    return _h(h) / "logs"


def ensure(h: Path | None = None) -> Path:
    """자료 폴더와 그 아래 폴더를 만든다(있으면 그대로). 자료 폴더 · auth 는 나만 열게."""
    h = _h(h)
    for p in (h, state_dir(h), logs_dir(h), backups_dir(h), auth_dir(h)):
        p.mkdir(parents=True, exist_ok=True)
    for p in (h, auth_dir(h)):
        os.chmod(p, PRIVATE)
    return h


def _light_from(path: Path) -> dict | None:
    """그 파일에서 포트 · 탭5 일련번호만. 없거나 깨졌으면 None(예외를 올리지 않는다)."""
    try:
        import yaml
        doc = yaml.safe_load(path.read_bytes().decode("utf-8"))
    except Exception:                                    # noqa: BLE001 — 없음 · UTF-8 아님 · 꼴 오류 · 권한
        return None
    if not isinstance(doc, dict):
        return None
    out = {"port": DEFAULT_PORT, "tab5_usb_serial": ""}
    server, display = doc.get("server"), doc.get("display")
    port = server.get("port") if isinstance(server, dict) else None
    if isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535:
        out["port"] = port
    serial = display.get("tab5_usb_serial") if isinstance(display, dict) else None
    if isinstance(serial, str):
        out["tab5_usb_serial"] = serial.strip()
    return out


def read_light(h: Path | None = None) -> dict:
    """설정 파일에서 포트와 탭5 일련번호만 — 메뉴 막대 · 탭5 창이 엔진 코드를 불러오지 않고 쓴다.
    settings.yaml 이 깨졌으면 마지막으로 잘 돈 판(엔진이 그것으로 도는 것과 같다), 그것도 없으면 기본값.
    source 는 "settings" · "last-good" · "default" — 기본값은 '읽지 못함'이라는 뜻이다(창은 이것을 '바뀜'으로 치지 않는다, 검토 I1)."""
    for name, f in (("settings", settings_file(h)), ("last-good", last_good_file(h))):
        got = _light_from(f)
        if got is not None:
            return {**got, "source": name}
    return {"port": DEFAULT_PORT, "tab5_usb_serial": "", "source": "default"}


TOKEN_FILE = "api-token"


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,}$")


def api_token(h: Path | None = None, create: bool = False) -> str:
    """접속 열쇠(설계 9) — 엔진이 처음 뜰 때 만든다(create). 메뉴 막대 · 탭5 창 · 관리 명령은 읽기만. 없으면 "".
    이 열쇠가 있어야 화면 · API 가 열린다 — 같은 맥의 다른 사용자(이 파일을 못 읽는다)는 메시지를 읽지 못한다.
    비었거나 짧은 파일(만드는 도중 꺼짐 · 손으로 비움)은 없는 것으로 친다 — 빈 열쇠가 문을 열어 두지 않게(검토 I1).
    만드는 쪽은 엔진 하나뿐이다(engine.lock) — 임시 파일에 다 쓰고 디스크에 내린 뒤 바꿔 놓는다."""
    p = auth_dir(h) / TOKEN_FILE
    try:
        t = p.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, UnicodeDecodeError):
        t = ""
    if TOKEN_RE.match(t) or not create:
        return t if TOKEN_RE.match(t) else ""
    import secrets
    ensure(h)
    t = secrets.token_urlsafe(32)
    tmp = p.with_name(TOKEN_FILE + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(t + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return t


NAMES = {"home": home, "settings": settings_file, "state": state_dir, "db": db_path, "auth": auth_dir,
         "logs": logs_dir, "backups": backups_dir}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in (*NAMES, "code", "port"):
        print("쓰기: python -m urgent_board.paths " + "|".join((*NAMES, "code", "port")), file=sys.stderr)
        return 2
    name = argv[0]
    print(CODE_ROOT if name == "code" else read_light()["port"] if name == "port" else NAMES[name]())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
