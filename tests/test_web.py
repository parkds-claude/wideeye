from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urgent_board import web
from urgent_board.config import load
from urgent_board.models import Event, Source
from urgent_board.store import Store
from urgent_board.web import create_app
ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
KST = ZoneInfo("Asia/Seoul")

def app(tmp_path, **kw):
    s = load(HOME); st = Store(tmp_path / "t.db"); return create_app(s, st, **kw), st

def app_with_waiting(tmp_path, **kw):
    """대기(읽은 것)를 거르는 장치를 시험할 때 쓴다. 자료(items)에는 대기가 늘 들어 있다 — 상자가 그것을 보일지는 display.fill_with_waiting 이 정한다."""
    return app(tmp_path, **kw)

def urgent_event(mid="9", hours=5):
    return Event(source=Source.KAKAO, chat_id="1", message_id=mid, chat_name="책마을-해오름 항공권 발권방", sender_id="u", sender_name="주하늘",
                 text="긴급 발권 확인 부탁드립니다", received_at=datetime.now(timezone.utc) - timedelta(hours=hours), is_from_me=False, is_read=False, is_group=False)

def test_quiet_state(tmp_path):
    a, _ = app(tmp_path); c = a.test_client()
    j = c.get("/api/items").get_json()
    assert j["items"] == [] and j["counts"] == {"urgent": 0, "waiting": 0} and "checked_at" in j and j["down"] == []
    html = c.get("/").get_data(as_text=True); assert 'id="board"' in html and "확인 중" in html

def test_page_is_landscape_and_offline(tmp_path):                  # 사용자 결정: 가로 960×540. 외부 자원 없이 뜬다
    a, _ = app(tmp_path); c = a.test_client()
    html = c.get("/").get_data(as_text=True)
    assert "width=960" in html and "http://" not in html and "https://" not in html
    css = c.get("/static/board.css").get_data(as_text=True)
    assert "960px" in css and "540px" in css and "http" not in css
    assert c.get("/static/fonts/PretendardVariable.woff2").status_code == 200
    assert c.get("/healthz").get_json() == {"ok": True}

def test_css_uses_only_allowed_type_sizes_and_no_motion(tmp_path):
    import re
    css = (ROOT / "urgent_board" / "static" / "board.css").read_text(encoding="utf-8")
    sizes = dict(re.findall(r"--(fs-[\w-]+):\s*(\d+)px", css))
    # 16 · 26 · 48 + 알림 덩어리 안에서만 15 · 14 · 13(2026-10-17 밤 "본문 폰트를 전체적으로 더 줄일 수 있는가?" — 그 전 18 · 15,
    # 2026-10-18 D14 "덩어리별 폰트/간격/줄간격을 줄이면" 세 덩어리 — 글 16 → 17(처음 15, 같은 날 "본문 폰트를 더 키워도") · 둘째 줄 16 → 15(처음 14, "15픽셀로 조금만 키우자") · 받은 때 14 → 13)
    # 세로(2026-10-18 설계 D6): 상자 이름 · 날짜 22, 고장 상태(끊김 · 전부 멈춤)의 제목 40
    assert sizes == {"fs-0": "15", "fs-1": "16", "fs-2": "26", "fs-5": "48", "fs-row": "17", "fs-when": "13", "fs-pname": "22", "fs-palarm": "40"}
    rule = lambda sel: css.split("\n" + sel + "{")[1].split("}")[0]
    # 16px 미만은 알림 덩어리 안에만 — 글(.todo) · 둘째 줄(.todo .meta) · 받은 때(.when). 그 밖(머리줄 · 이름 · "알림 없음")은 16px 이상(탭5 는 5인치다)
    assert css.count("var(--fs-row)") == 1 and "var(--fs-row)" in rule(".todo")
    assert css.count("var(--fs-0)") == 1 and "var(--fs-0)" in rule(".todo .meta")
    assert css.count("var(--fs-when)") == 1 and "var(--fs-when)" in rule(".when")
    assert not re.search(r"font-size:\s*\d", css)                  # 글자 크기는 토큰으로만
    assert not re.search(r"transition|blink|@keyframes|animation", css)     # 움직이는 것이 없다(쪽 넘김·흐르는 글을 없앴다 — 2026-10-14)
    colors = set(c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", css))
    # 남색 바탕의 무채색 일곱(사용자 결정 2026-10-14: 참고 그림의 짙은 남색) + 형광색 일곱 + 출처 글자 모래색(2026-10-17 "출처 색상만 바꾸고"). 빨강 없음("너무 긴급한 느낌")
    assert colors == {"#0d1424", "#141d31", "#19243c", "#27334f", "#f3f6fb", "#9aa7bd", "#7f8ca6", "#c9b48a",
                      "#00e054", "#c6ff00", "#ff9100", "#2bb8ff", "#ff3d9a", "#ffee00", "#b48cff"}
    flat = css.replace(" ", "")
    for token in ("--neon-green:#00e054", "--neon-cyan:#2bb8ff", "--neon-pink:#ff3d9a", "--neon-violet:#b48cff", "--neon-lime:#c6ff00", "--neon-orange:#ff9100",
                  "--urgent:var(--neon-orange)", "--ok:var(--neon-green)"):
        assert token in flat
    body = css.split("}", 2)[2]                                     # 토큰 선언(:root) 뒤 — 색은 토큰으로만 쓴다
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body)

