// 나 · 화면과 요약 · 업무시간 칸 — 입력과 칩뿐이다.
import { adder, ask, check, chips, field, h, problemsFor } from "./h.js";
import { peopleItems, personLabel, picker } from "./boxes.js";

const SOURCES = [["gmail", "메일"], ["kakao", "카톡"], ["telegram", "텔레그램"], ["imessage", "문자"], ["whatsapp", "왓츠앱"]];

function list(ctx, obj, key, placeholder) {
  obj[key] = obj[key] || [];
  return h("div", {},
    chips(obj[key], { onRemove: i => { obj[key].splice(i, 1); ctx.render(); } }),
    adder(placeholder, v => { if (!obj[key].includes(v)) obj[key].push(v); ctx.render(); }));
}

function num(ctx, obj, key, placeholder) {
  return h("input", { type: "number", min: "0", value: obj[key] ?? "", placeholder: placeholder ?? "", oninput: e => {
    if (e.target.value === "") delete obj[key]; else obj[key] = parseInt(e.target.value, 10);
    ctx.touch();
  } });
}

export function renderMe(ctx) {
  const st = ctx.state, me = st.doc.me = st.doc.me || {};
  st.doc.contacts = st.doc.contacts || [];
  const accounts = st.catalog.accounts || [];
  me.personal_accounts = me.personal_accounts || [];
  const personal = new Set(me.personal_accounts.map(x => x.toLowerCase()));
  return h("div", {},
    h("h2", {}, "나"),
    field("나를 부르는 말(단톡방에서 이 말이 들어 있으면 나를 부른 것으로 친다)", list(ctx, me, "names", "예: 팀장님, 홍길동"), problemsFor(st.problems, "me.names")),
    field("내 메일 주소(내가 보낸 것 · 나에게 온 것을 가른다)", list(ctx, me, "addresses", "me@example.com"), problemsFor(st.problems, "me.addresses")),
    h("div", { class: "field" }, h("span", { class: "label" }, "개인 메일 계정(어느 사업에도 안 걸린 메일은 '개인')"),
      accounts.length ? h("div", { class: "row" }, accounts.map(a => check(a.email, personal.has(a.email.toLowerCase()), v => {
        me.personal_accounts = me.personal_accounts.filter(x => x.toLowerCase() !== a.email.toLowerCase());
        if (v) me.personal_accounts.push(a.email);
        ctx.touch();
      }))) : null,
      chips(me.personal_accounts.filter(x => !accounts.some(a => a.email.toLowerCase() === x.toLowerCase())),
        { onRemove: i => { const rest = me.personal_accounts.filter(x => !accounts.some(a => a.email.toLowerCase() === x.toLowerCase())); me.personal_accounts.splice(me.personal_accounts.indexOf(rest[i]), 1); ctx.render(); } }),
      adder("연결하지 않은 개인 메일 주소", v => { if (!me.personal_accounts.includes(v)) me.personal_accounts.push(v); ctx.render(); })),
    h("div", { class: "field" }, h("span", { class: "label" }, "개인 · 기타로 둘 사람(사업 낱말이 들어 있어도 개인 · 기타 상자로 — 사업에 등록한 방에 쓴 글은 그 사업)"),
      chips(me.personal_senders = me.personal_senders || [], { label: id => personLabel(ctx, id), onRemove: i => { me.personal_senders.splice(i, 1); ctx.render(); } }),
      h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: () => picker(ctx, "개인 · 기타로 둘 사람 고르기", peopleItems(ctx), it => {
        if (!me.personal_senders.includes(String(it.id))) me.personal_senders.push(String(it.id));
        ctx.render();
      }) }, "목록에서 고르기")),
      problemsFor(st.problems, "me.personal_senders").map(p => h("span", { class: "problem" }, p.message))),
    field("내 전화번호 끝 4자리(문자에서 내가 보낸 것 가르기 보조 — 보통 비워 둔다)", list(ctx, me, "phone_tails", "1234")),
    h("h2", {}, "같은 사람의 여러 채널"),
    h("p", { class: "hint" }, "메일로 묻고 카톡으로 답해도 '답함'으로 보게 — 메일 주소 · 전화번호(+8210…) · kakao:카톡 이름 · telegram:텔레그램 이름."),
    st.doc.contacts.map((c, i) => h("div", { class: "biz" }, h("div", { class: "body" },
      field("이름", h("input", { type: "text", value: c.name || "", oninput: e => { c.name = e.target.value; ctx.touch(); } }), problemsFor(st.problems, `contacts[${i}]`)),
      list(ctx, c, "ids", "주소 · 번호 · kakao:이름"),
      h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: () => { st.doc.contacts.splice(i, 1); ctx.render(); } }, "지우기"))))),
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => { st.doc.contacts.push({ name: "새 사람", ids: [] }); ctx.render(); } }, "사람 더하기")));
}

