#!/usr/bin/env python3
"""화면 미리보기: 가짜 표본으로 상태별 화면을 띄운다(실제 메시지·수집기 없음, 임시 DB). 디자인·탭5 실물 확인용.
이름·방·번호·문장은 모두 지어낸 것이다 — 실제 사람·대화를 넣지 말 것.

쓰기: .venv/bin/python tools/preview.py <장면> [포트=5093]   (화면은 / , 전체 목록은 /list)
장면(탭5 는 사업 상자 여섯): busy(여러 사업 · 여러 출처 · 대기 · 긴급에서 뺀 것) · one(한 사업에 한 건) · quiet(전부 조용함) ·
      waiting(읽고 답 안 한 것만) · crowded(한 사업에 출처 넷 · 긴 요약 · 수집기 일부 멈춤) · down(긴급 하나 + 일부 멈춤) ·
      somedown(긴급 없이 일부 멈춤 — "없음"이 초록이 아니어야 한다) · alldown(항목 없이 전부 수집 중단)
환경 변수 PREVIEW_NO_DIGEST=1 이면 사업 요약을 넣어 두지 않는다(요약이 아직 없을 때 상자가 무엇을 보이는지 볼 때).
그림으로 뜨기: tools/snap.py http://127.0.0.1:5093/ <파일.png>   (끊김은 snap.py 의 마지막 인자로 "lost()" 를 준다)
"""
from __future__ import annotations
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from urgent_board.config import load            # noqa: E402
from urgent_board.models import Event, Source   # noqa: E402
from urgent_board.store import Store            # noqa: E402
from urgent_board.summarize import fit_line, recipe_for, text_hash    # noqa: E402
from urgent_board.triage.pipeline import build_items   # noqa: E402
from urgent_board.web import create_app         # noqa: E402

def _recent_tuesday_3pm() -> datetime:
    """가장 가까운 지난 화요일 15:00. 날짜를 박아 두면 일주일 뒤 저장소가 '7일 안'으로 자를 때 표본이 전부 빠져 빈 화면이 된다."""
    n = datetime.now(ZoneInfo("Asia/Seoul")).replace(minute=0, second=0, microsecond=0)
    d = n.replace(hour=15) - timedelta(days=(n.weekday() - 1) % 7)
    return d if d <= n else d - timedelta(days=7)


NOW = _recent_tuesday_3pm()


def _ev(n, src, chat, sender, text, ago_min, group=False, read=False, **kw) -> Event:
    return Event(source=src, chat_id=f"c{n}", message_id=str(n), chat_name=chat, sender_id=kw.pop("sender_id", f"u{n}"), sender_name=sender, text=text,
                 received_at=NOW - timedelta(minutes=ago_min), is_from_me=False, is_read=read, is_group=group, **kw)


URGENT = [
    _ev(1, Source.KAKAO, "✈ 항공권 발권방", "김하늘", "대장님 오늘 18시까지 발권 확정 부탁드립니다. 좌석이 풀립니다", 50, group=True),
    _ev(2, Source.GMAIL, "포장 부스 견적", "이도윤(가상통번역)", "포장 부스 견적 — 금일 중 회신 가능할까요? 업체가 내일 오전 마감이라고 합니다", 200,
        account="office@example.org", raw={"subject": "포장 부스 견적"}),
    _ev(3, Source.TELEGRAM, "합창행사 준비 단체방", "사무국", "대장님 합창 연주회 동선 변경 건 오늘 중 승인 부탁드립니다", 75, group=True),
    _ev(4, Source.IMESSAGE, "+821000000000", "+821000000000", "시립정원관 대관 계약서 확인 부탁드립니다. 전화 주세요", 130),
    _ev(5, Source.KAKAO, "박주무관", "박주무관", "주무관입니다. 봄 축제 준비 결과보고서 제출 기한 확인 부탁드립니다", 400),
    _ev(6, Source.GMAIL, "서예 동호회 일정", "Dr. Tanaka", "Calligraphy club schedule — could you confirm by EOD? Action required", 90,
        account="minji@example.com", raw={"subject": "Calligraphy club schedule"}),
    _ev(7, Source.KAKAO, "최대리", "최대리", "긴급합니다. 정원조성 포럼 좌석 배치 확정 부탁드립니다", 35),
    _ev(8, Source.WHATSAPP, "Anna K.", "Anna K.", "Could you share the program for next week? We need it for the visa letter", 65, sender_id="+48000000000"),
]
WAITING = [
    _ev(11, Source.KAKAO, "정대리", "정대리", "다음 주 답사 일정 가능하신지 알려 주세요", 190, read=True),
    _ev(12, Source.GMAIL, "점심 약속", "한소라", "점심 약속 — 이번 달 모임 장소 의견 주시면 좋겠습니다", 260, read=True, account="snack@example.com", raw={"subject": "점심 약속"}),
]
# 긴급 = 안 읽은 것(URGENT), 대기 = 읽었지만 답 안 한 요청(WAITING). 긴급에서 빠지는 것의 예(전체 목록 화면에 까닭과 함께 보인다)
LEFT_OUT = [
    _ev(21, Source.GMAIL, "가을 특가", "쇼핑몰", "(광고) 가을 특가 안내 — 수신거부는 아래 링크", 30, account="snack@example.com", raw={"subject": "(광고) 가을 특가 안내"}),
    _ev(22, Source.GMAIL, "결제 안내", "가상은행", "결제 안내 — 10월 결제가 완료되었습니다", 80, account="minji@example.com", raw={"subject": "결제 안내", "labels": ["CATEGORY_UPDATES"]}),
    _ev(23, Source.GMAIL, "회의록 공유", "박대리", "회의록 공유 — 참고하시기 바랍니다", 120, group=True, account="main@example.com", raw={"subject": "회의록 공유"}),
    _ev(24, Source.KAKAO, "행사기획자 모임", "아무개", "다음 달 세미나 장소 추천 부탁드려요", 15, group=True),
]
# 미리 넣어 두는 요약(실제로는 Claude Haiku 가 만든다)
SUMMARIES = {"1": "오늘 18시까지 발권 확정 요청", "2": "포장 부스 견적 금일 중 회신 요청", "3": "합창 연주회 동선 변경 오늘 중 승인 요청",
             "4": "시립정원관 대관 계약서 확인·전화 요청", "5": "봄 축제 준비 결과보고서 제출 기한 확인 요청", "6": "서예 동호회 일정 오늘 중 확정 요청",
             "7": "정원조성 포럼 좌석 배치 확정 요청"}
