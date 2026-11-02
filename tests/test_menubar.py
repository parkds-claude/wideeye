"""메뉴 막대의 계산(tools/menubar.py 의 순수 함수) — 막대 글 · 차림표 · 상태 묻기 · 여는 주소. AppKit 을 부르지 않는다."""
import io
import json
import urllib.error

from tools import menubar as mb

TASKS = [{"key": "k1", "box": "동네 합창 모임", "what": "기사 연락처 회신", "meta": "메일 김 · 10/23까지", "unread": True, "due_soon": True, "old": False,
          "link": "https://mail.google.com/mail/u/?authuser=a%40b.c#all/t1"},
         {"key": "k2", "box": "개인 · 기타", "what": "일정 답하기", "meta": "카톡 박 · 어제 10:00", "unread": False, "due_soon": False, "old": False,
          "link": ""}]
STATUS = {"totals": {"tasks": 8, "unread": 2}, "blind": "", "down": [], "checked_at": "21:30", "settings_error": "", "legacy_config": False,
          "cards": [{"key": "bazaar2", "name": "동네 합창 모임", "color": "yellow", "n": 3, "urgent": 1, "waiting": 2},
                    {"key": "team", "name": "책방운영단", "color": "cyan", "n": 1, "urgent": 0, "waiting": 1},
                    {"key": "calligraphy", "name": "동네 서예 동호회", "color": "pink", "n": 0, "urgent": 0, "waiting": 0}],
          "tasks": TASKS, "tab5": {"connected": True, "last_ok_at": 1000.0, "updated_at": 1005.0}, "version": "v"}


def with_(**kw):
    return {**STATUS, **kw}


def test_title_counts_tasks_and_trouble_marks():
    assert mb.title(None) == [("확인 불가", True)]                                   # 엔진이 답하지 않는다
    assert mb.title(STATUS) == [("할 일 ", False), ("8", True)]                       # 안 읽음 · 기한 임박이 있으면 숫자만 주황
    calm = with_(totals={"tasks": 3, "unread": 0}, tasks=[{**TASKS[1]}])
    assert mb.title(calm) == [("할 일 ", False), ("3", False)]                        # 읽은 할 일만 — 주황 아님
    assert mb.title(with_(totals={"tasks": 0, "unread": 0}, tasks=[])) == [("할 일 없음", False)]
    assert mb.title(with_(blind="all")) == [("확인 불가", True)]                       # 전부 멈춤 — "없음"이라 하지 않는다
    assert mb.title(with_(blind="some", down=["카톡"])) == [("할 일 ", False), ("8", True), (" !", True)]
    assert mb.title(with_(totals={"tasks": 0, "unread": 0}, tasks=[], settings_error="설정 파일 오류")) == [("할 일 없음", False), (" !", True)]


def labels(items):
    return [i.label for i in items]


def test_items_lines_and_actions():
    got = mb.items(with_(down=["카톡", "메일 1계정"], blind="some", settings_error="설정 파일 오류 — 이전 설정으로 동작 중: boxes[0].color"),
                   tab5_serial="", now=1010.0)
    ls = labels(got)
    assert ls[0] == "할 일 8 · 안 읽음 2 · 21:30 확인"
    down = next(i for i in got if i.label.startswith("수집 중단"))
    assert down.label == "수집 중단: 카톡 · 메일 1계정" and down.alert                        # 고장 줄은 주황 글자
    assert next(i for i in got if i.label.startswith("설정 파일 오류")).alert
    box = next(i for i in got if i.label.startswith("동네 합창 모임"))
    assert box.label == "동네 합창 모임\t할 일 3" and box.action == "open" and box.arg == "/list"
    assert next(i for i in got if i.label.startswith("동네 서예 동호회")).label == "동네 서예 동호회\t없음"
    acts = {i.label: (i.action, i.arg) for i in got}
    assert acts["전체 목록 열기"] == ("open", "/list") and acts["현황판 열기"] == ("open", "/") and acts["끝내기"][0] == "quit"
    assert acts["설정…"] == ("open", "/settings")                                     # setup_left 칸이 없는 옛 엔진 — /setup 이 없다
    manage = next(i for i in got if i.label == "관리")
    assert manage.action == "menu" and manage.children == mb.MANAGE                    # 자세한 설정 화면 · 엔진 다시 시작 · 기록 폴더는 하위로
    assert [c[1] for c in manage.children] == ["open", "restart", "logs"] and "엔진 다시 시작" not in ls
    assert not any(x.startswith("탭5") for x in ls)                                   # 탭5 를 쓰지 않는 사람
    assert ls[0] != "-" and ls[-1] == "끝내기" and "-" in ls


