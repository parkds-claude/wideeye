"""메뉴 막대 앱을 실제로(pyobjc) 띄워 막대 글과 차림표를 만들어 본다 — `--selftest` 는 1초쯤 막대에 보였다가 끝난다.
화면이 있는 로그인 세션에서만 돈다(봇·SSH 에서는 건너뛴다). 건너뛰려면 URGENT_BOARD_SKIP_GUI=1."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATUS = {"totals": {"tasks": 3, "unread": 1}, "blind": "some", "down": ["카톡"], "checked_at": "21:30", "settings_error": "",
          "legacy_config": False, "cards": [{"key": "a", "name": "동네 합창 모임", "n": 3, "urgent": 1, "waiting": 2}],
          "tasks": [{"key": "k1", "box": "동네 합창 모임", "what": "기사 연락처 회신", "meta": "메일 김 · 10/23까지", "unread": True, "due_soon": True,
                     "old": False, "link": ""}], "tab5": None}


def _gui_session() -> bool:
    if os.environ.get("URGENT_BOARD_SKIP_GUI"):
        return False
    try:
        return subprocess.run(["launchctl", "managername"], capture_output=True, text=True, timeout=5).stdout.strip() == "Aqua"
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _gui_session(), reason="화면이 있는 로그인 세션이 아니다")


def selftest(tmp_path, arg):
    env = dict(os.environ, URGENT_BOARD_HOME=str(tmp_path / "home"))
    return subprocess.run([sys.executable, str(ROOT / "tools" / "menubar.py"), "--selftest", arg], env=env,
                          capture_output=True, text=True, timeout=60)


def test_selftest_builds_the_bar_and_menu(tmp_path):
    f = tmp_path / "status.json"
    f.write_text(json.dumps(STATUS, ensure_ascii=False), encoding="utf-8")
    r = selftest(tmp_path, str(f))
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0] == "TITLE 할 일 3 !" and "ORANGE 3| !" in lines                      # 숫자와 ! 만 주황
    assert "MENU 수집 중단: 카톡" in lines and "MENU 동네 합창 모임\t할 일 3" in lines and "MENU 끝내기" in lines
    assert "MENU ● 기사 연락처 회신\t동네 합창 모임" in lines and "SUB 원문 열기|처리됨" in lines        # 할 일마다 하위 차림표
    assert "MENU 관리" in lines and "SUB 자세한 설정 화면|엔진 다시 시작|기록 폴더 열기" in lines      # 2026-10-16 — 설정 쪽을 하위로
    assert "MENU -" in lines


def test_selftest_without_an_engine(tmp_path):
    r = selftest(tmp_path, "none")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "TITLE 확인 불가" and "MENU 엔진이 답하지 않는다" in r.stdout
