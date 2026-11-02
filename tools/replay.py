#!/usr/bin/env python3
"""되돌아보기: 자료 폴더의 저장소에 있는 지난 7일 메시지를 지금 규칙으로 다시 판정해 긴급·대기 목록과 까닭을 표로 본다(오탐 조정용).

쓰기: .venv/bin/python tools/replay.py            긴급·대기 목록(본문 요약 포함)
      .venv/bin/python tools/replay.py --counts   건수만(본문 없음) — 등급·사업·출처·까닭별
      .venv/bin/python tools/replay.py --all      제외된 수신 메시지도 함께(누락 점검)
"""
from __future__ import annotations
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from urgent_board.config import load                       # noqa: E402
from urgent_board.store import Store                       # noqa: E402
from urgent_board.triage import preprocess                 # noqa: E402
from urgent_board.triage.pipeline import build_items, feed_only       # noqa: E402
from urgent_board.web import SOURCE_LABEL, waited_text     # noqa: E402

DAYS = 7


def rows(store: Store, settings, now: datetime) -> list[dict]:
    names = {b.key: b.name for b in settings.businesses} | {"personal": "개인", "other": "기타"}
    items = build_items(store.recent_events(days=DAYS), settings, now, store.dismissed_keys(now))
    return [{"grade": i.grade.value, "business": names.get(i.business, i.business), "source": SOURCE_LABEL.get(i.source.value, i.source.value),
             "sender": i.sender_name, "where": i.chat_name, "summary": i.summary, "reasons": list(i.reasons), "key": i.key,
             "waited": waited_text(i.waited_minutes, int((now - i.received_at).total_seconds() // 60))} for i in items]


def counts(rs: list[dict]) -> dict[str, dict]:
    return {"grade": dict(Counter(r["grade"] for r in rs)), "business": dict(Counter(r["business"] for r in rs)),
            "source": dict(Counter(r["source"] for r in rs)), "reason": dict(Counter(x for r in rs for x in r["reasons"]))}


def main() -> int:
    s = load()                                             # 자료 폴더(URGENT_BOARD_HOME)의 설정
    store = Store(s.db_path)
    now = datetime.now(timezone.utc)
    rs = rows(store, s, now)
    c = counts(rs)
    print(f"지난 {DAYS}일 · 메시지 {sum(1 for e in store.recent_events(days=DAYS) if not feed_only(e))}건 → 긴급 {c['grade'].get('urgent', 0)} · 대기 {c['grade'].get('waiting', 0)}")
    for title, key in (("사업", "business"), ("출처", "source"), ("까닭", "reason")):
        print(f"  {title}: " + ", ".join(f"{k} {v}" for k, v in sorted(c[key].items(), key=lambda kv: -kv[1])))
    if "--counts" in sys.argv:
        return 0
    print()
    for r in rs:
        print(f"[{'긴급' if r['grade'] == 'urgent' else '대기'}] {r['business']} · {r['source']} · {r['sender']} · {r['waited']}")
        print(f"       {r['summary']}")
        print(f"       까닭: {', '.join(r['reasons'])}" + (f"  (방: {r['where']})" if r['where'] != r['sender'] else ""))
    if "--all" in sys.argv:
        shown = {r["key"] for r in rs}
        print("\n— 제외된 수신 메시지(잡음·자동 발송 제외) —")
        for e in store.recent_events(days=DAYS):
            if e.is_from_me or e.key in shown or preprocess.is_noise(e, s.rules) or feed_only(e):     # 알림에만 들어가는 글은 판정 밖(2026-10-17)
                continue
            print(f"  {SOURCE_LABEL.get(e.source.value, e.source.value)} · {e.sender_name} · {preprocess.clean_text(e.text, s.rules)[:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