def test_settings_opens_the_simple_page_and_unfinished_setup_is_shown():
    """2026-10-16 AI 와 함께 설정 — '설정…'은 설정 한눈에, 필수 설정이 덜 끝났으면 머리줄 아래에 주황 한 줄."""
    got = mb.items(with_(setup_left=2, settings_error="설정 파일 오류"), tab5_serial="", now=1010.0)
    acts = {i.label: (i.action, i.arg) for i in got}
    assert acts["설정…"] == ("open", "/setup") and acts["설정이 남았다 — 2단계"] == ("open", "/setup")
    assert acts["설정 파일 오류"] == ("open", "/setup") and next(i for i in got if i.label.startswith("설정이 남았다")).alert
    ls = labels(mb.items(with_(setup_left=0), tab5_serial="", now=1010.0))
    assert not any(x.startswith("설정이 남았다") for x in ls)
    assert not any(x.startswith("설정이 남았다") for x in labels(mb.items(with_(setup_left=None), tab5_serial="", now=1010.0)))   # 읽지 못함 — 띄우지 않는다


def test_task_lines_have_open_and_dismiss():
    got = mb.items(STATUS, tab5_serial="", now=1010.0)
    t = [i for i in got if i.action == "task"]
    assert [i.label for i in t] == ["● 기사 연락처 회신\t동네 합창 모임", "○ 일정 답하기\t개인 · 기타"]
    assert t[0].alert and not t[1].alert                                              # 안 읽음 · 기한 임박은 주황
    assert t[0].children == (("원문 열기", "open_link", TASKS[0]["link"]), ("처리됨", "dismiss", "k1"))
    assert t[1].children[0] == ("원문 열기", "open_link", "")                          # 여는 주소가 없으면 전체 목록을 연다(앱)
    assert labels(got).index("● 기사 연락처 회신\t동네 합창 모임") < labels(got).index("동네 합창 모임\t할 일 3")      # 할 일이 상자 줄보다 위


def test_only_known_link_schemes_open():
    assert mb.link_or_list(5091, "T", "https://mail.google.com/mail/u/0/#all/x") == "https://mail.google.com/mail/u/0/#all/x"
    assert mb.link_or_list(5091, "T", "tg://resolve?domain=a").startswith("tg://")
    assert mb.link_or_list(5091, "T", "javascript:alert(1)") == mb.open_url(5091, "T", "/list")
    assert mb.link_or_list(5091, "", "") == "http://127.0.0.1:5091/list"


def test_tab5_line():
    """창이 살아 있는지는 상태 파일의 pid 로 본다 — 맥 화면이 잠든 동안 창은 그림을 보내지 않아 파일이 몇 분씩 묵는다(실측)."""
    alive = lambda pid: pid == 77                                                             # noqa: E731
    tab5 = {"pid": 77, "connected": True, "screen": True, "paused": False, "last_ok_at": 1000.0, "updated_at": 700.0}
    def line(t, serial="AA:BB"):
        got = mb.items(with_(tab5=t), tab5_serial=serial, now=1010.0, alive=alive)
        return next((i for i in got if i.label.startswith("탭5")), None)
    t = line(tab5)
    assert t.label == "탭5: 그림 10초 전" and not t.enabled                                    # 파일이 310초 묵었어도 창은 살아 있다
    assert line({**tab5, "pid": 78}).action == "display_start"                                 # 창 프로세스가 없다
    assert line(None).label == "탭5 연결 시작"
    assert line({**tab5, "connected": False}).label == "탭5: 찾는 중(USB 를 본다)"
    assert line({**tab5, "screen": False}).label == "탭5: 쉼(맥 화면이 잠들었거나 잠김)"
    assert line({**tab5, "paused": True}).label == "탭5: 포트를 잠시 놓음(진단 중)"
    assert line(tab5, serial="") is None                                                      # 탭5 를 쓰지 않는 사람


def test_items_when_the_engine_does_not_answer():
    got = mb.items(None, tab5_serial="", now=0.0)
    ls = labels(got)
    assert ls[0] == "엔진이 답하지 않는다" and not got[0].enabled
    assert "설정…" in ls and "관리" in ls and "끝내기" in ls and not any("할 일 " in x for x in ls)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_fetch_reads_the_whole_body_and_closes():
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, token=req.get_header("X-ub-token"), timeout=timeout)
        seen["resp"] = Resp(json.dumps(STATUS).encode())
        return seen["resp"]
    assert mb.fetch(5091, "TOK", opener=opener) == STATUS
    assert seen["url"] == "http://127.0.0.1:5091/api/status" and seen["token"] == "TOK" and seen["timeout"] == 5
    assert seen["resp"].closed


