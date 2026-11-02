#!/bin/sh
# WideEye 설치(코드 폴더 안에서) — install/wideeye-install.sh 를 이 폴더를 깔 곳이자 코드로 삼아 돌린다(결정 R11). 다시 돌려도 된다.
#   ./install.sh      고정 판 uv · 파이썬(이 폴더의 .tools/ · .python/) → .venv 와 부품(requirements.lock, 지문 확인) → Pretendard 글꼴
#                     → bin/wideeye install --no-open → 끝 줄에 다음 할 일. 화면은 열지 않는다(bin/wideeye open setup).
# 이미 .venv 가 다른 파이썬으로 있으면(이 작업장의 맥들) 지우지 않고 부품만 그 안에 깐다 — 전체 디스크 접근 허락이 그 파이썬에
# 걸려 있다(결정 D-T8-2). 옛 판(Homebrew 의 python3 로 가상 환경 · pip)은 git show 30b13ca:install.sh — 되돌릴 때만.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd -P)"
WIDEEYE_SOURCE="$DIR" WIDEEYE_HOME="$DIR" exec /bin/sh "$DIR/install/wideeye-install.sh" "$@"
