"""시험이 실제 자료 폴더(~/Library/Application Support/UrgentBoard)를 만들거나 읽지 않게, 모든 시험에서 자료 폴더를 임시 폴더로 둔다."""
import os

import pytest

os.environ["URGENT_BOARD_LABEL_PREFIX"] = "io.examplecorp.urgent-board-test"   # launchd 이름 — 시험이 운영 작업 이름을 만들지 않게(묶음 3)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("URGENT_BOARD_HOME", str(tmp_path_factory.mktemp("ub-home")))
