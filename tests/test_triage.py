from datetime import datetime, timedelta
from urgent_board.workhours import WorkCalendar
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from urgent_board.config import load
from urgent_board.models import Event, Source, Grade
from urgent_board.triage import needs_reply, preprocess, directed, unanswered, business, pipeline

KST = ZoneInfo("Asia/Seoul"); ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
S = load(HOME); R = S.rules
NOW = datetime(2026, 10, 13, 15, 0, tzinfo=KST)

def ev(text, *, src=Source.KAKAO, chat="방", chat_id="c1", sender="김과장", sid="u9", me=False, group=False, at=None, read=True, **kw):
    return Event(source=src, chat_id=chat_id, message_id=str(abs(hash((text, sid, at)))), chat_name=chat, sender_id=sid, sender_name=sender,
                 text=text, received_at=at or NOW - timedelta(hours=3), is_from_me=me, is_read=read, is_group=group, **kw)

@pytest.mark.parametrize("t", ["가능할까요?", "언제 오시나요", "몇 시죠", "확인 부탁드립니다", "회신 요망", "Could you confirm by Friday?", "자료 보내 주세요", "발권 진행해도 될지요"])
def test_question_or_request(t):
    assert needs_reply.needs_reply(t, R)

@pytest.mark.parametrize("t", ["알겠습니다", "수고하셨습니다", "https://a.b/c?x=1 올립니다", "넵", "그렇죠 맞습니다"])
def test_not_question(t):
    assert not needs_reply.needs_reply(t, R) or t == "그렇죠 맞습니다" and needs_reply.needs_reply(t, R) == ["q_end_soft"]

def test_noise_and_auto():
    assert preprocess.is_noise(ev("ㅋㅋㅋ"), R) and preprocess.is_noise(ev("👍"), R)
    assert preprocess.is_auto(ev("안내", src=Source.GMAIL, sid="noreply@site.com"), R, set())
    assert not preprocess.is_auto(ev("안내", src=Source.GMAIL, sid="info@client.go.kr"), R, {"info@client.go.kr"})   # 내가 답한 적 있음

def test_group_question_without_mention_is_not_directed():        # Review Focus 2
    assert not directed.is_directed(ev("오늘 몇 시에 모이나요?", group=True), S, None)
    assert directed.is_directed(ev("대장님 오늘 몇 시에 모이나요?", group=True), S, None)
    assert directed.is_directed(ev("오늘 몇 시에 모이나요?", group=True, mentioned_me=True), S, None)
    assert directed.is_directed(ev("오늘 몇 시에 모이나요?", group=True), S, NOW - timedelta(hours=3, minutes=5))   # 내 발언 5분 뒤
    assert directed.is_directed(ev("오늘 몇 시에 모이나요?", group=False), S, None)

def test_reply_on_other_channel_counts():                          # Review Focus 4
    q = ev("견적 가능할까요?", src=Source.GMAIL, sid="kim@client.com", sender="김과장")
    my_kakao = ev("네 보냈습니다", src=Source.KAKAO, sid="u9", sender="김과장", me=True, at=NOW - timedelta(hours=1))
    assert unanswered.is_unanswered(q, [], R)
    assert not unanswered.is_unanswered(q, [my_kakao], R, same_contact=True)

def test_business_tag():
    assert business.tag(ev("안녕", chat="합창단_책마을&해오름"), S) == "bazaar2"
    assert business.tag(ev("서예 동호회 일정 확인 부탁드립니다"), S) == "calligraphy-club"
    assert business.tag(ev("점심 뭐 먹을까요", src=Source.GMAIL, account="snack@example.com"), S) == "personal"

def test_pipeline_grades():
    e1 = ev("긴급! 오늘까지 회신 부탁드립니다", chat="책마을-해오름 항공권 발권방", chat_id="c3", sid="u7", sender="주하늘", group=True, mentioned_me=True, read=False, at=NOW - timedelta(minutes=40))   # 안 읽음 → 긴급
    e2 = ev("가능할까요?", chat_id="c2", sid="u8", sender="이대리", group=False, at=NOW - timedelta(minutes=30))   # 30분 — 아직 없음(다른 상대 — 같은 상대면 재촉 2회로 긴급)
    e3 = ev("자료 확인 부탁드립니다", group=False, at=NOW - timedelta(hours=3))         # 3시간 — 대기
    items = pipeline.build_items([e1, e2, e3], S, NOW, set())
    grades = {i.key: i.grade for i in items}
    assert grades[e1.key] == Grade.URGENT and grades[e3.key] == Grade.WAITING and e2.key not in grades
    assert items[0].grade == Grade.URGENT and items[0].business == "bookshop-team"

def test_nudge_is_noted_on_the_waiting_item():                     # 읽었으면 재촉이 있어도 '대기'(긴급은 안 읽은 것뿐)
    a = ev("자료 확인 부탁드립니다", group=False, at=NOW - timedelta(hours=3))
    b = ev("확인 가능할까요?", group=False, at=NOW - timedelta(minutes=30))
    items = pipeline.build_items([a, b], S, NOW, set())
    assert len(items) == 1 and items[0].grade == Grade.WAITING and "nudged" in items[0].reasons

# --- 화면에 바로 드러나는 묶음 규칙 (스펙 §5-3 ③, §5-3 '사라지는 때')
def test_split_messages_are_not_nudges():
    a = ev("내일 회의 몇 시에 할까요?", group=False, at=NOW - timedelta(minutes=31))
    b = ev("장소도 알려 주세요", group=False, at=NOW - timedelta(minutes=30))     # 1분 뒤 이어 보낸 것 — 재촉 아님
    assert pipeline.build_items([a, b], S, NOW, set()) == []

def test_merged_item_keeps_highest_grade():
    a = ev("자료 확인 부탁드립니다", group=False, read=False, at=NOW - timedelta(hours=3))   # 안 읽음 → 긴급
    b = ev("확인 가능할까요?", group=False, at=NOW - timedelta(hours=2, minutes=30))          # 읽음 → 대기
    items = pipeline.build_items([a, b], S, NOW, set())
    assert len(items) == 1 and items[0].grade == Grade.URGENT
    assert items[0].waited_minutes == 120 and items[0].key == b.key              # 12:00 수신 → 점심 빼고 2시간. 처리됨은 가장 최근 메시지 기준

def test_dismiss_hides_older_from_same_sender_until_new_message():
    a = ev("자료 확인 부탁드립니다", group=False, at=NOW - timedelta(hours=3))
    b = ev("확인 가능할까요?", group=False, at=NOW - timedelta(hours=2, minutes=30))
    assert pipeline.build_items([a, b], S, NOW, {b.key}) == []                   # 옛 메시지가 다시 뜨지 않는다
    c = ev("긴급합니다 회신 부탁드립니다", group=False, at=NOW - timedelta(minutes=5))
    items = pipeline.build_items([a, b, c], S, NOW, {b.key})
    assert [i.key for i in items] == [c.key]                                     # 새로 보내면 다시 뜬다

def test_other_sender_in_same_room_is_separate_item():
    a = ev("대장님 확인 부탁드립니다", chat_id="g1", sid="u1", sender="김", group=True, at=NOW - timedelta(hours=6))
    b = ev("대장님 견적 가능할까요?", chat_id="g1", sid="u2", sender="이", group=True, at=NOW - timedelta(hours=5, minutes=30))
    assert len(pipeline.build_items([a, b], S, NOW, set())) == 2

# --- 기한: 날짜·시각 뒤에 기한 꼬리말(까지·내로·중으로…)이 있을 때만 (조사 C §3)
from urgent_board.triage import grade as grading

def _dl(text):
    d = grading.find_deadline(text, NOW, R)
    return d.astimezone(KST).strftime("%m-%d %H:%M") if d else None

@pytest.mark.parametrize("text,want", [
    ("내일 회의 몇 시에 할까요?", None),                 # 날짜 언급일 뿐 기한이 아니다
    ("어제 보낸 자료 확인 부탁드립니다", None),
    ("1시간 뒤에 전화드릴게요", None),
    ("오늘까지 회신 부탁드립니다", "10-13 18:00"),        # 날짜만 있으면 그날 업무 종료
    ("오늘 18시까지 회신 부탁드립니다", "10-13 18:00"),
    ("내일 오전까지 부탁드립니다", "10-14 12:00"),        # 구간이면 구간 끝
    ("10/30까지 확정 필요합니다", "10-30 18:00"),
    ("금주 중으로 회신 바랍니다", "10-16 18:00"),
    ("5시 전까지 부탁드립니다", "10-13 17:00"),
])
def test_find_deadline_needs_deadline_suffix(text, want):
    assert _dl(text) == want

def test_date_mention_alone_is_not_urgent():
    e = ev("내일 회의 몇 시에 할까요?", group=False, at=NOW - timedelta(minutes=31))
    assert pipeline.build_items([e], S, NOW, set()) == []

# --- 실제 7일치를 돌려 찾은 결함(2026-10-13 되돌아보기) — 문장은 꼴만 남기고 바꿨다
import copy

@pytest.mark.parametrize("t", [
    "안녕하세요 여행사 김과장입니다. 잘 부탁드립니다!",                 # 인사말의 '부탁'
    "네 잘 확인하였고 시스템 반영 중에 있습니다. 곧 회신드리겠습니다",   # 상대가 회신하겠다는 말
    "답변드립니다. 일정은 변동 없습니다",
])
def test_courtesy_and_their_own_reply_are_not_requests(t):
    assert needs_reply.needs_reply(t, R) == []

@pytest.mark.parametrize("t", ["회신 부탁드립니다", "답변 주세요", "회신 요망", "답장 기다리겠습니다", "빠른 회신 바랍니다", "잘 부탁드립니다. 견적서 보내 주세요"])
def test_real_reply_requests_still_match(t):
    assert needs_reply.needs_reply(t, R)

def mail(text, *, sid="kim@client.com", sender="김과장", account="minji@example.com", thread="t1", me=False, at=None, to=None, subject=None,
         read=True, group=False, labels=None, headers=None):
    return ev(text, src=Source.GMAIL, chat=subject or text, chat_id=thread, sid=sid, sender=sender, me=me, at=at, account=account, thread_id=thread,
              read=read, group=group, raw={"subject": subject or text, "headers": headers or {}, "to": to or [account], "labels": labels or []})

