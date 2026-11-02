"""탭5 화면 스크립트(board.js) — node 로 실제 돌려 본다(브라우저 없이, 화면 객체는 빈 흉내). node 가 없으면 건너뛴다.

화면은 머리줄("WideEye N건" — 기기 이름 + 안 읽은 알림 수) + 사업 상자 여섯(최근 알림 세 덩어리씩 — 2026-10-18, 그 전 두 줄)이다(2026-10-17 사용자 "제목을 처리할 일이 아니라 알림으로 ·
전체 내용을 최근 알림 내용으로" — 그 전에는 할 일 상자, 2026-10-16). 서버가 주는 자료는 /api/items 의 feed(설계 docs/exec-plans/2026-10-17-notice-feed-design.md)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "urgent_board" / "static" / "board.js"
NODE = shutil.which("node") or shutil.which("node", path="/opt/homebrew/bin")

HARNESS = """
const vm = require("vm"), fs = require("fs");
// 화면 객체 흉내: id 마다 같은 것을 돌려주어 그린 결과(글자·클래스)를 볼 수 있다
const made = {};
const node = () => ({ textContent: "", className: "", dataset: {}, children: [], classList: { contains(c) { return false; }, add() {} },
                      style: { setProperty() {} }, replaceChildren(...k) { this.children = k; }, append(...k) { this.children.push(...k); },
                      addEventListener() {}, querySelectorAll: () => [], querySelector: () => null });
const byId = id => (made[id] = made[id] || node());
const env = { reloads: 0, storage: {} };
const ctx = vm.createContext({
  document: { getElementById: byId, createElement: node, querySelectorAll: () => [], fonts: null, documentElement: { dataset: { v: "v1" } } },
  location: { reload() { env.reloads += 1; } },
  sessionStorage: { getItem: k => env.storage[k] ?? null, setItem(k, v) { env.storage[k] = String(v); } },
  fetch: (...a) => env.fetch ? env.fetch(...a) : Promise.reject(new Error("no network")),
  AbortController, setInterval() {}, setTimeout, clearTimeout, clearInterval() {}, console: { error() {}, log: console.log }, Date, env, made });
