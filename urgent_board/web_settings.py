"""설정 화면의 API(설계 2026-10-15 제품판 5 · 6). 설정 문서를 읽고 고치는 일은 settings_doc · config 가 한다 — 여기는 HTTP 와 이어 붙이기만.

create_app 이 app.config["UB"] 에 {live, store, home, restart, clock} 를 두고 이 청사진을 등록한다.
- 저장: 꼴 검사 + 실제로 만들어 보기(config.problems — 지금 저장된 문서와 견줘 새 keychain 토큰을 막는다) → 판 확인 → 원자적 저장 → 곧바로 반영.
  연결 설정이 바뀌었으면 응답을 보낸 뒤 0.5초에 엔진을 다시 시작한다.
- 미리 보기: 지금 설정과 고친 설정으로 화면과 같은 길(board.screen)을 각각 돌려 상자마다의 건수와 옮겨지는 항목을 돌려준다. 쓰지 않는다.
- 비밀 값(토큰 · api_hash · 세션)은 돌려주지 않는다. 방 번호는 글자로 오간다."""
from __future__ import annotations
import copy
import threading
from datetime import date, datetime, timezone

from flask import Blueprint, current_app, jsonify, render_template, request

from . import board, config, paths, settings_doc, summarize
from .models import Event, Source
from .settings_doc import Conflict, Invalid, Problem, SettingsError

bp = Blueprint("settings", __name__)
RESTART_DELAY = 0.5               # 초 — 응답을 보낸 뒤 다시 시작한다(응답이 끊기지 않게)
MOVED_MAX = 50
CLASSIFY_NOTE = "같은 방 · 같은 사람의 다른 메시지에서 사업을 물려받는 것(③)은 이 시험에 나오지 않는다"


def _ub() -> dict:
    return current_app.config["UB"]


def _problems_json(ps) -> list[dict]:
    return [{"path": p.path, "message": p.message, "level": p.level} for p in ps]


def _normalize(doc: dict) -> dict:
    """보낸 사람의 전화번호를 +82 꼴로(메시지 앱의 보낸 사람 id 와 맞게)."""
    doc = copy.deepcopy(doc)
    for b in doc.get("boxes") or []:
        if isinstance(b, dict) and isinstance(b.get("senders"), list):
            b["senders"] = [settings_doc.normalize_phone(x) if isinstance(x, str) else x for x in b["senders"]]
    return doc


def later(fn, *args) -> None:
    """응답을 보낸 뒤에 부른다(엔진 다시 시작)."""
    t = threading.Timer(RESTART_DELAY, fn, args)
    t.daemon = True
    t.start()


def saved_doc() -> dict:
    """지금 저장된 문서(읽을 수 없으면 빈 것) — 새 keychain 토큰을 막을 때 견준다."""
    try:
        return settings_doc.read(_ub()["home"])[0]
    except (OSError, SettingsError):
        return {}


class Broken(Exception):
    """설정 파일이 깨져 연결 단추가 적을 곳이 없다 — 되돌릴 수 없는 일(Google 과 코드 바꾸기 · 로그인) 앞에서 멈춘다."""


BROKEN = "설정 파일에 문제가 있어 연결을 적을 수 없다 — 설정 화면에서 먼저 고친다"


def current_doc() -> tuple[dict, str]:
    """연결 단추가 고칠 문서와 판 — 파일이 없으면 예시 문서(처음 쓰는 사람, 설정 화면이 보이는 것과 같다), 깨졌으면 Broken(검토 I5)."""
    try:
        return settings_doc.read(_ub()["home"])
    except FileNotFoundError:
        return settings_doc.example(), ""
    except (OSError, SettingsError) as e:
        raise Broken(BROKEN) from e


def _body_doc():
    body = request.get_json(silent=True) or {}
    return body, (_normalize(body["doc"]) if isinstance(body.get("doc"), dict) else None)


def save_doc(doc: dict, version: str | None, reason: str):
    """저장하고 곧바로 반영한다 — 연결 설정이 바뀌었으면 엔진을 다시 시작한다. (응답 JSON, 상태 코드). 연결 화면도 쓴다."""
    ub = _ub()
    bad = [p for p in config.problems(doc, saved=saved_doc()) if p.level == "error"]
    if bad:
        return {"problems": _problems_json(bad)}, 422
    try:
        ver = settings_doc.save(ub["home"], doc, version)
    except Conflict:
        return {"error": "conflict"}, 409
    except Invalid as e:
        return {"problems": _problems_json(e.problems)}, 422
    what = ub["live"].check()
    if what == "restart" and ub.get("restart"):
        later(ub["restart"], reason)
    return {"version": ver, "applied": what}, 200