def test_auto_mail_by_sender_and_subject():
    assert preprocess.is_auto(mail("Verification Code — Dear user", sid="app@wearable.example"), R, set())
    assert preprocess.is_auto(mail("Confirm your email for your new account", sid="security@account.example.com"), R, set())
    assert preprocess.is_auto(mail("[서비스] 인증번호 안내", sid="hello@service.example"), R, set())
    assert not preprocess.is_auto(mail("견적 가능할까요?"), R, set())

def test_same_mail_in_two_accounts_is_one_item():
    a = mail("객실 타입 변경 요청사항 전달 드립니다. 확인 부탁드립니다", account="minji@example.com", thread="t1", at=NOW - timedelta(hours=3))
    b = mail("객실 타입 변경 요청사항 전달 드립니다. 확인 부탁드립니다", account="main@example.com", thread="t9", at=NOW - timedelta(hours=3))
    assert len(pipeline.build_items([a, b], S, NOW, set())) == 1

def test_reply_from_another_account_counts():
    q1 = mail("견적 가능할까요?", subject="견적 문의", account="minji@example.com", thread="t1", at=NOW - timedelta(hours=3))
    q2 = mail("견적 가능할까요?", subject="견적 문의", account="main@example.com", thread="t9", at=NOW - timedelta(hours=3))
    mine = mail("보냈습니다", subject="Re: 견적 문의", sid="main@example.com", sender="나", account="main@example.com", thread="t9", me=True,
                at=NOW - timedelta(hours=1), to=["kim@client.com"])
    assert pipeline.build_items([q1, q2, mine], S, NOW, set()) == []

def test_mail_on_another_subject_does_not_clear_other_requests():   # 재현율 우선 — 메일끼리는 같은 제목일 때만 답
    q = mail("항공권 진행 가능할까요?", subject="[운영진] 항공권 진행 요청", thread="t1", at=NOW - timedelta(hours=3))
    other = mail("잘 받았습니다", subject="Re: 웹포스터 시안", sid="minji@example.com", sender="나", thread="t5", me=True,
                 at=NOW - timedelta(hours=1), to=["kim@client.com"])
    assert len(pipeline.build_items([q, other], S, NOW, set())) == 1

def test_pipeline_reply_on_other_channel_counts_when_contact_is_registered():     # Review Focus 4 — 파이프라인 수준
    q = mail("견적 가능할까요?", at=NOW - timedelta(hours=3))
    their_kakao = ev("안녕하세요", chat="김과장", chat_id="k1", sid="u500", sender="김과장", at=NOW - timedelta(days=1))
    my_kakao = ev("네 보냈습니다", chat="김과장", chat_id="k1", sid="me", sender="나", me=True, at=NOW - timedelta(hours=1))
    assert len(pipeline.build_items([q, their_kakao, my_kakao], S, NOW, set())) == 1          # 등록 전: 다른 사람으로 본다
    S2 = copy.copy(S); S2.contacts = {"kim@client.com": "김과장", "kakao:김과장": "김과장"}
    assert pipeline.build_items([q, their_kakao, my_kakao], S2, NOW, set()) == []             # 등록 후: 카톡 답이 메일 질문의 답

def test_auto_sender_i_wrote_to_before_is_kept():                   # 스펙 §5-1 ① 단서
    q = mail("회신 부탁드립니다", sid="news@client.go.kr", sender="발주처", at=NOW - timedelta(hours=3))
    assert pipeline.build_items([q], S, NOW, set()) == []                                      # 자동 발송 주소 꼴 → 제외
    earlier = mail("자료 보냅니다", sid="minji@example.com", sender="나", me=True, thread="t0", at=NOW - timedelta(days=2), to=["news@client.go.kr"])
    assert len(pipeline.build_items([earlier, q], S, NOW, set())) == 1                         # 내가 쓴 적 있는 주소 → 남긴다

def test_business_tag_prefers_the_most_specific_keyword():
    assert business.tag(mail("초청작가 항공권 진행 요청", subject="[책방운영단] 초청작가 항공권 진행 요청"), S) == "bookshop-team"
    assert business.tag(mail("결과보고서 수정요청", subject="책방봄맞이축제 - 결과보고서 수정요청"), S) == "spring-festival"
    assert business.tag(mail("책방 관련 문의드립니다", subject="문의"), S) == "bookshop-team"            # '책방'만으로는 지금 진행 중인 운영진 일(아래 시험)


def test_mail_from_an_active_project_team_goes_to_that_box_not_to_a_finished_event_box():
    # 실제 화면(2026-10-14): 운영진 담당자의 메일(다음 달 행사 건)이 지난달에 끝난 '봄 축제 준비' 상자에 떴다. 사용자: "봄 축제 준비는
    # 이미 진행한 행사 · 책방운영단은 다음 달 행사"(이름은 지어낸 것). 서명의 기관 이름("… 책방 문화진흥 책방운영진")과 행사 이름에 든 '책방'·'문화진흥'이
    # 축제의 낱말이었고, 가장 긴 낱말이 이기는 셈에서 '문화진흥'(4자)이 '운영진'(3자)을 이겼다. (아래 글은 꼴만 남긴 지어낸 문장)
    sign = " 가나다 드림 --------- 재단법인 가나다 책방 문화진흥 책방운영진"
    assert business.tag(mail("계획안 참고 부탁드립니다." + sign, subject="가나다책방 문화진흥 확장개점 기념 북토크 계획(안)"), S) == "bookshop-team"
    assert business.tag(mail("과업지시서 송부드리오니 검토 부탁드립니다." + sign, subject="Re: FW: 북토크 준비 상황판 송부드립니다"), S) == "bookshop-team"
    # 축제이라고 적힌 것은 여전히 축제이다 — 운영진 서명이 붙어 있어도(행사 이름이 더 구체적이다)
    assert business.tag(mail("결과보고서 수정본 송부드립니다." + sign, subject="책방 봄맞이 축제 결과보고서"), S) == "spring-festival"
    assert business.tag(mail("정산 자료 요청드립니다", subject="책방봄맞이축제 정산"), S) == "spring-festival"
    # 서명 없이 온 같은 사람의 짧은 답장은 그 사람의 다른 메일에서 사업을 물려받는다(한 사업에만 걸려 있어야 물려받는다)
    known = mail("계획안 참고 부탁드립니다." + sign, subject="확장개점 기념 북토크 계획(안)", sid="staff@team.example", sender="가나다", thread="t1",
                 at=NOW - timedelta(days=1))
    reply = mail("네 확인했습니다. 내일 다시 보내 주실 수 있을까요", subject="RE: 웹포스터 시안 송부드립니다", sid="staff@team.example", sender="가나다", thread="t2",
                 read=False, at=NOW - timedelta(minutes=30))
    assert business.tag(reply, S) == "other"
    assert {i.business for i in pipeline.build_items([known, reply], S, NOW, set())} == {"bookshop-team"}

def test_mail_summary_drops_reply_prefixes():                      # 좁은 화면 — 'Re: Re:' 가 요약 자리를 먹지 않게
    q = mail("Re: Re: [행사 숙박] 명단 변경 — 확인 부탁드립니다", subject="Re: Re: [행사 숙박] 명단 변경", at=NOW - timedelta(hours=3))
    assert pipeline.build_items([q], S, NOW, set())[0].summary == "[행사 숙박] 명단 변경 — 확인 부탁드립니다"
    k = ev("Re: 라고 쓴 카톡 확인 부탁드립니다", group=False, at=NOW - timedelta(hours=3))
    assert pipeline.build_items([k], S, NOW, set())[0].summary.startswith("Re: 라고")      # 메일만


# ===== 최종 검토에서 나온 결함 (2026-10-13) =====
BIZ_ROOM = dict(chat="합창단_책마을&해오름", chat_id="100000000000002")      # 등록된 사업 방(설정 boxes — 시험용 자료 폴더)

@pytest.mark.parametrize("follow", ["참고로 좌석은 12C 입니다. 감사합니다", "네 확인했습니다. 추가로 명단도 보내드릴게요"])
def test_followup_with_closing_words_does_not_resolve_the_question(follow):       # C2 — 끝인사가 질문을 지우면 안 된다
    q = ev("발권 진행할까요? 좌석이 오늘 풀립니다", group=False, at=NOW - timedelta(hours=4))
    f = ev(follow, group=False, at=NOW - timedelta(hours=3, minutes=20))
    assert len(pipeline.build_items([q, f], S, NOW, set())) == 1

@pytest.mark.parametrize("follow", ["해결됐습니다 감사합니다", "감사합니다", "넵", "취소합니다", "아 그건 처리됐습니다"])
def test_short_resolution_from_the_asker_clears_the_question(follow):
    q = ev("발권 진행할까요?", group=False, at=NOW - timedelta(hours=4))
    f = ev(follow, group=False, at=NOW - timedelta(hours=3, minutes=20))
    assert pipeline.build_items([q, f], S, NOW, set()) == []

def test_followup_without_a_request_is_not_a_nudge():                              # I1
    q = ev("견적서 확인 부탁드립니다", group=False, at=NOW - timedelta(minutes=50))
    f = ev("첨부는 두 장입니다", group=False, at=NOW - timedelta(minutes=30))
    assert pipeline.build_items([q, f], S, NOW, set()) == []                       # 50분 — 아직 없음

def test_group_talk_to_someone_else_is_not_a_nudge():                              # I1
    q = ev("대장님 확인 부탁드립니다", chat_id="g1", group=True, at=NOW - timedelta(minutes=50))
    f = ev("이대리님 그 건은 제가 처리할게요. 확인 부탁드립니다", chat_id="g1", group=True, at=NOW - timedelta(minutes=30))
    assert pipeline.build_items([q, f], S, NOW, set()) == []

def test_keyword_business_tag_does_not_change_the_grade():
    e = mail("항공권 e-티켓 안내 — 예약이 확정되었습니다", sid="etickets@airline.example", sender="항공사", at=NOW - timedelta(minutes=45))
    assert business.tag(e, S) == "bookshop-team"                                     # 꼬리표는 키워드로 붙는다
    assert pipeline.build_items([e], S, NOW, set()) == []                          # 읽었고 질문도 없다

