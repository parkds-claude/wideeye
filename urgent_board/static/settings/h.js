// 설정 화면의 작은 도구 — 요소 만들기 · 칩 · 판 · 입력 칸. 글은 늘 글자로 넣는다(남이 쓴 방 이름 · 메시지가 들어간다).

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.setAttribute("style", v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value" || k === "checked" || k === "disabled" || k === "selected") el[k] = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}

// 문제 글(주황) — 그 칸의 경로에 정확히 걸린 것만
export function problemsFor(problems, path) {
  return (problems || []).filter(p => p.path === path);
}

// 입력 칸 하나 — 이름 · 입력 · (있으면) 문제
export function field(label, input, problems = []) {
  return h("label", { class: "field" + (problems.length ? " bad" : "") },
    h("span", { class: "label" }, label), input,
    problems.map(p => h("span", { class: "problem" }, p.message)));
}

export function check(label, checked, onChange, attrs = {}) {
  return h("label", { class: "check" },
    h("input", { type: "checkbox", checked, onchange: e => onChange(e.target.checked), ...attrs }), h("span", {}, label));
}

// 칩 — 값마다 하나, ×로 뺀다. extra(값, 차례) 가 있으면 칩 안에 더 넣는다
export function chips(values, { label = v => String(v), onRemove, extra, cls } = {}) {
  return h("div", { class: "chips" }, (values || []).map((v, i) =>
    h("span", { class: "chip" + (cls ? " " + cls(v, i) : "") }, label(v, i), extra ? extra(v, i) : null,
      onRemove ? h("button", { class: "x", type: "button", title: "빼기", onclick: () => onRemove(i) }, "×") : null)));
}

// 글 칸 + Enter(또는 쉼표)로 더하기
export function adder(placeholder, onAdd, button = "더하기") {
  const input = h("input", { type: "text", placeholder });
  const go = () => {
    const parts = input.value.split(",").map(s => s.trim()).filter(Boolean);
    input.value = "";
    parts.forEach(onAdd);
  };
  input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  return h("div", { class: "adder" }, input, h("button", { class: "btn small", type: "button", onclick: go }, button));
}

// 판 — 화면 가운데, 바깥은 어둡게. 돌려주는 함수로 닫는다
export function modal(title, body, actions = []) {
  const close = () => shade.remove();
  const shade = h("div", { class: "shade", onclick: e => { if (e.target === shade) close(); } },
    h("div", { class: "panel", role: "dialog", "aria-label": title }, h("h2", {}, title), body,
      h("div", { class: "actions" }, actions.map(a => h("button", { class: "btn" + (a.primary ? " primary" : ""), type: "button",
        onclick: () => { if (a.onClick) a.onClick(close); else close(); } }, a.label)))));
  document.body.append(shade);
  return close;
}

// 확인 판(브라우저 대화상자를 쓰지 않는다)
export function ask(title, text, yes, onYes) {
  modal(title, h("p", {}, text), [{ label: "취소" }, { label: yes, primary: true, onClick: close => { close(); onYes(); } }]);
}

export function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