// '화면과 요약'은 2026-10-17 위쪽 버튼으로 바꾸며 둘로 나눴다 — [화면](탭5 · 오래됨 · 답 기다림 · Claude 와 다룬 것) · [요약](Claude 로 보내는 것)
export function renderScreen(ctx) {
  const st = ctx.state, doc = st.doc;
  const disp = doc.display = doc.display || {};
  const hand = doc.handled = doc.handled || {};
  const devices = st.catalog.tab5 || [];
  const now = disp.tab5_usb_serial || "";
  const options = [h("option", { value: "", selected: !now }, "쓰지 않음")]
    .concat(devices.map(d => h("option", { value: d.serial, selected: d.serial === now }, `${d.serial} · ${d.description || d.device}`)));
  if (now && !devices.some(d => d.serial === now)) options.push(h("option", { value: now, selected: true }, `${now}(지금 붙어 있지 않음)`));
  return h("div", {},
    h("h2", {}, "화면"),
    field("오래됨으로 볼 날 수(열린 지 이만큼 지난 할 일은 상자 아래로, 흐리게)", num(ctx, disp, "old_task_days", "14"), problemsFor(st.problems, "display.old_task_days")),
    field("답 기다림으로 볼 날 수(이 날 수 안에 보낸 내 질문 · 요청에 상대 답이 없으면 상자에 회색 ● 로 — 0 이면 끔)", num(ctx, disp, "awaiting_days", "3"), problemsFor(st.problems, "display.awaiting_days")),
    field("탭5(USB 로 붙은 기기에서 고른다 — 바꾸면 탭5 창이 30초 안에 새 값으로 다시 뜬다)",
      h("select", { onchange: e => { disp.tab5_usb_serial = e.target.value; ctx.touch(); } }, options), problemsFor(st.problems, "display.tab5_usb_serial")),
    h("h2", {}, "Claude 와 다룬 것"),
    check("Claude Code 로 다룬 메일을 화면에서 뺀다(세션 기록을 읽기만 — Claude Code 를 쓰는 사람만)", !!hand.claude_sessions, v => { hand.claude_sessions = v; ctx.touch(); }));
}

export function renderSummary(ctx) {
  const st = ctx.state, doc = st.doc;
  const summ = doc.summary = doc.summary || {};
  summ.sources = summ.sources || ["gmail", "kakao", "imessage"];
  return h("div", {},
    h("h2", {}, "요약 설정"),
    check("메시지를 한 줄로 요약한다 — 켜면 화면에 뜰 메시지의 글이 Claude 로 전송된다", !!summ.enabled, v => {
      if (!v) { summ.enabled = false; ctx.touch(); return; }
      ask("요약 켜기", "화면에 뜰 메시지의 글(아래에서 고른 출처만)이 Claude 로 전송된다. 켤까?", "켜기", () => { summ.enabled = true; ctx.render(); });
      ctx.render();
    }),
    h("div", { class: "field" }, h("span", { class: "label" }, "요약에 보내도 되는 출처(빠진 출처는 원문 앞부분이 보인다)"),
      h("div", { class: "row" }, SOURCES.map(([k, name]) => check(k === "telegram" ? `${name}(API 약관상 기본으로 뺀다)` : name, summ.sources.includes(k), v => {
        summ.sources = summ.sources.filter(x => x !== k); if (v) summ.sources.push(k); ctx.touch();
      }))), problemsFor(st.problems, "summary.sources")),
    field("항목 요약 길이(글자)", num(ctx, summ, "max_chars", "60"), problemsFor(st.problems, "summary.max_chars")),
    field("할 일 문구 길이(글자 — 탭5 상자 한 줄)", num(ctx, summ, "task_max_chars", "14"), problemsFor(st.problems, "summary.task_max_chars")),
    field("하루 상한(건)", num(ctx, summ, "max_per_day", "300"), problemsFor(st.problems, "summary.max_per_day")),
    check("Claude 가 할 일 문구 · 기한을 뽑고, 읽은 것 가운데 할 일이 없는 것은 거른다(안 읽은 것에는 쓰지 않는다 · 거른 것은 전체 목록에 남는다)", summ.judge !== false, v => { summ.judge = v; ctx.touch(); }),
    check("규칙(등록 · 낱말)이 못 맞힌 글은 Claude 가 사업도 고른다 — 사업 이름 · 낱말 · 설명이 Claude 로 간다(전체 목록에 'Claude 추정')", summ.pick_business !== false, v => { summ.pick_business = v; ctx.touch(); }),
    field("할 일이 없는 것의 기준", h("textarea", { oninput: e => { summ.skip_when = e.target.value; ctx.touch(); } }, summ.skip_when || "")));
}