def test_read_attachment_or_sticker_only_message_is_not_shown():
    for t in ("[이모티콘]", "[첨부]", "[미디어]"):
        assert pipeline.build_items([ev(t, group=False, at=NOW - timedelta(minutes=45), **BIZ_ROOM)], S, NOW, set()) == []

def test_client_title_needs_a_question():                                          # I4 — 스펙 ⑤ '호칭 + 질문'
    e = ev("팀장님께 보고드렸습니다", group=False, at=NOW - timedelta(minutes=10), **BIZ_ROOM)
    assert pipeline.build_items([e], S, NOW, set()) == []                          # 질문 없음, 안 읽은 지 10분
    ctx = grading.GradeCtx(now=NOW, waited_min=10, nudges=1, is_dm=True, is_read=True, needs_reply=True)
    g, why, _ = grading.grade(ev("주무관입니다. 일정 확인 가능할까요?"), ctx, R)
    assert g == Grade.WAITING and why == ["client"]                                # 읽었으면 대기, 까닭은 남긴다

def test_pipeline_ignores_group_question_without_mention():                        # Review Focus 2 — 파이프라인 수준
    e = ev("오늘 몇 시에 모이나요?", group=True, read=False, at=NOW - timedelta(hours=5), **BIZ_ROOM)
    assert pipeline.build_items([e], S, NOW, set()) == []                          # 안 읽었어도 나에게 온 것이 아니면 안 뜬다

def test_weekend_event_day_counts_when_registered_as_workday():                    # I8 — 스펙 §5-3 주말 행사 운영일
    from datetime import date
    sat_now = datetime(2026, 10, 17, 15, 0, tzinfo=KST)
    q = ev("현장 동선 확인 부탁드립니다", group=False, at=datetime(2026, 10, 17, 10, 0, tzinfo=KST))
    assert pipeline.build_items([q], S, sat_now, set()) == []                      # 평소 토요일은 업무시간이 흐르지 않는다
    S2 = copy.copy(S); S2.rules = copy.copy(S.rules); S2.rules.calendar = WorkCalendar(extra_workdays=frozenset({date(2026, 10, 17)}))
    items = pipeline.build_items([q], S2, sat_now, set())
    assert len(items) == 1 and items[0].waited_minutes == 240

@pytest.mark.parametrize("t", ["발권 진행할까요? 좌석이 오늘 풀립니다", "내일 가능하신가요? 안 되시면 모레도 괜찮습니다.", "Is Friday OK? We are flexible."])
def test_question_followed_by_another_sentence_is_still_a_question(t):     # 질문이 메시지 맨 끝이 아니어도
    assert needs_reply.needs_reply(t, R)

@pytest.mark.parametrize("t", ["가격은 10,000원입니다. 감사합니다", "자료 올렸습니다. 참고하세요.", "3시에 도착합니다! 곧 뵙겠습니다"])
def test_statements_in_several_sentences_are_not_questions(t):
    assert needs_reply.needs_reply(t, R) == []


# ===== 사업 꼬리표 물려받기 (사용자 요청 2026-10-13: "어느 사업인지 알기 힘들다") =====
def test_untagged_message_inherits_the_senders_only_business():
    known = mail("숙박 명단 보냅니다", subject="[동네 합창 모임] 숙박 명단", sid="h@hotel.example", sender="호텔", thread="t1", at=NOW - timedelta(days=2))
    q = mail("객실 타입 변경 확인 부탁드립니다", subject="객실 타입 변경", sid="h@hotel.example", sender="호텔", thread="t2", at=NOW - timedelta(hours=3))
    assert business.tag(q, S) == "other"                                     # 이 메일만 보면 어느 사업인지 모른다
    items = pipeline.build_items([known, q], S, NOW, set())
    assert [i.business for i in items] == ["bazaar2"]                       # 같은 사람의 다른 메일이 한 사업에만 걸려 있다

def test_sender_seen_in_two_businesses_is_not_guessed():
    a = mail("자료 보냅니다", subject="[동네 합창 모임] 자료", sid="v@vendor.example", sender="업체", thread="t1", at=NOW - timedelta(days=2))
    b = mail("자료 보냅니다", subject="[책방운영단] 자료", sid="v@vendor.example", sender="업체", thread="t3", at=NOW - timedelta(days=1))
    q = mail("견적 확인 부탁드립니다", subject="견적", sid="v@vendor.example", sender="업체", thread="t2", at=NOW - timedelta(hours=3))
    assert [i.business for i in pipeline.build_items([a, b, q], S, NOW, set())] == ["other"]

def test_untagged_message_inherits_the_rooms_only_business():
    hint = ev("합창 연주회 리허설 일정 공유드립니다", chat="준비방", chat_id="g7", sid="u1", sender="김", group=True, at=NOW - timedelta(days=1))
    q = ev("대장님 동선 확인 부탁드립니다", chat="준비방", chat_id="g7", sid="u2", sender="이", group=True, at=NOW - timedelta(hours=6))
    assert [i.business for i in pipeline.build_items([hint, q], S, NOW, set())] == ["bazaar2"]

def test_inherited_business_shows_on_the_unread_item():
    known = mail("숙박 명단 보냅니다", subject="[동네 합창 모임] 숙박 명단", sid="h@hotel.example", sender="호텔", thread="t1", at=NOW - timedelta(days=2))
    fyi = mail("영수증 첨부합니다", subject="영수증", sid="h@hotel.example", sender="호텔", thread="t2", read=False, at=NOW - timedelta(minutes=45))
    items = pipeline.build_items([known, fyi], S, NOW, set())
    assert [(i.grade, i.business) for i in items] == [(Grade.URGENT, "bazaar2")]

def test_merged_mail_copies_show_the_known_business():
    a = mail("확인 부탁드립니다", subject="객실 변경", account="minji@example.com", thread="t1", at=NOW - timedelta(hours=3))
    b = mail("확인 부탁드립니다", subject="객실 변경", account="office@example.org", thread="t9", at=NOW - timedelta(hours=3))
    items = pipeline.build_items([a, b], S, NOW, set())
    assert len(items) == 1 and items[0].business == "bazaar2"              # 사무국 계정 사본이 사업을 알려 준다


# ===== 어느 메일 계정인지 (사용자 요청 2026-10-13) =====
def test_item_carries_the_mail_accounts_in_config_order():
    b = mail("확인 부탁드립니다", subject="객실 변경", account="main@example.com", thread="t9", at=NOW - timedelta(hours=3))
    a = mail("확인 부탁드립니다", subject="객실 변경", account="minji@example.com", thread="t1", at=NOW - timedelta(hours=3))
    items = pipeline.build_items([b, a], S, NOW, set())
    assert len(items) == 1 and items[0].accounts == ["minji@example.com", "main@example.com"]     # 설정(mail_labels) 순서
    k = ev("확인 부탁드립니다", group=False, at=NOW - timedelta(hours=3))
    assert pipeline.build_items([k], S, NOW, set())[0].accounts == []                              # 메일이 아니면 비어 있다


# ===== 사용자 판단 2026-10-13: "네가 긴급이라는 것은 하나도 급하지 않다" — 오래 답 안 한 것만으로는 긴급이 아니다 =====
def test_long_wait_alone_is_waiting_not_urgent():
    e = ev("자료 확인 부탁드립니다", group=False, at=NOW - timedelta(days=2))
    items = pipeline.build_items([e], S, NOW, set())
    assert len(items) == 1 and items[0].grade == Grade.WAITING and "waited_day" not in items[0].reasons

def test_read_message_with_explicit_urgency_waits_at_once():      # 읽었으면 긴급 낱말이 있어도 '대기' — 다만 2시간을 기다리지 않고 바로
    e = ev("긴급합니다. 오늘 중으로 회신 부탁드립니다", group=False, at=NOW - timedelta(minutes=20))
    items = pipeline.build_items([e], S, NOW, set())
    assert [i.grade for i in items] == [Grade.WAITING] and "urgent_word" in items[0].reasons

@pytest.mark.parametrize("t", [
    "단체버스 견적 문의드립니다. 계약금 입금 계좌 알려 주세요",          # 주제어일 뿐 재촉이 아니다(실제 오탐의 꼴)
    "결제 영수증과 발주서 보내 주세요",
    "행사가 취소된 건 아니죠? 일정 확인 부탁드립니다",
    "견적 제출 마감일이 언제인지 알려 주세요",
])
def test_topic_words_alone_are_not_urgent(t):
    assert not R.urgent_ko.search(t)
    items = pipeline.build_items([ev(t, group=False, at=NOW - timedelta(hours=3))], S, NOW, set())
    assert [i.grade for i in items] == [Grade.WAITING]

@pytest.mark.parametrize("t", ["긴급 확인 부탁드립니다", "급히 회신 바랍니다", "오늘 중으로 답변 주세요", "금일 내 확인 바랍니다", "지금 바로 전화 주세요",
                               "빠른 회신 부탁드립니다", "퇴근 전까지 알려 주세요", "재차 요청드립니다", "문제 발생했습니다. 확인 부탁드립니다", "통화 가능하실까요?"])
def test_explicit_urgency_words_still_match(t):
    assert R.urgent_ko.search(t)



# ===== 사용자 정의 2026-10-13: "긴급은 내가 확인하지 않은 메일, 메세지. 스팸, 광고성은 제외" · "나에게 온것을 보여줘야지" =====
def test_unread_direct_message_is_urgent_at_once():
    e = ev("자료 올렸습니다", group=False, read=False, at=NOW - timedelta(minutes=1))        # 질문이 아니어도, 방금 왔어도
    items = pipeline.build_items([e], S, NOW, set())
    assert [i.grade for i in items] == [Grade.URGENT] and "unread" in items[0].reasons
    assert pipeline.build_items([ev("자료 올렸습니다", group=False, read=True, at=NOW - timedelta(minutes=1))], S, NOW, set()) == []

