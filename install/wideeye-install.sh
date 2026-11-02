#!/bin/sh
# WideEye 설치 — 받아서 바로 돌리는 한 줄(`curl -fsSL …/install/wideeye-install.sh | sh`)용. 코드 폴더 안의 ./install.sh 도 이것을 부른다.
#
# 하는 일(차례): 맥 · macOS 판 · 칩 · 디스크 확인 → 깔 곳에 uv(고정 판 · 지문 확인) → 고정 판 파이썬(uv 가 받고 스스로 지문 확인)
#   → WideEye 코드(릴리스 묶음 · 지문 확인, 또는 WIDEEYE_SOURCE 폴더) → .venv 와 부품(requirements.lock — 부품마다 지문)
#   → Pretendard 글꼴(사용자 글꼴 폴더 · 이미 있으면 건너뜀 · 지문 확인) → `bin/wideeye install --no-open`
#   → 끝의 세 줄: 전체 디스크 접근에 넣을 파일 · `…/bin/wideeye` 전체 위치 · "다음: … guide next"(GUIDE.md 2-3 이 이 꼴을 기대한다).
# 관리자 암호를 묻지 않는다 — 모두 사용자 폴더에 쓴다. 다시 돌려도 된다(이미 있는 것은 건너뛴다).
# 실패하면 마지막 줄에 쉬운 말 한 줄을 남기고 종료값 1. 받다 끊긴 스크립트가 반만 돌지 않게 본문은 모두 main() 안에 있고,
# 마지막 줄이 main 을 부른다(설계 G10 · 결정 R11).
#
# 바꿀 수 있는 것(모두 없어도 된다):
#   WIDEEYE_HOME       깔 곳 = 코드 폴더(기본 $HOME/WideEye). uv 는 그 안 .tools/, 파이썬은 .python/, 부품은 .venv/
#   WIDEEYE_SOURCE     이 폴더의 코드를 쓴다(내려받지 않는다 — 개발 · 첫 릴리스 전 시험). ./install.sh 는 자기 폴더를 넘긴다
#   WIDEEYE_FONTS_DIR  글꼴을 넣을 곳(기본 $HOME/Library/Fonts)
#   WIDEEYE_FETCH      file:///폴더 — 인터넷 대신 그 폴더에서 같은 이름의 파일을 받는다(시험). 지문 확인은 그대로 한다
#   WIDEEYE_SYNC_PARTS 1 이면 이미 있던 .venv(다른 파이썬)의 부품도 잠금 판에 맞춘다 — 없으면 돌고 있는 부품은 그대로 둔다(판이 내려가지 않게)
#   자료 폴더(URGENT_BOARD_HOME) · 작업 이름(URGENT_BOARD_LABEL_PREFIX)은 `wideeye install` 이 그대로 읽는다.

