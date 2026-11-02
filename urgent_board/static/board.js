// 긴급현황판 화면(탭5): 머리줄("WideEye N건" — 기기 이름 + 안 읽은 알림 수) + 사업 상자 여섯 — 상자마다 최근 알림 세 덩어리(● 주황 안 읽음 · 흰색 읽음, D14 2026-10-18 — 그 전 둘).
// 이름은 늘 "WideEye"(와이드아이 — 넓게 보는 눈, 2026-10-18 사용자 결정. 처음 이름 Nudge 는 같은 이름의 맥 오픈소스(macadmins/Nudge)와 겹쳐 바꿨다). 상태는 그 뒤 한국어: "N건" · "없음" · "확인 불가".
// 2026-10-17 사용자 "제목을 처리할 일이 아니라 알림으로 · 전체 내용을 최근 알림 내용으로" — 서버의 feed 를 그린다(할 일 상자 cards 는 맥의 메뉴 막대 · 목록 몫).
// 10초마다 JSON 을 당겨 보이는 것이 바뀔 때만 다시 그린다(깜빡임 없음). 움직이는 것은 없다 — 쪽 넘김도 흐르는 글도 없다.
// 오른쪽 위의 시계는 이 페이지가 직접 센다(분이 바뀔 때 그 글자만) — 서버가 죽어도 가고, 그때는 "확인 불가 · 마지막 확인 HH:MM"과 나란히 보인다.
// 탭5 는 터치가 맥으로 오지 않는다 — 한 건씩 보기·원문 열기·처리됨은 맥에서 http://127.0.0.1:5091/list
// 죽은 것을 숨기지 않는다: 응답이 없거나 걸려 있으면 "확인 불가 · 연결 끊김", 수집기가 멈췄으면 "없음"을 초록으로 쓰지 않는다, 그리다 오류가 나면 "화면 오류".
// 화면 코드가 바뀌면(서버가 알려 주는 판 v) 스스로 새로 읽는다.
// 창(tools/kiosk_window.py)과의 약속: lastOk(마지막 성공 시각) · tick() · woke() 라는 이름을 바꾸지 않는다 — 창이 이것으로 페이지가 살아 있는지 묻는다.
const POLL_MS = 10000, FAILS_BEFORE_LOST = 3, STALE_MS = 45000, RELOAD_GAP_MS = 60000;
const GRACE_REST_MS = 60000, WHO_NAME_MAX = 7;        // 깨어난 유예를 다시 주기까지 쉬는 시간 · 머리줄에 적는 보낸 사람 이름의 글자 수
const TITLE = "WideEye", QUIET_TEXT = "알림 없음", BLIND_TEXT = "확인 불가";
let FETCH_MS = 8000;                                   // 이 안에 답이 없으면 끊고 실패로 센다(걸린 요청은 스스로 실패하지 않는다)
const VERSION = (document.documentElement.dataset || {}).v || "";      // 이 페이지가 읽힌 때의 화면 코드 판
let lastHash = "", fails = 0, lastChecked = "", lastOk = 0, isLost = false;
let graceUntil = 0;                                    // 깨어난 직후 첫 답을 기다리는 동안은 "오래됨"으로 끊김을 띄우지 않는다
let seq = 0, shownSeq = 0;                             // 요청 번호 — 늦게 끝난 옛 요청의 답은 버린다(낡은 자료가 새 자료를 덮지 않게)
let data = null;
const $ = id => document.getElementById(id);

