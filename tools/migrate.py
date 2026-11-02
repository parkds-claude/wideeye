#!/usr/bin/env python3
"""옛 설정(코드 폴더의 config/ · .env · state/ · auth/)을 자료 폴더의 설정 v2 로 옮긴다(설계 2026-10-15 제품판 4-5).

먼저 견준다: 옛 길로 읽은 설정과 새 문서로 만든 설정이 같은지, 저장소가 있으면 지난 7일을 두 설정으로 각각 판정해
(board.screen — 판정 → 화면에 낼 것 → 할 일 상자, 화면에 실제로 뜨는 것) 한 건도 다르지 않은지. 다르면 무엇이 다른지 적고 쓰지 않는다(종료값 1).

쓰기: .venv/bin/python tools/migrate.py --from ~/urgent-board              보기만(아무것도 쓰지 않는다)
      .venv/bin/python tools/migrate.py --from ~/urgent-board --write      같으면 쓴다(설정 · 저장소 사본 · 텔레그램 세션 사본 · 옛 설정 사본)
      … --print-scan <파일.json> --now <ISO 시각>                          옮긴 새 설정으로 판정한 결과를 파일로(본문 없음 — 옛 코드와 견줄 때)
저장소는 SQLite 백업 API 로 복사한다(옛 엔진이 돌고 있어도 된다 — WAL 을 cp 하지 않는다). 텔레그램 세션은 복사만 한다 —
옛 엔진과 새 엔진이 동시에 쓰면 텔레그램이 세션을 폐기한다(바꿔 끼울 때 옛 엔진을 먼저 끝낸다, 설계 13)."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from urgent_board import board, config, paths, settings_doc, settings_legacy, summarize   # noqa: E402
from urgent_board.store import Store                                                        # noqa: E402

DAYS = 7


class Report(NamedTuple):
    diffs: list[str]
    scan_diffs: list[str]
    wrote: bool
    note: str = ""


def snapshot(db: Path, dest: Path) -> Path:
    """돌고 있는 SQLite 파일의 한 순간을 그대로 복사(백업 API). 임시 이름에 받은 뒤 바꿔 놓는다 — 실패하면 아무것도 남기지 않는다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as src, closing(sqlite3.connect(tmp)) as out:
            src.backup(out)
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def names_by_id(store: Store) -> dict:
    """(출처, 방 번호 글자) → 가장 최근 방 이름 — 번호 항목의 이름표(판정에 안 쓴다)."""
    out = {}
    for e in sorted(store.recent_events(days=30), key=lambda e: e.received_at):
        if e.source.value in ("kakao", "telegram") and e.chat_name:
            out[(e.source.value, str(e.chat_id))] = e.chat_name
    return out


def scan_print(store: Store, s, now: datetime) -> dict:
    """화면에 실제로 뜨는 것까지의 판정 결과 — 키 · 등급 · 사업 · 기다린 분 · 뺀 것 · 상자. 본문은 넣지 않는다(사업 요약 글은 해시로)."""
    sc = board.screen(store, s, now, DAYS)                     # 웹 · 설정 화면의 미리 보기와 같은 한 길
    items, skipped, rec, seen = sc.items, sc.skipped, sc.recipe, sc.seen
    return {
        "items": [[i.key, i.grade.value, i.business, i.waited_minutes, i.count, list(i.reasons), list(i.accounts)] for i in items],
        "skipped": [[x.key, x.reason] for x in skipped],
        "unneeded": sorted(seen.unneeded), "handled": sorted(seen.handled),
        "cards": [[c.key, c.name, c.color, c.state, c.urgent, c.waiting, [summarize.text_hash(t.conv) for t in c.tasks]] for c in sc.cards],   # 대화 열쇠에 메일 제목이 들어 있다 — 해시로
        "recipes": [rec],
    }


def compare_scans(store: Store, a, b, now: datetime) -> list[str]:
    pa, pb = scan_print(store, a, now), scan_print(store, b, now)
    return [f"{k}: {len(pa[k])} → {len(pb[k])}" for k in pa if pa[k] != pb[k]]