LANDSCAPE_CSS_SHA256 = "3dc202ee4bcad7d6aa74f82a5c77f6418eec1e09bdf9ac5760c0dcb39a6c6886"   # 세로를 더하기 전의 board.css(커밋 af0f8ca) — 가로 화면의 정본


def test_portrait_rules_live_only_in_one_media_block():
    # 세로 화면(2026-10-18 설계 · 사용자 B 안): 가로 규칙은 한 글자도 바꾸지 않고 세로 블록 하나를 덧붙인다.
    #   견줄 것은 고정한 지문이다 — HEAD 와 견주면 이 커밋 뒤에 깨진다(코덱스 검토 사소 1). 가로를 일부러 고치면 지문을 새로 적는다
    import hashlib
    css = (ROOT / "urgent_board" / "static" / "board.css").read_text(encoding="utf-8")
    mark = "@media (orientation: portrait)"
    assert css.count(mark) == 1 and "@media" not in css[css.index(mark) + len(mark):]          # 블록 하나, 파일 끝에
    head = css[:css.index(mark)]
    head = head[:head.rindex("\n/*")] if head.rstrip().endswith("*/") else head                  # 블록 머리 주석은 세로의 것
    land = "\n".join(line for line in head.splitlines() if "--fs-pname:" not in line)          # 세로 토큰 줄은 가로가 아니다
    assert hashlib.sha256(land.strip().encode()).hexdigest() == LANDSCAPE_CSS_SHA256
    block = css[css.index(mark):].replace(" ", "")
    assert "html,body{width:540px;height:960px}" in block
    assert "grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:repeat(3,minmax(0,1fr))" in block     # 상자 2열 × 3행
    assert "-webkit-line-clamp:2" in block and "word-break:keep-all" in block                                         # 설명 두 줄까지(B 안)
    assert "var(--fs-pname)" in block and "var(--fs-palarm)" in block


def test_state_and_business_colors():
    css = (ROOT / "urgent_board" / "static" / "board.css").read_text(encoding="utf-8").replace(" ", "")
    assert "color:var(--urgent)" in css.split("#count{")[1].split("}")[0]   # 긴급 건수만 형광주황(면을 채우지 않는다)
    assert "main.quiet#count{color:var(--ok)}" in css               # 긴급 없음 = 형광초록
    down = css.split("#down{")[1].split("}")[0]
    assert "background:var(--urgent)" in down and "color:var(--bg)" in down     # 고장을 알리는 줄은 주황으로 채운다(긴급은 선·글자, 고장은 면)
    for name in ("green", "lime", "cyan", "pink", "yellow", "orange", "violet"):
        assert f".c-{name}{{--biz:var(--neon-{name})}}" in css       # 사업마다 형광색 하나
    assert "background:var(--biz,var(--muted))" in css.split(".rule{")[1].split("}")[0]     # 사업 색은 이름 밑줄에(개인·기타는 보조색)
    assert ".card.quiet.rule{background:color-mix(insrgb,var(--biz,var(--muted))45%,var(--card))}" in css     # 조용한 상자도 사업 색은 남긴다(흐리게)
    assert ".card.quiet.todos{" in css and "background:transparent;border-color:transparent" in css.split(".card.quiet.todos{")[1].split("}")[0]
    js = (ROOT / "urgent_board" / "static" / "board.js").read_text(encoding="utf-8")
    assert '" c-" + c.color' in js and '"card " + c.state' in js

def test_items_and_cards_carry_the_business_color(tmp_path):
    a, st = app(tmp_path); st.upsert_events([urgent_event()])
    j = a.test_client().get("/api/items").get_json()
    card = next(c for c in j["cards"] if c["state"] == "urgent")
    assert j["items"][0]["business_color"] == "cyan" and card["color"] == "cyan" and card["name"] == "책방운영단"     # 책방운영단 = 하늘(config)

def test_source_status_failure_is_shown(tmp_path):                 # Review Focus 5
    a, st = app(tmp_path); st.set_source_status("kakao", False, "db locked", datetime.now(timezone.utc))
    j = a.test_client().get("/api/items").get_json()
    assert j["sources"]["kakao"]["ok"] is False and j["down"] == ["카톡"]

def test_stale_source_counts_as_down(tmp_path):                    # 멈춘(예외 없이 걸린) 수집기도 '수집 중단'
    a, st = app(tmp_path); now = datetime.now(timezone.utc)
    st.set_source_status("imessage", True, "ok", now - timedelta(minutes=30))
    st.set_source_status("telegram", True, "ok", now - timedelta(minutes=3))
    j = a.test_client().get("/api/items").get_json()
    assert j["sources"]["imessage"]["stale"] is True and j["sources"]["telegram"]["stale"] is False
    assert j["down"] == ["문자"]