# 배치를 괴롭히는 표본 — 가장 긴 이름·출처·기한이 한 줄에 몰릴 때 무엇이 잘리는지 본다
LONG_SUMMARY = "다음 주 화요일 오전 9시 시청 앞 광장 동쪽 출입구 집결, 45인승 버스 2대 기사 연락처와 차량 번호 회신 요청"
STRESS = [
    _ev(31, Source.GMAIL, "숙박 명단 변경", "Alexandria Wolfeschlegelstein (Global Events Co.)", "내일 18시까지 숙박 명단 확정 부탁드립니다. " + LONG_SUMMARY, 500,
        account="office@example.org", raw={"subject": "숙박 명단 변경"}),
    _ev(32, Source.KAKAO, "동네 서예 동호회 회원·강사·전시 준비 통합 소통방 (2026 가을)", "최대리", "긴급합니다. 모레 12시까지 좌석 배치 확정 부탁드립니다", 480, group=True),
    _ev(33, Source.TELEGRAM, "합창행사 준비 단체방", "사무국", "대장님 금요일 17시까지 동선 변경 승인 부탁드립니다", 470, group=True),
    _ev(34, Source.IMESSAGE, "+821000000000", "+821000000000", "대관 계약서 확인 부탁드립니다. 오늘 18시까지 전화 주세요", 460),
    _ev(35, Source.GMAIL, "Calligraphy club schedule", "Dr. Tanaka", "Calligraphy club schedule — could you confirm by EOD? Action required", 90,
        account="minji@example.com", raw={"subject": "Calligraphy club schedule"}),
    _ev(36, Source.KAKAO, "박주무관", "박주무관", "주무관입니다. 봄 축제 준비 결과보고서 내일 오전까지 제출 부탁드립니다", 400),
    _ev(37, Source.GMAIL, "정원조성 포럼 좌석", "한소라", "정원조성 포럼 좌석 배치 — 금일 중 회신 가능할까요?", 35, account="main@example.com", raw={"subject": "정원조성 포럼 좌석"}),
    _ev(38, Source.GMAIL, "포장 부스 견적", "이도윤(가상통번역)", "포장 부스 견적 — 금일 중 회신 가능할까요? 업체가 내일 오전 마감이라고 합니다", 200,
        account="office@example.org", raw={"subject": "포장 부스 견적"}),
    _ev(39, Source.KAKAO, "정대리", "정대리", "답사 차량 9시 출발 확정인지 지금 바로 알려 주세요", 20),
]
# 한 줄에 가장 많이 몰리는 경우: 긴 사업 이름 + 여러 계정에 온 메일 + 오래 기다림 + 먼 기한
WIDE = [
    _ev(41 + i, Source.GMAIL, "정원조성 포럼 대관", "한소라", "정원조성 포럼 대관 — 다음 주 금요일 18시까지 계약서 회신 부탁드립니다. 금액은 1,250만원입니다.", 60 * 30,
        account=acc, sender_id="han@venue.example", raw={"subject": "정원조성 포럼 대관"}) for i, acc in enumerate(("minji@example.com", "main@example.com", "snack@example.com"))
] + [_ev(45, Source.KAKAO, "동네 서예 동호회 회원·강사·전시 준비 통합 소통방 (2026 가을)", "최대리", "대장님 긴급합니다. 모레 12시까지 좌석 배치 확정 부탁드립니다", 60 * 26, group=True, mentioned_me=True)]
STRESS_SUMMARIES = {"31": LONG_SUMMARY, "32": "모레 12시까지 좌석 배치 확정 요청", "33": "금요일 17시까지 동선 변경 승인 요청", "35": "서예 동호회 일정 오늘 중 확정 요청"}
# 한 사업(동네 합창 모임)에 네 길로 한꺼번에 — 출처 꼬리표 넷이 한 줄에 들어가는지, 긴 요약이 세 줄에서 잘리는지 본다
CROWDED = STRESS + [
    _ev(61, Source.IMESSAGE, "+821000000001", "+821000000001", "합창 연주회 음향 리허설 시간을 오늘 안에 알려 주세요. 업체가 기다립니다", 55),
    _ev(62, Source.KAKAO, "합창 공연팀", "오지휘", "합창 공연 순서 확정본을 오늘 17시까지 보내 주실 수 있을까요", 45),
    _ev(63, Source.GMAIL, "합창 연주회 초청장 문안", "후원회", "합창 연주회 초청장 문안 — 수정본 확인하시고 내일 오전까지 회신 부탁드립니다", 65,
        account="office@example.org", raw={"subject": "합창 연주회 초청장 문안"}),
]
SCENES = {"busy": URGENT + WAITING + LEFT_OUT, "one": URGENT[2:3], "quiet": [], "waiting": WAITING, "crowded": CROWDED, "down": URGENT[:1],
          "somedown": [], "alldown": []}
