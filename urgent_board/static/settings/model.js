// 설정 화면의 계산 — DOM 도 fetch 도 없다(tests/test_settings_js.py 가 node 로 돌려 본다).
// 방 번호 · 사용자 번호는 늘 글자로 다룬다(카톡 방 번호는 17자리까지 — 숫자로 바꾸면 끝자리가 바뀐다).

export const COLORS = ["green", "cyan", "pink", "yellow", "violet"];       // 사업에 주는 다섯 색(디자인 규칙 — 연두 · 주황은 주지 않는다)
const FIELD = { kakao: "kakao_rooms", telegram: "telegram_rooms" };

const isActive = b => b && b.active !== false;

// 다른 진행 사업이 쓰는 색(그 상자 자신은 빼고) — 색 동그라미에서 흐리게 · 못 고르게
export function takenColors(doc, index) {
  const out = new Set();
  (doc.boxes || []).forEach((b, i) => { if (i !== index && isActive(b) && b.color) out.add(b.color); });
  return out;
}

// 낱말 겹침 — 진행 사업 · 확정 전 일의 낱말끼리 같거나 한쪽이 다른 쪽을 품으면(가장 긴 낱말이 이기므로 알려 준다)
export function clashes(doc) {
  const owned = [];
  for (const b of doc.boxes || []) if (isActive(b)) for (const w of b.keywords || []) owned.push({ owner: b.name || b.key, word: w });
  for (const u of doc.unconfirmed || []) for (const w of u.keywords || []) owned.push({ owner: `확정 전: ${u.name}`, word: w });
  const out = [];
  for (let i = 0; i < owned.length; i++) {
    for (let j = 0; j < owned.length; j++) {
      const x = owned[i], y = owned[j];
      if (i === j || x.owner === y.owner) continue;
      const a = x.word.toLowerCase(), c = y.word.toLowerCase();
      if (a === c && i < j) out.push({ word: x.word, other: y.word, kind: "same", a: x.owner, b: y.owner });
      else if (a !== c && c.includes(a)) out.push({ word: x.word, other: y.word, kind: "contains", a: x.owner, b: y.owner });
    }
  }
  return out;
}

// 바뀐 칸 수 — 상자는 상자마다(늘거나 준 것 포함), 그 밖은 맨 위 칸마다
export function changes(saved, draft) {
  const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  let n = 0;
  const a = saved.boxes || [], b = draft.boxes || [];
  for (let i = 0; i < Math.max(a.length, b.length); i++) if (!same(a[i], b[i])) n++;
  const keys = new Set([...Object.keys(saved), ...Object.keys(draft)]);
  keys.delete("boxes");
  for (const k of keys) if (!same(saved[k], draft[k])) n++;
  return n;
}

// 방 칩의 이름 — 이름표 → 저장소에서 본 이름 → "번호 …". 번호 없는 항목은 이름으로 맞춘다는 것을 함께
export function roomLabel(room, source, catalog) {
  if (room.id === undefined || room.id === null || room.id === "") return `${room.name}(이름으로 맞춤)`;
  if (room.name) return room.name;
  const seen = ((catalog && catalog.rooms) || []).find(r => r.source === source && r.chat_id === room.id);
  return seen && seen.name ? seen.name : `번호 ${room.id}`;
}

export function addRoom(box, source, room) {
  if (room.id !== undefined && typeof room.id !== "string") throw new TypeError("방 번호는 글자여야 한다");
  const f = FIELD[source];
  box[f] = box[f] || [];
  if (room.id && box[f].some(r => r.id === room.id)) return box;
  const item = room.id ? { id: room.id } : { name: room.name };
  if (room.id && room.name) item.name = room.name;
  box[f].push(item);
  return box;
}

export function removeRoom(box, source, index) {
  (box[FIELD[source]] || []).splice(index, 1);
  return box;
}

export function toggleWatch(box, source, index) {
  const r = (box[FIELD[source]] || [])[index];
  if (r && r.id) { if (r.watch) delete r.watch; else r.watch = true; }
  return box;
}