export function renderHours(ctx) {
  const st = ctx.state, doc = st.doc;
  const wh = doc.workhours = doc.workhours || {};
  wh.lunch = wh.lunch || ["12:00", "13:00"];
  const rules = doc.rules = doc.rules || {};
  const th = rules.thresholds = rules.thresholds || {};
  const def = (st.ruleDefaults && st.ruleDefaults.thresholds) || {};
  const time = (obj, key, i) => h("input", { type: "time", value: i === undefined ? (obj[key] || "") : (obj[key][i] || ""), oninput: e => {
    if (i === undefined) obj[key] = e.target.value; else obj[key][i] = e.target.value;
    ctx.touch();
  } });
  const dates = (key, label) => {
    wh[key] = wh[key] || [];
    const pick = h("input", { type: "date" });
    return h("div", { class: "field" }, h("span", { class: "label" }, label),
      chips(wh[key], { onRemove: i => { wh[key].splice(i, 1); ctx.render(); } }),
      h("div", { class: "adder" }, pick, h("button", { class: "btn small", type: "button", onclick: () => { if (pick.value && !wh[key].includes(pick.value)) { wh[key].push(pick.value); wh[key].sort(); ctx.render(); } } }, "더하기")),
      problemsFor(st.problems, `workhours.${key}`).map(p => h("span", { class: "problem" }, p.message)));
  };
  return h("div", {},
    h("h2", {}, "업무시간"),
    h("p", { class: "hint" }, "기다린 시간과 '읽고 답 안 한 것'의 문턱은 업무시간으로 센다(점심 · 주말 · 공휴일은 흐르지 않는다)."),
    h("div", { class: "row" }, field("시작", time(wh, "start"), problemsFor(st.problems, "workhours.start")), field("끝", time(wh, "end"), problemsFor(st.problems, "workhours.end"))),
    h("div", { class: "row" }, field("점심 시작", time(wh, "lunch", 0)), field("점심 끝", time(wh, "lunch", 1))),
    problemsFor(st.problems, "workhours.lunch").map(p => h("span", { class: "problem" }, p.message)),
    field("공휴일 나라(KR · 비우면 공휴일을 보지 않는다)", h("input", { type: "text", value: wh.holidays ?? "KR", oninput: e => { wh.holidays = e.target.value.trim(); ctx.touch(); } }), problemsFor(st.problems, "workhours.holidays")),
    dates("extra_holidays", "회사 휴무(종무식 · 창립기념일 …)"),
    dates("extra_workdays", "주말 · 공휴일이어도 일하는 날(행사 운영일)"),
    h("h2", {}, "읽고 답 안 한 것을 대기로 볼 때까지(분)"),
    h("p", { class: "hint" }, "비우면 기본값. 질문 · 요청을 읽고 답하지 않은 채 업무시간으로 이만큼 지나면 대기로 뜬다."),
    h("div", { class: "row" },
      field(`1:1 메일 · 카톡 · 텔레그램(기본 ${def.unanswered_dm_minutes ?? 120})`, num(ctx, th, "unanswered_dm_minutes", String(def.unanswered_dm_minutes ?? 120)), problemsFor(st.problems, "rules.thresholds.unanswered_dm_minutes")),
      field(`단톡(기본 ${def.unanswered_group_minutes ?? 240})`, num(ctx, th, "unanswered_group_minutes", String(def.unanswered_group_minutes ?? 240)), problemsFor(st.problems, "rules.thresholds.unanswered_group_minutes")),
      field(`1:1 문자(기본 ${def.unanswered_sms_minutes ?? 20})`, num(ctx, th, "unanswered_sms_minutes", String(def.unanswered_sms_minutes ?? 20)), problemsFor(st.problems, "rules.thresholds.unanswered_sms_minutes"))));
}
