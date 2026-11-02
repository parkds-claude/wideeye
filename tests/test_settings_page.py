"""설정 화면의 틀과 화면 코드의 규칙(디자인 문서 '설정 화면' 절)."""
import re
from pathlib import Path

from urgent_board import config
from urgent_board.store import Store
from urgent_board.web import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "urgent_board" / "static"
MODULES = ("main.js", "h.js", "model.js", "boxes.js", "connect.js", "simple.js")


def test_page_loads_the_module_and_every_module_exists(tmp_path):
    c = create_app(config.load(ROOT / "tests" / "fixtures" / "home"), Store(tmp_path / "t.db")).test_client()
    html = c.get("/settings").get_data(as_text=True)
    assert 'type="module" src="/static/settings/main.js"' in html and '/static/settings.css' in html
    for m in MODULES:
        assert (STATIC / "settings" / m).exists(), m
        assert c.get(f"/static/settings/{m}").status_code == 200


def test_settings_code_never_writes_html_strings():
    """방 이름 · 보낸 사람 · 메시지 요약은 남이 쓴 글이다 — 글자로만 넣는다(textContent)."""
    for m in MODULES:
        src = (STATIC / "settings" / m).read_text(encoding="utf-8")
        assert "innerHTML" not in src and "outerHTML" not in src and "insertAdjacentHTML" not in src, m
        assert "alert(" not in src and "confirm(" not in src and "prompt(" not in src, m          # 브라우저 대화상자 대신 판(modal)


def test_settings_css_uses_tokens_and_no_motion():
    css = (STATIC / "settings.css").read_text(encoding="utf-8")
    root, rest = css.split("}", 1)[0], css.split("}", 1)[1]
    assert ":root" in root and not re.search(r"#[0-9a-fA-F]{3,8}\b", rest)                       # 색 값은 :root 의 토큰에만
    assert "transition" not in css and "animation" not in css and "box-shadow" not in css and "gradient" not in css
    for token in ("--bg:#171717", "--fg:#fafafa", "--muted:#b4b4b4", "--line:#2e2e2e", "--urgent:var(--neon-orange)"):
        assert token in css.replace(" ", "")
    assert all(f"--fs-{k}" in css for k in ("s", "m", "l", "xl"))
