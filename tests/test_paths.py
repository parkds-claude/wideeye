import os
import subprocess
import sys
from pathlib import Path

from urgent_board import paths

ROOT = Path(__file__).resolve().parents[1]


def test_home_default_and_env(monkeypatch, tmp_path):
    monkeypatch.delenv("URGENT_BOARD_HOME", raising=False)
    assert paths.home() == Path.home() / "Library" / "Application Support" / "UrgentBoard"
    monkeypatch.setenv("URGENT_BOARD_HOME", str(tmp_path / "h"))
    assert paths.home() == tmp_path / "h"
    assert paths.db_path() == tmp_path / "h" / "state" / "urgent.db"
    assert paths.settings_file() == tmp_path / "h" / "settings.yaml" and paths.auth_dir() == tmp_path / "h" / "auth"


def test_ensure_makes_private_folders(tmp_path):
    h = paths.ensure(tmp_path / "h")
    for name in ("state", "logs", "backups", "auth"):
        assert (h / name).is_dir()
    assert oct(os.stat(h).st_mode & 0o777) == "0o700" and oct(os.stat(h / "auth").st_mode & 0o777) == "0o700"


def test_read_light_defaults_and_values(tmp_path):
    assert paths.read_light(tmp_path) == {"port": 5091, "tab5_usb_serial": "", "source": "default"}      # 파일이 없으면 기본값
    (tmp_path / "settings.yaml").write_text("server: {port: 5191}\ndisplay: {tab5_usb_serial: 'AA:BB'}\n", encoding="utf-8")
    assert paths.read_light(tmp_path) == {"port": 5191, "tab5_usb_serial": "AA:BB", "source": "settings"}
    (tmp_path / "settings.yaml").write_text("- 깨진\n- 파일\n", encoding="utf-8")
    assert paths.read_light(tmp_path)["source"] == "default"                                             # 깨졌어도 예외 없음


def test_read_light_falls_back_to_last_good_when_the_file_is_broken(tmp_path):
    """검토 I1: 손으로 깨뜨린 설정 파일을 '일련번호가 사라졌다'로 읽으면 탭5 창이 탭5 를 끈다 — 마지막으로 잘 돈 판을 본다."""
    (tmp_path / "settings.last-good.yaml").write_text("display: {tab5_usb_serial: 'AA:BB'}\n", encoding="utf-8")
    (tmp_path / "settings.yaml").write_bytes(b"display: {tab5_usb_serial: \xff\n")
    assert paths.read_light(tmp_path) == {"port": 5091, "tab5_usb_serial": "AA:BB", "source": "last-good"}


def test_cli_prints_paths(tmp_path):
    env = dict(os.environ, URGENT_BOARD_HOME=str(tmp_path))
    out = subprocess.run([sys.executable, "-m", "urgent_board.paths", "logs"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and out.stdout.strip() == str(tmp_path / "logs")
    bad = subprocess.run([sys.executable, "-m", "urgent_board.paths", "nope"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert bad.returncode == 2


def test_shell_rule_matches_python():
    """kiosk.sh 는 파이썬을 띄우지 않고 같은 규칙을 직접 푼다(설계 3-2) — 글자가 같아야 한다."""
    sh = (ROOT / "tools" / "kiosk.sh").read_text(encoding="utf-8")
    assert 'HOME_DIR="${URGENT_BOARD_HOME:-$HOME/Library/Application Support/UrgentBoard}"' in sh
