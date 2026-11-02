// 연결 칸 — 출처마다 한 칸(위쪽 버튼 · 상태 · 단추 · 안내). 메일(사용자 자신의 Google 클라이언트) · 텔레그램(코드 · 2단계 비밀번호) · 카톡(실험 기능) · 문자 · 요약.
// 켬/끔은 설정 문서(draft)를 고친다 — 아래 띠로 미리 보고 저장. 로그인처럼 바로 일어나는 일은 단추가 엔진에 곧바로 보낸다.
import { ask, check, field, h, when } from "./h.js";

const STATE_TEXT = { ok: "정상", waiting: "아직 수집 전", off: "꺼짐", stopped: "수집 중단", reconnect: "다시 연결 필요" };

function stateLine(s) {
  if (!s) return h("span", { class: "state" }, "확인 중…");
  if (s.state === "stopped" || s.state === "reconnect")
    return h("span", {}, h("span", { class: "tag-bad" }, STATE_TEXT[s.state]), " ", h("span", { class: "state" }, s.note || ""));
  return h("span", { class: "state" }, [STATE_TEXT[s.state] || s.state, s.at ? `${when(s.at)} 수집` : "", s.state === "ok" ? s.note : ""].filter(Boolean).join(" · "));
}

function enabled(ctx, key, label) {
  const acc = ctx.state.doc.accounts = ctx.state.doc.accounts || {};
  acc[key] = acc[key] || { enabled: false };
  return check(label, !!acc[key].enabled, v => { acc[key].enabled = v; ctx.touch(); });
}

// 텔레그램 봇 보고서를 탭5 알림에 넣을까(2026-10-17 D9 — 설정 display.feed_bots, 적지 않았으면 켬). 할 일 · 목록에는 늘 넣지 않는다
function botsOnBoard(ctx) {
  const d = ctx.state.doc.display = ctx.state.doc.display || {};
  return h("div", {}, check("봇 보고서도 탭5 알림에 보이기", d.feed_bots !== false, v => { d.feed_bots = v; ctx.touch(); }),
    h("p", { class: "hint" }, "입찰 보고서처럼 봇이 보낸 글이에요. 탭5 알림에만 뜨고, 맥의 할 일 목록에는 들어가지 않아요."));
}

// 이 맥의 자료를 읽는 알림 둘(2026-10-17 알림 2단계): 부재중 전화(아이폰 통화 기록) · 팀즈 알림(맥 알림 센터). 문자와 같은 전체 디스크 접근 권한을 쓴다
function local(ctx, c, key, title, label, probe, okText, hint) {
  return h("div", { class: "src" },
    h("div", { class: "head" }, h("b", {}, title), stateLine(c && c.sources && c.sources[key])),
    enabled(ctx, key, label),
    h("p", { class: probe && probe.ok ? "state" : "hint warn" }, probe ? (probe.ok ? okText : probe.why) : ""),
    h("p", { class: "hint" }, hint));
}

function result(el, text, bad) { el.textContent = text; el.className = "hint" + (bad ? " warn" : ""); }

