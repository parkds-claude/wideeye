// 전체 목록: "처리됨"을 누르면 그 항목을 지우고 다시 읽는다.
document.querySelectorAll("button[data-key]").forEach(b => b.addEventListener("click", async () => {
  b.disabled = true;
  try {
    await fetch("/api/dismiss", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: b.dataset.key }) });
  } finally { location.reload(); }
}));

// 사업 고르기(가르치기 — 2026-10-16): 고르면 그 보낸 사람(방 묶음은 방)을 그 사업에 등록하고 다시 읽는다. 실패하면 알리고 되돌린다
document.querySelectorAll("select.teach").forEach(sel => sel.addEventListener("change", async () => {
  sel.disabled = true;
  let ok = false;
  try {
    const r = await fetch("/api/teach", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: sel.dataset.key, box: sel.value }) });
    ok = r.ok;
  } catch { ok = false; }
  if (ok) { location.reload(); return; }
  sel.value = sel.dataset.was; sel.disabled = false;
  sel.title = "저장하지 못했다 — 다시 골라 본다";
}));
