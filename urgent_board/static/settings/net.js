// 엔진과 이야기하기 — 답이 JSON 이 아니어도, 엔진이 다시 시작하는 중이라 닿지 않아도 늘 { status, ok, data } 를 돌려준다(검토 M8).
// status 0 = 닿지 않음. 연결 단추 · 연결 설정 저장 뒤 엔진은 몇 초 동안 다시 시작한다 — 읽기는 retry 로 다시 해 본다.

export const DOWN = "엔진이 답하지 않는다 — 다시 시작하는 중이면 몇 초 뒤에 된다";

export async function call(fetchFn, method, url, body) {
  let r;
  try {
    r = await fetchFn(url, { method, credentials: "same-origin", headers: body !== undefined ? { "Content-Type": "application/json" } : {},
                             body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch (_) {
    return { status: 0, ok: false, data: { error: DOWN } };
  }
  let data = {};
  try { data = await r.json(); } catch (_) { /* 본문이 JSON 이 아니다 */ }
  return { status: r.status, ok: r.ok, data };
}

export async function retry(fn, { tries = 10, wait = 1000, sleep = ms => new Promise(done => setTimeout(done, ms)) } = {}) {
  let r;
  for (let i = 0; i < tries; i++) {
    r = await fn();
    if (r.status !== 0) return r;
    if (i < tries - 1) await sleep(wait);
  }
  return r;
}