function gmail(ctx, c) {
  const msg = h("p", { class: "hint" });
  const file = h("input", { type: "file", accept: ".json,application/json", style: "display:none", onchange: async e => {
    const f = e.target.files[0]; if (!f) return;
    const r = await ctx.api("POST", "/api/connect/gmail/client", { text: await f.text() });
    result(msg, r.ok ? `클라이언트 파일을 넣었어요(…${r.data.client_id_tail}). 이제 [메일 계정 더하기]를 눌러 계정을 연결해 주세요. ${r.data.hint}` : r.data.error || "넣지 못했어요.", !r.ok);
    ctx.loadConnect();
  } });
  const add = async () => {
    const r = await ctx.api("POST", "/api/connect/gmail/start", {});
    if (!r.ok) { result(msg, r.data.error || "시작하지 못했어요. 잠시 뒤 다시 눌러 주세요.", true); return; }
    window.open(r.data.url, "_blank", "noopener");
    result(msg, "새 창에서 Google 계정을 고르고 허용하면 '연결했어요' 화면이 떠요. 그 창을 닫고 이 화면을 새로 고침하면 계정 상태가 바뀌어 있어요. " + r.data.hint);
  };
  const accounts = (c && c.sources && c.sources.gmail) || [];
  return h("div", { class: "src" },
    h("div", { class: "head" }, h("b", {}, "메일(Gmail)"), h("span", { class: "state" }, accounts.length ? `계정 ${accounts.length}개` : "연결된 계정 없음")),
    accounts.map(a => [h("div", { class: "row" }, h("span", {}, a.label ? `${a.label} — ${a.email}` : a.email), stateLine(a),
      h("span", { class: "state" }, a.token === "keychain" ? "다른 앱의 로그인을 빌려 씀" : "이 현황판으로 로그인함"),
      h("button", { class: "btn small", type: "button", onclick: () => ask("메일 계정 끊기", `${a.email} 의 메일을 더는 모으지 않아요. 상자 설정에 적힌 이 계정은 그대로 남아요.`, "끊기", async () => {
        const r = await ctx.api("POST", "/api/connect/gmail/remove", { alias: a.alias });
        ctx.say(r.ok ? "끊었어요. 현황판이 잠깐 다시 시작해요." : (r.data.error || "끊지 못했어요."), !r.ok);
        await ctx.reloadAll();
      }) }, "끊기")),
      // 계정 줄 바로 아래 — 줄과 안내를 한 쌍으로 돌려준다(2026-10-17: 안내를 map 의 둘째 인자 자리에 두어 계정이 있으면 칸 전체가 오류로 멈췄다)
      (a.state === "stopped" || a.state === "reconnect") ? h("p", { class: "hint warn" }, "이 계정은 지금 메일을 못 읽고 있어요. 아래 [메일 계정 더하기]를 눌러 이 계정으로 다시 로그인하면 고쳐져요(상자 설정은 그대로예요).") : null]),
    c && c.google_client
      ? h("p", { class: "hint" }, "준비가 끝났어요. 계정마다 [메일 계정 더하기]를 눌러 Google 계정을 고르고 허용해 주세요. 브라우저에 이미 로그인된 계정이면 비밀번호 없이 고르기만 하면 돼요.")
      : h("p", { class: "hint" }, "메일을 읽으려면 Google 의 허락이 필요해요. 처음 한 번만 아래 준비를 해 주세요."),
    h("ol", {},
      h("li", {}, "Google Cloud 콘솔(console.cloud.google.com)에서 프로젝트를 하나 만들고, 'Gmail API'를 사용 설정해 주세요."),
      h("li", {}, "'OAuth 동의 화면'에서 사용자 유형은 '외부', 앱 이름만 적고, 범위에 'gmail.readonly'(메일 읽기만)를 넣어 주세요. 마지막에 '프로덕션으로 게시'를 눌러 주세요 — '테스트'로 두면 7일마다 다시 연결해야 해요."),
      h("li", {}, "'사용자 인증 정보' > 'OAuth 클라이언트 ID 만들기'에서 애플리케이션 유형을 '데스크톱 앱'으로 고르고, 만들어진 JSON 파일을 내려받아 주세요."),
      h("li", {}, "그 파일을 [클라이언트 파일 넣기]로 넣고, [메일 계정 더하기]를 눌러 계정마다 로그인해 주세요. 처음에는 '확인되지 않은 앱' 경고가 나올 수 있어요 — 직접 만드신 앱이니 '고급' → '…(으)로 이동'을 누르시면 돼요.")),
    h("div", { class: "row" }, h("span", { class: "state" }, c && c.google_client ? "Google 클라이언트 파일: 있음" : "Google 클라이언트 파일: 없음"),
      h("label", { class: "btn" }, "클라이언트 파일 넣기", file),
      h("button", { class: "btn", type: "button", disabled: !(c && c.google_client), onclick: add }, "메일 계정 더하기")),
    msg);
}