def test_down_labels_group_mail_accounts():
    src = {"gmail:a": {"ok": False, "stale": False}, "gmail:b": {"ok": True, "stale": True}, "gmail:c": {"ok": True, "stale": False},
           "kakao": {"ok": False, "stale": False}, "telegram": {"ok": True, "stale": False}}
    assert web.down_labels(src) == ["카톡", "메일 2계정"]

def test_item_payload_and_dismiss(tmp_path):
    a, st = app(tmp_path); st.upsert_events([urgent_event()])
    c = a.test_client(); j = c.get("/api/items").get_json(); assert j["counts"]["urgent"] == 1
    it = j["items"][0]
    assert it["grade"] == "urgent" and it["business_name"] == "책방운영단" and it["source"] == "카톡" and it["sender_name"] == "주하늘"
    assert it["summary"] == "긴급 발권 확인 부탁드립니다" and it["where"] == "책마을-해오름 항공권 발권방" and it["waited_text"]
    assert c.post("/api/dismiss", json={"key": it["key"]}).status_code == 204
    assert c.get("/api/items").get_json()["counts"]["urgent"] == 0
    assert c.post("/api/dismiss", json={}).status_code == 400

def test_waited_text():
    assert web.waited_text(130, 130) == "기다린 2시간 10분" and web.waited_text(120, 500) == "기다린 2시간"
    assert web.waited_text(45, 45) == "기다린 45분"
    assert web.waited_text(0, 1) == "방금" and web.waited_text(0, 600) == "업무시간 밖 수신"

def test_deadline_text():
    now = datetime(2026, 10, 13, 15, 0, tzinfo=KST)
    assert web.deadline_text(datetime(2026, 10, 13, 18, 0, tzinfo=KST), now) == "오늘 18:00"
    assert web.deadline_text(datetime(2026, 10, 14, 12, 0, tzinfo=KST), now) == "내일 12:00"
    assert web.deadline_text(datetime(2026, 10, 30, 18, 0, tzinfo=KST), now) == "10/30 18:00"
    assert web.deadline_text(None, now) == ""

def test_where_is_blank_when_it_repeats_sender_or_is_mail():
    assert web.where_text("주하늘", "주하늘", "kakao") == ""
    assert web.where_text("견적 문의", "김과장", "gmail") == ""
    assert web.where_text("합창 연습 일정방", "사무국", "telegram") == "합창 연습 일정방"

def test_where_strips_pictographs():                                # 흑백 화면 — 방 이름의 그림 문자는 뺀다
    assert web.where_text("🍎책마을-해오름 항공권 발권방", "주하늘", "kakao") == "책마을-해오름 항공권 발권방"
    assert web.where_text("✈️ 발권방 ⭐", "주하늘", "kakao") == "발권방"

def test_sender_text_is_short_enough_to_leave_room_for_the_summary():   # 메일 발신자 이름은 소속까지 붙어 길다(아래는 모두 지어낸 이름)
    assert web.sender_text("김하늘(가상책방운영진)") == "김하늘"
    assert web.sender_text("가상환경공단 이도윤") == "가상환경공단 이도윤"
    assert web.sender_text("+821000000000") == "010-0000-0000"
    assert web.sender_text("dana.lee@hotel.example") == "dana.lee"
    assert web.sender_text("Alexander Maximilian Mustermann") == "Alexander Maximi…"
    assert web.sender_text("[가상기획] 박대리") == "박대리" and web.sender_text("") == "(이름 없음)"

def test_item_has_display_sender(tmp_path):
    a, st = app(tmp_path); st.upsert_events([urgent_event()])
    assert a.test_client().get("/api/items").get_json()["items"][0]["sender"] == "주하늘"

def test_api_breaks_counts_down_by_business(tmp_path):              # 사업 상자마다의 건수 — 사업을 모르는 것은 여섯째 상자로
    a, st = app(tmp_path)
    other = Event(source=Source.KAKAO, chat_id="2", message_id="7", chat_name="아무개", sender_id="u2", sender_name="아무개",
                  text="긴급 견적 확인 부탁드립니다", received_at=datetime.now(timezone.utc) - timedelta(hours=5), is_from_me=False, is_read=False, is_group=False)
    st.upsert_events([urgent_event(), other])
    j = a.test_client().get("/api/items").get_json()
    assert {c["key"]: c["urgent"] for c in j["cards"] if c["urgent"]} == {"bookshop-team": 1, "etc": 1}
    assert j["totals"] == {"tasks": 2, "unread": 2, "awaiting": 0} and j["counts"] == {"urgent": 2, "waiting": 0}       # 머리줄 = 처리할 일(2026-10-16)
    assert "by_business" not in j and "display" not in j and "waiting_hidden" not in j      # 줄 화면에만 쓰던 것은 내주지 않는다
    assert [i["business_known"] for i in j["items"]] == [True, False] or [i["business_known"] for i in j["items"]] == [False, True]

