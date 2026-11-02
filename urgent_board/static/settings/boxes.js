// 상자 칸 — 사업 상자 여섯의 작은 그림, 사업마다 이름 · 색 · 진행 · 메모 · 모을 곳 · 낱말, 확정 전 일, 분류 방식과 분류 시험.
import { adder, ask, check, chips, field, h, modal, problemsFor, when } from "./h.js";
import { COLORS, addRoom, clashes, moveBox, newBox, owners, problemsAt, removeRoom, roomLabel, toggleWatch } from "./model.js";

export const SOURCE_NAME = { kakao: "카톡", telegram: "텔레그램", imessage: "문자", whatsapp: "왓츠앱", gmail: "메일" };     // 엔진의 출처마다 한국어 이름(시험이 본다)
const OWN_SLOTS = 5;

function text(ctx, obj, key, attrs = {}) {
  return h("input", { type: "text", value: obj[key] || "", oninput: e => { obj[key] = e.target.value; ctx.touch(); }, ...attrs });
}

function mini(doc) {
  const active = (doc.boxes || []).filter(b => b.active !== false);
  const own = active.slice(0, OWN_SLOTS);
  const etc = active.length > OWN_SLOTS ? "그 밖" : (doc.etc_name || "개인 · 기타");
  return h("div", {},
    h("div", { class: "mini" },
      own.map(b => h("div", { class: "box" + (b.color ? " c-" + b.color : "") }, h("div", { class: "name" }, b.name || b.key), h("span", { class: "bar-line" }))),
      h("div", { class: "box" }, h("div", { class: "name" }, etc), h("span", { class: "bar-line" }))),
    active.length > OWN_SLOTS ? h("p", { class: "hint" }, `넘친 사업 ${active.length - OWN_SLOTS}개는 '그 밖' 상자에 함께 뜬다.`) : null);
}

function summaryLine(b) {
  const parts = [];
  if ((b.mail_accounts || []).length) parts.push(`메일 ${b.mail_accounts.length}`);
  if ((b.kakao_rooms || []).length) parts.push(`카톡 ${b.kakao_rooms.length}`);
  if ((b.telegram_rooms || []).length) parts.push(`텔레그램 ${b.telegram_rooms.length}`);
  if ((b.senders || []).length) parts.push(`보낸 사람 ${b.senders.length}`);
  parts.push(`낱말 ${(b.keywords || []).length}`);
  return (b.active === false ? "쉬는 중 · " : "") + parts.join(" · ");
}

function boxName(doc, key) {
  const b = (doc.boxes || []).find(x => x.key === key);
  return b ? (b.name || b.key) : key;
}

// 목록에서 고르기 — 방(카톡 · 텔레그램) 또는 사람(보낸 사람). '나' 칸의 개인 · 기타로 둘 사람도 쓴다
export function picker(ctx, title, items, onPick) {
  const search = h("input", { type: "text", placeholder: "찾기" });
  const list = h("ul", { class: "pick" });
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    list.replaceChildren(...items.filter(it => !q || it.label.toLowerCase().includes(q) || String(it.id).includes(q)).slice(0, 200)
      .map(it => h("li", { onclick: () => { onPick(it); close(); } }, h("span", {}, it.label), h("span", { class: "meta" }, it.meta))));
    if (!list.children.length) list.append(h("li", { class: "hint" }, "보이는 것이 없다 — 엔진이 지난 7일에 본 것만 나온다."));
  };
  search.addEventListener("input", draw);
  const close = modal(title, h("div", {}, search, list), [{ label: "닫기" }]);
  draw();
  search.focus();
}

function roomPicker(ctx, box, source) {
  const own = owners(ctx.state.doc);
  const items = (ctx.state.catalog.rooms || []).filter(r => r.source === source).map(r => {
    const owner = own.get(`${source}:${r.chat_id}`);
    return { id: r.chat_id, label: r.name || `번호 ${r.chat_id}`, name: r.name,
             meta: `${r.group ? "단톡" : "1:1"} · 최근 ${when(r.last)} · 글 ${r.count}` + (owner && owner !== box.key ? ` · ${boxName(ctx.state.doc, owner)}에 있음` : "") };
  });
  picker(ctx, `${SOURCE_NAME[source]} 방 고르기`, items, it => { addRoom(box, source, { id: String(it.id), name: it.name || undefined }); ctx.render(); });
}

