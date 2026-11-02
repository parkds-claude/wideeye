// 긴급현황판 설정 화면(/settings) — 설계 2026-10-15 제품판 5. 상태는 draft 한 덩어리, 저장은 통째로 PUT(미리 보기를 먼저).
// 입력 칸을 고치면 draft 만 바꾸고 아래 띠의 "바뀐 것 N개"만 고친다(칸의 커서를 잃지 않게). 칩 · 상자처럼 꼴이 바뀌면 그 칸을 다시 그린다.
import { h, modal } from "./h.js";
import { changes, normalize } from "./model.js";
import { DOWN, call, retry } from "./net.js";
import { renderBoxes } from "./boxes.js";
import { renderClaude, renderKakao, renderMail, renderSms, renderTelegram } from "./connect.js";
import { renderMe, renderScreen, renderSummary, renderHours } from "./simple.js";

// 위쪽 한 줄 버튼(사용자 2026-10-17 "각 상세설정은 상단에 버튼으로 갈 것. [메일] [텔레그램] [문자] [카톡] [기타]" · "한 줄에 모두").
// 그전에는 왼쪽 세로 목록에 상자 · 연결 · 나 · 화면과 요약 · 업무시간 다섯이었다
const TABS = [["boxes", "상자", renderBoxes], ["mail", "메일", renderMail], ["telegram", "텔레그램", renderTelegram], ["sms", "문자", renderSms],
              ["kakao", "카톡", renderKakao], ["summary", "요약", ctx => h("div", {}, renderClaude(ctx), renderSummary(ctx))], ["me", "나", renderMe],
              ["screen", "화면", renderScreen], ["hours", "업무시간", renderHours]];
const CONNECT_TABS = new Set(["mail", "telegram", "sms", "kakao", "summary"]);     // 연결 점검(/api/connect)을 보이는 칸
const OLD_TABS = { connect: "mail", display: "screen" };                             // 예전 주소(#connect · #display)도 열린다
const clone = x => JSON.parse(JSON.stringify(x));

// 주소의 #칸이름으로 그 칸을 연다(설정 명령의 `setup connect` 가 #connect 로 연다 — 2026-10-16 AI 와 함께 설정)
const startTab = OLD_TABS[(location.hash || "").slice(1)] || (location.hash || "").slice(1);
const state = { doc: null, saved: null, version: "", problems: [], catalog: { rooms: [], people: [], accounts: [], tab5: [], owners: {} },
                connect: null, tab: TABS.some(t => t[0] === startTab) ? startTab : "boxes", open: new Set(), ruleDefaults: {}, colors: [], top: null, say: "", sayBad: false };

async function api(method, url, body) {
  const r = await call(fetch, method, url, body);
  if (r.status === 401) say("접속 열쇠가 없어요 — 이 쪽을 새로 고침하면 열쇠를 넣는 칸이 나와요.", true);
  else if (r.status === 0) say(r.data.error, true);
  return r;
}

function say(text, bad = false) {
  state.say = text; state.sayBad = bad;
  const el = document.getElementById("say");
  if (el) { el.textContent = text; el.className = "say" + (bad ? " warn" : ""); }
}

// 입력이 바뀌었다 — 아래 띠만 고친다
function touch() {
  const n = changes(state.saved, state.doc);
  const count = document.getElementById("count");
  if (count) { count.textContent = n ? `바뀐 것 ${n}개` : "바뀐 것 없음"; count.className = "count" + (n ? " dirty" : ""); }
  for (const id of ["revert", "save"]) { const b = document.getElementById(id); if (b) b.disabled = n === 0; }
}

function render() {
  const y = window.scrollY;
  const app = document.getElementById("app");
  const tab = TABS.find(t => t[0] === state.tab) || TABS[0];
  const top = [];
  if (state.top && state.top.settings_error) top.push(h("p", { class: "notice bad" }, state.top.settings_error));
  if (state.top && state.top.legacy_config) top.push(h("p", { class: "notice bad" }, "자료 폴더에 설정이 없고 옛 config/ 가 있어요 — tools/migrate.py 로 옮겨 주세요."));
  app.replaceChildren(
    h("div", { class: "wrap" },
      h("h1", {}, "긴급현황판 설정", h("small", {}, "고치면 미리 보고 저장해요 — 10초 안에 현황판에 반영돼요")),
      h("nav", {}, TABS.map(([key, name]) => h("button", { type: "button", class: key === state.tab ? "on" : "", onclick: () => { state.tab = key; history.replaceState(null, "", "#" + key); render(); window.scrollTo(0, 0); } }, name))),
      h("section", { class: "main" }, top, tab[2](ctx))),
    h("div", { class: "footer" }, h("div", { class: "in" },
      h("span", { id: "count", class: "count" }),
      state.problems.length ? h("span", { class: "warn" }, `문제 ${state.problems.length}개`) : null,
      h("span", { id: "say", class: "say" + (state.sayBad ? " warn" : "") }, state.say),
      h("span", { class: "grow" }),
      h("button", { id: "revert", class: "btn", type: "button", onclick: revert }, "되돌리기"),
      h("button", { id: "save", class: "btn primary", type: "button", onclick: preview }, "미리 보고 저장"))));
  touch();
  window.scrollTo(0, y);
}