def test_screen_fits_six_cards_with_three_notice_chunks():            # 사용자 결정 2026-10-14 · 16: 머리줄 + 사업 상자 여섯(세 칸 두 줄), 2026-10-18 상자마다 알림 세 덩어리(D14)
    css = (ROOT / "urgent_board" / "static" / "board.css").read_text(encoding="utf-8").replace(" ", "")
    assert "--pad:20px;--gap:12px;--head:56px" in css               # 20 + 56 + 12 + (상자 210 × 2 + 12) + 20 = 540
    assert "grid-template-columns:repeat(3,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr))" in css
    todo = css.split("\n.todo{")[1].split("}")[0]
    assert "font-size:var(--fs-row)" in todo and "line-height:var(--row)" in todo
    # 높이 셈(board.css 의 .card 주석): 판 안쪽 약 133px 에 덩어리 (22 + 18) × 3 + 사이 4 × 2 = 128px — 여기 값을 키우면 셋째 덩어리가 잘린다.
    # 실측은 tools/snap.py 로(2026-10-18 crowded: 넘침 0 · 마지막 덩어리 아래 9.7px). 값을 바꾸면 다시 잴 것
    assert "--row:22px;--row-meta:18px;--chunk-gap:4px" in css and "line-height:var(--row-meta)" in css.split("\n.todo.meta{")[1].split("}")[0]
    assert "gap:var(--chunk-gap);padding:5px8px" in css.split("\n.todos{")[1].split("}")[0]
    assert "gap:5px;min-width:0;min-height:0;padding:8px10px" in css.split("\n.card{")[1].split("}")[0]
    assert web.FEED_ON_CARD == 3 and web.TASKS_ON_CARD == 2                  # 탭5 알림은 세 줄, 할 일 상자(메뉴 막대)는 그대로
    html = (ROOT / "urgent_board" / "templates" / "board.html").read_text(encoding="utf-8")
    assert 'id="cards"' in html and 'id="title"' in html and 'id="count"' in html

def test_source_text_names_the_mail_account():                      # 사용자 요청: 어느 메일인지 보이게
    labels = {"minji@example.com": "업무", "office@example.org": "사무국"}
    assert web.source_text("gmail", ["minji@example.com"], labels) == "메일 업무"
    assert web.source_text("gmail", ["minji@example.com", "main@example.com", "office@example.org"], labels) == "메일 업무 +2"
    assert web.source_text("gmail", ["someone.long.name@x.example"], labels) == "메일 someone.long"       # 모르는 계정은 @ 앞 12자
    assert web.source_text("gmail", [], labels) == "메일" and web.source_text("kakao", [], labels) == "카톡"

def test_api_source_shows_the_account(tmp_path):
    a, st = app_with_waiting(tmp_path)
    st.upsert_events([Event(source=Source.GMAIL, chat_id="t1", message_id="m1", chat_name="견적", sender_id="kim@client.example", sender_name="김과장",
                            text="긴급 견적 확인 부탁드립니다", received_at=datetime.now(timezone.utc) - timedelta(hours=5), is_from_me=False,
                            is_read=True, is_group=False, thread_id="t1", account="office@example.org", raw={"subject": "견적", "headers": {}, "to": []})])
    assert a.test_client().get("/api/items").get_json()["items"][0]["source"] == "메일 사무국"


# ===== 요약 · 확인할 길 (사용자 요청 2026-10-13) =====
from urgent_board import summarize

LONG_TEXT = "안녕하세요 대장님, 요청 주신 다음 주 단체버스 45인승 2대 견적서 첨부드립니다. 검토 후 진행 여부 회신 부탁드립니다."

def mail_event(mid="m1", *, read=False, account="office@example.org", sender="kim@client.example", labels=None, group=False, text=LONG_TEXT, subject="견적"):
    return Event(source=Source.GMAIL, chat_id="t" + mid, message_id=mid, chat_name=subject, sender_id=sender, sender_name="김과장",
                 text=text, received_at=datetime.now(timezone.utc) - timedelta(hours=1), is_from_me=False, is_read=read, is_group=group, thread_id="t" + mid,
                 account=account, link=f"https://mail.google.com/mail/u/?authuser={account.replace('@', '%40')}#all/t{mid}",
                 raw={"subject": subject, "headers": {}, "to": [account], "labels": labels or []})

def test_api_shows_the_saved_summary_instead_of_the_raw_text(tmp_path):
    a, st = app(tmp_path); st.upsert_events([mail_event()])
    c = a.test_client(); it = c.get("/api/items").get_json()["items"][0]
    assert it["ai"] is False and it["summary"].startswith("안녕하세요 대장님")          # 요약이 아직 없으면 원문 앞부분
    st.put_summary(it["key"], summarize.text_hash(LONG_TEXT), "옛 방식으로 만든 요약", datetime.now(timezone.utc))
    assert c.get("/api/items").get_json()["items"][0]["ai"] is False                              # 요약 설정이 바뀌기 전에 만든 것은 쓰지 않는다
    st.put_summary(it["key"], summarize.text_hash(LONG_TEXT, summarize.recipe_for(load(HOME))), "단체버스 견적 회신 요청", datetime.now(timezone.utc))
    it = c.get("/api/items").get_json()["items"][0]
    assert it["ai"] is True and it["summary"] == "단체버스 견적 회신 요청" and it["raw_summary"].startswith("안녕하세요 대장님")