async function tick() {
  let j;
  const mine = ++seq;
  const stop = new AbortController(), timer = setTimeout(() => stop.abort(), FETCH_MS);
  try {
    const r = await fetch("/api/items", { cache: "no-store", signal: stop.signal });
    if (!r.ok) throw new Error(r.status);
    j = await r.json();
  } catch { if (mine > shownSeq) { fails += 1; if (fails >= FAILS_BEFORE_LOST) lost(); } return; }
  finally { clearTimeout(timer); }
  if (mine < shownSeq) return;                         // 더 새 요청의 답이 이미 그려졌다
  shownSeq = mine;
  if (stale(j.v)) return;
  fails = 0; lastOk = Date.now(); isLost = false; lastChecked = j.checked_at;
  try {
    // 서버가 상자 자료를 주지 않는다(옛 서버가 떠 있다) — 빈 화면으로 두지 않고 알린다
    const f = j.feed;
    if (!f || !Array.isArray(f.cards) || !f.totals || typeof f.totals.unread !== "number") throw new Error("상자 자료 없음");
    const checked = checkedText(j);                      // 같은 글이면 건드리지 않는다 — 쓸데없이 다시 그리게 하지 않는다
    if ($("checked").textContent !== checked) $("checked").textContent = checked;
    const h = shape(j);
    if (h === lastHash) { data = j; return; }
    lastHash = h; render(j);
  } catch (e) { broken(e); }
}

// 화면 코드가 바뀌었으면 새로 읽는다. 판이 계속 어긋나도 되풀이해 읽지 않게 1분에 한 번만.
function stale(v) {
  if (!v || !VERSION || v === VERSION) return false;
  let last = 0;
  try { last = Number(sessionStorage.getItem("reloadedAt") || 0); } catch { /* 저장소가 없으면 그냥 읽는다 */ }
  if (Date.now() - last < RELOAD_GAP_MS) return false;
  try { sessionStorage.setItem("reloadedAt", Date.now()); } catch { /* 위와 같음 */ }
  location.reload();
  return true;
}

// 그리다 오류가 났다 — 옛 화면을 조용히 남겨 두지 않는다. 다음 회차에 다시 그려 본다
function broken(e) {
  lastHash = "";
  $("down").textContent = "화면 오류 · " + String((e && e.message) || e).slice(0, 40);
  console.error(e);
}

// 다시 그릴지를 가르는 열쇠 — 화면에 보이는 것 전부(알림 상자 · 줄 · 건수 · 멈춘 것 · 뺀 것). 확인 시각은 뺀다(분마다 바뀐다)
function shape(j) { return JSON.stringify([j.feed, j.down, j.blind || "", !!j.book_error, j.handled_shown || 0, j.handled_who || []]); }

// 오른쪽 위 첫 줄: 오늘 받은 알림의 수(있을 때만 — 안 읽은 수는 왼쪽 머리글이 말한다)
// 수집기가 전부 멈췄으면 적지 않는다 — "확인 불가" 옆의 "확인 15:00"은 서로 어긋난 말이다(서버는 살아 있지만 확인한 것이 없다)
// "확인 HH:MM"은 적지 않는다 — 지금 시각이 따로 뜨고(시계), 서버가 답하지 않으면 "마지막 확인 HH:MM"이 고장 줄에 뜬다
function checkedText(j) {
  if (j.blind === "all") return "";
  const t = ((j.feed || {}).totals || {}).today || 0;
  return t > 0 ? "오늘 " + t + "건" : "";
}

// 지금 시각 "HH:MM"(한국 시각 — 서버가 적는 시각과 같은 기준. 이 맥의 시간대 설정에 기대지 않는다)
function clockText(ms) { return new Date(ms + 9 * 3600 * 1000).toISOString().slice(11, 16); }

// 오늘 날짜 "YYYY-MM-DD(요일)"(한국 시각) — 시각 왼쪽에 작게(사용자 2026-10-17 "날짜가 없으니.. 헷갈리네")
function dateText(ms) {
  const d = new Date(ms + 9 * 3600 * 1000);
  return d.toISOString().slice(0, 10) + "(" + "일월화수목금토"[d.getUTCDay()] + ")";
}