DOWN_ALL = ("kakao", "imessage", "telegram", "gmail:workbase", "gmail:choir")


def build(scene: str):
    st = Store(Path(tempfile.mkdtemp(prefix="urgent-preview-")) / "preview.db")
    st.upsert_events(SCENES[scene])
    settings = load(ROOT / "tests" / "fixtures" / "home")      # 지어낸 장면은 시험용 설정으로(운영 설정에 기대지 않는다)
    settings.handled = {**settings.handled, "claude_sessions": False}                  # 미리보기는 다른 세션의 기록을 보지 않는다
    known = {**SUMMARIES, **STRESS_SUMMARIES}
    for it in build_items(st.recent_events(days=3650), settings, NOW, set()):          # 요약이 붙은 모습을 보인다
        mid = it.key.rsplit(":", 1)[-1]
        if mid in known:
            st.put_summary(it.key, text_hash(it.text, recipe_for(settings)), known[mid], NOW)
    _fake_task_lines(st, settings)
    for name in ("imessage", "telegram", "gmail:workbase", "gmail:choir"):
        st.set_source_status(name, True, "preview", NOW)
    st.set_source_status("kakao", scene not in ("down", "crowded", "somedown"), "preview", NOW)
    if scene in ("down", "crowded"):
        st.set_source_status("gmail:workbase", False, "preview", NOW)
        st.set_source_status("gmail:choir", False, "preview", NOW)
    if scene == "alldown":
        for name in DOWN_ALL:
            st.set_source_status(name, False, "preview", NOW)
    return create_app(settings, st, clock=lambda: NOW)


DUE_DAYS = {"1": 0, "31": 8, "32": 2}               # 지어낸 기한(오늘 · 다음 주 · 모레) — 기한 임박(주황)과 아닌 것을 함께 보인다


def _fake_task_lines(st: Store, settings) -> None:
    """할 일 문구 · 기한이 붙은 모습을 보인다(실제로는 요약과 같은 호출에서 Claude Haiku 가 뽑는다) — 지어낸 요약에서 '요청'을 떼고 줄인 것."""
    known = {**SUMMARIES, **STRESS_SUMMARIES}
    r = recipe_for(settings)
    for it in build_items(st.recent_events(days=3650), settings, NOW, set()):
        mid = it.key.rsplit(":", 1)[-1]
        if mid in known:
            due = (NOW + timedelta(days=DUE_DAYS[mid])).date() if mid in DUE_DAYS else None
            st.put_task_line(it.key, text_hash(it.text, r), fit_line(known[mid].removesuffix(" 요청"), 14), due, NOW)


def main() -> int:
    scene = sys.argv[1] if len(sys.argv) > 1 else "busy"
    if scene not in SCENES:
        print(__doc__); return 2
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5093
    build(scene).run(host="127.0.0.1", port=port, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