function telegram(ctx, c) {
  const msg = h("p", { class: "hint" });
  const v = { api_id: "", api_hash: "", phone: "", code: "", password: "" };
  const inp = (key, placeholder, type = "text") => h("input", { type, placeholder, oninput: e => { v[key] = e.target.value; } });
  const pw = h("div", { style: "display:none" }, field("2단계 비밀번호", inp("password", "텔레그램 2단계 비밀번호", "password")));
  const s = c && c.sources && c.sources.telegram;
  return h("div", { class: "src" },
    h("div", { class: "head" }, h("b", {}, "텔레그램"), stateLine(s)),
    enabled(ctx, "telegram", "텔레그램 모으기"),
    botsOnBoard(ctx),
    h("p", { class: "hint" }, "텔레그램 계정으로 직접 로그인해서 읽어요(공식 봇이 아니라 사용자 로그인 방식이에요). 텔레그램 글은 약관 때문에 기본으로는 요약(Claude)에 보내지 않아요 — '요약' 칸에서 바꿀 수 있어요."),
    h("ol", {}, h("li", {}, "my.telegram.org 에 로그인해 'API development tools'에서 api_id 와 api_hash 를 받아 주세요(사람마다 하나씩 받아요)."),
      h("li", {}, "아래 칸에 넣고 [코드 받기]를 누른 뒤, 텔레그램 앱으로 온 코드를 넣고 [로그인]을 눌러 주세요.")),
    h("p", { class: "state" }, c && c.telegram ? `api 값: ${c.telegram.api ? "있음" : "없음"} · 세션: ${c.telegram.session ? "있음" : "없음"}` : ""),
    h("div", { class: "row" }, inp("api_id", "api_id"), inp("api_hash", "api_hash"), inp("phone", "전화번호(010-…)")),
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: async e => {
      const b = e.currentTarget; b.disabled = true;                   // 텔레그램과 이야기하는 몇 초 동안 화면이 그대로면 눌린 줄 모른다(2026-10-17)
      result(msg, "텔레그램에 코드를 요청하고 있어요…(몇 초 걸려요)");
      try {
        const r = await ctx.api("POST", "/api/connect/telegram/code", { api_id: v.api_id, api_hash: v.api_hash, phone: v.phone });
        result(msg, r.ok ? `${r.data.sent || "코드를 보냈어요."} 받은 코드를 넣고 [로그인]을 눌러 주세요.` : r.data.error, !r.ok);
      } finally { b.disabled = false; }
    } }, "코드 받기"), inp("code", "받은 코드"),
      h("button", { class: "btn", type: "button", onclick: async () => {
        result(msg, "로그인하고 있어요…");
        const r = await ctx.api("POST", "/api/connect/telegram/signin", { code: v.code, password: v.password });
        if (r.data.password_needed) { pw.style.display = ""; result(msg, "2단계 비밀번호를 넣고 [로그인]을 다시 눌러 주세요."); return; }
        result(msg, r.ok ? "로그인했어요. 현황판이 잠깐 다시 시작해요." : r.data.error, !r.ok);
        if (r.ok) await ctx.reloadAll();
      } }, "로그인"),
      h("button", { class: "btn small", type: "button", onclick: () => ask("텔레그램 끊기", "로그인 정보를 지우고 텔레그램을 더는 모으지 않아요.", "끊기", async () => {
        const r = await ctx.api("POST", "/api/connect/telegram/remove", {});
        ctx.say(r.ok ? "끊었어요. 현황판이 잠깐 다시 시작해요." : (r.data.error || "끊지 못했어요."), !r.ok);
        await ctx.reloadAll();
      }) }, "끊기")),
    pw, msg);
}

function kakao(ctx, c) {
  const msg = h("p", { class: "hint" });
  const k = c && c.kakaocli;
  return h("div", { class: "src" },
    h("div", { class: "head" }, h("b", {}, "카카오톡(실험 기능)"), stateLine(c && c.sources && c.sources.kakao)),
    enabled(ctx, "kakao", "카카오톡 모으기"),
    h("p", { class: "hint" }, "맥 카카오톡의 자료를 따로 설치한 kakaocli 로 읽기만 해요. 공식 기능이 아니라서 카카오톡이 바뀌면 멈출 수 있어요."),
    h("p", { class: "state" }, k ? (k.found ? `kakaocli: ${k.path} ${k.version}` : "kakaocli: 없음 — 터미널에서 brew install silver-flight-group/tap/kakaocli 로 설치해 주세요") : ""),
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: async () => {
      const r = await ctx.api("POST", "/api/connect/kakao/check", {});
      result(msg, r.data.ok ? `잘 읽혀요 — 방 ${r.data.rooms}개.` : `${r.data.error} — 터미널에서 kakaocli auth 를 실행해 보세요(사용자 번호가 필요하면 --user-id <번호>).`, !r.data.ok);
    } }, "시험 조회")), msg);
}