def test_list_page_lets_me_see_everything_open_it_and_clear_it(tmp_path):
    a, st = app(tmp_path)
    st.upsert_events([mail_event("m1"), urgent_event("9"),
                      mail_event("m2", sender="news@shop.example", subject="뉴스레터", text="이번 주 소식을 전해드립니다. 수신거부는 아래 링크"),
                      mail_event("m3", sender="pay@bank.example", subject="결제 안내", text="결제가 완료되었습니다", labels=["CATEGORY_UPDATES"]),
                      mail_event("m4", sender="lee@client.example", subject="참조 건", text="참고하세요", group=True)])
    html = a.test_client().get("/list").get_data(as_text=True)
    assert "처리할 일 2" in html and "김과장" in html and "주하늘" in html
    assert 'href="https://mail.google.com/mail/u/?authuser=office%40example.org#all/tm1"' in html and "열기" in html     # 그 계정의 그 스레드
    assert 'data-key="gmail:tm1:m1"' in html and "처리됨" in html
    assert "할 일에서 뺀 안 읽은 것 3" in html and "광고" in html and "자동 알림" in html and "참조·단체 메일" in html                 # 뺀 것도 보인다
    assert "http://" not in html.replace("http://127.0.0.1", "") and "list.css" in html

def test_list_page_only_links_known_schemes(tmp_path):
    a, st = app(tmp_path)
    e = mail_event("m9"); e.link = "javascript:alert(1)"
    st.upsert_events([e])
    html = a.test_client().get("/list").get_data(as_text=True)
    assert "javascript:" not in html and "김과장" in html

def test_only_local_host_names_are_served(tmp_path):                 # DNS 리바인딩: 남의 웹페이지가 이름을 127.0.0.1 로 돌려 /list 를 읽는 것
    a, st = app(tmp_path); st.upsert_events([mail_event()]); c = a.test_client()
    for path in ("/list", "/api/items", "/"):
        assert c.get(path, headers={"Host": "evil.example:5091"}).status_code == 403
        assert c.get(path, headers={"Host": "127.0.0.1:5091"}).status_code == 200
        assert c.get(path, headers={"Host": "localhost:5091"}).status_code == 200
    assert c.post("/api/dismiss", json={"key": "k"}, headers={"Host": "evil.example"}).status_code == 403

def test_dismiss_rejects_keys_that_are_not_short_strings(tmp_path):
    a, _ = app(tmp_path); c = a.test_client()
    assert c.post("/api/dismiss", json={"key": ["a", "b"]}).status_code == 400
    assert c.post("/api/dismiss", json={"key": "x" * 500}).status_code == 400
    assert c.post("/api/dismiss", json={"key": "kakao:1:9"}).status_code == 204

def test_list_page_labels_noise_and_answered_reasons(tmp_path):
    assert web.SKIP_LABEL["answered"] and web.SKIP_LABEL["noise"]


# ===== 대기 목록을 '내용에 따라' 고르기 (사용자 2026-10-13) =====
def _verdict(st, it, needed):
    r = summarize.recipe_for(load(HOME))
    st.put_summary(it["key"], summarize.text_hash(it_text(st, it["key"]), r), "요약", datetime.now(timezone.utc))
    st.put_verdict(it["key"], summarize.text_hash(it_text(st, it["key"]), r), needed, datetime.now(timezone.utc))

def it_text(st, key):
    from urgent_board.triage.pipeline import build_items
    return next(i.text for i in build_items(st.recent_events(days=7), load(HOME), datetime.now(timezone.utc), set()) if i.key == key)

def test_waiting_item_judged_unneeded_leaves_the_board_but_stays_on_the_list(tmp_path):
    # 읽은 것이 바로 대기로 오르게 재촉 낱말을 넣는다(시험을 돌리는 시각의 업무시간에 기대지 않는다)
    a, st = app_with_waiting(tmp_path); st.upsert_events([mail_event("m1", read=True, text="긴급 견적 확인 부탁드립니다"),
                                             mail_event("m2", read=True, sender="lee@client.example", subject="다른 건", text="긴급 일정 확인 부탁드립니다")])
    c = a.test_client(); j = c.get("/api/items").get_json()
    assert [i["grade"] for i in j["items"]] == ["waiting", "waiting"] and j["counts"]["waiting"] == 2
    drop = next(i for i in j["items"] if i["key"].endswith("m1"))
    _verdict(st, drop, False)
    j = c.get("/api/items").get_json()
    assert [i["key"] for i in j["items"]] == [k for k in (i["key"] for i in j["items"]) if not k.endswith("m1")] and j["counts"]["waiting"] == 1
    assert j["unneeded_total"] == 1 and sum(c["n"] for c in j["cards"]) == 1                      # 사업 상자의 할 일에서도 빠진다
    html = c.get("/list").get_data(as_text=True)
    assert "할 일에서 뺀 것 1" in html and "할 일 없다고 판단" in html and "처리할 일 1" in html       # 조용히 사라지지 않는다 — 전체 목록에 까닭과 함께