def test_unread_mail_to_me_is_urgent_but_cc_is_not():
    to_me = mail("회의록 공유드립니다", read=False, at=NOW - timedelta(hours=1))
    cc = mail("회의록 공유드립니다", subject="참조 메일", thread="t2", sid="lee@client.com", read=False, group=True, at=NOW - timedelta(hours=1))
    items = pipeline.build_items([to_me, cc], S, NOW, set())
    assert [i.key for i in items] == [to_me.key] and items[0].grade == Grade.URGENT

def test_unread_group_message_needs_a_mention():
    plain = ev("내일 일정 공유드립니다", chat_id="g1", sid="u1", group=True, read=False, at=NOW - timedelta(hours=1))
    called = ev("대장님 내일 일정 공유드립니다", chat_id="g1", sid="u2", sender="이", group=True, read=False, at=NOW - timedelta(minutes=50))
    assert [i.key for i in pipeline.build_items([plain, called], S, NOW, set())] == [called.key]

def test_unread_message_i_already_answered_is_not_shown():           # 폰에서 답했는데 맥의 읽음이 늦게 반영되는 경우
    q = ev("내일 가능하실까요?", group=False, read=False, at=NOW - timedelta(hours=1))
    mine = ev("네 가능합니다", group=False, me=True, sid="me", sender="나", at=NOW - timedelta(minutes=30))
    assert pipeline.build_items([q, mine], S, NOW, set()) == []

@pytest.mark.parametrize("kw", [
    dict(labels=["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"]),                       # Gmail 이 광고로 분류
    dict(labels=["INBOX", "UNREAD", "CATEGORY_SOCIAL"]),
    dict(labels=["INBOX", "UNREAD", "CATEGORY_UPDATES"]),                          # 자동 알림(영수증·공지)
    dict(headers={"list-unsubscribe": "<mailto:x@y>"}),                            # 대량 발송
    dict(headers={"precedence": "bulk"}),
    dict(subject="(광고) 가을 특가 안내"),
    dict(sid="newsletter@shop.example"),
])
def test_unread_spam_ads_and_notices_are_not_urgent(kw):
    e = mail("이번 주 소식을 전해드립니다", read=False, at=NOW - timedelta(hours=1), **kw)
    assert pipeline.build_items([e], S, NOW, set()) == []

def test_unread_ad_text_message_is_not_urgent():
    sms = ev("(광고) 가을 세일 최대 50% 무료거부 0801234567", src=Source.IMESSAGE, chat="+821000000000", sid="+821000000000", group=False, read=False)
    web = ev("[Web발신] 인증번호 123456 을 입력해 주세요", src=Source.IMESSAGE, chat="+821000000001", sid="+821000000001", chat_id="c9", group=False, read=False)
    assert pipeline.build_items([sms, web], S, NOW, set()) == []

def test_notice_category_from_someone_i_wrote_to_is_kept():           # Gmail 이 '업데이트'로 잘못 분류한 거래처 메일
    earlier = mail("자료 보냅니다", sid="minji@example.com", sender="나", me=True, thread="t0", at=NOW - timedelta(days=2), to=["kim@client.com"])
    e = mail("수정본 보내드립니다", read=False, labels=["INBOX", "UNREAD", "CATEGORY_UPDATES"], at=NOW - timedelta(hours=1))
    assert [i.grade for i in pipeline.build_items([earlier, e], S, NOW, set())] == [Grade.URGENT]

def test_urgent_comes_first_newest_first_then_waiting():
    old = ev("첫 번째", chat_id="a", sid="u1", sender="갑", group=False, read=False, at=NOW - timedelta(hours=5))
    new = ev("두 번째", chat_id="b", sid="u2", sender="을", group=False, read=False, at=NOW - timedelta(minutes=5))
    wait = ev("확인 부탁드립니다", chat_id="c", sid="u3", sender="병", group=False, at=NOW - timedelta(hours=6))
    assert [i.key for i in pipeline.build_items([old, wait, new], S, NOW, set())] == [new.key, old.key, wait.key]

def test_item_text_keeps_the_whole_message_for_summarizing():
    a = ev("안녕하세요 대장님, 다음 주 수요일 답사 일정 관련해서 연락드립니다.", group=False, read=False, at=NOW - timedelta(minutes=10))
    b = ev("차량은 9시에 출발하면 될지 확인 부탁드립니다. " + "가" * 200, group=False, read=False, at=NOW - timedelta(minutes=9))
    it = pipeline.build_items([a, b], S, NOW, set())[0]
    assert "답사 일정" in it.text and "9시에 출발" in it.text and len(it.summary) <= 80 and len(it.text) <= 2000

def test_skipped_unread_lists_what_was_left_out_and_why():           # 뺀 것도 어딘가에서는 보여야 한다(전체 목록 화면)
    ad = mail("이번 주 소식", subject="뉴스레터", sid="news@shop.example", thread="t1", read=False)
    notice = mail("결제가 완료되었습니다", subject="결제 안내", sid="pay@bank.example", thread="t2", read=False, labels=["CATEGORY_UPDATES"])
    cc = mail("참고하세요", subject="참조", sid="lee@client.com", thread="t3", read=False, group=True)
    chat = ev("내일 일정 공유드립니다", chat="준비방", chat_id="g1", sid="u1", group=True, read=False)
    shown = mail("확인 부탁드립니다", subject="직접 온 것", thread="t4", read=False)
    read_one = mail("읽은 광고", sid="news@shop.example", thread="t5")
    got = {x.key: x.reason for x in pipeline.skipped_unread([ad, notice, cc, chat, shown, read_one], S, NOW, set())}
    assert got == {ad.key: "auto", notice.key: "notice", cc.key: "cc", chat.key: "group"}



# ===== 독립 검토(2026-10-13 오후)에서 나온 것: 안 읽은 진짜가 조용히 사라지는 경로 =====
def test_unread_mention_is_not_hidden_by_someone_elses_reply():
    m = ev("대장님 내일 일정 확인 부탁드립니다", chat_id="g1", sid="u1", sender="김", group=True, read=False, at=NOW - timedelta(minutes=20))
    other = ev("제가 확인해서 알려드릴게요", chat_id="g1", sid="u2", sender="이", group=True, read=False, at=NOW - timedelta(minutes=18))
    assert [i.key for i in pipeline.build_items([m, other], S, NOW, set())] == [m.key]      # 남이 받아 줬어도 나는 아직 안 봤다

def test_unread_hidden_by_my_later_message_is_listed_as_answered():
    q = ev("내일 가능하실까요?", group=False, read=False, at=NOW - timedelta(hours=1))
    mine = ev("네 가능합니다", group=False, me=True, sid="me", sender="나", at=NOW - timedelta(minutes=30))
    assert pipeline.build_items([q, mine], S, NOW, set()) == []
    assert {x.key: x.reason for x in pipeline.skipped_unread([q, mine], S, NOW, set())} == {q.key: "answered"}   # 사라지지 않고 까닭과 함께 남는다

def test_unread_is_not_hidden_by_my_message_on_another_channel():
    S2 = copy.copy(S); S2.contacts = {"kim@client.com": "김과장", "kakao:김과장": "김과장"}
    q = ev("도면 수정본 확인 부탁드립니다", chat="김과장", chat_id="k1", sid="u500", sender="김과장", group=False, read=False, at=NOW - timedelta(hours=1))
    other_topic = mail("회의록 보냅니다", subject="회의록", sid="minji@example.com", sender="나", me=True, thread="t7", at=NOW - timedelta(minutes=30), to=["kim@client.com"])
    assert [i.key for i in pipeline.build_items([q, other_topic], S2, NOW, set())] == [q.key]

def test_unread_mail_answered_from_another_account_is_listed_as_answered():
    q = mail("견적 가능할까요?", subject="견적 문의", account="minji@example.com", thread="t1", read=False, at=NOW - timedelta(hours=3))
    mine = mail("보냈습니다", subject="Re: 견적 문의", sid="main@example.com", sender="나", account="main@example.com", thread="t9", me=True,
                at=NOW - timedelta(hours=1), to=["kim@client.com"])
    assert pipeline.build_items([q, mine], S, NOW, set()) == []
    assert [x.reason for x in pipeline.skipped_unread([q, mine], S, NOW, set())] == ["answered"]

@pytest.mark.parametrize("t,src", [("[첨부]", Source.IMESSAGE), ("[미디어]", Source.TELEGRAM)])
def test_unread_attachment_only_message_to_me_is_urgent(t, src):                  # 거래처가 보낸 도면·견적서는 대개 파일 한 장이다
    items = pipeline.build_items([ev(t, src=src, group=False, read=False, at=NOW - timedelta(minutes=10))], S, NOW, set())
    assert [i.grade for i in items] == [Grade.URGENT]

def test_unread_sticker_or_filler_is_left_out_but_listed():
    a = ev("[이모티콘]", chat_id="a", sid="u1", group=False, read=False)
    b = ev("넵 감사합니다~", chat_id="b", sid="u2", group=False, read=False)
    assert pipeline.build_items([a, b], S, NOW, set()) == []
    assert sorted(x.reason for x in pipeline.skipped_unread([a, b], S, NOW, set())) == ["noise", "noise"]

@pytest.mark.parametrize("t,noise", [("ㅋㅋㅋ", True), ("넵!", True), ("감사합니다~", True), ("👍", True), ("네 감사합니다", True), ("ok", True),
                                     ("사고", False), ("다시 합시다", False), ("수고", False), ("?", False), ("네 내일 3시에 뵙겠습니다", False)])
def test_noise_is_whole_words_not_a_bag_of_letters(t, noise):
    assert preprocess.is_noise(ev(t), R) is noise

def test_urgent_item_waits_since_its_oldest_unread_message():
    old_read = ev("자료 확인 부탁드립니다", group=False, at=NOW - timedelta(hours=4))                 # 읽고 답 안 함(대기)
    new_unread = ev("추가 자료 보냅니다", group=False, read=False, at=NOW - timedelta(minutes=5))      # 방금 온 안 읽은 것
    it = pipeline.build_items([old_read, new_unread], S, NOW, set())[0]
    assert it.grade == Grade.URGENT and it.waited_minutes == 5                                        # '기다린 4시간'이 아니다