# ==== 고정 판 · 지문 — 판을 올릴 때는 이 묶음만 고치고 지문도 함께 바꾼다(curl | sh 로는 다른 파일을 읽을 수 없어 여기에 둔다) ====
# 이름은 모두 PIN_ 으로 시작한다(아래에서 사람의 UV_* 설정을 지울 때 함께 지워지지 않게). 시험이 주소마다 지문이 있는지 본다.
# uv: 공식 릴리스 astral-sh/uv 0.12.23(2026-10-03). 지문은 릴리스의 .sha256 파일 · sha256.sum · GitHub 이 붙인 digest ·
#   GitHub 출처 증명(release.yml 이 서명) 넷이 모두 같은 값(2026-10-19 확인).
PIN_UV_VERSION='0.12.23'
PIN_UV_ARM_URL='https://github.com/astral-sh/uv/releases/download/0.12.23/uv-aarch64-apple-darwin.tar.gz'
PIN_UV_ARM_SHA256='50487ae565ccd96e499056b4674d438f4c53170202617b4c759defe0c6a1b544'
PIN_UV_INTEL_URL='https://github.com/astral-sh/uv/releases/download/0.12.23/uv-x86_64-apple-darwin.tar.gz'
PIN_UV_INTEL_SHA256='960da44cb4b73685206ddd250b19e0a117fa41095710c1038f081f5cb613efb4'
# 파이썬: uv 가 python-build-standalone 에서 받고, uv 안에 적힌 지문(위 uv 판의 download-metadata.json)으로 스스로 확인한다 —
#   uv 를 지문으로 고정했으니 파이썬도 고정된다. 3.12.15 · 빌드 20261003: Apple 칩 ad8d0c637c0a36b9…481a2 · 인텔 562c30864ece2cb1…bd355.
#   이 판을 올리면 전체 디스크 접근에 넣을 파일의 위치가 바뀐다(사람이 허락을 다시 준다).
PIN_PY_VERSION='3.12.15'
# Pretendard 1.3.9(OFL) 공식 릴리스 묶음 — 안의 고정 꼴 글꼴 9개가 저장소 태그 v1.3.9 의 파일과 같음을 확인(git blob 대조, 2026-10-19).
PIN_FONT_URL='https://github.com/orioncactus/pretendard/releases/download/v1.3.9/Pretendard-1.3.9.zip'
PIN_FONT_SHA256='04be351a74d6bf7d60c480a3087e51d185485d35a52023142af1df19eb8c428a'
# WideEye 릴리스 묶음 — 릴리스 때 주소(GitHub 릴리스 파일 …/releases/download/<판>/wideeye-<판>.tar.gz — 결정 R12, GitHub 이 그때그때
#   만드는 archive 묶음이 아니다)와 지문을 채운다. 비어 있으면 내려받지 않고 멈춘다(WIDEEYE_SOURCE 로만 깐다).
PIN_APP_URL='https://github.com/parkds-claude/wideeye/releases/download/v0.1.0/wideeye-v0.1.0.tar.gz'
PIN_APP_SHA256='0fc5c011a2cccbc91b5a4fe3b0348a215648d69f1c4d1b9bcdc37675af084ca2'
# 휠이 없어 소스로 짓는 부품(지을 도구 setuptools 는 install/requirements-build.lock 의 지문으로 확인한다).
PIN_SOURCE_BUILDS='pyaes'
PIN_NEED_KB='1048576'
# ==== 고정 판 끝 ====

say() {
  printf '%s\n' "$*"
}

fail() {
  # 쉬운 말 한 줄 — 사람에게 그대로 전해진다(개발자 낱말 금지: tests/forbidden_words.txt). 마지막 줄이 되게 오류 쪽으로.
  printf '%s\n' "$*" >&2
  exit 1
}

fetch() {
  # 주소 → 파일. WIDEEYE_FETCH(file://) 가 있으면 그 폴더에서 같은 이름을 받는다. 받지 못하면 0 이 아닌 값.
  if [ -n "${WIDEEYE_FETCH:-}" ]; then
    case $WIDEEYE_FETCH in
      file://*) ;;
      *) return 2 ;;
    esac
    curl -fsSL --proto '=file' -o "$2" "${WIDEEYE_FETCH%/}/${1##*/}"
  else
    # 멈춘 연결이 끝없이 기다리지 않게 — 연결 20초, 30초 동안 초당 1KB 아래면 끊는다(검토 4. uv 의 받기는 uv 의 기본 30초 제한)
    curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 2 --connect-timeout 20 --speed-limit 1024 --speed-time 30 \
      -o "$2" "$1"
  fi
}

get() {
  # 주소 지문 파일 — 받고 나서 쓰기 전에 지문부터 본다. 다르면 지우고 멈춘다(풀거나 깔지 않는다).
  fetch "$1" "$3" || fail "인터넷에서 필요한 파일을 받지 못했어요 — 연결을 확인하고 같은 한 줄을 다시 돌려 주세요."
  got=$(shasum -a 256 "$3" | awk '{print $1}') || got=''
  if [ -z "$2" ] || [ "$got" != "$2" ]; then
    rm -f "$3"
    fail "받은 파일이 미리 정해 둔 값(지문)과 달라 쓰지 않고 멈췄어요 — 잠시 뒤 다시 돌려 보고, 또 그러면 WideEye 저장소에 알려 주세요."
  fi
}

run_uv() {
  # uv 의 임시 파일(잠금 파일 등)도 이 설치의 임시 폴더에 — 끝나면 함께 지운다(실측: 그냥 두면 사람의 임시 폴더에 남았다)
  env TMPDIR="$TMPD" "$UV" "$@"
}