// 엔진이 지난 7일에 본 사람 — note(p) 는 줄 끝에 덧붙일 말
export function peopleItems(ctx, note = () => "") {
  return (ctx.state.catalog.people || []).map(p => ({ id: p.id, label: p.name && p.name !== p.id ? `${p.name} (${p.id})` : p.id,
    meta: `${SOURCE_NAME[p.source] || p.source} · 최근 ${when(p.last)} · ${p.count}건` + note(p) }));
}

function personPicker(ctx, box) {
  const own = owners(ctx.state.doc);
  const items = peopleItems(ctx, p => {
    const owner = own.get(`sender:${String(p.id).toLowerCase()}`);
    return owner && owner !== box.key ? ` · ${boxName(ctx.state.doc, owner)}에 있음` : "";
  });
  picker(ctx, "보낸 사람 고르기", items, it => {
    box.senders = box.senders || [];
    if (!box.senders.includes(it.id)) box.senders.push(String(it.id));
    ctx.render();
  });
}

export function personLabel(ctx, id) {
  const p = (ctx.state.catalog.people || []).find(x => String(x.id).toLowerCase() === String(id).toLowerCase());
  return p && p.name && p.name !== p.id ? `${p.name} (${id})` : String(id);
}

function rooms(ctx, box, source, i) {
  const f = source === "kakao" ? "kakao_rooms" : "telegram_rooms";
  return h("div", {},
    h("span", { class: "label hint" }, `${SOURCE_NAME[source]} 방`),
    chips(box[f], {
      label: r => roomLabel(r, source, ctx.state.catalog),
      cls: r => (r.watch ? "watching" : ""),
      extra: (r, j) => r.id ? h("button", { class: "x watch", type: "button", title: "나를 부르지 않은 안 읽은 글도 방마다 한 줄로 올린다",
        onclick: () => { toggleWatch(box, source, j); ctx.render(); } }, r.watch ? "지켜봄" : "지켜보기") : null,
      onRemove: j => { removeRoom(box, source, j); ctx.render(); } }),
    problemsAt(ctx.state.problems, `boxes[${i}].${f}`).map(p => h("span", { class: "problem" }, p.message)),
    h("div", { class: "row" },
      h("button", { class: "btn small", type: "button", onclick: () => roomPicker(ctx, box, source) }, "목록에서 고르기"),
      source === "kakao" ? adder("방 이름으로 더하기(번호를 모를 때 — 이름이 바뀌면 안 맞는다)", v => { addRoom(box, "kakao", { name: v }); ctx.render(); }, "이름으로 더하기") : null));
}