// 시계를 맞춘다. 글자가 같으면 건드리지 않는다 — 1초마다 불려도 실제로 쓰는 것은 분이 바뀔 때 한 번뿐이다
function clock() {
  const c = $("clock"), now = Date.now(), d = dateText(now), t = clockText(now);
  if (!c || c.dataset.at === d + " " + t) return;
  c.dataset.at = d + " " + t;
  const ds = document.createElement("span"), ts = document.createElement("span");
  ds.className = "date"; ds.textContent = d;
  ts.className = "time"; ts.textContent = t;
  c.replaceChildren(ds, ts);
}

// 서버가 응답하지 않을 때: 건수도 "없음"도 그대로 두면 거짓이 된다 — "확인 불가"로 바꾸고 상자는 흐리게 남긴다(낡은 것임을 보인다)
function lost() {
  lastHash = ""; isLost = true;
  // 수집기 상태 표시(blind-*)도 뗀다 — 끊긴 동안에는 그것도 낡은 정보다. 다시 받으면 render 가 새로 쓴다
  const b = $("board"); b.className = b.className.replace(/ (blind-\w+|loading)/g, "").replace(/ lost\b/g, "") + " lost";
  $("label").textContent = TITLE; $("count").textContent = BLIND_TEXT;      // 이름은 그대로, 상태가 "확인 불가"(주황 — board.css)
  $("down").textContent = "현황판 연결 끊김" + (lastChecked ? " · 마지막 확인 " + lastChecked : "");
  $("checked").textContent = "";                                    // 마지막 확인 시각은 끊김 줄에 한 번만
  $("hint").textContent = "";                                       // "다룬 것 N건 뺌"도 낡은 정보다(검수 2026-10-14)
}

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

// 새 자료가 왔을 때: 머리줄을 쓰고 상자 여섯을 그린다
function render(j) {
  data = j;
  const n = j.feed.totals.unread || 0, blind = j.blind || "";            // n = 안 읽은 알림. blind: "all" = 수집기가 전부 멈췄다, "some" = 일부 멈췄다
  // 멈춘 것: 수집기("… 수집 중단")와 할 일 장부("할 일 장부 오류" — 장부를 못 읽어 7일 안의 할 일만 보인다)
  const stopped = [j.down.length ? j.down.join(" · ") + " 수집 중단" : "", j.book_error ? "할 일 장부 오류" : ""].filter(Boolean).join(" · ");
  const handled = j.handled_shown > 0 ? j.handled_shown : 0;
  $("board").className = (n > 0 ? "urgent" : "quiet") + (blind ? " blind-" + blind : "");
  // 수집기가 전부 멈췄는데 안 읽은 알림이 없다 — "없음"은 거짓이다. 일부만 멈췄으면 "없음"에서 초록을 빼고(CSS) 무엇이 멈췄는지 오른쪽에 적는다
  const dead = n === 0 && blind === "all";
  $("label").textContent = TITLE;
  $("count").textContent = dead ? BLIND_TEXT : n > 0 ? n + "건" : "없음";
  // 오른쪽 둘째 줄은 하나뿐: 멈춘 것이 먼저(주황), 다룬 것은 그 끝에 짧게. 멈춘 것이 없으면 다룬 것을 보낸 사람과 함께(보조색)
  // Claude 와 다뤄서 뺀 것을 적는 까닭 — 잘못 빠져도 조용히 "없음"이 되지 않게(목록은 맥의 /list)
  $("down").textContent = stopped + (stopped && handled ? " · 다룬 것 " + handled + "건 뺌" : "");
  const who = handled ? whoText(j.handled_who) : "";
  $("hint").textContent = handled ? "Claude 와 다룬 것 " + handled + "건 뺌" + (who ? " · " + who : "") : "";
  draw(j.feed.cards, blind);
}