// 이미 어느 사업에 든 방 · 사람 · 계정 — "kakao:번호" → 사업 key(위쪽 상자가 먼저)
export function owners(doc) {
  const out = new Map();
  const put = (k, v) => { if (!out.has(k)) out.set(k, v); };
  for (const b of doc.boxes || []) {
    for (const r of b.kakao_rooms || []) if (r.id) put(`kakao:${r.id}`, b.key);
    for (const r of b.telegram_rooms || []) if (r.id) put(`telegram:${r.id}`, b.key);
    for (const s of b.senders || []) put(`sender:${String(s).toLowerCase()}`, b.key);
    for (const s of b.mail_accounts || []) put(`mail:${String(s).toLowerCase()}`, b.key);
  }
  return out;
}

export function moveBox(doc, index, delta) {
  const j = index + delta, list = doc.boxes || [];
  if (j < 0 || j >= list.length) return doc;
  [list[index], list[j]] = [list[j], list[index]];
  return doc;
}

export function newBox(doc) {
  const keys = new Set((doc.boxes || []).map(b => b.key));
  let n = 1;
  while (keys.has(`box-${n}`)) n++;
  const taken = takenColors(doc, -1);
  return { key: `box-${n}`, name: "새 사업", color: COLORS.find(c => !taken.has(c)) || "", active: true, memo: "", about: "",
           mail_accounts: [], senders: [], kakao_rooms: [], telegram_rooms: [], keywords: [] };
}

// 그 칸(또는 그 아래)에 걸린 문제들 — "boxes[1]" 은 "boxes[1].color" 를 품고 "boxes[10]" 은 품지 않는다
export function problemsAt(problems, path) {
  return (problems || []).filter(p => p.path === path || p.path.startsWith(path + ".") || p.path.startsWith(path + "["));
}

// 화면이 쓰는 꼴을 채운다 — 읽을 때 저장본과 고치는 본에 똑같이(화면을 보기만 했는데 '바뀐 것'이 생기지 않게).
// 채우는 값은 엔진의 기본값과 뜻이 같다(요약 허용 출처 · 점심 시각 등) — 저장하면 그 값이 파일에 적힐 뿐이다.
export function normalize(doc) {
  const list = (o, k) => { if (!Array.isArray(o[k])) o[k] = []; };
  const obj = (o, k) => { if (!o[k] || typeof o[k] !== "object" || Array.isArray(o[k])) o[k] = {}; return o[k]; };
  list(doc, "boxes");
  for (const b of doc.boxes) for (const k of ["mail_accounts", "senders", "kakao_rooms", "telegram_rooms", "keywords"]) list(b, k);
  list(doc, "unconfirmed");
  for (const u of doc.unconfirmed) list(u, "keywords");
  list(doc, "contacts");
  for (const c of doc.contacts) list(c, "ids");
  const me = obj(doc, "me");
  for (const k of ["names", "addresses", "phone_tails", "personal_accounts", "personal_senders"]) list(me, k);
  const acc = obj(doc, "accounts");
  list(acc, "gmail");
  for (const k of ["telegram", "kakao", "imessage", "whatsapp"]) { const a = obj(acc, k); if (typeof a.enabled !== "boolean") a.enabled = false; }
  // 이 맥의 자료를 읽는 출처(부재중 전화 · 팀즈 알림 — 2026-10-17 알림 2단계)는 적지 않았으면 켬(엔진 config.LOCAL_SOURCES 와 같다)
  for (const k of ["calls", "teams"]) { const a = obj(acc, k); if (typeof a.enabled !== "boolean") a.enabled = true; }
  obj(doc, "display");
  const summ = obj(doc, "summary");
  if (!Array.isArray(summ.sources)) summ.sources = ["gmail", "kakao", "imessage"];
  obj(doc, "handled");
  const wh = obj(doc, "workhours");
  if (!Array.isArray(wh.lunch)) wh.lunch = ["12:00", "13:00"];
  list(wh, "extra_holidays"); list(wh, "extra_workdays");
  obj(obj(doc, "rules"), "thresholds");
  return doc;
}