function business(ctx, b, i) {
  const st = ctx.state, doc = st.doc;
  const open = st.open.has(i) || problemsAt(st.problems, `boxes[${i}]`).length > 0;
  const taken = new Set((doc.boxes || []).filter((x, j) => j !== i && x.active !== false && x.color).map(x => [x.color, x.name || x.key]).map(([c]) => c));
  const takenBy = c => { const x = (doc.boxes || []).find((y, j) => j !== i && y.active !== false && y.color === c); return x ? (x.name || x.key) : ""; };
  const isNew = !(st.saved.boxes || []).some(x => x.key === b.key);
  const mine = clashes(doc).filter(c => c.a === (b.name || b.key));
  const header = h("header", { onclick: () => { if (st.open.has(i)) st.open.delete(i); else st.open.add(i); ctx.render(); } },
    h("span", { class: "dot" }), h("span", { class: "name" }, b.name || "(이름 없음)"), h("span", { class: "sum" }, summaryLine(b)));
  if (!open) return h("div", { class: "biz" + (b.color ? " c-" + b.color : "") + (b.active === false ? " off" : "") }, header);
  const accounts = st.catalog.accounts || [];
  return h("div", { class: "biz" + (b.color ? " c-" + b.color : "") + (b.active === false ? " off" : "") }, header,
    h("div", { class: "body" },
      problemsAt(st.problems, `boxes[${i}]`).filter(p => !/\.(name|color|key)$/.test(p.path)).map(p => h("span", { class: "problem" }, `${p.path}: ${p.message}`)),
      field("이름", text(ctx, b, "name"), problemsFor(st.problems, `boxes[${i}].name`)),
      isNew ? field("열쇠(영문 소문자 · 숫자 · -, 처음에만 정한다)", text(ctx, b, "key"), problemsFor(st.problems, `boxes[${i}].key`))
            : h("p", { class: "hint" }, `열쇠 ${b.key}`),
      h("div", { class: "field" + (problemsFor(st.problems, `boxes[${i}].color`).length ? " bad" : "") },
        h("span", { class: "label" }, "색"),
        h("div", { class: "colors" }, COLORS.map(c => h("button", { type: "button", class: `c-${c}` + (b.color === c ? " on" : ""),
          disabled: b.active !== false && taken.has(c) && b.color !== c, title: taken.has(c) ? `${takenBy(c)} 가 쓰는 색` : c,
          onclick: () => { b.color = c; ctx.render(); } }))),
        problemsFor(st.problems, `boxes[${i}].color`).map(p => h("span", { class: "problem" }, p.message))),
      check("진행 중(끄면 상자를 차지하지 않는다 — 이 사업의 메시지는 개인 · 기타로)", b.active !== false, v => { b.active = v; ctx.render(); }),
      field("Claude 에게 줄 설명(발주처 · 담당자 · 행사 날짜 한두 줄 — 규칙이 못 맞힌 글의 사업을 고를 때 Claude 로 간다)",
        h("textarea", { oninput: e => { b.about = e.target.value; ctx.touch(); } }, b.about || "")),
      field("메모(판정에 쓰지 않는다 · 밖으로 나가지 않는다)", h("textarea", { oninput: e => { b.memo = e.target.value; ctx.touch(); } }, b.memo || "")),
      h("div", { class: "row" },
        h("button", { class: "btn small", type: "button", disabled: i === 0, onclick: () => { moveBox(doc, i, -1); st.open = new Set([i - 1]); ctx.render(); } }, "위로"),
        h("button", { class: "btn small", type: "button", disabled: i === doc.boxes.length - 1, onclick: () => { moveBox(doc, i, 1); st.open = new Set([i + 1]); ctx.render(); } }, "아래로"),
        h("button", { class: "btn small", type: "button", onclick: () => ask("사업 지우기", `'${b.name || b.key}' 를 지운다 — 이 사업의 메시지는 개인 · 기타로 간다(저장해야 바뀐다).`, "지우기",
          () => { doc.boxes.splice(i, 1); st.open = new Set(); ctx.render(); }) }, "지우기")),
      h("h3", {}, "모을 곳"),
      h("p", { class: "hint" }, "등록한 방 · 메일 계정 · 보낸 사람의 메시지는 낱말과 상관없이 이 사업으로 온다(둘 이상에 등록되면 위쪽 상자가 이긴다)."),
      h("span", { class: "label hint" }, "메일 계정(이 계정으로 온 메일은 전부)"),
      accounts.length ? h("div", { class: "row" }, accounts.map(a => check(a.label ? `${a.label} (${a.email})` : a.email, (b.mail_accounts || []).map(x => x.toLowerCase()).includes(a.email.toLowerCase()), v => {
        b.mail_accounts = (b.mail_accounts || []).filter(x => x.toLowerCase() !== a.email.toLowerCase());
        if (v) b.mail_accounts.push(a.email);
        ctx.touch();
      }))) : h("p", { class: "hint" }, "연결된 메일 계정이 없다 — 연결 칸에서 더한다."),
      h("span", { class: "label hint" }, "보낸 사람(메일 주소 · 전화번호 · 카톡/텔레그램 사용자)"),
      chips(b.senders, { label: v => personLabel(ctx, v), onRemove: j => { b.senders.splice(j, 1); ctx.render(); } }),
      h("div", { class: "row" },
        h("button", { class: "btn small", type: "button", onclick: () => personPicker(ctx, b) }, "목록에서 고르기"),
        adder("메일 주소나 전화번호(010-…)", v => { b.senders = b.senders || []; if (!b.senders.includes(v)) b.senders.push(v); ctx.render(); })),
      rooms(ctx, b, "kakao", i),
      rooms(ctx, b, "telegram", i),
      h("h3", {}, "낱말"),
      h("p", { class: "hint" }, "글 · 방 이름 · 제목에 이 낱말이 있으면 이 사업으로 — 여러 사업의 낱말이 걸리면 가장 긴 낱말이 이긴다."),
      chips(b.keywords, { onRemove: j => { b.keywords.splice(j, 1); ctx.render(); } }),
      adder("낱말(쉼표로 여러 개)", v => { b.keywords = b.keywords || []; if (!b.keywords.includes(v)) b.keywords.push(v); ctx.render(); }),
      problemsAt(st.problems, `boxes[${i}].keywords`).map(p => h("span", { class: "problem" }, p.message)),
      mine.map(c => h("p", { class: "hint warn" }, c.kind === "same" ? `'${c.word}' 는 ${c.b} 에도 있다 — 위쪽이 이긴다` : `'${c.word}' 는 ${c.b} 의 '${c.other}' 안에 든다 — 그 글에서는 더 긴 '${c.other}' 가 이긴다`))));
}

