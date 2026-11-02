"""연결 점검 — 읽기만 한다(설계 7). 탭5 기기 찾기는 USB 목록만 본다(포트를 열지 않는다 — 탭5 창이 독점하고 있다)."""
from __future__ import annotations
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

ESPRESSIF_VID = 0x303A


def tab5_devices(lister=None) -> list[dict]:
    """USB 로 붙은 Espressif 기기(탭5 · 스택짱 …) — 설정 화면의 "탭5 고르기". 일련번호가 탭5 창이 찾는 열쇠다."""
    if lister is None:
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        lister = list_ports.comports
    out = []
    for p in lister():
        if getattr(p, "vid", None) == ESPRESSIF_VID and getattr(p, "serial_number", None):
            out.append({"device": p.device, "serial": p.serial_number, "description": getattr(p, "description", "") or ""})
    return out


MESSAGES_DB = Path.home() / "Library" / "Messages" / "chat.db"
ADDRESS_BOOK = Path.home() / "Library" / "Application Support" / "AddressBook"
NOTE_MAX = 120                    # 수집기 상태 글은 이만큼만 보인다(오류 글에 무엇이 섞일지 모른다 — 검토 19)
NO_ACCESS = "전체 디스크 접근 권한이 필요해요. 시스템 설정 > 개인정보 보호 및 보안 > 전체 디스크 접근 권한에서 아래 파이썬 파일을 더해 주세요."


def _probe(db: Path, table: str) -> dict:
    try:
        os.stat(db)                     # Path.exists() 는 권한 오류도 '없음'으로 돌려준다(2026-10-28 왓츠앱 검토) — 둘을 가른다
    except FileNotFoundError:
        return {"ok": False, "why": "자료가 없어요(이 맥에서 그 앱을 쓴 적이 없을 수 있어요)."}
    except OSError:
        return {"ok": False, "why": NO_ACCESS}
    try:
        with closing(sqlite3.connect(Path(db).as_uri() + "?mode=ro", uri=True)) as c:      # 경로에 빈칸("Application Support" · "Group Containers")
            c.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchall()
        return {"ok": True, "why": ""}
    except sqlite3.Error:
        return {"ok": False, "why": NO_ACCESS}


def messages_access(db: Path = MESSAGES_DB) -> dict:
    """메시지 앱(문자) 자료를 이 프로세스가 읽을 수 있는가 — 엔진이 부르면 엔진의 권한을 본다."""
    return _probe(db, "message")


def contacts_access(root: Path = ADDRESS_BOOK) -> dict:
    """연락처(주소록)를 읽을 수 있는가 — 문자의 보낸 사람 이름."""
    dbs = [root / "AddressBook-v22.abcddb", *sorted(Path(root).glob("Sources/*/AddressBook-v22.abcddb"))] if Path(root).exists() else []
    found = [d for d in dbs if d.exists()]
    if not found:
        return {"ok": False, "why": "주소록 자료가 없어요."}
    return _probe(found[-1], "ZABCDRECORD")


CALL_HISTORY = Path.home() / "Library" / "Application Support" / "CallHistoryDB" / "CallHistory.storedata"
NOTIFICATIONS = Path.home() / "Library" / "Group Containers" / "group.com.apple.usernoted" / "db2" / "db"


def calls_access(db: Path = CALL_HISTORY) -> dict:
    """맥의 통화 기록(아이폰 통화가 넘어온 것)을 읽을 수 있는가 — 부재중 전화(2026-10-17). 파일이 없으면 이 맥은 통화를 잇지 않은 것."""
    return _probe(db, "ZCALLRECORD")


def teams_access(db: Path = NOTIFICATIONS) -> dict:
    """맥 알림 센터를 읽을 수 있는가 — 팀즈 알림(2026-10-17)."""
    return _probe(db, "record")


def whatsapp_access(db: Path | None = None, running=None) -> dict:
    """맥 왓츠앱 앱의 DB 를 읽을 수 있는가 + 앱 본체가 켜져 있는가(2026-10-28 — 꺼져 있으면 새 글을 볼 수 없다, 왓츠앱 설계 D11)."""
    from ..collectors import whatsapp
    return {**_probe(db or whatsapp.DB, "ZWAMESSAGE"), "running": bool((running or whatsapp.app_running)())}


def python_binary() -> str:
    """전체 디스크 접근 권한을 줄 파일 — 이 파이썬의 실제 실행 파일(가상 환경의 링크를 끝까지 따라간 것)."""
    return str(Path(os.path.realpath(sys.executable)))


def _version(path: str, run) -> str:
    try:
        r = run([path, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (r.stdout or r.stderr or "").strip().splitlines()[0][:80] if r.returncode == 0 else ""


def claude(run=subprocess.run) -> dict:
    """요약에 쓰는 claude 명령 — 요약기와 같은 자리(summarize.find_claude)를 본다."""
    from ..summarize import find_claude
    path = find_claude()
    version = _version(path, run)
    return {"path": path, "found": bool(version), "version": version}


def kakaocli(run=subprocess.run) -> dict:
    from ..collectors import kakao
    path = kakao.find_kakaocli() or ""
    version = _version(path, run) if path else ""
    return {"path": path, "found": bool(path), "version": version}


def status(settings, sources: dict) -> dict:
    """출처마다 상태 — off(꺼 둠) · waiting(아직 수집 전) · ok · stopped(수집 중단) · reconnect(메일 토큰 만료 — 다시 연결)."""
    def one(name: str, enabled: bool) -> dict:
        if not enabled:
            return {"enabled": False, "state": "off", "note": "", "at": ""}
        st = sources.get(name)
        if st is None:
            return {"enabled": True, "state": "waiting", "note": "아직 수집 전", "at": ""}
        note = str(st.get("note") or "")[:NOTE_MAX]
        state = "ok" if st.get("ok") else "reconnect" if "invalid_grant" in note else "stopped"
        return {"enabled": True, "state": state, "note": note, "at": st.get("at", "")}

    a = settings.accounts or {}
    return {
        "gmail": [{**one(f"gmail:{g.get('alias')}", True), "alias": g.get("alias", ""), "email": g.get("email", ""),
                   "label": g.get("label", "") or "", "token": "file" if str(g.get("token", "")).startswith("file:") else "keychain"}
                  for g in a.get("gmail") or []],
        **{k: one(k, bool((a.get(k) or {}).get("enabled"))) for k in ("telegram", "kakao", "imessage", "whatsapp", "calls", "teams")},
    }