@bp.get("/settings")
def page():
    return render_template("settings.html")


SAFE_INT = 2 ** 53 - 1


def for_browser(v):
    """브라우저로 보낼 설정 문서 — JSON 을 오가도 뜻이 같게(검토 I3). yaml 이 날짜로 읽은 값은 "YYYY-MM-DD" 로(Flask 는
    "Sat, 17 Oct 2026 …" 로 보낸다), 브라우저의 수가 담지 못하는 큰 정수(따옴표 없이 적은 17자리 카톡 방 번호)는 글자로."""
    if isinstance(v, dict):
        return {k: for_browser(x) for k, x in v.items()}
    if isinstance(v, list):
        return [for_browser(x) for x in v]
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) > SAFE_INT:
        return str(v)
    return v


@bp.get("/api/settings")
def get_settings():
    ub = _ub()
    s = ub["live"].get()
    try:
        doc, ver = settings_doc.read(ub["home"])
        ps = config.problems(doc)
    except FileNotFoundError:
        doc, ver, ps = settings_doc.example(), "", []
    except (OSError, SettingsError) as e:
        doc, ver, ps = {}, settings_doc.version(paths.settings_file(ub["home"])), [Problem("", str(e))]
    rules = settings_doc.default_rules()
    return jsonify(doc=for_browser(doc), version=ver, problems=_problems_json(ps), settings_error=s.settings_error,
                   legacy_config=s.legacy_config, colors=list(settings_doc.BUSINESS_COLORS),
                   rule_defaults={"thresholds": rules.get("thresholds", {})})


@bp.put("/api/settings")
def put_settings():
    body, doc = _body_doc()
    if doc is None:
        return jsonify(error="doc"), 400
    out, code = save_doc(doc, body.get("version") or "", "설정 화면에서 연결 설정을 바꿨다")
    return jsonify(out), code


def box_namer(s):
    """사업 key → 화면의 상자 이름(진행 사업 다섯까지는 제 이름, 그 밖과 개인 · 기타는 여섯째 상자의 이름)."""
    cards = board.build_task_cards([], s.businesses, s.etc_name, datetime.now(timezone.utc), s.rules.calendar)
    own = {c.key: c.name for c in cards[:-1]}
    return lambda key: own.get(key, cards[-1].name)


@bp.post("/api/settings/preview")
def preview():
    _, doc = _body_doc()
    if doc is None:
        return jsonify(error="doc"), 400
    ub = _ub()
    found = config.problems(doc, saved=saved_doc())
    bad = [p for p in found if p.level == "error"]
    if bad:
        return jsonify(problems=_problems_json(bad)), 422
    warnings = _problems_json([p for p in found if p.level == "warn"])
    now = ub["clock"]()
    cur, new = ub["live"].get(), config.from_doc(doc, ub["home"])
    a, b = board.screen(ub["store"], cur, now), board.screen(ub["store"], new, now)
    name_a, name_b = box_namer(cur), box_namer(new)
    # 할 일(대화) 단위로 견준다 — 같은 할 일이 어느 상자로 옮겨지는가(2026-10-16 할 일 현황판)
    where_a = {t.conv: name_a(t.business) for t in a.tasks}
    where_b = {t.conv: name_b(t.business) for t in b.tasks}
    every = {t.conv: t for t in a.tasks} | {t.conv: t for t in b.tasks}
    moved = [{"sender": every[k].sender_name, "summary": every[k].what, "from": where_a.get(k, "—"), "to": where_b.get(k, "—")}
             for k in sorted(set(where_a) | set(where_b)) if where_a.get(k) != where_b.get(k)]

    def boxes(sc):
        return [{"key": c.key, "name": c.name, "color": c.color, "urgent": c.urgent, "waiting": c.waiting} for c in sc.cards]
    resummarize = a.recipe != b.recipe
    return jsonify(before=boxes(a), after=boxes(b), moved=moved[:MOVED_MAX], moved_total=len(moved), resummarize=resummarize,
                   warnings=warnings)


