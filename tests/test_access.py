from pathlib import Path

import pytest

from urgent_board import config, paths
from urgent_board.store import Store
from urgent_board.web import create_app

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
OURS = {"Origin": "http://localhost"}                    # 시험 클라이언트의 주소(실제로는 http://127.0.0.1:5091)


def app(tmp_path, token="T0KEN"):
    return create_app(config.load(HOME), Store(tmp_path / "t.db"), token=token).test_client()


def test_token_file_is_made_once_and_private(tmp_path):
    t = paths.api_token(tmp_path, create=True)
    assert len(t) >= 40 and paths.api_token(tmp_path) == t and paths.api_token(tmp_path / "none") == ""
    assert oct((paths.auth_dir(tmp_path) / "api-token").stat().st_mode & 0o777) == "0o600"


def test_pages_and_api_need_the_token(tmp_path):
    c = app(tmp_path)
    assert c.get("/healthz").status_code == 200 and c.get("/static/board.css").status_code == 200     # 감시 · 글꼴은 열쇠 없이
    for path in ("/", "/list", "/api/items"):
        assert c.get(path).status_code == 401, path
    assert c.get("/api/items", headers={"X-UB-Token": "T0KEN"}).status_code == 200
    r = c.get("/auth?t=T0KEN&next=/list")
    assert r.status_code == 302 and r.headers["Location"].endswith("/list") and "ub=T0KEN" in r.headers["Set-Cookie"]
    assert "HttpOnly" in r.headers["Set-Cookie"] and "SameSite=Strict" in r.headers["Set-Cookie"]   # 남의 사이트가 연 길로는 쿠키가 안 간다(검토 I2)
    assert c.get("/list").status_code == 200                                     # 쿠키가 심어졌다
    assert c.get("/auth?t=WRONG&next=/").status_code == 401


def test_locked_page_says_how_to_open_and_leaks_nothing(tmp_path):
    r = app(tmp_path).get("/settings")
    body = r.get_data(as_text=True)
    assert r.status_code == 401 and "T0KEN" not in body
    assert "auth/api-token" in body and 'action="/auth"' in body and 'name="t"' in body          # 열쇠를 넣어 여는 칸(검토 M10)
    assert 'name="next" value="/settings"' in body                                              # 연 뒤 그 쪽으로


def test_empty_or_short_token_file_is_made_again(tmp_path):
    """빈 열쇠 파일(만드는 도중 꺼짐 · 손으로 비움)이 문을 열어 두지 않는다(검토 I1)."""
    f = paths.auth_dir(tmp_path) / "api-token"
    paths.ensure(tmp_path)
    f.write_text("")
    assert paths.api_token(tmp_path) == ""                                       # 읽기만 하는 쪽(창 · 메뉴 막대): 열쇠 없음
    t = paths.api_token(tmp_path, create=True)
    assert len(t) >= 40 and f.read_text().strip() == t and oct(f.stat().st_mode & 0o777) == "0o600"
    f.write_text("abc\n")
    assert paths.api_token(tmp_path) == ""
    assert paths.api_token(tmp_path, create=True) not in ("", "abc")


def test_an_empty_token_does_not_open_the_doors(tmp_path):
    with pytest.raises(ValueError):
        create_app(config.load(HOME), Store(tmp_path / "t.db"), token="")


def test_auth_next_stays_on_this_site(tmp_path):
    c = app(tmp_path)
    for bad in ("//evil.example/", "https://evil.example/", "javascript:alert(1)"):
        r = c.get("/auth", query_string={"t": "T0KEN", "next": bad})
        assert r.status_code == 302 and r.headers["Location"].endswith("/") and "evil" not in r.headers["Location"]


def test_writes_need_json_and_our_origin(tmp_path):
    c = app(tmp_path, token=None)                                                # 열쇠가 꺼져 있어도 남의 페이지는 막는다
    assert c.post("/api/dismiss", json={"key": "k"}, headers=OURS).status_code == 204
    assert c.post("/api/dismiss", json={"key": "k"}).status_code == 204          # Origin 이 없는 요청(같은 맥의 도구)
    assert c.post("/api/dismiss", data='{"key": "k"}', content_type="text/plain").status_code == 415
    assert c.post("/api/dismiss", json={"key": "k"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/dismiss", json={"key": "k"}, headers={"Origin": "http://localhost:6666"}).status_code == 403
    assert c.post("/api/dismiss", json={"key": "k"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_without_token_everything_is_open_like_before(tmp_path):
    c = app(tmp_path, token=None)
    assert c.get("/api/items").status_code == 200 and c.get("/").status_code == 200