def test_urgent_item_is_never_dropped_by_the_verdict(tmp_path):         # 긴급 = 나에게 온 안 읽은 것 — 이 뜻은 판단으로 바꾸지 않는다
    a, st = app(tmp_path); st.upsert_events([mail_event("m1")])
    c = a.test_client(); it = c.get("/api/items").get_json()["items"][0]
    assert it["grade"] == "urgent"
    _verdict(st, it, False)
    j = c.get("/api/items").get_json()
    assert j["counts"] == {"urgent": 1, "waiting": 0} and j["unneeded_total"] == 0

def test_verdicts_are_ignored_when_judging_is_off(tmp_path):
    s = load(HOME); s.summary = {**s.summary, "judge": False}; s.display = {**s.display, "show_waiting": True}; st = Store(tmp_path / "t.db"); a = create_app(s, st)
    st.upsert_events([mail_event("m1", read=True, text="긴급 견적 확인 부탁드립니다")])
    c = a.test_client(); it = c.get("/api/items").get_json()["items"][0]
    r = summarize.recipe(s.summary)
    st.put_verdict(it["key"], summarize.text_hash(it_text(st, it["key"]), r), False, datetime.now(timezone.utc))
    assert c.get("/api/items").get_json()["counts"]["waiting"] == 1


# ===== 읽은 것은 탭5 에서 뺀다 (사용자 2026-10-13 18시: "내가 읽었던 것, 너와 대책을 세웠던 것은 빼줘") =====
def test_card_lists_unread_first_then_read_and_the_list_keeps_both(tmp_path):
    # 사용자 2026-10-16: "내가 읽고/확인해도 까먹기도 하거든" — 읽은 요청도 끝날 때까지 상자에 남는다(차례는 최근 것부터, 주황 수는 안 읽은 것)
    from dataclasses import replace
    s = load(HOME); st = Store(tmp_path / "t.db"); a = create_app(s, st)
    older = replace(mail_event("m2", read=True, sender="lee@client.example", subject="다른 건", text="긴급 일정 확인 부탁드립니다"),
                    received_at=datetime.now(timezone.utc) - timedelta(hours=3))
    st.upsert_events([mail_event("m1"), older])
    c = a.test_client(); j = c.get("/api/items").get_json()
    assert j["totals"] == {"tasks": 2, "unread": 1, "awaiting": 0}
    card = next(x for x in j["cards"] if x["n"])
    assert card["state"] == "urgent" and card["urgent"] == 1 and card["waiting"] == 1 and card["n"] == 2
    assert [t["unread"] for t in card["tasks"]] == [True, False] and all(set(t) == {"what", "who", "when", "unread", "due_soon", "old", "awaiting"} for t in card["tasks"])
    html = c.get("/list").get_data(as_text=True)
    assert "처리할 일 2" in html and "● 안 읽음" in html and "○ 읽음" in html

def test_box_with_only_read_tasks_still_speaks(tmp_path):        # 2026-10-16: 채움 설정은 없어졌다 — 읽은 할 일도 늘 보인다(흰 수)
    s = load(HOME); s.display = {**s.display, "fill_with_waiting": False}; st = Store(tmp_path / "t.db"); a = create_app(s, st)
    st.upsert_events([mail_event("m2", read=True, text="긴급 일정 확인 부탁드립니다")])
    j = a.test_client().get("/api/items").get_json()
    assert [c["state"] for c in j["cards"] if c["n"]] == ["waiting"] and j["totals"] == {"tasks": 1, "unread": 0, "awaiting": 0}

def app_handling(tmp_path, on=True):
    s = load(HOME); s.handled = {**s.handled, "claude_sessions": on}; st = Store(tmp_path / "t.db"); return create_app(s, st), st

def test_mail_handled_in_a_claude_session_leaves_the_board_but_stays_on_the_list(tmp_path):
    a, st = app_handling(tmp_path); st.upsert_events([mail_event("m1"), mail_event("m2", sender="lee@client.example", subject="다른 건")])
    c = a.test_client(); assert c.get("/api/items").get_json()["counts"]["urgent"] == 2
    st.touch_many({"m1": datetime.now(timezone.utc)})                          # 도착한 뒤 Claude 세션이 이 메일을 도구로 다뤘다
    j = c.get("/api/items").get_json()
    assert [i["key"].endswith("m2") for i in j["items"]] == [True] and j["counts"]["urgent"] == 1 and j["handled_total"] == 1
    assert sum(c["urgent"] for c in j["cards"]) == 1                              # 사업 상자의 건수에서도 빠진다
    html = c.get("/list").get_data(as_text=True)
    assert "Claude 와 다룬 것 1" in html and "처리할 일 1" in html                # 조용히 사라지지 않는다 — 전체 목록에 남는다