@bp.post("/api/classify")
def classify():
    from .triage import business
    body, doc = _body_doc()
    ub = _ub()
    s = ub["live"].get()
    if doc is not None and not [p for p in config.problems(doc) if p.level == "error"]:
        s = config.from_doc(doc, ub["home"])                 # 고치는 중인 설정으로 시험한다
    try:
        src = Source(str(body.get("source") or "gmail"))
    except ValueError:
        return jsonify(error="source"), 400
    ev = Event(source=src, chat_id=str(body.get("chat_id") or ""), message_id="classify", chat_name=str(body.get("chat_name") or ""),
               sender_id=str(body.get("sender_id") or ""), sender_name=str(body.get("sender_name") or ""), text=str(body.get("text") or ""),
               received_at=datetime.now(timezone.utc), is_from_me=False, is_read=False, is_group=False, account=str(body.get("account") or ""))
    key, registered, why = business.explain(ev, s)
    return jsonify(key=key, registered=registered, why=why, box=box_namer(s)(key), note=CLASSIFY_NOTE)


@bp.get("/api/catalog")
def catalog():
    from .connect import probes
    ub = _ub()
    s = ub["live"].get()
    owners: dict[str, str] = {}
    for b in s.businesses:
        for i in b.kakao_chat_ids:
            owners.setdefault(f"kakao:{i}", b.key)
        for i in b.telegram_chat_ids:
            owners.setdefault(f"telegram:{i}", b.key)
        for x in b.senders:
            owners.setdefault(f"sender:{x.lower()}", b.key)
        for x in b.mail_accounts:
            owners.setdefault(f"mail:{x.lower()}", b.key)
    return jsonify(**ub["store"].catalog(), owners=owners, tab5=probes.tab5_devices(),
                   accounts=[{k: str(g.get(k, "") or "") for k in ("alias", "email", "label")} for g in s.accounts.get("gmail") or []])


@bp.post("/api/restart")
def restart():
    ub = _ub()
    if not ub.get("restart"):
        return jsonify(error="no restart"), 503
    later(ub["restart"], "설정 화면에서 다시 시작")
    return jsonify(ok=True), 202


SAMPLE = "다음 주 화요일 오후 3시 회의 자료를 월요일까지 공유해 주실 수 있을까요?"      # 요약 시험 — 지어낸 글(실제 메시지를 보내지 않는다)
PRIVACY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


@bp.get("/api/connect")
def connect_status():
    """연결 화면 — 출처마다 상태와 점검. 비밀 값은 있음 · 없음만."""
    from .connect import probes
    ub = _ub()
    s = ub["live"].get()
    auth = paths.auth_dir(s.home)
    return jsonify(sources=probes.status(s, ub["store"].source_status()), python=probes.python_binary(),
                   messages=probes.messages_access(), contacts=probes.contacts_access(), claude=probes.claude(),
                   calls=probes.calls_access(), teams=probes.teams_access(), whatsapp=probes.whatsapp_access(),
                   kakaocli=probes.kakaocli(), google_client=(auth / "google-client.json").exists(),
                   telegram={"api": (auth / "telegram.json").exists(), "session": (auth / "telethon.session").exists()})


@bp.post("/api/connect/kakao/check")
def kakao_check():
    from .collectors import kakao
    binary = kakao.find_kakaocli()
    if not binary:
        return jsonify(ok=False, error="kakaocli 가 없다 — brew install silver-flight-group/tap/kakaocli")
    try:
        rows = kakao.query("SELECT COUNT(*) FROM NTChatRoom", kakao.load_auth(helper=kakao.configured_helper(_ub()["live"].get().accounts)), binary)
        return jsonify(ok=True, rooms=int(rows[0][0]))
    except Exception as e:                                       # noqa: BLE001 — 오류 글에는 열쇠가 없다(kakao.query 가 가린다)
        return jsonify(ok=False, error=str(e)[:200])


@bp.post("/api/connect/summary/test")
def summary_test():
    ub = _ub()
    s = ub["live"].get()
    summarizer = ub.get("summarizer") or summarize.ClaudeSummarizer(model=s.summary.get("model", "haiku"), max_chars=int(s.summary.get("max_chars") or 60))
    try:
        r = summarizer.summarize("시험", "", SAMPLE)
        return jsonify(ok=True, summary=getattr(r, "summary", r))
    except Exception as e:                                       # noqa: BLE001
        return jsonify(ok=False, error=str(e)[:200])


@bp.post("/api/connect/open-privacy")
def open_privacy():
    """시스템 설정의 '전체 디스크 접근 권한' 화면을 연다(사람이 직접 켠다)."""
    import subprocess
    subprocess.run(["open", PRIVACY_PANE], timeout=10, check=False)
    return jsonify(ok=True)


