from pathlib import Path
from urgent_board.collectors import gmail, kakao
from urgent_board.models import Source

def test_gmail_parse_thread_marks_sent_and_unread():
    th = {"id": "t1", "messages": [
        {"id": "m1", "internalDate": "1760000000000", "labelIds": ["INBOX", "UNREAD"],
         "payload": {"headers": [{"name": "From", "value": "김과장 <kim@client.com>"}, {"name": "Subject", "value": "견적 가능할까요?"}, {"name": "To", "value": "minji@example.com"}]},
         "snippet": "견적 가능할까요?"},
        {"id": "m2", "internalDate": "1760003600000", "labelIds": ["SENT"],
         "payload": {"headers": [{"name": "From", "value": "minji@example.com"}, {"name": "Subject", "value": "Re: 견적"}]}, "snippet": "보냈습니다"}]}
    evs = gmail.parse_thread(th, {"minji@example.com"}, "minji@example.com")
    assert [e.is_from_me for e in evs] == [False, True]
    assert evs[0].sender_id == "kim@client.com" and evs[0].thread_id == "t1" and evs[0].is_read is False
    assert evs[0].raw["subject"] == "견적 가능할까요?"
    assert evs[0].source == Source.GMAIL and evs[0].account == "minji@example.com" and not evs[0].is_group

def test_gmail_cc_only_is_group():
    th = {"id": "t2", "messages": [
        {"id": "m1", "internalDate": "1760000000000", "labelIds": ["INBOX"],
         "payload": {"headers": [{"name": "From", "value": "kim@client.com"}, {"name": "Subject", "value": "공유"}, {"name": "To", "value": "other@client.com"}, {"name": "Cc", "value": "minji@example.com"}]},
         "snippet": "참고 바랍니다"}]}
    evs = gmail.parse_thread(th, {"minji@example.com"}, "minji@example.com")
    assert evs[0].is_group is True and evs[0].is_read is True

def test_kakao_rows_to_events():
    cols = ["chatId", "chatName", "activeMembersCount", "logId", "authorId", "type", "message", "sentAt", "attachment", "sender"]
    rows = [[1, "책마을-해오름 항공권 발권방", 7, 10, 500, 1, "발권 진행할까요?", 1800000000, '{"mentions":[{"user_id":42}]}', "주하늘"],
            [1, "책마을-해오름 항공권 발권방", 7, 11, 42, 1, "네 진행하세요", 1800000100, None, "김민수"],
            [1, "책마을-해오름 항공권 발권방", 7, 12, 500, 12, "", 1800000200, None, "주하늘"]]   # 이모티콘
    evs = kakao.rows_to_events([cols] + rows, my_user_id=42)
    assert len(evs) == 3 and evs[0].mentioned_me and not evs[0].is_from_me and evs[1].is_from_me and evs[0].is_group
    assert evs[2].text == "[이모티콘]" and evs[0].source == Source.KAKAO and evs[0].chat_id == "1"

def test_kakao_rows_tolerates_bad_attachment():
    cols = ["chatId", "chatName", "activeMembersCount", "logId", "authorId", "type", "message", "sentAt", "attachment", "sender"]
    evs = kakao.rows_to_events([cols, [2, "홍길동", 2, 5, 7, 1, "안녕하세요", 1760000000, "{not json", ""]], my_user_id=42)
    assert len(evs) == 1 and not evs[0].mentioned_me and not evs[0].is_group and evs[0].sender_name == "7"


# --- 카톡 수집기: 조회 함수를 끼워 넣어 시험한다(실제 DB 없이). kakaocli 결과에는 머리글 행이 없다.
from datetime import datetime, timezone
import pytest

def _fake_query(unread_by_chat):
    def q(sql, auth=None):
        if "NTChatContext" in sql:
            return [[42]]
        if "countOfNewMessage" in sql:
            return [[cid, n] for cid, n in unread_by_chat.items()]
        return [[1, "항공방", 7, 10, 500, 1, "첫 질문 가능할까요?", 1800000000, None, "주하늘"],
                [1, "항공방", 7, 11, 42, 1, "네", 1800000100, None, "김민수"],
                [1, "항공방", 7, 12, 500, 1, "둘째 질문 가능할까요?", 1800000200, None, "주하늘"],
                [1, "항공방", 7, 13, 600, 1, "셋째 확인 부탁드립니다", 1800000300, None, "이대리"]]
    return q