# ===== 품질 반복 2회차 — 검수: 묶인 옛 메일의 id 때문에 새 안 읽은 메일이 빠질 수 있다 =====
def test_item_refs_cover_the_newest_mail_and_its_copies_but_not_older_mail_in_the_group():
    def mail(mid, tid, minutes_ago, account):
        return Event(source=Source.GMAIL, chat_id=tid, message_id=mid, chat_name="견적", sender_id="kim@client.example", sender_name="김과장",
                     text="견적 — 견적서 확인 부탁드립니다", received_at=NOW - timedelta(minutes=minutes_ago), is_from_me=False, is_read=False, is_group=False,
                     thread_id=tid, account=account, raw={"subject": "견적", "headers": {}, "to": [account], "labels": []})
    events = [mail("old1", "t-old", 60 * 26, "minji@example.com"),               # 어제 온 같은 제목의 메일(같은 묶음)
              mail("new1", "t-a", 5, "minji@example.com"), mail("new2", "t-b", 5, "main@example.com")]     # 방금 온 메일과 다른 계정의 사본
    items = pipeline.build_items(events, S, NOW, set())
    assert len(items) == 1
    assert set(items[0].refs) == {"new1", "t-a", "new2", "t-b"}                  # 옛 메일을 그 뒤에 다뤘다고 새 메일이 빠지면 안 된다


# ===== 지켜보는 방 — 사용자 2026-10-14: "텔레그램 합창단 단톡방 — 안 읽은 것은 표시해줘. 하나로" =====
def _watching(*rooms):
    s = copy.copy(S); s.watch_rooms = frozenset(rooms); return s

def _room(text, sid, sender, minutes_ago, *, read=False, chat_id="g1", src=Source.TELEGRAM, **kw):
    return ev(text, src=src, chat="운영회의방", chat_id=chat_id, sid=sid, sender=sender, group=True, read=read, at=NOW - timedelta(minutes=minutes_ago), **kw)

def test_unread_posts_in_a_watched_room_become_one_item():
    a = _room("발표회 영상 초안 올립니다", "u1", "가", 180)
    b = _room("포스터 이미지를 쓸지 고민입니다", "u2", "나", 120)
    c = _room("일요일 일정이 자유 일정으로 바뀌는 게 맞는지요", "u1", "가", 20)
    items, skipped = pipeline.scan([a, b, c], _watching(("telegram", "g1")), NOW, set())
    assert len(items) == 1 and skipped == []                                     # 나를 부르지 않았어도 올린다 — 방마다 하나로
    it = items[0]
    assert it.grade == Grade.URGENT and it.count == 3 and it.chat_name == "운영회의방" and it.source == Source.TELEGRAM
    assert it.key == c.key and it.received_at == c.received_at and it.link == c.link      # 가장 최근 글이 대표다
    assert it.waited_minutes == pipeline.business_minutes(a.received_at, NOW, R.calendar)      # 기다린 시간은 가장 오래된 안 읽은 글부터
    assert it.summary.startswith("가: 일요일")                                    # 요약이 아직 없을 때 보일 것: 가장 최근 글
    assert it.text.index("가: 발표회") < it.text.index("나: 포스터") < it.text.index("가: 일요일")      # 요약에 건넬 글: 누가 무엇을, 시간순
    # 지켜보지 않는 방이면 전과 같다 — 화면에 안 올리고 '단톡방 일반 글'로 남긴다
    items, skipped = pipeline.scan([a, b, c], _watching(), NOW, set())
    assert items == [] and [x.reason for x in skipped] == ["group"] * 3
    # 출처가 다르면 같은 번호의 방이어도 다른 방이다
    assert pipeline.scan([a, b, c], _watching(("kakao", "g1")), NOW, set())[0] == []

def test_watched_room_counts_only_what_is_unread_and_goes_away_when_read():
    W = _watching(("telegram", "g1"))
    old = _room("어제 회의록입니다", "u1", "가", 600, read=True)
    new = _room("오늘 3시에 모입니다", "u2", "나", 30)
    mine = _room("네 알겠습니다", "me", "나", 25, me=True)
    sticker = _room("ㅋㅋㅋ", "u3", "다", 10)
    it, = pipeline.scan([old, new, mine, sticker], W, NOW, set())[0]
    assert it.count == 1 and "회의록" not in it.text and "ㅋㅋ" not in it.text       # 읽은 것 · 내 글 · 추임새는 세지 않는다
    assert pipeline.scan([old, _room("오늘 3시에 모입니다", "u2", "나", 30, read=True)], W, NOW, set())[0] == []      # 방을 읽으면 사라진다

def test_a_mention_in_a_watched_room_still_stands_alone():
    W = _watching(("telegram", "g1"))
    plain = _room("영상 초안 올립니다", "u1", "가", 60)
    called = _room("대장님 내일까지 확인 부탁드립니다", "u2", "나", 40)
    items = pipeline.scan([plain, called], W, NOW, set())[0]
    assert sorted(i.count for i in items) == [0, 1]                              # 나를 부른 글은 전처럼 따로, 나머지만 묶는다
    assert next(i for i in items if i.count == 0).key == called.key

def test_dismissing_a_watched_room_hides_it_until_someone_posts_again():
    W = _watching(("telegram", "g1"))
    a = _room("영상 초안 올립니다", "u1", "가", 60); b = _room("확인했습니다 수정본 올립니다", "u2", "나", 40)
    assert pipeline.scan([a, b], W, NOW, {b.key})[0] == []                        # 묶음을 처리됨으로 지우면 그 방의 그때까지 글이 다 들어간다(다른 사람 글도)
    c = _room("최종본입니다", "u3", "다", 5)
    it, = pipeline.scan([a, b, c], W, NOW, {b.key})[0]
    assert it.count == 1 and it.key == c.key                                     # 새 글이 오면 그것부터 다시 뜬다

def test_watch_rooms_setting_is_read_as_source_and_chat_id():
    from urgent_board.config import parse_rooms
    assert parse_rooms([{"source": "Telegram", "chat_id": -100000000001}, {"source": "kakao", "chat_id": "481"}]) == frozenset({("telegram", "-100000000001"), ("kakao", "481")})
    assert parse_rooms(None) == frozenset() and parse_rooms([{"source": "telegram"}, "엉뚱한 줄", {"chat_id": 1}]) == frozenset()      # 꼴이 틀린 줄은 버린다
    assert all(isinstance(r, tuple) and len(r) == 2 for r in S.watch_rooms)      # 실제 설정도 읽힌다


def test_unconfirmed_work_goes_to_personal_etc_not_to_a_box_that_shares_its_words():
    # 실제 화면(2026-10-14): 같은 협의회의 입찰 건(정책설명회 대행 제안평가 안내)이 '마을 정원 가꾸기' 상자에 떴다 — 제목의 '생활녹지'이
    # 정원조성의 낱말이었다. 사용자: "아직 확정된 사업이 아님. 개인·기타로 가야함". 확정 전 일(unconfirmed)의 낱말이 더 길게 걸리면 개인·기타.
    q = mail("제안서 평가는 다음 주 발표입니다", subject="2026년 생활녹지 마을실천 사례 나눔 포럼 운영 대행 제안서 평가 안내")
    assert business.tag(q, S) == "other"
    hotel = mail("연회장 견적 송부드립니다", subject="호텔_가나구청 행사팀 [2026년 생활녹지마을실천 실천사례나눔 포럼 행사]")
    assert business.tag(hotel, S) == "other"
    assert business.tag(mail("점심 뭐 먹을까요 — 정책설명회 건", account="snack@example.com"), S) == "personal"   # 개인 계정이면 개인(같은 상자)
    # 같은 협의회라도 확정된 정원조성 일은 그대로 정원조성이다
    assert business.tag(mail("포럼 발표자료 취합 부탁드립니다", subject="구청 공원도시분야 컨퍼런스(시립정원관)"), S) == "garden"
    # 확정 전 일은 상자를 차지하지 않는다 — 사업 상자 여섯은 그대로
    assert all(b.key != "other" for b in S.businesses) and len([b for b in S.businesses if b.active]) == 5


# 2026-10-16 사용자 "정수빈 해오름의 텔레그램 내용은 개인/기타 사항임": 회사 단톡 '해오름'에 올린 그 사람의 글 가운데 '합창'가 든 것이
# 동네 합창 모임(낱말 '합창')로 갔다. 사람을 개인 · 기타로 두면(설정 me.personal_senders — 보낸 사람 번호) 사업 낱말이 들어 있어도 개인 · 기타.
def _with_personal(*ids):
    from urgent_board import config as cfg, settings_doc
    doc = settings_doc.read(HOME)[0]
    doc["me"]["personal_senders"] = list(ids)
    return cfg.from_doc(doc, HOME)


def test_a_person_kept_personal_goes_to_personal_etc_even_with_a_business_word():
    her = ev("합창메시지 아카이빙 링크 공유드립니다", src=Source.TELEGRAM, chat="해오름", chat_id="-900", sid="900001", sender="가나다 해오름", group=True)
    assert business.tag(her, S) == "bazaar2"                                   # 지금은 낱말 '합창'로 동네 합창 모임
    s = _with_personal("900001")
    assert business.explain(her, s) == ("personal", False, "개인 · 기타로 둔 사람")
    other = ev("내일 회의 몇 시죠", src=Source.TELEGRAM, chat="해오름", chat_id="-900", sid="900002", sender="라마바 해오름", group=True)
    tags = pipeline._business_tags([her, other], s)
    assert tags[her.key][0] == "personal" and tags[other.key][0] == "other"      # 같은 방의 다른 글도 합창단으로 끌려가지 않는다
    assert pipeline._business_tags([her, other], S)[other.key][0] == "bazaar2"  # (설정 없이는 끌려갔다)
    room = next(b for b in s.businesses if b.key == "bazaar2" and b.telegram_chat_ids)
    in_room = ev("합창 대회 자료입니다", src=Source.TELEGRAM, chat="방", chat_id=str(room.telegram_chat_ids[0]), sid="900001", group=True)
    assert business.tag(in_room, s) == "bazaar2"                               # 사업에 등록한 방에 쓴 글은 그 사업(등록이 먼저)