has_font() {
  for f in "$1"/Pretendard-*.otf; do
    [ -e "$f" ] && return 0
  done
  return 1
}

main() {
  set -eu
  umask 022
  exec </dev/null                       # 아래 명령이 설치 글(curl | sh 의 입력)을 읽어 먹지 않게
  PATH=/usr/bin:/bin:/usr/sbin:/sbin    # 이 맥의 기본 도구만(다른 판의 tar · curl 을 쓰지 않게)
  export PATH

  # ---- 0. 이 맥 ----
  [ "$(uname -s)" = Darwin ] || fail "WideEye 는 맥에서만 깔 수 있어요."
  [ "$(id -u)" != 0 ] || fail "관리자 계정으로 돌리지 마세요 — 평소 쓰는 계정의 터미널에서 같은 한 줄을 다시 돌려 주세요."
  ver=$(sw_vers -productVersion 2>/dev/null) || ver=''
  major=${ver%%.*}
  case $major in
    '' | *[!0-9]*) fail "이 맥의 macOS 판을 읽지 못했어요 — 같은 한 줄을 다시 돌려 주세요." ;;
  esac
  [ "$major" -ge 13 ] || fail "이 맥의 macOS 는 $ver 이에요 — WideEye 는 13(Ventura) 이상에서 돌아요. macOS 를 올린 뒤 다시 돌려 주세요."
  arch=$(uname -m)
  if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
    arch=arm64                          # 인텔 흉내(Rosetta)로 돈 터미널이어도 Apple 칩 것을 깐다
  fi
  case $arch in
    arm64) uv_url=$PIN_UV_ARM_URL uv_sha=$PIN_UV_ARM_SHA256 ;;
    x86_64) uv_url=$PIN_UV_INTEL_URL uv_sha=$PIN_UV_INTEL_SHA256 ;;
    *) fail "이 맥의 칩($arch)을 알아보지 못했어요 — WideEye 는 Apple 칩과 인텔 맥에서 돌아요." ;;
  esac

  # ---- 1. 깔 곳 ----
  [ -n "${HOME:-}" ] || fail "이 계정의 홈 폴더를 알 수 없어요 — 평소 쓰는 터미널에서 다시 돌려 주세요."
  APP=${WIDEEYE_HOME:-$HOME/WideEye}
  case $APP in
    /*) ;;
    *) fail "깔 곳은 / 로 시작하는 전체 위치여야 해요: $APP" ;;
  esac
  if [ -d "$APP" ]; then
    APP=$(cd "$APP" && pwd -P) || fail "깔 곳을 열지 못했어요: $APP"
    if [ ! -e "$APP/.tools/wideeye-install" ] && [ ! -e "$APP/bin/wideeye" ] \
       && [ -n "$(ls -A "$APP" | grep -v -x -e .DS_Store || true)" ]; then
      fail "'$APP' 에 이미 다른 것이 들어 있어 멈췄어요 — 그 폴더를 옮기거나 비운 뒤 다시 돌려 주세요."
    fi
  fi
  SRC=${WIDEEYE_SOURCE:-}
  if [ -n "$SRC" ]; then
    SRC=$(cd "$SRC" 2>/dev/null && pwd -P) || fail "고른 코드 폴더를 열지 못했어요: ${WIDEEYE_SOURCE:-}"
    [ -f "$SRC/bin/wideeye" ] && [ -f "$SRC/requirements.lock" ] || fail "고른 코드 폴더에 WideEye 가 없어요: $SRC"
  else
    case $PIN_APP_URL in
      https://*) ;;
      *) PIN_APP_SHA256='' ;;               # 저장소 주소가 아직 정해지지 않았다
    esac
    [ -n "$PIN_APP_SHA256" ] || fail "아직 내려받을 판이 없어요 — WideEye 가 첫 판을 내면 같은 한 줄로 깔 수 있어요."
  fi
  FONTS=${WIDEEYE_FONTS_DIR:-$HOME/Library/Fonts}
  probe=$APP
  while [ ! -d "$probe" ]; do
    probe=$(dirname "$probe")
  done
  free=$(df -Pk "$probe" 2>/dev/null | awk 'NR == 2 {print $4}') || free=''
  case $free in
    '' | *[!0-9]*) ;;                   # 읽지 못하면 막지 않는다(대략 보는 것이다)
    *) [ "$free" -ge "$PIN_NEED_KB" ] || fail "디스크 공간이 모자라요 — 1GB 쯤 비운 뒤 같은 한 줄을 다시 돌려 주세요." ;;
  esac

  mkdir -p "$APP/.tools" || fail "깔 곳에 폴더를 만들지 못했어요: $APP"
  APP=$(cd "$APP" && pwd -P)
  : >"$APP/.tools/wideeye-install"      # 이 폴더는 WideEye 를 깐 곳이다(다시 돌릴 때 '다른 것'으로 보지 않게)
  TMPD=$(mktemp -d "${TMPDIR:-/tmp}/wideeye-install.XXXXXX") || fail "임시 폴더를 만들지 못했어요 — 디스크 공간을 확인하고 다시 돌려 주세요."
  trap 'rm -rf "$TMPD"' EXIT
  trap 'exit 130' INT TERM HUP
  say "WideEye 를 깔아요 — 깔 곳: $APP"

  # uv 에게는 이 설치의 값만 준다(사람의 uv 설정 · 다른 받을 곳을 쓰지 않는다)
  for v in $(env | sed -n 's/^\(UV_[A-Za-z0-9_]*\)=.*/\1/p'); do
    unset "$v"
  done
  unset VIRTUAL_ENV CONDA_PREFIX PYTHONHOME PYTHONPATH 2>/dev/null || true
  TOOLS=$APP/.tools
  PYDIR=$APP/.python
  VENV=$APP/.venv
  UV=$TOOLS/uv
  UV_NO_CONFIG=1
  UV_CACHE_DIR=$TMPD/uv-cache          # 받은 부품 사본은 끝나면 지운다(깔린 것은 .venv 에 따로 있다 — 실측 200MB)
  UV_PYTHON_INSTALL_DIR=$PYDIR
  export UV_NO_CONFIG UV_CACHE_DIR UV_PYTHON_INSTALL_DIR

  # ---- 2. uv ----
  if [ -x "$UV" ] && run_uv --version 2>/dev/null | grep -q "^uv $PIN_UV_VERSION[ (]"; then
    say "[1/6] 설치 도구 uv $PIN_UV_VERSION — 이미 있어요"
  else
    say "[1/6] 설치 도구 uv $PIN_UV_VERSION 받기"
    get "$uv_url" "$uv_sha" "$TMPD/uv.tar.gz"
    mkdir "$TMPD/uv" && tar -xzf "$TMPD/uv.tar.gz" -C "$TMPD/uv" || fail "받은 설치 도구를 풀지 못했어요 — 같은 한 줄을 다시 돌려 주세요."
    new=$(find "$TMPD/uv" -type f -name uv | head -n 1)
    [ -n "$new" ] || fail "받은 설치 도구가 비어 있어요 — 같은 한 줄을 다시 돌려 주세요."
    cp "$new" "$UV.new" && chmod 755 "$UV.new" && mv -f "$UV.new" "$UV" || fail "설치 도구를 깔 곳에 넣지 못했어요: $TOOLS"
  fi

  # ---- 3. 고정 판 파이썬과 .venv ----
  # 이미 다른 파이썬으로 만든 .venv(심볼릭 링크 포함)는 지우지 않는다 — 전체 디스크 접근 허락이 그 파이썬에 걸려 있다(결정 D-T8-2).
  vpy=''
  if [ -e "$VENV/bin/python" ]; then
    vpy=$(readlink -f "$VENV/bin/python" 2>/dev/null) || vpy=''
  fi
  case $vpy in
    "$PYDIR"/cpython-"$PIN_PY_VERSION"-*) venv_mode=ready ;;
    "$PYDIR"/*) venv_mode=renew ;;
    '') venv_mode=new ;;
    *) venv_mode=keep ;;
  esac
  if [ -L "$VENV" ]; then
    venv_mode=keep
  fi
  if [ "$venv_mode" = keep ] || [ "$venv_mode" = ready ]; then
    [ -x "$vpy" ] && "$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
      || fail "이미 있는 실행 환경이 돌지 않아요 — '$VENV' 폴더를 지운 뒤 같은 한 줄을 다시 돌려 주세요."
  fi
  # 이미 있던 실행 환경(다른 파이썬)의 부품: 불러와지면 그대로 둔다(돌고 있는 맥의 판을 내리지 않는다 — 검토 2).
  # 빠졌거나 WIDEEYE_SYNC_PARTS=1 이면 잠금으로 맞춘다. 다만 .venv 가 다른 폴더를 가리키는 링크면 절대 바꾸지 않는다.
  parts=install
  if [ "$venv_mode" = keep ]; then
    if [ "${WIDEEYE_SYNC_PARTS:-}" != 1 ] && "$VENV/bin/python" -c 'import flask, yaml, telethon, serial, objc' >/dev/null 2>&1; then
      parts=leave
    elif [ -L "$VENV" ]; then
      fail "이 폴더의 실행 환경은 다른 폴더를 가리키고 있어서 바꾸지 않아요 — 그 폴더에서 같은 한 줄을 돌려 주세요."
    fi
  fi
  if [ "$venv_mode" = keep ]; then
    say "[2/6] 실행 환경 — 이미 있는 것을 그대로 써요(바꾸면 '전체 디스크 접근' 허락을 다시 줘야 해요. 새로 만들려면 $VENV 폴더를 지우고 다시)"
  elif [ "$venv_mode" = ready ]; then
    say "[2/6] 실행 환경 $PIN_PY_VERSION — 이미 있어요"
  else
    say "[2/6] 실행 환경 $PIN_PY_VERSION 받기"
    run_uv python install -q --no-bin "$PIN_PY_VERSION" \
      || fail "WideEye 가 쓰는 실행 도구를 받지 못했어요 — 연결을 확인하고 같은 한 줄을 다시 돌려 주세요."
    pybin=$(run_uv python find --managed-python "$PIN_PY_VERSION") || pybin=''
    case $pybin in
      "$PYDIR"/*) ;;
      *) fail "받은 실행 도구를 찾지 못했어요 — 같은 한 줄을 다시 돌려 주세요." ;;
    esac
  fi

  # ---- 4. WideEye 코드 ----
  if [ -n "$SRC" ] && [ "$SRC" = "$APP" ]; then
    say "[3/6] WideEye 코드 — 이 폴더 그대로"
  elif [ -n "$SRC" ]; then
    say "[3/6] WideEye 코드 — $SRC 에서 옮기기"
    if [ -e "$SRC/.git" ] && xcode-select -p >/dev/null 2>&1; then
      # 저장소에 올린 파일만(작업 중인 산출물 · 비밀 파일은 옮기지 않는다)
      (cd "$SRC" && git ls-files -z >"$TMPD/files" && tar --null -T "$TMPD/files" -cf "$TMPD/code.tar") \
        || fail "코드 폴더의 파일을 모으지 못했어요: $SRC"
    else
      (cd "$SRC" && tar --exclude ./.git --exclude ./.venv --exclude ./.python --exclude ./.tools -cf "$TMPD/code.tar" .) \
        || fail "코드 폴더의 파일을 모으지 못했어요: $SRC"
    fi
    tar -xpf "$TMPD/code.tar" -C "$APP" || fail "코드를 깔 곳에 옮기지 못했어요: $APP"
  else
    say "[3/6] WideEye 코드 받기"
    get "$PIN_APP_URL" "$PIN_APP_SHA256" "$TMPD/app.tar.gz"
    mkdir "$TMPD/app" && tar -xzf "$TMPD/app.tar.gz" -C "$TMPD/app" --strip-components 1 \
      || fail "받은 WideEye 묶음을 풀지 못했어요 — 같은 한 줄을 다시 돌려 주세요."
    [ -f "$TMPD/app/bin/wideeye" ] || fail "받은 묶음에 WideEye 가 없어요 — WideEye 저장소에 알려 주세요."
    (cd "$TMPD/app" && tar -cf "$TMPD/code.tar" .) && tar -xpf "$TMPD/code.tar" -C "$APP" \
      || fail "코드를 깔 곳에 옮기지 못했어요: $APP"
  fi
  [ -f "$APP/requirements.lock" ] && [ -f "$APP/install/requirements-build.lock" ] \
    || fail "WideEye 코드에 부품 목록이 없어요 — WideEye 저장소에 알려 주세요."

  # ---- 5. .venv 와 부품(부품마다 지문 — 소스로 짓는 부품의 지을 도구까지) ----
  if [ "$venv_mode" = new ] || [ "$venv_mode" = renew ]; then
    if [ -e "$VENV" ]; then
      run_uv venv -q --no-project --python "$pybin" --clear "$VENV" || fail "실행 환경을 만들지 못했어요 — 같은 한 줄을 다시 돌려 주세요."
    else
      run_uv venv -q --no-project --python "$pybin" "$VENV" || fail "실행 환경을 만들지 못했어요 — 같은 한 줄을 다시 돌려 주세요."
    fi
  fi
  if [ "$parts" = leave ]; then
    say "[4/6] 부품은 그대로 둬요(이미 있는 실행 환경 — 돌고 있는 판을 바꾸지 않아요)"
  else
    say "[4/6] WideEye 에 필요한 부품 깔기(부품마다 지문 확인)"
    run_uv pip install -q --python "$VENV/bin/python" --require-hashes --only-binary :all: --no-binary "$PIN_SOURCE_BUILDS" \
      -b "$APP/install/requirements-build.lock" -r "$APP/requirements.lock" \
      || fail "WideEye 에 필요한 부품을 받지 못했어요 — 연결을 확인하고 같은 한 줄을 다시 돌려 주세요."
  fi
  "$VENV/bin/python" -c 'import flask, yaml, telethon, serial, objc' >/dev/null 2>&1 \
    || fail "깐 부품을 불러오지 못했어요 — 같은 한 줄을 다시 돌려 주세요. 또 그러면 WideEye 저장소에 알려 주세요."

  # ---- 6. Pretendard 글꼴(현황판 글자 — 깔린 글꼴을 먼저 쓴다) ----
  if has_font "$FONTS" || has_font /Library/Fonts; then
    say "[5/6] Pretendard 글꼴 — 이미 있어요"
  else
    say "[5/6] Pretendard 글꼴 받기"
    get "$PIN_FONT_URL" "$PIN_FONT_SHA256" "$TMPD/font.zip"
    mkdir "$TMPD/font" && unzip -q -j "$TMPD/font.zip" 'public/static/Pretendard-*.otf' -d "$TMPD/font" \
      || fail "받은 글꼴 묶음을 풀지 못했어요 — 같은 한 줄을 다시 돌려 주세요."
    has_font "$TMPD/font" || fail "받은 글꼴 묶음에 글꼴이 없어요 — WideEye 저장소에 알려 주세요."
    mkdir -p "$FONTS" || fail "글꼴 폴더를 만들지 못했어요: $FONTS"
    for f in "$TMPD/font"/Pretendard-*.otf; do
      [ -e "$FONTS/${f##*/}" ] || cp "$f" "$FONTS/${f##*/}" || fail "글꼴을 넣지 못했어요: $FONTS"
    done
  fi

  # ---- 7. 이 맥에 등록 ----
  WE=$APP/bin/wideeye
  say "[6/6] WideEye 를 이 맥에 등록하기"
  # 등록 명령의 출력은 삼킨다 — 그 안의 1판 안내 · '지금 허락' 글이 아래 끝 줄과 어긋난다(검토 1). 실패할 때만 보인다.
  if ! "$WE" install --no-open >"$TMPD/register.log" 2>&1; then
    cat "$TMPD/register.log" >&2
    fail "WideEye 를 이 맥에 등록하지 못했어요 — 바로 위의 글을 AI 에게 보여 주세요."
  fi

  real=$(readlink -f "$VENV/bin/python" 2>/dev/null) || real=$VENV/bin/python
  say ""
  say "WideEye 를 깔았어요: $APP"
  say "나중에 '전체 디스크 접근 권한'에 넣을 파일(지금은 하지 않아요 — 그 단계에서 길이 알려 줘요): $real"
  say "WideEye 명령: $WE"
  say "다음: AI 에게 '$WE guide next' 를 부르라고 하세요."
}

main "$@"