def test_kakao_collect_without_header_row_and_my_id():
    col = kakao.KakaoCollector(query=_fake_query({1: 0}), auth={})
    evs = col.collect(datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert [e.message_id for e in evs] == ["10", "11", "12", "13"]
    assert [e.is_from_me for e in evs] == [False, True, False, False]
    assert all(e.is_read for e in evs)                      # 안 읽은 수 0 → 전부 읽음

def test_kakao_only_last_n_incoming_are_unread():
    col = kakao.KakaoCollector(query=_fake_query({1: 2}), auth={})
    evs = {e.message_id: e for e in col.collect(datetime(2025, 1, 1, tzinfo=timezone.utc))}
    assert evs["10"].is_read is True                        # 안 읽은 수 2 → 마지막 수신 두 건만 안 읽음
    assert evs["12"].is_read is False and evs["13"].is_read is False
    assert evs["11"].is_read is True                        # 내 것은 늘 읽음

def test_kakao_collect_failure_propagates():                # Review Focus 5 — run_loop 가 받아 "수집 중단"으로 적는다
    def boom(sql, auth=None):
        raise RuntimeError("kakaocli query rc=1")
    with pytest.raises(RuntimeError):
        kakao.KakaoCollector(query=boom, auth={}).collect(datetime(2025, 1, 1, tzinfo=timezone.utc))

def test_kakao_constructor_has_no_side_effects():
    kakao.KakaoCollector()                                  # 조회·하위 프로세스 없이 만들어져야 한다(실패는 collect 에서)

def test_kakao_all_mention_counts_as_mention():             # 실제 DB 에 있는 '전체 멘션'(attachment.all_mention)
    cols = ["chatId", "chatName", "activeMembersCount", "logId", "authorId", "type", "message", "sentAt", "attachment", "sender"]
    evs = kakao.rows_to_events([cols, [1, "방", 9, 5, 7, 1, "내일까지 회신 부탁드립니다", 1760000000, '{"all_mention":true}', "김"],
                                      [1, "방", 9, 6, 7, 1, "참고", 1760000001, '{"all_mention":false}', "김"]], my_user_id=42)
    assert evs[0].mentioned_me is True and evs[1].mentioned_me is False

def test_business_rooms_match_by_id_even_if_renamed():
    from pathlib import Path
    from datetime import datetime, timezone
    from urgent_board.config import load
    from urgent_board.models import Event
    from urgent_board.triage import business
    s = load(Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "home")
    e = Event(source=Source.KAKAO, chat_id="100000000000003", message_id="1", chat_name="이름이 바뀐 방", sender_id="7", sender_name="김",
              text="안녕하세요", received_at=datetime.now(timezone.utc), is_from_me=False, is_read=False, is_group=True)
    assert business.tag(e, s) == "bookshop-team"

def test_kakao_notification_channel_rooms_are_skipped():   # 방 유형 2 = 알림 채널(카드사·은행·택배) — 실측 2026-10-13
    cols = ["chatId", "chatName", "activeMembersCount", "logId", "authorId", "type", "message", "sentAt", "attachment", "sender", "roomType"]
    evs = kakao.rows_to_events([cols, [1, "카드사", 2, 5, 7, 1, "결제일 안내. 확인해 주세요", 1760000000, None, "카드사", 2],
                                      [2, "김과장", 2, 6, 8, 1, "확인 부탁드립니다", 1760000001, None, "김과장", 0]], my_user_id=42)
    assert [e.chat_id for e in evs] == ["2"]
    assert "roomType" in kakao.COLS and "r.type" in kakao.SQL

def test_gmail_keeps_recipients_for_reply_matching():
    th = {"id": "t1", "messages": [{"id": "m2", "internalDate": "1760003600000", "labelIds": ["SENT"], "snippet": "보냈습니다",
          "payload": {"headers": [{"name": "From", "value": "minji@example.com"}, {"name": "Subject", "value": "Re: 견적"},
                                  {"name": "To", "value": "김과장 <Kim@Client.com>"}, {"name": "Cc", "value": "lee@client.com, park@x.org"}]}}]}
    e = gmail.parse_thread(th, {"minji@example.com"}, "minji@example.com")[0]
    assert e.raw["to"] == ["kim@client.com", "lee@client.com", "park@x.org"]


# --- Gmail 수집기: 가짜 서비스로 쪽 넘김·연결 재사용을 시험한다(네트워크·Keychain 없이)
class _Req:
    def __init__(self, fn): self._fn = fn
    def execute(self): return self._fn()

class _FakeGmail:
    """users().threads().list()/get() 만 흉내 낸다. 스레드 3개를 2쪽에 나눠 준다."""
    def __init__(self, fail=False): self.fail = fail; self.gets = []
    def users(self): return self
    def threads(self): return self
    def list(self, userId, q, maxResults, pageToken=None):
        if self.fail: return _Req(lambda: (_ for _ in ()).throw(RuntimeError("invalid_grant")))
        page = {None: {"threads": [{"id": "t1"}, {"id": "t2"}], "nextPageToken": "p2"}, "p2": {"threads": [{"id": "t3"}]}}[pageToken]
        return _Req(lambda: page)
    def get(self, userId, id, format, metadataHeaders):
        self.gets.append(id)
        return _Req(lambda: {"id": id, "messages": [{"id": "m-" + id, "internalDate": "1760000000000", "labelIds": ["INBOX"], "snippet": "가능할까요?",
                             "payload": {"headers": [{"name": "From", "value": "kim@client.com"}, {"name": "To", "value": "a@x.com"}, {"name": "Subject", "value": "문의 " + id}]}}]})

def test_gmail_collect_follows_pages_and_reuses_the_service():
    built = []
    def factory():
        built.append(1); return _FakeGmail()
    col = gmail.GmailCollector("a", "a@x.com", "file:gmail-a.json", set(), Path("/nonexistent"), service_factory=factory)
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert [e.chat_id for e in col.collect(since)] == ["t1", "t2", "t3"]
    col.collect(since)
    assert len(built) == 1 and col.name == "gmail:a"            # 매 주기 토큰을 새로 받지 않는다

def test_gmail_failure_drops_the_service_so_next_sweep_rebuilds():
    built = []
    def factory():
        built.append(1); return _FakeGmail(fail=len(built) == 1)
    col = gmail.GmailCollector("a", "a@x.com", "file:gmail-a.json", set(), Path("/nonexistent"), service_factory=factory)
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError):
        col.collect(since)
    assert len(col.collect(since)) == 3 and len(built) == 2     # 다시 로그인한 토큰을 다음 주기에 읽는다