def test_mail_touched_before_it_arrived_or_with_the_switch_off_stays(tmp_path):
    a, st = app_handling(tmp_path); st.upsert_events([mail_event("m1")])
    st.touch_many({"tm1": datetime.now(timezone.utc) - timedelta(hours=5)})    # 같은 대화를 다룬 것은 이 메일이 오기 전이다
    assert a.test_client().get("/api/items").get_json()["counts"]["urgent"] == 1
    b, st2 = app_handling(tmp_path / "off", on=False); st2.upsert_events([mail_event("m1")]); st2.touch_many({"m1": datetime.now(timezone.utc)})
    j = b.test_client().get("/api/items").get_json()
    assert j["counts"]["urgent"] == 1 and j["handled_total"] == 0


# ===== 죽은 것을 숨기지 않는다 (품질 반복 1회차) =====
def test_api_says_how_blind_the_board_is(tmp_path):
    a, st = app(tmp_path); now = datetime.now(timezone.utc); c = a.test_client()
    assert c.get("/api/items").get_json()["blind"] == ""                       # 아직 아무 수집기도 기록이 없다(처음 뜰 때) — 모른다고 하지 않는다
    for name in ("kakao", "imessage", "gmail:a"): st.set_source_status(name, True, "ok", now)
    assert c.get("/api/items").get_json()["blind"] == ""
    st.set_source_status("kakao", False, "boom", now)
    j = c.get("/api/items").get_json(); assert j["blind"] == "some" and j["down"] == ["카톡"]
    for name in ("imessage", "gmail:a"): st.set_source_status(name, False, "boom", now)
    assert c.get("/api/items").get_json()["blind"] == "all"
    for name in ("kakao", "imessage", "gmail:a"): st.set_source_status(name, True, "ok", now - timedelta(hours=3))      # 기록이 오래됨 = 멈춘 것
    assert c.get("/api/items").get_json()["blind"] == "all"

def test_api_and_page_carry_the_same_asset_version(tmp_path):
    a, _ = app(tmp_path); c = a.test_client()
    v = c.get("/api/items").get_json()["v"]
    assert v and len(v) >= 8 and f'data-v="{v}"' in c.get("/").get_data(as_text=True)
    assert web.asset_version(ROOT / "urgent_board") == v and web.asset_version(tmp_path) == web.asset_version(tmp_path)     # 파일이 그대로면 같다


# ===== 품질 반복 2회차 =====
def test_api_counts_handled_items_that_the_board_would_otherwise_show(tmp_path):
    a, st = app_handling(tmp_path)
    st.upsert_events([mail_event("m1"), mail_event("m2", read=True, sender="lee@client.example", subject="다른 건", text="긴급 일정 확인 부탁드립니다")])
    now = datetime.now(timezone.utc); st.touch_many({"m1": now, "m2": now})
    j = a.test_client().get("/api/items").get_json()
    assert j["handled_total"] == 2 and j["handled_shown"] == 1                  # 읽은 것(대기)은 어차피 탭5 에 안 뜬다 — 뜰 뻔한 것(긴급)만 센다
    assert j["handled_who"] == ["김과장"]                                        # 4회차(검수): 건수만이 아니라 누구의 것을 뺐는지도 — 같은 사람은 한 번만

def test_list_says_when_the_left_out_list_is_cut(tmp_path):
    a, st = app(tmp_path)
    st.upsert_events([mail_event(f"n{i}", sender=f"news{i}@shop.example", subject=f"소식 {i}", text="이번 주 소식을 전해드립니다. 수신거부는 아래 링크") for i in range(web.SKIPPED_MAX + 5)])
    html = a.test_client().get("/list").get_data(as_text=True)
    assert f"할 일에서 뺀 안 읽은 것 {web.SKIPPED_MAX + 5}" in html and f"앞의 {web.SKIPPED_MAX}건만" in html


# ===== 지켜보는 방 — 사용자 2026-10-14: "텔레그램 합창단 단톡방 — 안 읽은 것은 표시해줘. 하나로" =====
def test_a_watched_room_shows_up_as_one_urgent_row_with_the_count_in_the_senders_place(tmp_path):
    s = load(HOME); s.watch_rooms = frozenset({("telegram", "g1")}); st = Store(tmp_path / "t.db"); a = create_app(s, st)
    def post(mid, who, text, minutes):
        return Event(source=Source.TELEGRAM, chat_id="g1", message_id=mid, chat_name="운영회의방", sender_id=who, sender_name=who, text=text,
                     received_at=datetime.now(timezone.utc) - timedelta(minutes=minutes), is_from_me=False, is_read=False, is_group=True)
    st.upsert_events([post("1", "가", "발표회 영상 초안 올립니다", 50), post("2", "나", "포스터 이미지를 쓸지 고민입니다", 30), post("3", "가", "일요일 일정 확인 바랍니다", 5)])
    j = a.test_client().get("/api/items").get_json()
    assert j["counts"]["urgent"] == 1 and len(j["items"]) == 1                   # 세 글이 한 줄 · 긴급 1건
    row = j["items"][0]
    assert row["sender"] == "안 읽은 글 3건" and row["count"] == 3 and row["where"] == "운영회의방" and row["grade"] == "urgent"
    assert row["summary"].startswith("가: 일요일")                                # 요약이 아직 없으면 가장 최근 글
    assert j["skipped_total"] == 0                                               # '단톡방 일반 글'로 빠지지 않는다