const [file, expr] = process.argv.slice(-2);          // node -e 는 스크립트 경로 칸이 없다
vm.runInContext(fs.readFileSync(file, "utf8"), ctx);
Promise.resolve(vm.runInContext(expr, ctx)).then(v => { console.log(JSON.stringify(v)); process.exit(0); });
"""

def run(expr: str):
    if not NODE:
        pytest.skip("node 가 없다")
    r = subprocess.run([NODE, "-e", HARNESS, str(JS), expr], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)

CSS = ROOT / "urgent_board" / "static" / "board.css"
HTML = ROOT / "urgent_board" / "templates" / "board.html"


def quiet(key: str, name: str) -> dict:
    return {"key": key, "name": name, "color": "", "state": "quiet", "n": 0, "urgent": 0, "waiting": 0, "tasks": [], "more": 0}


def todo(what="도면 수정본 확인 회신", who="메일 김과장", when="오늘 14:20", unread=True, due_soon=False, old=False) -> dict:
    return {"what": what, "who": who, "when": when, "unread": unread, "due_soon": due_soon, "old": old}


def card(key="b1", name="사업1", color="yellow", tasks=None, more=0, n=None) -> dict:
    """알림 상자 하나 — n = 상자의 안 읽은 줄 수(서버가 센다 — 보이는 두 줄 밖의 것까지). 안 읽은 것이 있으면 urgent, 읽은 줄만이면 waiting."""
    ts = [todo()] if tasks is None else list(tasks)
    hot = sum(1 for t in ts if t["unread"]) if n is None else n
    state = "urgent" if hot else "waiting" if ts else "quiet"
    return {"key": key, "name": name, "color": color, "state": state, "n": hot, "urgent": hot, "waiting": len(ts) + more - hot, "tasks": ts, "more": more}


CARDS = [quiet(f"b{i}", f"사업{i}") for i in range(1, 6)] + [quiet("etc", "개인 · 기타")]


def view(cards=None, unread=0, today=0, **kw) -> dict:
    """서버가 주는 자료 — 탭5 는 feed(알림 상자 · 수)를 그린다."""
    return {"feed": {"cards": CARDS if cards is None else cards, "totals": {"unread": unread, "today": today}},
            "down": [], "display": {}, "checked_at": "15:00", "blind": "", "v": "v1", **kw}


VIEW = view()
URGENT = view([card()] + CARDS[1:], unread=1, today=1)
HEAD = "{label: made.label.textContent, count: made.count.textContent, cls: made.board.className, down: made.down.textContent, hint: made.hint.textContent, checked: document.getElementById('checked').textContent}"


def js(view) -> str:
    return json.dumps(view, ensure_ascii=False)


def shown(view) -> dict:
    """render 한 뒤 머리줄에 무엇이 적혔는지."""
    return run("(j => { render(j); return %s; })(%s)" % (HEAD, js(view)))


def css() -> str:
    return CSS.read_text(encoding="utf-8").replace(" ", "")


# ===== 머리줄 =====
def test_header_is_the_device_name_and_counts_unread_notifications():
    # 사용자 2026-10-17 "제목을 처리할 일이 아니라 알림으로" → "타이틀은 영어로 · 이 기기의 명칭이 될 가능성이 높기에 위트, 정확, 짧은 영단어" → Nudge → 2026-10-18 WideEye(Nudge 는 맥 오픈소스 macadmins/Nudge 와 이름이 겹쳤다 — 사용자가 직접 지은 WideUreyes 를 검토해 읽기 쉬운 WideEye 로).
    # 이름은 늘 그대로, 그 뒤가 상태 — 수는 안 읽은 알림(휴대폰 알림 숫자처럼, 설계 D3)
    out = shown(view([card()] + CARDS[1:], unread=8, today=9))
    assert out["label"] == "WideEye" and out["count"] == "8건" and out["cls"] == "urgent" and out["down"] == "" and out["hint"] == ""
    none = shown(view(today=4))                                                       # 오늘 온 것이 있어도 다 읽었으면
    assert none["label"] == "WideEye" and none["count"] == "없음" and none["cls"] == "quiet"
    first = run("[made.label.textContent, made.count.textContent]")                  # 첫 답이 오기 전에도 이름이 먼저 뜬다(상태는 "확인 중" — 보조색)
    assert first == ["WideEye", "확인 중"] and "main.loading#label,main.loading#count{color:var(--muted)}" in css()
    c = css()
    assert "#count{margin-left:10px;font-weight:800;color:var(--urgent)}" in c       # 건수는 형광주황
    assert "main.quiet#count{color:var(--ok)}" in c                                   # "없음" = 형광초록(전부 확인했고 없다)


def test_first_line_on_the_right_says_how_many_came_today():
    # "확인 HH:MM"은 적지 않는다(2026-10-14) — 지금 시각이 시계로 따로 뜨고, 서버가 답하지 않으면 "마지막 확인 HH:MM"이 고장 줄에 뜬다
    # 안 읽은 수는 왼쪽 머리글이 말한다 — 오른쪽은 오늘 받은 알림 수(설계 D3)
    assert run("checkedText(%s)" % js(VIEW)) == ""
    assert run("checkedText(%s)" % js(view(unread=1, today=2))) == "오늘 2건"
    assert run("checkedText(%s)" % js(view(unread=3, today=0))) == ""


def test_clock_shows_the_time_in_korea_and_is_written_only_when_the_minute_changes():
    # 사용자 2026-10-14: "지금 현재 시간도 어딘가에 뜨게" — 머리줄 맨 오른쪽. 페이지가 직접 센다(서버가 죽어도 간다)
    assert run("[clockText(Date.UTC(2026, 9, 14, 1, 5, 59)), clockText(Date.UTC(2026, 9, 14, 15, 0, 0)), clockText(Date.UTC(2026, 9, 14, 14, 59, 59))]") == ["10:05", "00:00", "23:59"]
    out = run("""(() => { const real = Date.now; let t = Date.UTC(2026, 9, 14, 1, 5, 10); Date.now = () => t;
      const el = document.getElementById("clock"); let writes = 0;
      const shown = () => el.children.map(k => k.className + ":" + k.textContent).join(" ");
      el.replaceChildren = (...k) => { writes += 1; el.children = k; };
      clock(); t += 1000; watch(); t += 20000; watch(); const before = [writes, shown()];
      t += 40000; watch(); watch(); Date.now = real; return [before, [writes, shown()]]; })()""")
    assert out == [[1, "date:2026-10-14(수) time:10:05"], [2, "date:2026-10-14(수) time:10:06"]]   # 1초마다 불려도 쓰는 것은 분이 바뀔 때 한 번
    html = HTML.read_text(encoding="utf-8")
    assert 'id="clock"' in html and html.index('id="info"') < html.index('id="clock"')      # 머리줄의 맨 오른쪽
    c = css()
    assert "#clock{flex:none;font-size:var(--fs-5);font-weight:500" in c and "#info{flex:1;min-width:0" in c      # 머리글과 같은 크기 · 가늘게 · 보조 줄이 남는 폭을 쓴다
    assert "#clock.date{font-size:var(--fs-2)" in c                                 # 날짜는 시각 왼쪽에 작게(사용자 2026-10-17)
    src = JS.read_text(encoding="utf-8")
    assert "clock(); tick();" in src                                                  # 서버의 첫 답을 기다리지 않고 바로 뜬다


def test_clock_keeps_going_when_the_server_is_gone():
    out = run("""(() => { render(%s); lastChecked = "15:00"; lost(); clock();
      return {clock: document.getElementById("clock").children.map(k => k.textContent.length), label: made.label.textContent + " " + made.count.textContent, down: made.down.textContent}; })()""" % js(VIEW))
    assert out["clock"] == [13, 5] and out["label"] == "WideEye 확인 불가" and "마지막 확인 15:00" in out["down"]      # 날짜 · 시각과 마지막 확인이 나란히 — 얼마나 됐는지 보인다


def test_date_is_the_korean_day_with_its_weekday():
    # 사용자 2026-10-17 "우측상단에 '2026-07-17(일)' 뜨고 옆에 시간이 뜨도록" · "날짜가 없으니.. 헷갈리네" — 한국 시각으로 날이 바뀐다
    assert run("[dateText(Date.UTC(2026, 9, 17, 0, 0)), dateText(Date.UTC(2026, 9, 17, 14, 59, 59)), dateText(Date.UTC(2026, 9, 17, 15, 0)),"
               " dateText(Date.UTC(2026, 6, 18, 3, 0)), dateText(Date.UTC(2026, 11, 31, 15, 0))]") == \
        ["2026-10-17(토)", "2026-10-17(토)", "2026-10-18(일)", "2026-07-18(토)", "2027-01-01(금)"]


def test_quiet_board_does_not_claim_all_clear_when_collectors_are_down():
    # 실측(그림으로 뜬 화면, 2026-10-13): 수집기가 전부 멈췄는데 화면 한가운데 초록색 "긴급 없음" — 가장 나쁜 고장("죽었는데 긴급 없음")
    ok = shown(VIEW); assert ok["count"] == "없음" and "blind" not in ok["cls"] and ok["down"] == ""
    alld = shown({**VIEW, "down": ["문자", "카톡", "메일 5계정"], "blind": "all"})
    assert alld["label"] == "WideEye" and alld["count"] == "확인 불가" and "blind-all" in alld["cls"] and alld["down"] == "문자 · 카톡 · 메일 5계정 수집 중단"
    some = shown({**VIEW, "down": ["카톡"], "blind": "some"})
    assert some["label"] == "WideEye" and some["count"] == "없음" and "blind-some" in some["cls"] and some["down"] == "카톡 수집 중단"
    urgent = shown(view([card()] + CARDS[1:], unread=2, down=["카톡"], blind="some"))
    assert urgent["count"] == "2건" and urgent["down"] == "카톡 수집 중단"            # 안 읽은 알림이 있으면 건수는 그대로, 멈춘 것은 오른쪽에
    stale = shown({**URGENT, "down": ["문자", "카톡"], "blind": "all"})
    assert stale["label"] == "WideEye" and stale["count"] == "1건" and "수집 중단" in stale["down"]     # 전부 멈췄어도 남아 있는 알림은 보인다
    # 그림으로 확인(2026-10-14): 머리줄은 "확인 불가"인데 상자 여섯이 모두 "확인할 것 없음" — 상자도 확인하지 못했다고 말해야 한다
    assert {c["sum"] for c in drawn({**VIEW, "down": ["문자", "카톡"], "blind": "all"})} == {"확인 불가"}
    assert {c["sum"] for c in drawn({**VIEW, "down": ["카톡"], "blind": "some"})} == {"알림 없음"}      # 일부만 멈췄으면 머리줄이 무엇이 멈췄는지 말한다
    c = css()
    assert "main.quiet.blind-some#count{color:var(--muted)}" in c                     # 일부만 멈췄으면 "없음"에서 초록을 뺀다
    assert "main.quiet.blind-all#count,main.lost#count{color:var(--urgent)}" in c     # "확인 불가"는 주황(이름 "WideEye"는 흰색 그대로)
    assert "background:var(--urgent);color:var(--bg)" in c.split("#down{")[1].split("}")[0]      # 고장 줄은 주황 면(검수: 주황 글자면 긴급 꼬리표와 가려지지 않는다)
    # "확인 불가" 옆에 "확인 15:00"을 두지 않는다 — 서로 어긋난 말이다
    assert run("checkedText(%s)" % js(view(unread=3, today=3, blind="all"))) == ""
    assert run("checkedText(%s)" % js(view(unread=3, today=3, blind="some"))) == "오늘 3건"


def test_board_says_how_many_it_left_out_because_claude_handled_them():
    # 검수(2026-10-13): Claude 와 다룬 것이 긴급을 빼도 화면에 아무 표시가 없었다 — 잘못 빠져도 초록 "없음"만 보였다
    out = shown({**VIEW, "handled_shown": 2})
    assert out["hint"] == "Claude 와 다룬 것 2건 뺌" and "blind" not in out["cls"] and out["down"] == ""
    assert shown(VIEW)["hint"] == ""
    who = shown({**VIEW, "handled_shown": 4, "handled_who": ["가", "나", "다", "라"]})
    assert who["hint"] == "Claude 와 다룬 것 4건 뺌 · 가 외 3명"                        # 건수만으로는 무엇이 빠졌는지 모른다 — 보낸 사람을 적는다
    assert shown({**VIEW, "handled_shown": 1, "handled_who": ["가"]})["hint"] == "Claude 와 다룬 것 1건 뺌 · 가"
    # 그림으로 본 것: 긴 이름이 한 줄을 다 차지해 "외 N명"이 잘려 나갔다 — 이름을 줄이고 수는 남긴다
    assert run("whoText(%s)" % js(["한국환경연구원 정책연구실 담당", "나"])) == "한국환경연구원… 외 1명"
    # 오른쪽 둘째 줄은 하나뿐이다: 멈춘 것이 먼저이고 다룬 것은 그 끝에 붙는다
    both = shown({**VIEW, "down": ["카톡"], "blind": "some", "handled_shown": 2, "handled_who": ["가"]})
    assert both["down"] == "카톡 수집 중단 · 다룬 것 2건 뺌"
    assert "#down:not(:empty)~#hint{display:none}" in css()
    a = {**VIEW, "handled_shown": 1, "handled_who": ["가"]}; b = {**a, "handled_who": ["나"]}
    assert run("shape(%s) !== shape(%s)" % (js(a), js(b))) is True                    # 사람이 바뀌면 다시 그린다
    html = HTML.read_text(encoding="utf-8")
    assert 'id="hint"' in html and 'class="quiet loading"' in html                    # 처음 "확인 중"은 초록이 아니다
    assert "main.loading#label,main.loading#count{color:var(--muted)}" in css()      # 이름 · "확인 중" 모두 보조색


# ===== 상자 =====
DRAWN = """made.cards.children.map(c => ({cls: c.className, name: c.children[0].children[0].textContent,
  num: c.children[0].children.length > 1 ? c.children[0].children[1].textContent + "/" + c.children[0].children[1].className : null,
  lines: c.children[2].children.map(t => [t.className, t.children[0].textContent, t.children[1].textContent,
                                          t.children[2].children.map(x => x.className + ":" + x.textContent).join("|")]),
  sum: c.children[2].children.length ? "" : c.children[2].textContent}))"""


def drawn(view) -> list:
    return run("(j => { render(j); return %s; })(%s)" % (DRAWN, js(view)))


def test_six_cards_are_drawn_in_the_order_given():
    cards = [card("b1", "가", "yellow", tasks=[todo("연락처 회신", "메일 김", "오늘 14:20", unread=True),
                                              todo("수하물 안내", "메일 이", "10/12 09:30", unread=False)], more=1, n=2),
             card("b2", "나", "violet", tasks=[todo("일정 공유드립니다", "카톡 박", "어제 10:00", unread=False),
                                              todo("자료 잘 받았습니다", "텔레그램 최", "10/11 11:00", unread=False)])] + CARDS[2:]
    out = drawn(view(cards, unread=2, today=1))
    assert [c["name"] for c in out] == ["가", "나", "사업3", "사업4", "사업5", "개인 · 기타"]     # 건수에 따라 자리를 바꾸지 않는다
    assert out[0]["cls"] == "card urgent c-yellow" and out[0]["num"] == "2/num"                  # 안 읽은 줄 수(보이는 두 줄 밖의 것까지 — 서버가 센다) — 주황
    assert out[0]["lines"] == [["todo unread", "●", "연락처 회신", "who:메일 김|when:오늘 14:20"],
                               ["todo", "●", "수하물 안내", "who:메일 이|when:10/12 09:30"]]      # 표시는 늘 ● — 주황 안 읽음 · 흰색 읽음
    assert out[1]["cls"] == "card waiting c-violet" and out[1]["num"] is None and len(out[1]["lines"]) == 2     # 읽은 알림만 — 수 없이 ○ 두 줄이 남는다(D2)
    assert out[2] == {"cls": "card quiet", "name": "사업3", "num": None, "lines": [], "sum": "알림 없음"}      # 조용한 상자는 비워 두지 않는다
    c = css()
    assert ".todo.unread.dot{color:var(--urgent)}" in c and ".dot{color:var(--fg)}" in c and ".todo.awaiting.dot{color:var(--muted)}" in c and ".todo.old.dot{color:var(--faint)}" in c and ".when.due{color:var(--urgent);font-weight:700}" in c
    assert ".num.read{color:var(--fg)}" in c and ".todo.old.what{color:var(--muted)" in c
    assert ".rule{flex:none;width:56px;height:4px;border-radius:2px;background:var(--biz,var(--muted))}" in c      # 사업 색은 이름 밑줄에
    assert ".card.urgent{border:2pxsolidcolor-mix(insrgb,var(--urgent)70%,var(--line));padding:7px9px}" in c      # 주황빛 테두리(면은 채우지 않는다) · 선이 굵어진 만큼 여백 1px 덜(보통 8px 10px — D14)
    assert "white-space:nowrap" in c.split(".what{")[1].split("}")[0] and "text-overflow:ellipsis" in c.split(".what{")[1].split("}")[0]


def test_only_what_is_shown_triggers_a_redraw():
    later = {**URGENT, "checked_at": "15:01"}                                         # 확인 시각만 바뀌었다 — 다시 그리지 않는다
    other = view([card(tasks=[todo(when="오늘 14:21")])] + CARDS[1:], unread=1, today=1)
    more = view([card()] + CARDS[1:], unread=2, today=1)
    expr = "(([a, b, c, d]) => [shape(a) === shape(b), shape(a) === shape(c), shape(a) === shape(d)])(%s)" % js([URGENT, later, other, more])
    assert run(expr) == [True, False, False]


def test_fewer_than_six_cards_still_fill_the_screen():
    # 진행 사업이 다섯 미만이면 상자가 여섯이 안 된다(서버는 진행 사업 + 여섯째만 준다) — 빈 칸을 남기지 않게 칸 수를 맞춘다
    assert run("(j => { render(j); return made.cards.className; })(%s)" % js(VIEW)) == "n6"
    assert run("(j => { render(j); return made.cards.className; })(%s)" % js(view(CARDS[:4]))) == "n4"
    c = css()
    assert "#cards.n4{grid-template-columns:repeat(2,minmax(0,1fr))}" in c and "#cards.n1,#cards.n2,#cards.n3{grid-template-rows:minmax(0,1fr)}" in c


def test_nothing_moves_on_the_screen():
    # 상자는 늘 여섯이고 요약은 세 줄에 맞춰 온다 — 쪽 넘김도 흐르는 글도 없다(움직임이 없으면 그래픽 프로세스가 쉴 수 있다)
    import re
    assert not re.search(r"@keyframes|animation|transition", CSS.read_text(encoding="utf-8"))
    src = JS.read_text(encoding="utf-8")
    assert "setProperty" not in src and "pageAt" not in src and "pointerdown" not in src     # 길게 눌러 처리됨도 없다(상자 하나가 여러 건이다 — /list 에서)


# ===== 죽은 것을 숨기지 않는다 · 스스로 돌아온다 (2026-10-13 품질 반복에서 얻은 것 — 상자 화면에서도 지킨다) =====
def test_hung_request_counts_as_a_failure_and_stale_screen_says_so():
    # fetch 가 답 없이 걸려 있으면 실패로 세지지 않아, 옛 화면이 확인 시각만 멈춘 채 남았다
    src = JS.read_text(encoding="utf-8")
    assert "AbortController" in src and "FETCH_MS" in src and "signal" in src
    out = run("""(async () => { env.fetch = (u, o) => new Promise((_, no) => o.signal.addEventListener("abort", () => no(new Error("aborted"))));
                  const t0 = Date.now(); FETCH_MS = 30; await tick(); await tick(); await tick();
                  return {fails, down: made.down.textContent, label: made.count.textContent, waited: Date.now() - t0 < 3000}; })()""")
    assert out["fails"] >= 3 and "연결 끊김" in out["down"] and out["label"] == "확인 불가" and out["waited"]
    stale = run("(() => { render(%s); lastOk = Date.now() - 999000; watch(); return %s; })()" % (js(URGENT), HEAD))
    assert "연결 끊김" in stale["down"] and stale["label"] == "WideEye" and stale["count"] == "확인 불가"      # 낡은 건수를 남기지 않는다


def test_page_reloads_itself_when_the_screen_code_changes_but_not_in_a_loop():
    # 화면 코드(board.js·css)를 고쳐도 창을 다시 띄워야 반영됐다 — 서버가 알려 주는 판(v)이 달라지면 스스로 새로 읽는다
    resp = lambda v: "Promise.resolve({ok: true, json: () => Promise.resolve(%s)})" % js({**VIEW, "v": v})
    same = run("(async () => { env.fetch = () => %s; await tick(); return env.reloads; })()" % resp("v1")); assert same == 0
    changed = run("(async () => { env.fetch = () => %s; await tick(); await tick(); await tick(); return env.reloads; })()" % resp("v2")); assert changed == 1     # 한 번만


def test_render_error_and_an_old_server_are_shown_instead_of_a_blank_screen():
    # 서버가 상자 자료를 주지 않으면(화면만 새것이고 서버가 옛것일 때) 빈 화면이 아니라 "화면 오류"
    old = {"v": "v1", "checked_at": "15:00", "items": [], "counts": {"urgent": 0, "waiting": 0}, "down": []}
    out = run("(async () => { env.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve(%s)}); await tick(); return made.down.textContent; })()" % js(old))
    assert "화면 오류" in out and "상자 자료 없음" in out
    back = run("""(async () => { env.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve(%s)}); await tick();
                   env.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve(%s)}); await tick(); return %s; })()""" % (js(old), js(VIEW), HEAD))
    assert back["down"] == "" and back["count"] == "없음"                              # 다음 회차에 제대로 오면 스스로 돌아온다


def test_lost_board_is_not_green():
    # 실측(창이 준 그림, 서버를 얼렸을 때): "확인 불가"가 초록색으로 떴다 — 초록은 "전부 확인했고 없다"에만 쓴다
    out = run("(() => { render(%s); lastChecked = '15:00'; lost(); return %s; })()" % (js(VIEW), HEAD))
    assert " lost" in out["cls"] and out["label"] == "WideEye" and out["count"] == "확인 불가" and out["checked"] == "" and "마지막 확인 15:00" in out["down"]
    back = run("(() => { render(%s); lost(); lost(); render(%s); return made.board.className; })()" % (js(VIEW), js(VIEW)))
    assert "lost" not in back                                                         # 다시 받으면 돌아온다
    assert "main.lost#cards{opacity:" in css()                                        # 끊긴 동안 남아 있는 상자는 흐리게(낡은 것임을 보인다)
    # 이름 "WideEye"가 붙어 고장 줄 자리가 좁아졌다 — 끊김 · 전부 멈춤에서는 두 줄까지 접어 "마지막 확인 15:00"이 잘리지 않게(그림으로 확인, 2026-10-17)
    assert "main.lost#down:not(:empty),main.blind-all#down:not(:empty){white-space:normal;" in css() and "-webkit-line-clamp:2" in css()


def test_lost_while_partly_blind_still_says_so_in_orange():
    # 검수: 일부 수집기가 멈춘 채 서버와 끊기면 회색 "확인 불가"만 남고 끊김 줄이 숨었다
    out = run("(() => { render(%s); lastChecked = '15:00'; lost(); return %s; })()" % (js({**VIEW, "down": ["카톡"], "blind": "some"}), HEAD))
    assert "blind" not in out["cls"] and " lost" in out["cls"] and "연결 끊김" in out["down"]
    gone = run("(() => { render(%s); lost(); return made.hint.textContent; })()" % js({**VIEW, "handled_shown": 2, "handled_who": ["가"]}))
    assert gone == ""                                                                 # 끊긴 동안에는 "다룬 것 N건 뺌"도 낡은 정보다(검수 2026-10-14)


def test_late_answer_from_an_older_request_does_not_overwrite_a_newer_one():
    # 검수 재현: 겹친 요청 둘이 순서가 바뀌어 끝나면 낡은 자료("1건")가 화면에 남았다
    old, new = {**URGENT, "checked_at": "10:00"}, view(checked_at="10:01")
    expr = """(async () => { let n = 0;
      env.fetch = () => { n += 1; const mine = n; return new Promise(ok => setTimeout(() => ok({ok: true, json: () => Promise.resolve(mine === 1 ? %s : %s)}), mine === 1 ? 60 : 5)); };
      const a = tick(), b = tick(); await Promise.all([a, b]);
      return {count: made.count.textContent, checked: made.checked.textContent}; })()""" % (js(old), js(new))
    assert run(expr) == {"count": "없음", "checked": ""}


def test_waking_up_does_not_flash_lost_before_the_first_answer():
    # 숨어 있다 보일 때 오래됨 판정이 먼저 돌아 "끊김"이 잠깐 번쩍일 수 있었다 — 깨어난 직후에는 첫 답을 기다린다
    out = run("""(async () => { render(%s); lastOk = Date.now() - 999000;
      env.fetch = () => new Promise(ok => setTimeout(() => ok({ok: true, json: () => Promise.resolve(%s)}), 30));
      const p = woke(); watch(); const during = made.down.textContent; await p; watch();
      return {during, after: made.down.textContent, count: made.count.textContent}; })()""" % (js(VIEW), js(VIEW)))
    assert "끊김" not in out["during"] and "끊김" not in out["after"] and out["count"] == "없음"


def test_wake_grace_is_not_extended_while_the_display_keeps_flapping():
    # 화면이 붙었다 빠지기를 되풀이하면 깰 때마다 유예가 늘어 "끊김"이 끝없이 미뤄질 수 있었다 — 한 번 주면 1분 동안 다시 주지 않는다
    out = run("""(() => { env.fetch = () => new Promise(() => {}); const real = Date.now; let t = real();
      Date.now = () => t; woke(); const first = graceUntil; t += 5000; woke(); const second = graceUntil;
      t = first + GRACE_REST_MS + 1; woke(); const third = graceUntil; Date.now = real;
      return {same: first === second, later: third > first}; })()""")
    assert out == {"same": True, "later": True}


def test_the_window_can_still_ask_the_page_whether_it_is_alive():
    # 창(tools/kiosk_window.py)은 lastOk · tick · woke 라는 이름으로 페이지에 묻는다 — 화면을 다시 써도 이 이름은 그대로여야 한다
    src = JS.read_text(encoding="utf-8")
    assert "lastOk = 0" in src and "async function tick()" in src and "function woke()" in src
    assert run("[typeof lastOk, typeof tick, typeof woke]") == ["number", "function", "function"]
    window = (ROOT / "tools" / "kiosk_window.py").read_text(encoding="utf-8")
    assert "typeof lastOk" in window and "typeof woke" in window


# ===== 2026-10-14 — 탭5 창의 그래픽 프로세스가 밤새 2.4GB 까지 샜다(웹 글꼴 사본이 1분에 하나씩) =====
def test_pages_prefer_the_installed_font_over_the_web_font():
    # 깔린 글꼴은 이름으로만 건네져 쌓일 것이 없다. 웹 글꼴이 앞에 오면 탭5 창에서 다시 샌다 — 순서를 지킨다
    for name in ("board.css", "list.css"):
        text = (ROOT / "urgent_board" / "static" / name).read_text(encoding="utf-8")
        body = next(l for l in text.splitlines() if l.startswith("body{") and "font-family" in l)
        stack = body.split("font-family:")[1].split(";")[0]
        assert stack.index("Pretendard,") < stack.index('"Pretendard Variable"'), name
        assert '@font-face{font-family:"Pretendard Variable"' in text                # 깔려 있지 않은 기기를 위해 남겨 둔다


def test_the_checked_line_is_not_rewritten_when_it_says_the_same_thing():
    out = run("""(async () => { env.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve(%s)});
      await tick(); let writes = 0; const el = made.checked; let v = el.textContent;
      Object.defineProperty(el, "textContent", { get: () => v, set: x => { writes += 1; v = x; } });
      await tick(); await tick(); return {writes, text: v}; })()""" % js(view(unread=1, today=2)))
    assert out["writes"] == 0 and out["text"] == "오늘 2건"


def test_book_error_is_said_and_a_half_new_server_is_a_screen_error():
    # 검토 I3: 장부 고장은 "할 일 장부 오류"(수집 중단이 아니다). 작은 것 ⑥: 알림 상자는 있는데 수(feed.totals.unread)가 없으면 "화면 오류",
    # 알림을 주지 않는 옛 서버(할 일 상자 cards 만 준다 — 화면 코드만 새것일 때)도 "화면 오류"
    out = run("(() => { render(%s); return %s; })()" % (js({**VIEW, "book_error": True, "blind": "some"}), HEAD))
    assert out["down"] == "할 일 장부 오류"
    half = {**VIEW, "feed": {"cards": CARDS, "totals": {"tasks": 1}}}
    old = {"cards": CARDS, "totals": {"tasks": 0, "unread": 0}, "down": [], "checked_at": "15:00", "blind": "", "v": "v1"}
    for bad in (half, old):
        got = run("(async () => { env.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve(%s)}); await tick(); return made.down.textContent; })()" % js(bad))
        assert "화면 오류" in got


def test_awaiting_lines_keep_their_own_mark_if_ever_drawn():
    # 답 기다림(내가 보낸 것)은 탭5 에 오지 않는다(2026-10-17 — 맥의 목록에만, 알림은 받은 것만). 그리는 틀은 표시를 그대로 안다(회색 ●)
    wait = {**todo("배차 재확인", "메일 도하람", "오늘 11:08", unread=False), "awaiting": True}
    out = drawn(view([card("b1", "가", "yellow", tasks=[wait])] + CARDS[1:]))
    assert out[0]["lines"] == [["todo awaiting", "●", "배차 재확인", "who:메일 도하람|when:오늘 11:08"]]
    assert run("checkedText(%s)" % js({**view(today=1), "totals": {"tasks": 2, "unread": 1, "awaiting": 3}})) == "오늘 1건"      # 할 일 쪽 수는 탭5 에 적지 않는다
    assert ".todo.awaiting" in css()



def test_source_tag_is_drawn_apart_from_the_text_in_its_own_color():
    # 사용자 2026-10-17 "'메일/팀즈/문자/텔레그램'을 본문 텍스트와 색상 다르게 할 것. 헤갈림" — 둘째 줄 맨 앞의 작은 판(.src)
    line = {**todo("회의 자료 공유드립니다", "김과장", "오늘 14:20", unread=True), "src": "텔레"}
    out = drawn(view([card(tasks=[line])] + CARDS[1:], unread=1))
    assert out[0]["lines"] == [["todo unread", "●", "회의 자료 공유드립니다", "src:텔레|who:김과장|when:오늘 14:20"]]
    src = css().split(".src{")[1].split("}")[0]
    assert src == "flex:none;color:var(--src)"                                       # 글자 색만(같은 날 "굵게하고 키우니.. 본문내용이 안보이게 됨. 출처 색상만 바꾸고")
    assert "--src:#c9b48a" in css()                                                   # 사업 색 · 주황 · 보조 회색과 다른 모래색
    when = css().split(".when{")[1].split("}")[0]
    assert "font-size:var(--fs-when)" in when and "letter-spacing:-" in when          # 받은 때는 작게 · 좁게 — 보낸 사람이 더 보이게
    assert "--fs-0:15px" in css() and "--fs-when:13px" in css()                        # 둘째 줄 15px 보다 작게(D14 2026-10-18 — 그 전 14px)
    plain = drawn(view([card(tasks=[todo("일정 답하기", "카톡 박", "어제 10:00", unread=False)])] + CARDS[1:]))
    assert plain[0]["lines"][0][3] == "who:카톡 박|when:어제 10:00"                                           # 꼬리표가 없는 자료(옛 서버 · 할 일 줄)도 그대로 그린다