function revert() {
  state.doc = clone(state.saved); state.problems = []; say("저장된 설정으로 되돌렸다."); render();
}

async function preview() {
  const r = await api("POST", "/api/settings/preview", { doc: state.doc });
  if (r.status === 422) {
    state.problems = r.data.problems || [];
    render(); say(`문제 ${state.problems.length}개 — 주황 글이 붙은 칸을 고친다.`, true);
    return;
  }
  if (!r.ok) { say(r.data.error || "미리 보지 못했다.", true); return; }
  state.problems = [];
  const p = r.data, after = Object.fromEntries((p.after || []).map(b => [b.key, b]));
  const rows = (p.before || []).map(b => {
    const a = after[b.key] || { urgent: 0, waiting: 0, name: b.name };
    const cell = (x, y) => h("td", { class: x === y ? "same" : "" }, x === y ? String(x) : `${x} → ${y}`);
    return h("tr", {}, h("td", {}, b.name === a.name ? b.name : `${b.name} → ${a.name}`), cell(b.urgent, a.urgent), cell(b.waiting, a.waiting));
  });
  (p.after || []).filter(a => !(p.before || []).some(b => b.key === a.key)).forEach(a =>
    rows.push(h("tr", {}, h("td", {}, `(새) ${a.name}`), h("td", {}, String(a.urgent)), h("td", {}, String(a.waiting)))));
  const body = h("div", {},
    h("p", { class: "hint" }, "지금 설정과 고친 설정으로 지난 7일 자료를 판정해 견준 것이다(화면에 뜨는 것과 같은 길)."),
    h("table", { class: "pre" }, h("tr", {}, h("th", {}, "상자"), h("th", {}, "급함"), h("th", {}, "나머지")), rows),
    h("h3", {}, p.moved_total ? `옮겨지는 항목 ${p.moved_total}건` : "옮겨지는 항목 없음"),
    h("ul", { class: "moved" }, (p.moved || []).map(m => h("li", {}, h("span", { class: "who" }, m.sender || "(이름 없음)"), " — ", m.summary || "",
      h("div", { class: "to" }, `${m.from} → ${m.to}`)))),
    p.moved_total > (p.moved || []).length ? h("p", { class: "hint" }, `그 밖 ${p.moved_total - p.moved.length}건`) : null,
    p.resummarize ? h("p", { class: "notice" }, "요약 방식이 바뀌어 저장된 요약을 모두 다시 만든다 — 1~3분 동안 원문 앞부분이 보이고, 하루 상한을 쓴다.") : null,
    (p.warnings || []).length ? h("div", { class: "notice" }, h("b", {}, "알림"), h("ul", { class: "moved" }, p.warnings.map(w => h("li", {}, w.message)))) : null);
  modal("이렇게 바뀐다", body, [{ label: "취소" }, { label: "저장", primary: true, onClick: save }]);
}

async function save(close) {
  const r = await api("PUT", "/api/settings", { doc: state.doc, version: state.version });
  close();
  if (r.status === 409) { say("설정 파일이 그 사이 바뀌었다(다른 창이나 손으로) — 새로 읽은 뒤 다시 고친다.", true); await load(); return; }
  if (r.status === 422) { state.problems = r.data.problems || []; render(); say(`문제 ${state.problems.length}개`, true); return; }
  if (!r.ok) { say(r.status === 0 ? r.data.error : "저장하지 못했다.", true); return; }
  state.version = r.data.version; state.saved = clone(state.doc); state.problems = [];
  say(r.data.applied === "restart" ? "저장했다 — 연결 설정이 바뀌어 엔진이 다시 시작한다(5초쯤)." : "저장했다 — 10초 안에 현황판에 반영된다.");
  render();
}

async function load() {
  const s = await retry(() => api("GET", "/api/settings"));             // 엔진이 다시 시작하는 중이면 몇 초 기다려 본다
  const c = await api("GET", "/api/catalog");
  if (!s.ok) { say(s.status === 0 ? s.data.error : "설정을 읽지 못했다.", true); return; }
  if (state.say === DOWN) say("");                                      // 기다리는 동안 띄운 글을 걷는다
  state.top = s.data; state.doc = normalize(s.data.doc || {}); state.saved = clone(state.doc); state.version = s.data.version;
  state.problems = (s.data.problems || []).filter(p => p.level === "error");
  state.ruleDefaults = s.data.rule_defaults || {}; state.colors = s.data.colors || [];
  if (c.ok) state.catalog = c.data;
  render();
  loadConnect();
}

async function loadConnect() {
  const r = await api("GET", "/api/connect");
  if (r.ok) { state.connect = r.data; if (CONNECT_TABS.has(state.tab)) render(); }
}

// 연결 단추(로그인 · 끊기)는 엔진이 설정 파일을 바로 고친다 — 그 뒤에는 새로 읽는다(고치던 것이 있으면 사라진다고 알린다)
async function reloadAll() {
  const dirty = changes(state.saved, state.doc) > 0;
  await load();
  if (dirty) say("연결이 바뀌어 설정을 새로 읽었다 — 저장하지 않은 고침은 사라졌다.", true);
}

const ctx = { state, api, render, touch, say, loadConnect, reloadAll };
load();