# ---- Gmail 연결(설계 7-1) ----------------------------------------------------------------------------------------------
OAUTH_COOKIE = "ub_oauth"
REFRESH_HINT = ("Google 이 이 계정의 오래 쓰는 로그인 정보(refresh token)를 주지 않았어요. 예전에 이 앱을 허락한 적이 있으면 그래요. "
                "Google 계정 > 보안 > '타사 앱 및 서비스'에서 이 앱의 접근을 지운 뒤 다시 연결해 주세요.")
PRODUCTION_HINT = "참고: Google Cloud 의 'OAuth 동의 화면'이 '테스트' 상태면 7일마다 다시 연결해야 해요. '프로덕션으로 게시'해 두시면 그럴 일이 없어요."


def _oauth():
    from .connect import gmail_oauth
    ub = _ub()
    flows = ub.setdefault("oauth_flows", gmail_oauth.Flows())
    return gmail_oauth, flows, ub.get("oauth_post") or gmail_oauth.http_post, ub.get("oauth_get") or gmail_oauth.http_get


def _result(ok: bool, title: str, message: str, hint: str = "", code: int = 200):
    return render_template("connect_result.html", ok=ok, title=title, message=message, hint=hint), code


@bp.post("/api/connect/gmail/client")
def gmail_client():
    go, _, _, _ = _oauth()
    body = request.get_json(silent=True) or {}
    try:
        c = go.save_client(paths.auth_dir(_ub()["live"].get().home), str(body.get("text") or ""))
    except go.FlowError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, client_id_tail=c["client_id"][-24:], hint=PRODUCTION_HINT)


@bp.post("/api/connect/gmail/start")
def gmail_start():
    import secrets
    go, flows, _, _ = _oauth()
    s = _ub()["live"].get()
    client = go.load_client(paths.auth_dir(s.home))
    if client is None:
        return jsonify(ok=False, error="먼저 Google 클라이언트 파일을 넣어 주세요."), 400
    nonce = secrets.token_urlsafe(16)
    url, _state = flows.start(client, f"{request.scheme}://{request.host}/connect/gmail/callback", nonce)
    r = jsonify(ok=True, url=url, hint=PRODUCTION_HINT)
    r.set_cookie(OAUTH_COOKIE, nonce, max_age=go.FLOW_SECONDS, httponly=True, samesite="Lax", path="/connect/gmail")
    return r


@bp.get("/connect/gmail/callback")
def gmail_callback():
    """Google 이 돌아오는 길(접속 열쇠 없이 열린다 — 대신 state · 쿠키 · 10분 · 한 번). 계정을 설정에 더하면 엔진이 다시 시작한다."""
    go, flows, post, get = _oauth()
    if request.args.get("error"):
        return _result(False, "메일 연결", "Google 로그인이 끝나지 않았어요(취소했거나 허락하지 않으셨어요). 설정 화면에서 [메일 계정 더하기]를 다시 눌러 주세요.", code=400)
    s = _ub()["live"].get()
    auth = paths.auth_dir(s.home)
    try:
        current_doc()                                            # 적을 곳이 있는지 먼저 — 코드는 한 번만 바꿀 수 있다
    except Broken as e:
        return _result(False, "메일 연결", str(e), code=409)
    try:
        verifier, redirect_uri = flows.finish(request.args.get("state") or "", request.cookies.get(OAUTH_COOKIE) or "")
        client = go.load_client(auth)
        if client is None:
            raise go.FlowError("Google 클라이언트 파일이 없어요. 설정 화면에서 먼저 넣어 주세요.")
        tok = go.exchange(client, request.args.get("code") or "", verifier, redirect_uri, post=post)
        if not tok.get("refresh_token"):
            return _result(False, "메일 연결", REFRESH_HINT, code=400)
        email = go.profile_email(tok["access_token"], get=get)
    except go.FlowError as e:
        return _result(False, "메일 연결", str(e), code=400)
    except Exception as e:                                       # noqa: BLE001 — 네트워크 등. 본문 · 토큰은 넣지 않는다
        return _result(False, "메일 연결", f"Google 과 연결하지 못했어요({type(e).__name__}). 인터넷 연결을 확인하고 잠시 뒤 다시 해 주세요.", code=502)
    try:
        doc, ver = current_doc()
    except Broken as e:
        return _result(False, "메일 연결", str(e), code=409)
    gmail = doc.setdefault("accounts", {}).setdefault("gmail", [])
    same = next((g for g in gmail if str(g.get("email", "")).lower() == email.lower()), None)
    alias = str(same["alias"]) if same else go.alias_for(email, {str(g.get("alias")) for g in gmail})
    name = go.write_token(auth, alias, go.token_payload(client, tok))
    if same:
        same["token"] = f"file:{name}"                         # 다시 연결 — 별칭 · 꼬리표는 그대로
    else:
        gmail.append({"alias": alias, "email": email, "label": alias, "token": f"file:{name}"})
    out, code = save_doc(doc, ver, "메일 계정을 연결했다")
    if code != 200:
        return _result(False, "메일 연결", "로그인은 됐는데 설정에 저장하지 못했어요. 설정 화면을 다시 열어 확인해 주세요.", code=500)
    return _result(True, "메일 연결", f"{email} 계정을 연결했어요. 이 창은 닫으셔도 돼요.",
                   "현황판이 잠깐 다시 시작하고, 5분쯤 뒤부터 이 계정의 메일이 올라와요. 다른 계정도 연결하시려면 설정 화면에서 [메일 계정 더하기]를 한 번 더 눌러 주세요. " + PRODUCTION_HINT)