// 상자 여섯. 차례는 서버가 준 그대로(고정) — 여기서 바꾸지 않는다. 상자 = 이름 줄(이름 · 안 읽은 수) · 사업 색 밑줄 · 알림 세 덩어리(몇 개를 보낼지는 서버 web.FEED_ON_CARD).
// 알림 한 줄 = ●(주황 안 읽음 · 흰색 읽음) · 글(굵게, 한 줄 줄임표) · 둘째 줄(어디 누가 · 받은 때). 그리는 틀은 할 일 줄과 같다(due_soon · old · awaiting 도 그대로 받는다).
// 수집기가 전부 멈췄으면 조용한 상자도 "알림 없음"이라고 하지 않는다 — 확인하지 못한 것이다
function draw(cards, blind) {
  const box = $("cards"), nothing = blind === "all" ? BLIND_TEXT : QUIET_TEXT;
  box.className = "n" + cards.length;                              // 상자가 여섯이 안 되면(진행 사업이 다섯 미만) 칸 수를 맞춘다(CSS)
  box.replaceChildren(...cards.map(c => {
    const card = el("article", "card " + c.state + (c.color ? " c-" + c.color : ""));
    const top = el("div", "top"); top.append(el("h2", "name", c.name));
    if (c.n > 0) top.append(el("b", c.state === "urgent" ? "num" : "num read", c.n));
    const todos = el("div", "todos");
    if (!(c.tasks || []).length) todos.textContent = nothing;
    (c.tasks || []).forEach(t => {
      const line = el("div", "todo" + (t.awaiting ? " awaiting" : t.unread ? " unread" : "") + (t.old ? " old" : ""));       // 표시는 늘 ● — 색이 중요도(board.css)
      const meta = el("div", "meta");
      if (t.src) meta.append(el("span", "src", t.src));            // 출처 꼬리표 — 본문과 다른 색(board.css .src, 사용자 2026-10-17 "헤갈림")
      meta.append(el("span", "who", t.who), el("span", t.due_soon ? "when due" : "when", t.when));
      line.append(el("span", "dot", "●"), el("span", "what", t.what), meta);
      todos.append(line);
    });
    card.append(top, el("div", "rule"), todos);
    return card;
  }));
}

// 창이 숨어 있다 다시 보일 때 창(kiosk_window.py)이 부른다: 바로 새 자료를 받고, 그 답이 오기 전에 "끊김"이 번쩍이지 않게 한다
// 유예는 한 번 주면 1분 동안 다시 주지 않는다 — 화면이 붙었다 빠지기를 되풀이해도 "끊김"이 끝없이 미뤄지지 않게
function woke() {
  const now = Date.now();
  if (now > graceUntil + GRACE_REST_MS) graceUntil = now + FETCH_MS + 2000;
  return tick();
}

// 뺀 것을 보낸 사람: 첫 사람만 적고 나머지는 수로("가 외 2명"). 긴 이름은 줄인다 — "외 N명"이 잘려 나가지 않게
function whoText(who) {
  const w = (who || []).filter(Boolean);
  if (!w.length) return "";
  const first = [...String(w[0])];
  return (first.length > WHO_NAME_MAX ? first.slice(0, WHO_NAME_MAX).join("") + "…" : first.join("")) + (w.length > 1 ? " 외 " + (w.length - 1) + "명" : "");
}

// 1초마다 본다: 시계를 맞추고, 마지막 성공이 오래됐으면 — 요청이 걸렸든 타이머가 멎었든 "끊김"으로(깨어난 직후의 첫 답을 기다리는 동안은 빼고)
function watch() {
  clock();
  if (lastOk && !isLost && Date.now() > graceUntil && Date.now() - lastOk > STALE_MS) lost();
}

// 처음 읽힐 때부터 이름을 쓴다(틀 board.html 의 "확인 중"은 첫 답이 오기 전 상태 자리로 — 보조색)
$("label").textContent = TITLE; $("count").textContent = "확인 중";
clock(); tick(); setInterval(tick, POLL_MS); setInterval(watch, 1000);