# ===== 협회·단체의 행사 공지 = 광고·홍보 — 사용자 2026-10-14: "협회 토론회 안내 --> 광고,홍보성으로 보임" =====
# 실제 글: 협회 담당자가 단톡방에 돌린 행사 안내(이번 주 5건). 장소의 '시립정원관'이 정원조성의 낱말이라 정원조성로 분류돼 있었다.
PROMO = "[2026 동네 책읽기 모임 개최 및 참석 안내]\n안녕하세요, 모임 가나다 총무입니다.\n아래 내용 확인하시어 회원사 여러분의 많은 관심과 참여 부탁드립니다.\n장소: 시립정원관 소강당"

def test_association_event_notice_is_promo():
    assert preprocess.junk_reason(ev(PROMO, group=True, read=False), R, set()) == "ad"
    assert preprocess.junk_reason(ev("가나시 행사 개최 안내 — 아래 내용 참고하시어 많은 관심과 참여 바랍니다.", read=False), R, set()) == "ad"
    assert preprocess.junk_reason(ev("[모집] 부산 답사 — 많은 관심과 신청 부탁드립니다", read=False), R, set()) == "ad"
    # 업무 글의 비슷한 말은 광고가 아니다
    for t in ("관심 가져 주셔서 감사합니다. 참여 여부 회신 부탁드립니다", "회원사 명단 보내 드립니다", "많은 분들이 참여하실 예정입니다"):
        assert preprocess.junk_reason(ev(t, read=False), R, set()) == "", t
    # 1:1 로 와도 안 읽은 채 긴급이 되지 않고, 까닭(광고)과 함께 '긴급에서 뺀 것'에 남는다
    items, skipped = pipeline.scan([ev(PROMO, read=False, at=NOW - timedelta(minutes=30))], S, NOW, set())
    assert items == [] and [x.reason for x in skipped] == ["ad"]

def test_promo_does_not_lend_its_business_to_the_room():
    # 광고 글이 낱말로 걸린 사업(정원조성)을 같은 방의 다음 글에 물려주지 않는다
    promo = ev(PROMO, chat="협회 공지방", chat_id="k9", sid="u7", sender="가나다", group=True, at=NOW - timedelta(days=1))
    later = ev("대장님 회비 납부 확인 부탁드립니다", chat="협회 공지방", chat_id="k9", sid="u7", sender="가나다", group=True, read=False,
               at=NOW - timedelta(minutes=30))
    assert business.tag(promo, S) == "garden"                         # 글만 보면 정원조성에 걸린다
    it, = pipeline.build_items([promo, later], S, NOW, set())
    assert it.key == later.key and it.business == "other"

def test_watched_room_keeps_posts_that_look_like_promo():
    # 지켜보는 방은 사용자가 통째로 보겠다고 한 방이다("혹시나 내가 읽지 않았으면 정확히 표기할 것") — 행사 공지꼴의 글도 센다
    a = _room("[동네 음악회] 토요일 저녁 공원 야외무대 공연 안내. 많은 관심과 참여 부탁드립니다", "u1", "가", 30)
    b = _room("현수막 시안 올립니다", "u2", "나", 20)
    items, skipped = pipeline.scan([a, b], _watching(("telegram", "g1")), NOW, set())
    assert len(items) == 1 and items[0].count == 2 and skipped == []
    # 나를 부른 글도 마찬가지 — 광고 표식으로 빠지지 않는다
    called = _room("대장님 행사 홍보물 확인 부탁드립니다. 많은 관심과 참여 부탁드립니다", "u2", "나", 10)
    items, _ = pipeline.scan([called], _watching(("telegram", "g1")), NOW, set())
    assert len(items) == 1 and items[0].key == called.key

def test_mail_gmail_filed_as_update_still_lends_its_business():
    # Gmail 이 거래처 메일을 '업데이트'로 분류해도(자동 알림으로 빠짐) 사업은 물려준다 — 물려주지 않는 것은 광고·홍보만
    known = mail("북토크 계획안 송부드립니다", subject="확장개점 기념 북토크 계획(안)", sid="new@team.example", sender="가나다",
                 thread="t1", labels=["CATEGORY_UPDATES"], at=NOW - timedelta(days=1))
    reply = mail("네 확인했습니다. 내일 다시 보내 주실 수 있을까요", subject="RE: 시안", sid="new@team.example", sender="가나다",
                 thread="t2", read=False, at=NOW - timedelta(minutes=30))
    assert {i.business for i in pipeline.build_items([known, reply], S, NOW, set())} == {"bookshop-team"}


# 2026-10-15 사용자: 물음표 없는 간접 질문("…언제까지 …궁금합니다!!")이 대기로 안 떴다 — 질문으로 친다(꼴만 남긴 문장)
@pytest.mark.parametrize("t", ["자료는 언제까지 받으실지 궁금합니다!!", "일정이 어떻게 되는지 궁금해요", "한 가지 여쭙니다", "견적 문의드립니다"])
def test_indirect_question_needs_a_reply(t):
    assert needs_reply.needs_reply(t, R)


def test_indirect_question_reason_is_named():
    assert "q_indirect" in needs_reply.needs_reply("자료는 언제까지 받으실지 궁금합니다", R)


@pytest.mark.parametrize("t", ["궁금증이 풀렸습니다", "궁금했는데 해결됐어요 감사합니다"])
def test_resolved_curiosity_is_not_a_question(t):
    assert needs_reply.needs_reply(t, R) == []


# 2026-10-15 사용자: "읽은 문자 요청은 20분으로" — 1:1 문자만 업무시간 20분, 메일 · 카톡 · 텔레그램은 그대로 2시간
def test_read_sms_request_waits_20_business_minutes():
    from urgent_board.triage import grade as grading
    e = ev("자료 보내 주세요", src=Source.IMESSAGE)
    early = grading.GradeCtx(now=NOW, waited_min=19, nudges=1, is_dm=True, is_read=True, needs_reply=True)
    late = grading.GradeCtx(now=NOW, waited_min=20, nudges=1, is_dm=True, is_read=True, needs_reply=True)
    assert grading.grade(e, early, R)[0] == Grade.NONE
    assert grading.grade(e, late, R)[:2] == (Grade.WAITING, ["waited"])
    k = ev("자료 보내 주세요", src=Source.KAKAO)
    assert grading.grade(k, late, R)[0] == Grade.NONE                       # 카톡 1:1 은 여전히 2시간
    m = ev("자료 보내 주세요", src=Source.GMAIL)
    assert grading.grade(m, late, R)[0] == Grade.NONE                       # 메일도 그대로


# 2026-10-16 사용자 "읽어도 10분 후에는 표시가 되게 해줘" — 대기 문턱을 분으로 적고(이 맥은 설정에서 셋 다 10분), 기본값은 그대로
def test_waiting_thresholds_in_minutes_can_be_ten():
    from urgent_board import config as cfg
    from urgent_board.triage import grade as grading
    r = copy.deepcopy(sd_default_rules())
    r["thresholds"].update(unanswered_dm_minutes=10, unanswered_group_minutes=10, unanswered_sms_minutes=10)
    ten = cfg.build_rules(r, R.calendar)
    for src, dm in ((Source.GMAIL, True), (Source.KAKAO, True), (Source.TELEGRAM, False), (Source.IMESSAGE, True)):
        e = ev("자료 보내 주세요", src=src)
        early = grading.GradeCtx(now=NOW, waited_min=9, nudges=1, is_dm=dm, is_read=True, needs_reply=True)
        late = grading.GradeCtx(now=NOW, waited_min=10, nudges=1, is_dm=dm, is_read=True, needs_reply=True)
        assert grading.grade(e, early, ten)[0] == Grade.NONE, src
        assert grading.grade(e, late, ten)[:2] == (Grade.WAITING, ["waited"]), src
    late = grading.GradeCtx(now=NOW, waited_min=10, nudges=1, is_dm=True, is_read=True, needs_reply=False)
    assert grading.grade(ev("잘 받았습니다", src=Source.GMAIL), late, ten)[0] == Grade.NONE      # 질문 · 요청이 아니면 그대로 뜨지 않는다
    assert R.thresholds["unanswered_dm_minutes"] == 120 and R.thresholds["unanswered_group_minutes"] == 240     # 기본값은 그대로


def sd_default_rules():
    from urgent_board import settings_doc
    return settings_doc.default_rules()


# 2026-10-15 사용자 "책방운영단 메일은 다 읽었는데.. 왜?": 업무 주소(minji@) 앞으로 온 메일이 workbase@ 메일함에도 사본으로 들어온다.
# 한 메일함에서 읽었으면 그 메일은 확인한 것 — 다른 메일함의 사본이 안 읽음이어도 긴급이 아니다(보낸 사람 · 제목 · Date 머리글이 같은 것)
_H = {"date": "Thu, 15 Oct 2026 13:28:41 +0900 (KST)", "from": "고과장 <ko@client.com>", "subject": "숙박 명단 공유"}


def _copies(read_a, read_b, date_b=None):
    at = NOW - timedelta(hours=1)
    a = mail("숙박 명단을 공유드립니다", sid="ko@client.com", sender="고과장", account="minji@example.com", thread="ta", at=at, read=read_a,
             subject="숙박 명단 공유", headers=dict(_H), to=["minji@example.com"])
    b = mail("숙박 명단을 공유드립니다", sid="ko@client.com", sender="고과장", account="main@example.com", thread="tb", at=at, read=read_b,
             subject="숙박 명단 공유", headers=dict(_H, date=date_b or _H["date"]), to=["minji@example.com"])
    return [a, b]


def test_a_mail_read_in_one_of_my_mailboxes_is_read_everywhere():
    items = pipeline.build_items(_copies(True, False), S, NOW, set())
    assert not [i for i in items if i.grade == Grade.URGENT]


def test_copies_both_unread_stay_urgent():
    items = pipeline.build_items(_copies(False, False), S, NOW, set())
    assert [i.grade for i in items] == [Grade.URGENT]


def test_same_subject_but_a_different_mail_is_not_a_copy():
    items = pipeline.build_items(_copies(True, False, date_b="Thu, 15 Oct 2026 14:02:10 +0900 (KST)"), S, NOW, set())
    assert [i.grade for i in items] == [Grade.URGENT]                         # 다른 때 보낸 다른 메일 — 읽지 않았다