class _Http:
    def __init__(self): self.closed = 0
    def close(self): self.closed += 1

class _FlakyGmail(_FakeGmail):
    """첫 list 가 네트워크 오류로 끊긴다(오래 쉰 연결이 끊김 · 잠에서 깬 직후). 진짜 요청처럼 .http 를 단다."""
    def __init__(self, errors): super().__init__(); self.errors = list(errors); self.http = _Http()
    def list(self, userId, q, maxResults, pageToken=None):
        req = super().list(userId, q, maxResults, pageToken)
        def run():
            if self.errors:
                raise self.errors.pop(0)
            return req.execute()
        r = _Req(run); r.http = self.http
        return r

def test_gmail_retries_a_network_blip_within_the_sweep(monkeypatch):   # 2026-10-28 맥북: 1초 끊김에 회차가 통째로 실패해 5분 동안 "수집 중단"
    waits = []
    monkeypatch.setattr(gmail.time, "sleep", waits.append)
    import socket
    fake = _FlakyGmail([ConnectionResetError(54, "Connection reset by peer"), OSError(49, "Can't assign requested address"), socket.gaierror(8, "nodename")])
    col = gmail.GmailCollector("a", "a@x.com", "file:gmail-a.json", set(), Path("/nonexistent"), service_factory=lambda: fake)
    assert [e.chat_id for e in col.collect(datetime(2025, 1, 1, tzinfo=timezone.utc))] == ["t1", "t2", "t3"]
    assert fake.http.closed == 3 and len(waits) == 3            # 다시 하기 전에 끊긴 연결을 버린다