function unconfirmed(ctx) {
  const doc = ctx.state.doc;
  doc.unconfirmed = doc.unconfirmed || [];
  return h("div", {},
    h("h2", {}, "확정 전 일"),
    h("p", { class: "hint" }, "아직 따내지 않은 일(입찰 중 등) — 이 낱말이 사업의 낱말보다 길게 걸리면 상자를 차지하지 않고 개인 · 기타로 간다. 따내면 사업으로 옮긴다."),
    doc.unconfirmed.map((u, i) => h("div", { class: "biz" }, h("div", { class: "body" },
      field("이름", text(ctx, u, "name"), problemsFor(ctx.state.problems, `unconfirmed[${i}]`)),
      chips(u.keywords, { onRemove: j => { u.keywords.splice(j, 1); ctx.render(); } }),
      adder("낱말(겨룰 사업의 낱말보다 길게)", v => { u.keywords = u.keywords || []; if (!u.keywords.includes(v)) u.keywords.push(v); ctx.render(); }),
      field("메모", h("textarea", { oninput: e => { u.memo = e.target.value; ctx.touch(); } }, u.memo || "")),
      h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: () => { doc.unconfirmed.splice(i, 1); ctx.render(); } }, "지우기"))))),
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => { doc.unconfirmed.push({ name: "새 확정 전 일", keywords: [], memo: "" }); ctx.render(); } }, "확정 전 일 더하기")));
}

function classify(ctx) {
  const q = { source: "gmail", chat_id: "", chat_name: "", sender_id: "", sender_name: "", account: "", text: "" };
  const out = h("div", { class: "out hint" });
  const input = (key, placeholder) => h("input", { type: "text", placeholder, oninput: e => { q[key] = e.target.value; } });
  const run = async () => {
    const r = await ctx.api("POST", "/api/classify", { ...q, doc: ctx.state.doc });
    if (!r.ok) { out.replaceChildren("시험하지 못했다"); return; }
    const color = ((ctx.state.doc.boxes || []).find(b => b.key === r.data.key) || {}).color;
    out.replaceChildren("→ ", h("span", { class: "boxname" + (color ? " c-" + color : "") }, r.data.box), ` · ${r.data.why}`, h("div", {}, r.data.note));
  };
  return h("div", { class: "classify" },
    h("h2", {}, "분류 시험"),
    h("p", { class: "hint" }, "고치는 중인 설정으로 시험한다(저장하지 않아도 된다)."),
    h("div", { class: "row" },
      h("select", { onchange: e => { q.source = e.target.value; } }, Object.entries(SOURCE_NAME).reverse().map(([k, v]) => h("option", { value: k }, v))),
      input("chat_name", "방 이름 · 메일 제목"), input("chat_id", "방 번호(있으면)")),
    h("div", { class: "row" }, input("sender_id", "보낸 사람(주소 · 번호)"), input("account", "받은 메일 계정(메일이면)")),
    field("글", h("textarea", { oninput: e => { q.text = e.target.value; } })),
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: run }, "어느 상자로 가나")), out);
}

export function renderBoxes(ctx) {
  const doc = ctx.state.doc;
  doc.boxes = doc.boxes || [];
  return h("div", {},
    h("h2", {}, "상자"),
    h("p", { class: "hint" }, "탭5 에 사업 상자가 이 차례로 뜬다 — 진행 중인 사업 다섯까지와 개인 · 기타. 사업이 적으면 상자도 적다."),
    mini(doc),
    doc.boxes.map((b, i) => business(ctx, b, i)),
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => { doc.boxes.push(newBox(doc)); ctx.state.open = new Set([doc.boxes.length - 1]); ctx.render(); } }, "사업 더하기")),
    field("여섯째 상자의 이름", text(ctx, doc, "etc_name"), problemsFor(ctx.state.problems, "etc_name")),
    unconfirmed(ctx),
    h("h2", {}, "분류 방식"),
    h("ol", { class: "hint", style: "margin:8px 0 0 20px" },
      h("li", {}, "등록한 방 · 메일 계정 · 보낸 사람 — 둘 이상에 등록되면 위쪽 상자가 이긴다"),
      h("li", {}, "사업 · 확정 전 일의 낱말 가운데 가장 긴 것(같으면 많이 걸린 쪽, 그래도 같으면 위쪽)"),
      h("li", {}, "어디에도 안 걸린 짧은 답은 같은 방 · 같은 사람의 다른 메시지가 한 사업에만 걸려 있으면 그 사업"),
      h("li", {}, "개인 메일 계정이면 개인, 아니면 기타(둘 다 여섯째 상자)")),
    classify(ctx));
}