def run(src: Path, home: Path, write: bool = False, engine: str = "external", now: datetime | None = None,
        extra_holidays_file: Path = settings_legacy.EXTRA_HOLIDAYS_FILE, print_scan: Path | None = None) -> Report:
    src, home, now = Path(src), Path(home), now or datetime.now(timezone.utc)
    raw = settings_legacy.read_raw(src, extra_holidays_file)
    old = settings_legacy.load_legacy(src, extra_holidays_file)
    db = src / "state" / "urgent.db"
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(snapshot(db, Path(tmp) / "snap.db")) if db.exists() else None
        doc = settings_legacy.to_doc(raw, engine=engine, names_by_id=names_by_id(store) if store else None)
        bad = [p for p in config.problems(doc) if p.level == "error"]
        if bad:
            return Report([f"문서: {p.path} — {p.message}" for p in bad], [], False)
        new = config.from_doc(doc, home)
        diffs = settings_legacy.compare(old, new)
        scan_diffs = compare_scans(store, old, new, now) if store else []
        if print_scan is not None and store is not None:
            Path(print_scan).write_text(json.dumps(scan_print(store, new, now), ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
        if diffs or scan_diffs or not write:
            return Report(diffs, scan_diffs, False)
        if paths.settings_file(home).exists():
            return Report(diffs, scan_diffs, False, f"이미 설정이 있다({paths.settings_file(home)}) — 덮어쓰지 않는다")
        # 차례: 자료(저장소 · 세션 · api 값 · 옛 설정 사본)를 먼저, 설정 파일은 맨 마지막 — 중간에 실패해도 다시 돌릴 수 있게(검토 M5).
        # 저장소와 텔레그램 세션은 둘 다 SQLite 라 백업 API 로(옛 엔진이 열어 두고 쓰는 중이어도 온전한 사본 — 검토 I4)
        paths.ensure(home)
        legacy = paths.backups_dir(home) / "legacy-config"
        legacy.mkdir(parents=True, exist_ok=True)
        for name in ("businesses.yaml", "rules.yaml"):
            shutil.copy2(src / "config" / name, legacy / name)
        if store and not paths.db_path(home).exists():
            snapshot(db, paths.db_path(home))
        auth = paths.auth_dir(home)
        session = src / (raw.env.get("TELEGRAM_SESSION_PATH") or "auth/telethon.session")
        if session.exists() and not (auth / "telethon.session").exists():
            snapshot(session, auth / "telethon.session")
        if raw.env.get("TELEGRAM_API_ID") and raw.env.get("TELEGRAM_API_HASH") and not (auth / "telegram.json").exists():
            p = auth / "telegram.json"
            p.write_text(json.dumps({"api_id": int(raw.env["TELEGRAM_API_ID"]), "api_hash": raw.env["TELEGRAM_API_HASH"]}), encoding="utf-8")
            os.chmod(p, 0o600)
        settings_doc.save(home, doc, None)
        settings_doc.mark_good(home)
        return Report(diffs, scan_diffs, True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="옛 설정을 자료 폴더의 설정 v2 로 옮긴다(먼저 견준다)")
    ap.add_argument("--from", dest="src", required=True, type=Path)
    ap.add_argument("--home", type=Path, default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--engine", choices=settings_doc.ENGINES, default="external")
    ap.add_argument("--print-scan", type=Path, default=None)
    ap.add_argument("--now", default="")
    a = ap.parse_args(argv)
    home = (a.home or paths.home()).expanduser()
    now = datetime.fromisoformat(a.now) if a.now else None
    rep = run(a.src.expanduser(), home, write=a.write, engine=a.engine, now=now, print_scan=a.print_scan)
    for d in rep.diffs:
        print("설정이 다르다 —", d)
    for d in rep.scan_diffs:
        print("판정이 다르다 —", d)
    if rep.note:
        print(rep.note)
    if rep.diffs or rep.scan_diffs:
        print("다른 것이 있어 쓰지 않았다.")
        return 1
    print("옛 설정과 새 설정이 같다(설정 · 지난 7일 판정)." + (f" 썼다: {home}" if rep.wrote else " 쓰려면 --write"))
    return 0 if (rep.wrote or not a.write) else 1


if __name__ == "__main__":
    raise SystemExit(main())