def test_gmail_gives_up_on_a_long_outage_and_still_reports_it(monkeypatch):   # 죽은 것을 살아 있는 것처럼 보이지 않게 — 오래 끊기면 수집 중단
    waits = []
    monkeypatch.setattr(gmail.time, "sleep", waits.append)
    fake = _FlakyGmail([TimeoutError("The read operation timed out")] * 20)
    col = gmail.GmailCollector("a", "a@x.com", "file:gmail-a.json", set(), Path("/nonexistent"), service_factory=lambda: fake)
    with pytest.raises(TimeoutError):
        col.collect(datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert waits == list(gmail.NET_WAITS) and sum(waits) <= 90

def test_gmail_does_not_retry_a_bad_token(monkeypatch):     # 토큰이 틀린 것은 다시 해도 안 된다 — 곧바로 수집 중단
    waits = []
    monkeypatch.setattr(gmail.time, "sleep", waits.append)
    col = gmail.GmailCollector("a", "a@x.com", "file:gmail-a.json", set(), Path("/nonexistent"), service_factory=lambda: _FakeGmail(fail=True))
    with pytest.raises(RuntimeError):
        col.collect(datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert waits == []


def test_gmail_keeps_labels_for_junk_rules():               # 광고·자동 알림을 가르려면 Gmail 이 붙인 분류가 필요하다
    th = {"id": "t1", "messages": [{"id": "m1", "internalDate": "1760000000000", "labelIds": ["UNREAD", "CATEGORY_PROMOTIONS", "INBOX"], "snippet": "특가",
          "payload": {"headers": [{"name": "From", "value": "shop@x.example"}, {"name": "To", "value": "minji@example.com"}, {"name": "Subject", "value": "세일"}]}}]}
    e = gmail.parse_thread(th, {"minji@example.com"}, "minji@example.com")[0]
    assert e.raw["labels"] == ["CATEGORY_PROMOTIONS", "INBOX", "UNREAD"] and e.is_read is False

def test_gmail_link_opens_the_thread_in_the_right_account():
    th = {"id": "t77", "messages": [{"id": "m1", "internalDate": "1760000000000", "labelIds": ["INBOX"], "snippet": "x",
          "payload": {"headers": [{"name": "From", "value": "kim@client.example"}, {"name": "To", "value": "snack@example.com"}, {"name": "Subject", "value": "s"}]}}]}
    e = gmail.parse_thread(th, set(), "snack@example.com")[0]
    assert e.link == "https://mail.google.com/mail/u/?authuser=snack%40example.com#all/t77"


def test_kakao_query_hides_the_key_on_failure():
    import subprocess
    from urgent_board.collectors import kakao
    seen = {}
    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Error: file is not a database")
    with pytest.raises(kakao.KakaocliError) as e:
        kakao.query("SELECT 1", {"database_path": "/db", "key": "SECRETKEY"}, "/bin/kakaocli", run=run)
    assert "SECRETKEY" not in str(e.value) and "--key" in seen["cmd"] and "file is not a database" in str(e.value)
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)
    with pytest.raises(kakao.KakaocliError) as e:
        kakao.query("SELECT 1", {"database_path": "/db", "key": "SECRETKEY"}, "/bin/kakaocli", run=slow)
    assert "SECRETKEY" not in str(e.value)                                  # TimeoutExpired 의 글에는 명령 줄(열쇠)이 든다 — 넣지 않는다


def test_kakao_query_without_auth_lets_kakaocli_find_the_key():
    import subprocess
    from urgent_board.collectors import kakao
    def run(cmd, **kw):
        assert "--key" not in cmd and "--db" not in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="[[3]]", stderr="")
    assert kakao.query("SELECT COUNT(*) FROM NTChatRoom", None, "/bin/kakaocli", run=run) == [[3]]