@bp.post("/api/connect/gmail/remove")
def gmail_remove():
    body = request.get_json(silent=True) or {}
    alias = str(body.get("alias") or "")
    try:
        doc, ver = current_doc()
    except Broken as e:
        return jsonify(ok=False, error=str(e)), 409
    gmail = (doc.get("accounts") or {}).get("gmail") or []
    gone = [g for g in gmail if g.get("alias") == alias]
    if not gone:
        return jsonify(ok=False, error="그 계정을 찾지 못했어요. 설정 화면을 새로 고침해 주세요."), 404
    doc["accounts"]["gmail"] = [g for g in gmail if g.get("alias") != alias]
    out, code = save_doc(doc, ver, "메일 계정을 끊었다")
    if code != 200:
        return jsonify(out), code
    token = str(gone[0].get("token") or "")
    if token.startswith("file:"):                               # 우리가 만든 토큰 파일만 지운다(다른 앱의 키체인 항목은 건드리지 않는다)
        (paths.auth_dir(_ub()["home"]) / token[5:]).unlink(missing_ok=True)
    return jsonify(ok=True, **out)


# ---- 텔레그램 연결(설계 7-2) -------------------------------------------------------------------------------------------

def _telegram():
    from .connect import telegram_login
    ub = _ub()
    if "telegram_login" not in ub:
        ub["telegram_login"] = telegram_login.TelegramLogin(paths.auth_dir(ub["live"].get().home), client_factory=ub.get("telegram_factory"))
    return telegram_login, ub["telegram_login"]


def _set_telegram(enabled: bool, reason: str):
    """설정의 accounts.telegram.enabled 를 맞추고, 바뀌지 않았어도 엔진을 다시 시작한다(세션 파일이 바뀌었다)."""
    ub = _ub()
    doc, ver = current_doc()
    doc.setdefault("accounts", {})["telegram"] = {"enabled": enabled}
    out, code = save_doc(doc, ver, reason)
    if code == 200 and out.get("applied") != "restart" and ub.get("restart"):
        later(ub["restart"], reason)
    return out, code


@bp.post("/api/connect/telegram/code")
def telegram_code():
    tl, login = _telegram()
    body = request.get_json(silent=True) or {}
    try:
        sent = login.send_code(body.get("api_id"), str(body.get("api_hash") or ""), str(body.get("phone") or ""))
    except tl.LoginError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, sent=sent)                        # 코드를 어디서 찾을지(앱 · 문자 · 이메일 …)


@bp.post("/api/connect/telegram/signin")
def telegram_signin():
    tl, login = _telegram()
    body = request.get_json(silent=True) or {}
    try:
        current_doc()                                            # 켜 둘 곳이 있는지 먼저 — 로그인한 뒤에 못 적으면 세션만 남는다
    except Broken as e:
        return jsonify(ok=False, error=str(e)), 409
    try:
        result = login.sign_in(str(body.get("code") or ""), str(body.get("password") or ""))
    except tl.LoginError as e:
        return jsonify(ok=False, error=str(e)), 400
    if result == "password_needed":
        return jsonify(ok=False, password_needed=True)
    try:
        out, code = _set_telegram(True, "텔레그램에 로그인했다")
    except Broken as e:
        return jsonify(ok=False, error=str(e)), 409
    return (jsonify(ok=True), 200) if code == 200 else (jsonify({"ok": False, "error": "로그인은 됐는데 설정에 저장하지 못했어요. 설정 화면을 다시 열어 확인해 주세요.", **out}), code)