function imessage(ctx, c) {
  const m = c && c.messages, ct = c && c.contacts;
  return h("div", { class: "src" },
    h("div", { class: "head" }, h("b", {}, "문자(메시지 앱)"), stateLine(c && c.sources && c.sources.imessage)),
    enabled(ctx, "imessage", "문자 모으기"),
    h("p", { class: m && m.ok ? "state" : "hint warn" }, m ? (m.ok ? "메시지 앱 자료를 읽을 수 있어요." : m.why) : ""),
    h("p", { class: ct && ct.ok ? "state" : "hint warn" }, ct ? (ct.ok ? "연락처를 읽을 수 있어요(문자에 이름이 보여요)." : `연락처: ${ct.why}`) : ""),
    c ? h("p", { class: "hint" }, "전체 디스크 접근 권한을 줄 파일: ", h("code", {}, c.python)) : null,
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => ctx.api("POST", "/api/connect/open-privacy", {}) }, "시스템 설정 열기")),
    h("p", { class: "hint" }, "아이폰 문자가 맥에 오게 하려면 아이폰의 설정 > 메시지 > 문자 메시지 전달에서 이 맥을 켜 주세요."),
    local(ctx, c, "whatsapp", "왓츠앱", "왓츠앱 모으기", c && c.whatsapp,
          c && c.whatsapp && !c.whatsapp.running ? "자료는 읽히지만 왓츠앱 앱이 꺼져 있어요 — 켜 두어야 새 글이 보여요." : "왓츠앱 자료를 읽을 수 있어요.",
          "맥 왓츠앱 앱의 자료를 읽기만 해요(문자와 같은 권한). 앱이 꺼져 있으면 새 글을 볼 수 없어 '수집 중단'으로 보여요. 채널 · 숨긴 대화는 빼요."),
    local(ctx, c, "calls", "부재중 전화", "부재중 전화 모으기", c && c.calls, "통화 기록을 읽을 수 있어요.",
          "받지 않은 전화가 탭5 알림에 떠요(다시 걸거나 24시간이 지나면 흰 ●). 아이폰의 설정 > 전화 > 다른 기기에서 통화에서 이 맥을 켜 두면 기록이 맥에 와요."),
    local(ctx, c, "teams", "팀즈 알림", "팀즈 알림 모으기", c && c.teams, "맥 알림 센터를 읽을 수 있어요.",
          "이 맥에 뜬 팀즈 알림이 탭5 알림에 떠요(알림을 지우거나 팀즈에서 읽으면 흰 ●). 맥이 잠들었거나 팀즈가 꺼져 있던 동안의 것은 빠져요."));
}

function summary(ctx, c) {
  const msg = h("p", { class: "hint" });
  const cl = c && c.claude;
  return h("div", { class: "src" },
    h("div", { class: "head" }, h("b", {}, "요약(Claude)"), h("span", { class: "state" }, cl ? (cl.found ? `${cl.path} ${cl.version}` : "claude 명령이 없어요 — Claude Code 를 설치하고 로그인해 주세요") : "")),
    h("p", { class: "hint" }, "요약을 켜고 끄는 것과 보낼 출처는 아래 '요약 설정'에서 정해요. 켜면 화면에 뜰 메시지의 글이 Claude 로 전송돼요."),
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", disabled: !(cl && cl.found), onclick: async () => {
      result(msg, "부르는 중…(10~20초)");
      const r = await ctx.api("POST", "/api/connect/summary/test", {});
      result(msg, r.data.ok ? `요약: ${r.data.summary}` : r.data.error, !r.data.ok);
    } }, "시험 요약(지어낸 글)")), msg);
}

// 위쪽 한 줄 버튼에서 출처마다 한 칸(사용자 2026-10-17 "각 상세설정은 상단에 버튼으로 갈 것. [메일] [텔레그램] [문자] [카톡] [기타]") —
// 그전에는 '연결' 한 칸에 다섯이 길게 이어졌다. 연결 점검(/api/connect)은 그대로 한 번 읽어 다섯 칸이 함께 쓴다
// 칸 제목은 각 출처의 머리줄(이름 · 상태)이 맡는다 — 큰 제목을 따로 두면 "메일" 아래 "메일(Gmail)"처럼 두 번 나왔다
function page(body) {
  return ctx => {
    const c = ctx.state.connect;
    return h("div", {}, c ? null : h("p", { class: "hint" }, "연결 상태를 점검하는 중이에요…"), body(ctx, c));
  };
}
export const renderMail = page(gmail);
export const renderTelegram = page(telegram);
export const renderSms = page(imessage);
export const renderKakao = page(kakao);
export const renderClaude = page(summary);