# ===== 채움 — 사용자 2026-10-14: "'긴급없음'은 너무 없어 보인다. 과거 내용이라도 보여줘" =====
def _filling(tmp_path, on=True):
    s = load(HOME); s.display = {**s.display, "fill_with_waiting": on}; s.watch_rooms = frozenset()
    s.summary = {**s.summary, "judge": False}; s.handled = {**s.handled, "claude_sessions": False}
    st = Store(tmp_path / "t.db"); return create_app(s, st), st

def _read_request(n):
    return mail_event(f"w{n}", read=True, sender=f"p{n}@client.example", subject=f"건 {n}", text="내일까지 견적 회신 부탁드립니다. 확인 가능할까요?")

def test_card_shows_two_tasks_and_how_many_more(tmp_path):
    a, st = _filling(tmp_path)
    st.upsert_events([_read_request(n) for n in range(9)])                       # 읽었지만 답 안 한 요청 9건, 안 읽은 것 0
    j = a.test_client().get("/api/items").get_json()
    card = next(c for c in j["cards"] if c["n"])
    assert j["totals"] == {"tasks": 9, "unread": 0, "awaiting": 0} and len(card["tasks"]) == 2 and card["more"] == 7
    assert card["state"] == "urgent" and all(t["due_soon"] for t in card["tasks"])        # 읽었어도 '내일까지'면 기한 임박 — 주황
    st.upsert_events([mail_event("u1", sender="new@client.example", subject="새 건")])      # 같은 사업에 안 읽은 것이 하나 온다
    j = a.test_client().get("/api/items").get_json()
    card = next(c for c in j["cards"] if c["urgent"])
    assert card["state"] == "urgent" and card["n"] == 10 and card["tasks"][0]["unread"] is True     # 안 읽은 것이 맨 위

def test_no_sources_at_all_is_not_shown_as_all_clear(tmp_path):
    """검토 I6: 수집기가 하나도 없는 설정(빈 설정으로 물러섬 · 다 꺼 둠)이 초록 "긴급 없음"으로 보이면 '전부 확인했고 없다'는 거짓이 된다."""
    s = load(HOME)
    s.accounts = {"gmail": [], "telegram": {"enabled": False}, "kakao": {"enabled": False}, "imessage": {"enabled": False}}
    j = create_app(s, Store(tmp_path / "t.db")).test_client().get("/api/items").get_json()
    assert j["blind"] == "all" and j["down"] == ["모든 출처"]                  # 탭5: "확인 불가 · 모든 출처 수집 중단"
    s2 = load(HOME)                                                       # 수집기가 있는데 아직 기록이 없을 때(막 뜸)는 그대로 — "확인 중"
    assert create_app(s2, Store(tmp_path / "t2.db")).test_client().get("/api/items").get_json()["blind"] == ""


def test_whatsapp_counts_as_a_source_and_its_link_is_safe():    # 왓츠앱 설계 D2 · D10
    from types import SimpleNamespace
    from urgent_board import web as w
    assert w.has_sources(SimpleNamespace(accounts={"whatsapp": {"enabled": True}}))
    assert w.safe_link("whatsapp://send?phone=821000000000") == "whatsapp://send?phone=821000000000"
    assert w.safe_link("whatsapp://") == "whatsapp://"


def test_teams_or_calls_count_only_when_turned_on_by_hand():
    """곁가지(부재중 전화 · 팀즈 알림)는 적지 않았으면 켜지지만 '이은 곳'이 아니다(2026-10-17). 직접 켠 것(enabled: true — 길의 잇기 ·
    setup source … on)은 친다 — 팀즈만 이은 사람의 화면이 "모든 출처 수집 중단"으로 뜨던 것(코덱스 검수 2026-11-02)."""
    from types import SimpleNamespace
    from urgent_board import web as w
    assert not w.has_sources(SimpleNamespace(accounts={}))
    assert not w.has_sources(SimpleNamespace(accounts={"teams": None, "calls": {}}))
    assert not w.has_sources(SimpleNamespace(accounts={"teams": {"enabled": False}}))
    assert not w.has_sources(SimpleNamespace(accounts={"teams": {"enabled": "yes"}}))         # 꼴이 틀린 값은 치지 않는다
    assert w.has_sources(SimpleNamespace(accounts={"teams": {"enabled": True}}))
    assert w.has_sources(SimpleNamespace(accounts={"calls": {"enabled": True}}))