@bp.post("/api/connect/telegram/remove")
def telegram_remove():
    tl, _ = _telegram()
    auth = paths.auth_dir(_ub()["home"])
    try:
        out, code = _set_telegram(False, "텔레그램 연결을 끊었다")
    except Broken as e:
        return jsonify(ok=False, error=str(e)), 409
    if code != 200:
        return jsonify(ok=False, **out), code
    tl.mark_remove(auth)                                         # 지우기는 다시 시작한 엔진이(돌던 엔진이 아직 쓰고 있다 — 검토 I4)
    return jsonify(ok=True)


# ---- 가르치기(2026-10-16 분류 키우기 — docs/exec-plans/2026-10-16-classify-teach-and-pick.md) ------------------------------

def teach_target(conv: str) -> tuple[str, str, str] | None:
    """대화 열쇠(tasks.conv) → ("room", 출처, 방 번호) 또는 ("sender", 출처, 보낸 사람). 열쇠의 꼴은 pipeline.conv_id · _room_item."""
    parts = conv.split("|")
    if parts[0] == "room" and len(parts) >= 3:
        return ("room", parts[1], "|".join(parts[2:])) if parts[1] in settings_doc.ROOM_FIELD else None
    if parts[0] in ("gmail", "naver") and len(parts) >= 3:
        return ("sender", parts[0], parts[1])
    if len(parts) >= 3:
        return ("sender", parts[0], parts[-1])
    return None


def awaiting_target(store, s, key: str) -> tuple[str, str, str] | None:
    """답 기다림 줄(내가 보낸 글의 key) → ("sender", 출처, 받는 사람). 메일은 나 말고 첫 받는 사람, 1:1 대화는 그 방의 상대(없으면 방 번호)."""
    ev = store.event(key)
    if ev is None or not ev.is_from_me:
        return None
    if ev.source.value in ("gmail", "naver"):
        mine = {a.lower() for a in s.my_addresses}
        to = [str(a).lower() for a in (ev.raw or {}).get("to", []) if "@" in str(a) and str(a).lower() not in mine]
        return ("sender", ev.source.value, to[0]) if to else None
    if ev.is_group:
        return None
    others = [e for e in store.recent_events(days=30) if e.source == ev.source and e.chat_id == ev.chat_id and not e.is_from_me]
    return ("sender", ev.source.value, others[-1].sender_id if others else str(ev.chat_id))


@bp.post("/api/teach")
def teach():
    """전체 목록에서 할 일의 사업을 골랐다 — 그 보낸 사람(방 묶음이면 방)을 고른 상자에 등록하고(다른 곳에서는 뺀다),
    장부의 열린 할 일 가운데 같은 사람(방)의 것도 곧바로 옮긴다. box 는 진행 사업의 key 또는 "personal"(개인 · 기타)."""
    ub = _ub()
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("key"), str) or not isinstance(body.get("box"), str):
        return jsonify(ok=False, error="key · box"), 400
    key, box = body["key"], body["box"]
    s = ub["live"].get()
    names = {b.key: b.name for b in s.businesses if b.active}
    if box != settings_doc.PERSONAL and box not in names:
        return jsonify(ok=False, error="그런 진행 사업이 없다"), 400
    store = ub["store"]
    rows = store.tasks("open")
    row = next((r for r in rows if r["key"] == key), None)
    target = teach_target(row["conv"]) if row else awaiting_target(store, s, key)
    if target is None:
        return jsonify(ok=False, error="그 할 일이 없다"), 404
    row = row or {"chat_name": ""}
    kind, source, value = target
    if not value:
        return jsonify(ok=False, error="보낸 사람을 알 수 없다"), 400
    out, code = {}, 409
    for _ in range(2):                                          # 그사이 설정이 바뀌었으면(판 충돌) 한 번 더
        try:
            doc, ver = current_doc()
        except Broken as e:
            return jsonify(ok=False, error=str(e)), 409
        if kind == "room":
            settings_doc.teach_room(doc, source, value, row["chat_name"] or "", box)
        else:
            settings_doc.teach_sender(doc, value, box)
        out, code = save_doc(doc, ver, "목록에서 사업을 골랐다")
        if code != 409:
            break
    if code != 200:
        return jsonify(ok=False, **out), code
    same = [r["conv"] for r in rows if teach_target(r["conv"]) == target]
    store.retag_tasks(same, box)
    return jsonify(ok=True, what="방" if kind == "room" else "보낸 사람", box=names.get(box, s.etc_name), moved=len(same))