def test_fetch_failures_are_none_and_error_bodies_are_drained():
    body = io.BytesIO(b'{"error": "token"}')

    def unauthorized(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "UNAUTHORIZED", {}, body)
    assert mb.fetch(5091, "", opener=unauthorized) is None and body.closed
    for exc in (ConnectionRefusedError(61, "refused"), TimeoutError("timed out"), urllib.error.URLError("x")):
        def broken(req, timeout, exc=exc):
            raise exc
        assert mb.fetch(5091, "T", opener=broken) is None
    assert mb.fetch(5091, "T", opener=lambda req, timeout: Resp(b"<html>")) is None
    assert mb.fetch(5091, "T", opener=lambda req, timeout: Resp(b"[1]")) is None


def test_open_url_carries_the_token_only_when_there_is_one():
    assert mb.open_url(5091, "T-k_1", "/settings") == "http://127.0.0.1:5091/auth?t=T-k_1&next=/settings"
    assert mb.open_url(5191, "", "/list") == "http://127.0.0.1:5191/list"                # 열쇠가 아직 없다 — 잠긴 쪽이 열쇠 칸을 준다


def test_cut_off_or_garbled_answers_are_none_too():
    """엔진이 다시 시작하는 순간에 걸린 답(본문이 잘림 · HTTP 가 아님)도 확인 불가 — 묻는 스레드가 죽지 않게(검토 I2)."""
    import http.client

    class Cut(Resp):
        def read(self, *a):
            raise http.client.IncompleteRead(b"{", 10)

    def garbled(req, timeout):
        raise http.client.BadStatusLine("garbage")
    assert mb.fetch(5091, "T", opener=lambda req, timeout: Cut(b"")) is None
    assert mb.fetch(5091, "T", opener=garbled) is None


def test_one_poll_never_raises_and_old_numbers_turn_into_unknown():
    def broken_token():
        raise PermissionError(13, "denied")
    light, st = mb.poll_once(lambda: {"port": 5091}, broken_token, lambda port, token: STATUS)
    assert light == {"port": 5091} and st is None
    assert mb.poll_once(lambda: {"port": 5091}, lambda: "T", lambda port, token: STATUS)[1] == STATUS
    assert mb.stale(last_done=100.0, now=125.0) is False and mb.stale(last_done=100.0, now=131.0) is True     # 30초 넘게 답이 안 옴


def test_a_click_carries_its_own_action():
    """차림표가 열린 채 다시 만들어져도 누른 줄의 동작이 바뀌지 않게 — 줄이 제 동작을 들고 있다(검토 I8)."""
    for it in (mb.Item("설정…", "open", "/settings"), mb.Item("끝내기", "quit"), mb.Item("엔진 다시 시작", "restart")):
        assert mb.decode_action(mb.encode_action(it)) == (it.action, it.arg)


def test_book_error_line():                                           # 검토 I3
    got = mb.items(with_(book_error=True, blind="some"), tab5_serial="", now=1010.0)
    line = next(i for i in got if i.label == "할 일 장부 오류")
    assert line.alert and not line.enabled


def test_menubar_opens_whatsapp_links_like_the_engine():    # 왓츠앱 검토 I1 — 메뉴 막대의 목록은 web.SAFE_LINK 와 같아야 한다
    from urgent_board import web
    assert set(mb.SAFE_LINKS) == set(web.SAFE_LINK)
    assert mb.link_or_list(5091, "T", "whatsapp://send?phone=821000000000") == "whatsapp://send?phone=821000000000"


def test_display_start_is_offered_only_under_the_production_name_and_data_folder(tmp_path):
    """탭5 창 작업은 운영 이름 하나뿐 — 시험 이름 · 다른 자료 폴더로 뜬 메뉴 막대는 '탭5 연결 시작'을 내놓지 않는다(검토 3)."""
    from tools import launchd as ld
    from urgent_board import paths
    tab5 = {"connected": False, "pid": 78}
    got = mb.items(with_(tab5=tab5), tab5_serial="SER", now=1010.0, alive=lambda p: False, display_ok=False)
    assert not any(it.action == "display_start" for it in got)
    assert any(it.label.startswith("탭5") and not it.action for it in got)
    assert any(it.action == "display_start" for it in mb.items(with_(tab5=tab5), tab5_serial="SER", now=1010.0, alive=lambda p: False))
    assert mb.display_allowed({}) is True
    assert mb.display_allowed({"URGENT_BOARD_LABEL_PREFIX": ld.DEFAULT_PREFIX, "URGENT_BOARD_HOME": str(paths.DEFAULT_HOME)}) is True
    assert mb.display_allowed({"URGENT_BOARD_LABEL_PREFIX": "io.examplecorp.urgent-board-test"}) is False
    assert mb.display_allowed({"URGENT_BOARD_HOME": str(tmp_path)}) is False