def test_a_copy_counted_as_read_leaves_a_reason_in_the_list():
    _, skipped = pipeline.scan(_copies(True, False), S, NOW, set())
    assert [s.reason for s in skipped] == ["read_elsewhere"] and skipped[0].account == "main@example.com"


# --- 할 일 장부와의 약속(검토 C1 · I4 · I1, 2026-10-16): 판정은 '읽었고 질문 · 요청인데 문턱 전이라 등급이 없는 대화'를 함께 알린다
def test_read_request_inside_the_threshold_is_pending_not_shown():
    e = ev("견적서 보내 주실 수 있을까요?", at=NOW - timedelta(minutes=3), read=True)
    j = pipeline.judge([e], S, NOW, set())
    assert j.items == [] and j.pending == {pipeline.conv_id(e)}
    assert pipeline.scan([e], S, NOW, set()) == ([], [])                   # 옛 꼴(둘)은 그대로


def test_read_plain_message_is_not_pending():
    e = ev("회의 자료 공유드립니다 좋은 하루 되세요", at=NOW - timedelta(minutes=3), read=True)
    assert pipeline.judge([e], S, NOW, set()).pending == set()


def test_read_request_past_the_threshold_is_shown_not_pending():
    e = ev("견적서 보내 주실 수 있을까요?", at=NOW - timedelta(hours=3), read=True)
    j = pipeline.judge([e], S, NOW, set())
    assert [i.grade for i in j.items] == [Grade.WAITING] and j.pending == set()


def test_sms_group_request_stays_pending_for_the_group_threshold():   # 검토 I4: 장부가 문자를 늘 20분으로 세어 단톡 요청을 닫았다 다시 열었다
    e = ev("견적서 언제 보내 주실 수 있나요?", src=Source.IMESSAGE, group=True, mentioned_me=True, at=NOW - timedelta(minutes=30), read=True)
    j = pipeline.judge([e], S, NOW, set())
    assert j.items == [] and j.pending == {pipeline.conv_id(e)}


def test_rule_deadline_counts_from_when_the_message_came():           # 검토 I1: "내일까지"가 날마다 하루씩 밀렸다
    mon = datetime(2026, 10, 12, 10, 0, tzinfo=KST)
    it, = pipeline.build_items([ev("내일까지 회신 부탁드립니다", at=mon, read=False)], S, NOW, set())
    assert it.deadline.astimezone(KST).strftime("%m-%d %H:%M") == "10-13 18:00"
    fri = datetime(2026, 10, 9, 10, 0, tzinfo=KST)
    it, = pipeline.build_items([ev("내일까지 회신 부탁드립니다", at=fri, read=False)], S, NOW, set())
    assert it.deadline.astimezone(KST).strftime("%m-%d") == "10-10" and "deadline_soon" not in it.reasons     # 지난 기한도 알려 준다(할 일의 '지남')


# --- 분류 키우기(2026-10-16 — docs/exec-plans/2026-10-16-classify-teach-and-pick.md): 규칙이 아무 데도 못 건 항목만 Claude 가 사업을 고른다
def test_only_items_no_rule_caught_are_free_for_claude():
    free = ev("다음 주 일정 확인 부탁드립니다", read=False, chat_id="f1", sid="f1")
    word = ev("서예 동호회 일정 확인 부탁드립니다", read=False, chat_id="f2", sid="f2")
    unconf = mail("제안서 평가는 블라인드 발표입니다 확인 부탁드립니다", sid="a@b.c", account="main@example.com", read=False, thread="t9",
                  subject="2026년 생활녹지 마을실천 사례 나눔 포럼 운영 대행 제안서 평가 안내")
    items = {i.key: i for i in pipeline.build_items([free, word, unconf], S, NOW, set())}
    assert items[free.key].business == "other" and items[free.key].business_free
    assert not items[word.key].business_free
    assert items[unconf.key].business == "other" and not items[unconf.key].business_free       # 확정 전 일은 Claude 에게 맡기지 않는다


def test_inherited_or_personal_items_are_not_free():
    a = ev("서예 동호회 자료입니다", chat_id="g1", sid="u1", read=True, at=NOW - timedelta(hours=5))
    b = ev("이것도 확인 부탁드립니다", chat_id="g1", sid="u1", read=False)
    it, = pipeline.build_items([a, b], S, NOW, set())
    assert it.business == "calligraphy-club" and not it.business_free                              # 같은 방 · 같은 사람에게서 물려받음
    her = ev("다음 주 일정 확인 부탁드립니다", read=False, chat_id="p1", sid="p1")
    it, = pipeline.build_items([her], _with_personal("p1"), NOW, set())
    assert it.business == "personal" and not it.business_free


# --- 답 기다림(2026-10-16 — 사용자가 질문 창에서 "답 기다림도 보이기"): 내가 보낸 질문 · 요청에 상대 답이 아직 없는 것
def _mine(text, subject, to=("kim@client.com",), at=None, thread="w1"):
    return mail(text, sid="minji@example.com", sender="나", me=True, to=list(to), subject=subject, thread=thread, at=at or NOW - timedelta(hours=2))


def test_my_request_without_their_reply_is_awaiting_until_they_answer():
    m = _mine("견적서 회신 부탁드립니다", "서예 동호회 버스 견적 요청")
    j = pipeline.judge([m], S, NOW, set())
    a, = j.awaiting
    assert a.kind == "awaiting" and a.key == m.key and a.business == "calligraphy-club" and j.items == []
    reply = mail("견적서 보내드립니다", sid="kim@client.com", subject="Re: 서예 동호회 버스 견적 요청", thread="w1", at=NOW - timedelta(hours=1))
    assert pipeline.judge([m, reply], S, NOW, set()).awaiting == []                  # 답이 오면 사라진다
    other_box = mail("견적서 보내드립니다", sid="kim@client.com", account="main@example.com", subject="RE: 서예 동호회 버스 견적 요청",
                     thread="w1-copy", at=NOW - timedelta(hours=1))
    assert pipeline.judge([m, other_box], S, NOW, set()).awaiting == []             # 다른 메일함으로 온 답도(같은 사람 · 같은 제목)


def test_awaiting_leaves_out_announcements_group_talk_plain_notes_old_and_dismissed():
    many = _mine("자료 회신 부탁드립니다", "공지", to=("a@x.com", "b@x.com", "c@x.com", "d@x.com"), thread="w2")
    plain = _mine("자료 보내드립니다", "자료", thread="w3")
    old = _mine("회신 부탁드립니다", "옛 요청", thread="w4", at=NOW - timedelta(days=5))
    group = ev("다들 의견 주실 수 있나요?", me=True, group=True, chat_id="g9", sid="me")
    gone = _mine("확인 부탁드립니다", "지운 것", thread="w5")
    assert pipeline.judge([many, plain, old, group, gone], S, NOW, {gone.key}).awaiting == []


def test_awaiting_takes_the_business_and_name_of_the_person_written_to():
    their = mail("서예 동호회 일정 공유드립니다", sid="lee@x.com", sender="봉미르", subject="동호회 일정", thread="w6", at=NOW - timedelta(days=1))
    mine = _mine("다음 주 회의 가능하신지 회신 부탁드립니다", "회의", to=("lee@x.com",), thread="w7")
    a, = pipeline.judge([their, mine], S, NOW, set()).awaiting
    assert a.business == "calligraphy-club" and a.sender_name == "봉미르"              # 그 사람의 다른 메일에서 물려받는다


def test_awaiting_in_a_one_to_one_chat():
    m = ev("내일 오전에 통화 가능하실까요?", me=True, chat_id="d1", chat="봉미르 교수", sid="me", at=NOW - timedelta(hours=1))
    a, = pipeline.judge([m], S, NOW, set()).awaiting
    assert a.chat_name == "봉미르 교수" and a.conv.startswith("await|")
    r = ev("네 가능합니다", chat_id="d1", chat="봉미르 교수", sid="u5", at=NOW - timedelta(minutes=30))
    assert pipeline.judge([m, r], S, NOW, set()).awaiting == []


# --- 사업에 걸린 자동 알림(2026-10-16 — 사용자가 질문 창에서 "사업에 걸린 것만 안 읽었을 때 띄움"): PayPal 결제 · Booking 예약 확인
def test_unread_business_notice_shows_in_its_box_until_read():
    from dataclasses import replace as _replace
    pay = mail("서예 동호회 참가비 결제대금을 받았습니다", sid="billing@example.com", subject="결제대금 수령 출처: x@y.com", read=False,
               labels=["CATEGORY_UPDATES", "INBOX", "UNREAD"], thread="n1", at=NOW - timedelta(hours=1))
    it, = pipeline.build_items([pay], S, NOW, set())
    assert it.business == "calligraphy-club" and it.grade == Grade.URGENT and "business_notice" in it.reasons
    assert pipeline.build_items([_replace(pay, is_read=True)], S, NOW, set()) == []


def test_notices_without_a_sure_business_and_ads_stay_out():
    bank = mail("10월 결제가 완료되었습니다", sid="noti@bank.example", subject="결제 안내", read=False, labels=["CATEGORY_UPDATES"], thread="n2")
    ad = mail("서예 동호회 굿즈 특가", sid="shop@x.com", subject="(광고) 서예 동호회 특가", read=False, labels=["CATEGORY_PROMOTIONS"], thread="n3")
    acct = mail("새 기기에서 로그인했습니다", sid="no-reply@accounts.google.com", subject="보안 알림", read=False, labels=["CATEGORY_UPDATES"],
                account="office@example.org", thread="n4")                       # 계정으로만 걸린 것(그 계정의 모든 알림)은 띄우지 않는다
    items, skipped = pipeline.scan([bank, ad, acct], S, NOW, set())
    assert items == [] and {x.reason for x in skipped} == {"notice", "ad"}


def test_the_same_request_sent_one_by_one_to_many_is_a_broadcast_not_awaiting():
    # 실제 자료(2026-10-16): 알바 모집 문자를 여섯 명에게 따로 보낸 것이 답 기다림 6건이 됐다 — 같은 글을 넷 이상에게 보냈으면 공지로 본다
    text = "다음 달 가나시 행사 스태프를 모집합니다 가능하신 날짜 회신 부탁드립니다"
    sent = [ev(text, src=Source.IMESSAGE, me=True, chat_id=f"+8210000000{i}", chat=f"알바{i}", sid="me", at=NOW - timedelta(hours=2, minutes=i))
            for i in range(4)]
    assert pipeline.judge(sent, S, NOW, set()).awaiting == []
    assert len(pipeline.judge(sent[:3], S, NOW, set()).awaiting) == 3              # 셋까지는 한 사람 한 사람의 답을 기다린다