def test_kakao_auth_order(tmp_path):
    from urgent_board.collectors import kakao
    cache, helper = tmp_path / "auth.json", tmp_path / "helper.py"
    assert kakao.load_auth(cache, helper) is None                           # 둘 다 없다 → kakaocli 가 스스로 찾는다
    calls = []
    helper.write_text("")
    def refresh(cmd, **kw):
        calls.append(cmd); cache.write_text('{"database_path": "/db", "key": "k"}')
    assert kakao.load_auth(cache, helper, run=refresh) == {"database_path": "/db", "key": "k"} and calls and calls[0][-2:] == ["auth", "--refresh"]
    assert kakao.load_auth(cache, helper, run=lambda *a, **k: 1 / 0) == {"database_path": "/db", "key": "k"}   # 캐시가 있으면 새로 고치지 않는다
    cache.write_text("깨짐")
    assert kakao.load_auth(cache, tmp_path / "none.py") is None


def test_kakao_module_no_longer_imports_kakao_archive():
    src = (Path(__file__).resolve().parents[1] / "urgent_board" / "collectors" / "kakao.py").read_text(encoding="utf-8")
    assert "kakao-archive" not in src and "_load_kakao_archive" not in src


def test_gmail_token_refs(tmp_path):
    import json
    from urgent_board.collectors import gmail
    (tmp_path / "gmail-a.json").write_text(json.dumps({"refresh_token": "r"}), encoding="utf-8")
    assert gmail.read_token("file:gmail-a.json", tmp_path) == {"refresh_token": "r"}
    got = gmail.read_token("keychain:Svc.a/oauth2_token", tmp_path,
                           get_password=lambda s, a: '{"refresh_token": "k"}' if (s, a) == ("Svc.a", "oauth2_token") else None)
    assert got == {"refresh_token": "k"}
    for bad in ("file:../x.json", "keychain:nope", "http://x"):
        with pytest.raises(ValueError):
            gmail.read_token(bad, tmp_path)
    with pytest.raises(RuntimeError, match="없다"):
        gmail.read_token("file:none.json", tmp_path)
    with pytest.raises(RuntimeError, match="없다"):
        gmail.read_token("keychain:Svc.a/oauth2_token", tmp_path, get_password=lambda s, a: None)


class _SentGmail(_FakeGmail):
    """받은편지함 질의에는 t1, 보낸 편지 질의에는 t1(겹침) · t9(7일 넘은 스레드에 단 내 답장 + 남의 옛 글)."""
    def __init__(self): super().__init__(); self.qs = []
    def list(self, userId, q, maxResults, pageToken=None):
        self.qs.append(q)
        return _Req(lambda: {"threads": [{"id": "t1"}, {"id": "t9"}] if "in:sent" in q else [{"id": "t1"}]})
    def get(self, userId, id, format, metadataHeaders):
        if id != "t9":
            return super().get(userId, id, format, metadataHeaders)        # 부모가 gets 에 적는다
        self.gets.append(id)
        def msg(mid, frm, labels, ms):
            return {"id": mid, "internalDate": ms, "labelIds": labels, "snippet": "",
                    "payload": {"headers": [{"name": "From", "value": frm}, {"name": "To", "value": "kim@client.com"}, {"name": "Subject", "value": "Re: 견적"}]}}
        return _Req(lambda: {"id": "t9", "messages": [msg("o1", "kim@client.com", ["UNREAD"], "1759000000000"), msg("r9", "a@x.com", ["SENT"], "1760000000000")]})


def test_gmail_also_reads_my_recent_sent_mail_but_only_my_own():   # 검토 I5: 7일 넘은 스레드에 단 내 답장은 받은편지함 질의에 안 걸릴 수 있다
    fake = _SentGmail()
    col = gmail.GmailCollector("a", "a@x.com", "file:gmail-a.json", set(), Path("/nonexistent"), service_factory=lambda: fake)
    got = col.collect(datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert any("in:sent" in q for q in fake.qs) and fake.gets.count("t1") == 1          # 겹친 스레드는 한 번만 읽는다
    assert [(e.chat_id, e.message_id, e.is_from_me) for e in got] == [("t1", "m-t1", False), ("t9", "r9", True)]   # 보낸 편지 쪽은 내 글만