# ===== 외국어 문의(사용자 2026-10-17 "합창단 사무국 - 외국어문의 역시 반영되어야 함. 일반 메일과 동일하게 분석, 반영") =====
# 질문 · 요청 규칙은 한국어 · 영어 문장 틀만 안다 — 그 밖의 말로 쓴 글은 '질문 · 요청일 수 있음'(foreign)으로 보고 같은 길로 보낸다.
# 할 일인지는 요약과 같은 Claude 호출이 가린다. 아래 문장은 꼴만 남긴 지어낸 글이다(실제 메일 본문 · 이름이 아니다).
FOREIGN_ASK = [
    "Здравствуйте! Подскажите, пожалуйста, когда будет известна программа поездки",       # 러시아어
    "您好，请问签证需要准备哪些材料，什么时候可以收到邀请函",                              # 중국어
    "سلام، لطفا برنامه سفر و زمان پرواز را برای ما بفرستید",                               # 페르시아어
    "Bonjour, pourriez-vous nous envoyer le programme de la visite avant vendredi",       # 프랑스어
    "こんにちは。ビザの書類について教えていただけますか",                                  # 일본어
]


@pytest.mark.parametrize("t", FOREIGN_ASK)
def test_foreign_text_is_a_possible_request(t):
    assert "foreign" in needs_reply.needs_reply(t, R)


@pytest.mark.parametrize("t", ["Could you confirm by Friday?", "Thank you very much for the information, we will attend.", "Received, thanks a lot",
                               "자료 확인 부탁드립니다", "네 알겠습니다 감사합니다 내일 뵙겠습니다", "OK", "넵", "Noted with thanks",
                               "회의 자료 공유드립니다. Regards, Kim (Senior Manager, Global Events Team)"])
def test_korean_and_english_are_not_called_foreign(t):
    assert "foreign" not in needs_reply.needs_reply(t, R)


def test_foreign_reply_with_a_korean_signature_is_still_foreign():
    t = "Подскажите, пожалуйста, нужно ли брать с собой документы и во сколько начинается программа. 동네 합창단 정기공연 준비팀"
    assert "foreign" in needs_reply.needs_reply(t, R)


def test_read_foreign_inquiry_goes_the_same_way_as_a_korean_request():
    """읽은 외국어 문의도 한국어 요청과 같은 길 — 문턱이 지나면 대기, 문턱 전이면 장부가 ○ 로 남길 대화(pending), 내가 답하면 끝."""
    acct = "office@example.org"
    q = mail(FOREIGN_ASK[0], sid="guest@example.ru", sender="Guest", account=acct, thread="t9", subject="Re: Программа", at=NOW - timedelta(hours=3))
    j = pipeline.judge([q], S, NOW, set())
    assert [i.grade for i in j.items] == [Grade.WAITING] and "foreign" in j.items[0].reasons
    early = mail(FOREIGN_ASK[1], sid="guest2@example.cn", sender="Guest2", account=acct, thread="t8", subject="回复：通知", at=NOW - timedelta(minutes=3))
    j = pipeline.judge([early], S, NOW, set())
    assert not j.items and pipeline.conv_id(early) in j.pending
    mine = mail("Здравствуйте, программа во вложении.", sid=acct, sender="사무국", account=acct, thread="t9", subject="Re: Программа", me=True,
                to=["guest@example.ru"], at=NOW - timedelta(hours=1))
    assert not pipeline.judge([q, mine], S, NOW, set()).items


def test_my_mail_in_a_foreign_language_is_not_awaiting_just_because_rules_cannot_read_it():
    """2026-10-17: 외국어 문의를 넓히자 사무국이 외국어로 보낸 안내 메일이 답 기다림으로 번졌다(사용자 "내가 보낸건데").
    foreign 은 받은 글에만 쓴다 — 내 글은 한국어 · 영어 규칙이 질문 · 요청으로 볼 때만 답 기다림이다."""
    notice = _mine("Здравствуйте! Сообщаем программу концерта и расписание поездки.", "Программа концерта")
    assert pipeline.judge([notice], S, NOW, set()).awaiting == []
    ask = _mine("Could you confirm your flight number by Friday?", "Flight details")
    assert len(pipeline.judge([ask], S, NOW, set()).awaiting) == 1                  # 영어 질문은 그대로 답 기다림


# --- 홍보 메일 · 자동 문자 더 거르기(사용자 2026-10-17 "둘 다 오케") — 꼴만 남긴 지어낸 주소 · 글
def test_mail_from_a_notice_or_marketing_subdomain_is_auto_unless_i_wrote_to_it():
    promo = ev("새 상품이 들어왔어요", src=Source.GMAIL, sid="mail@notice.shop.example", sender="가상몰", raw={"subject": "새 상품", "labels": ["CATEGORY_PERSONAL"]})
    assert preprocess.junk_reason(promo, R, set()) == "auto"                                  # Gmail 이 '개인'으로 분류해도
    assert preprocess.junk_reason(promo, R, {"mail@notice.shop.example"}) == ""              # 내가 메일을 보낸 적 있는 주소는 빼지 않는다
    for sid in ("kim@mail.univ.ac.kr", "lee@client.example", "park@mail.com", "choi@news.city.go.kr"):    # 학교 · 공공 · 보통 주소
        assert preprocess.junk_reason(ev("견적 회신 부탁드립니다", src=Source.GMAIL, sid=sid, raw={"subject": "견적"}), R, set()) == "", sid


def test_texts_from_company_numbers_not_in_contacts_are_auto():
    card = ev("가상카드1234승인 박*성 12,000원 일시불", src=Source.IMESSAGE, sid="15880000@botplatform.example", sender="15880000@botplatform.example")
    line = ev("가상카드1234해외승인 USD 10.00", src=Source.IMESSAGE, sid="+82220000000", sender="+82220000000")
    assert preprocess.junk_reason(card, R, set()) == "auto" and preprocess.junk_reason(line, R, set()) == "auto"
    person = ev("내일 뵙겠습니다", src=Source.IMESSAGE, sid="+821012345678", sender="+821012345678")
    office = ev("내일 뵙겠습니다", src=Source.IMESSAGE, sid="+827012345678", sender="+827012345678")      # 인터넷 전화(070)는 사람 번호로 본다
    known = ev("배송 출발했습니다", src=Source.IMESSAGE, sid="+82220000000", sender="가상택배 기사님")       # 연락처에 있는 곳(이름이 붙는다)
    abroad = ev("Hello, is the hotel confirmed?", src=Source.IMESSAGE, sid="+15550001111", sender="+15550001111")      # 해외 번호(독립 검토 — 해외에서 오는 사람의 연락)
    apple_id = ev("내일 자료 보내 드릴게요", src=Source.IMESSAGE, sid="someone@icloud.example", sender="someone@icloud.example")   # 사람의 Apple ID
    assert [preprocess.junk_reason(e, R, set()) for e in (person, office, known, abroad, apple_id)] == ["", "", "", "", ""]
    assert preprocess.junk_reason(line, R, {"+82220000000"}) == ""                          # 내가 그 번호에 문자를 보낸 적이 있다


def test_mail_prefixes_that_staff_also_use_are_not_auto():
    # 독립 검토(2026-10-17): mail. · info. · e. 는 회사 직원 주소에도 쓰인다 — 확실히 홍보용인 접두어만
    for sid in ("kim@mail.company.co.kr", "lee@info.company.co.kr", "park@e.company.example"):
        assert preprocess.junk_reason(ev("견적 회신 부탁드립니다", src=Source.GMAIL, sid=sid, raw={"subject": "견적"}), R, set()) == "", sid
    assert preprocess.junk_reason(ev("소식", src=Source.GMAIL, sid="hello@email.service.example", raw={"subject": "소식"}), R, set()) == "auto"


def test_my_sms_reply_protects_the_number_through_the_pipeline():
    line = ev("가상카드 대표번호입니다 확인 부탁드립니다", src=Source.IMESSAGE, chat_id="s1", sid="+82220000000", sender="+82220000000", read=False, at=NOW - timedelta(hours=1))
    me = ev("네 확인했습니다", src=Source.IMESSAGE, chat_id="s1", sid="me", sender="나", me=True, at=NOW - timedelta(hours=3))
    assert pipeline.feed([line], S, NOW, set()) == [] and len(pipeline.feed([me, line], S, NOW, set())) == 1


# 왓츠앱(설계 D7 · D8): 같은 번호의 문자와 한 사람, 읽은 1:1 요청은 1:1 문턱(문자 문턱이 아니다)
def test_whatsapp_and_sms_from_same_number_are_one_person():
    w = ev("자료 보내 주세요", src=Source.WHATSAPP, sid="+821000000000")
    t = ev("자료 보내 주세요", src=Source.IMESSAGE, sid="+821000000000")
    assert unanswered.contact_key(w) == unanswered.contact_key(t) == "+821000000000"


def test_read_whatsapp_request_waits_after_the_dm_threshold():
    from urgent_board.triage import grade as grading
    e = ev("자료 보내 주세요", src=Source.WHATSAPP, sid="+821000000000")
    dm = R.thresholds["unanswered_dm_minutes"]
    early = grading.GradeCtx(now=NOW, waited_min=dm - 1, nudges=1, is_dm=True, is_read=True, needs_reply=True)
    late = grading.GradeCtx(now=NOW, waited_min=dm, nudges=1, is_dm=True, is_read=True, needs_reply=True)
    assert grading.grade(e, early, R)[0] == Grade.NONE
    assert grading.grade(e, late, R)[:2] == (Grade.WAITING, ["waited"])
    sms = R.thresholds["unanswered_sms_minutes"]
    assert grading.grade(e, grading.GradeCtx(now=NOW, waited_min=sms, nudges=1, is_dm=True, is_read=True, needs_reply=True), R)[0] == Grade.NONE
