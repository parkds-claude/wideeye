#!/bin/sh
# 탭5 현황판 창(tools/kiosk_window.py) — 현황판 페이지를 화면 밖에서 그리고, 그림(1280×720 JPEG)으로 떠서 USB-C 직렬로 탭5 에 보낸다
# (설계 docs/exec-plans/2026-10-15-tab5-usb-frames-design.md). 창은 맥의 어느 화면에도 나타나지 않는다. 전체 화면은 쓰지 않는다.
#
# 창은 launchd 작업(io.github.wideeye.kiosk)으로 돈다 — 로그인하면 스스로 뜨고, 죽으면 10초 안에 되살아난다.
# (2026-10-13: 로그아웃하자 창이 닫혀 탭5 가 비었고, 창이 죽어도 되살리는 것이 없었다. 사용자가 이 작업에 한해 권한을 줌.)
#   tools/kiosk.sh            등록하고 띄운다(이미 떠 있으면 그대로)
#   tools/kiosk.sh restart    창을 껐다가 다시 띄운다 — 창 코드(kiosk_window.py · tab5_link.py)를 고친 뒤. 화면 코드(board.js·css)는 페이지가 스스로 새로 읽는다
#   tools/kiosk.sh stop       끈다(다음 로그인 때 다시 뜬다 — 아예 빼려면 uninstall)
#   tools/kiosk.sh status     떠 있는지
#   tools/kiosk.sh uninstall  끄고 등록을 지운다(plist 는 ~/Library/LaunchAgents/_disabled/ 로 옮긴다)
#   tools/kiosk.sh shot [png] 창이 지금 그리는 것을 그림으로 뜬다(기본 logs/kiosk-shot.png — 탭5 로 가는 것과 같은 1280×720)
#   tools/kiosk.sh tab5 pause [분] | resume | -- <mon_ctl 인자…>
#                             탭5 직렬 포트는 이 창이 독점한다(다른 도구는 열지 못한다). 진단 · 펌웨어 올리기 전에 잠시 놓는다(기본 10분).
#                             `--` 는 놓고 → 탭5 펌웨어의 mon_ctl.py 를 돌리고 → 돌려준다(끊겨도). 예: tab5 -- run stat sleep:6 · tab5 -- run shot:/tmp/t.png
#   tools/kiosk.sh print      등록할 plist 를 표준 출력으로만 낸다(아무것도 건드리지 않는다 — 시험용)
#   tools/kiosk.sh doctor     다음 로그인 때 스스로 뜰 조건과 탭5 연결 상태를 본다(아무것도 건드리지 않는다 · 포트도 열지 않는다) — 하나라도 "주의"면 종료 코드 1
# 탭5 가 붙어 있지 않아도 떠 있는다 — 탭5 연결이 다시 찾고, 그동안 탭5 는 3분 뒤 "현황판이 오지 않습니다"를 보인다(기록은 자료 폴더의 logs/kiosk.log).
DIR="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
# 자료 폴더 — urgent_board/paths.py 와 같은 규칙(파이썬을 띄우지 않는다 · tests/test_paths.py 가 글자가 같은지 본다)
HOME_DIR="${URGENT_BOARD_HOME:-$HOME/Library/Application Support/UrgentBoard}"
STATE_DIR="$HOME_DIR/state"
LOGS_DIR="$HOME_DIR/logs"
LABEL="io.github.wideeye.kiosk"
DEFAULT_PREFIX="io.github.wideeye"     # tools/launchd.py 의 DEFAULT_PREFIX 와 같다(tests/test_kiosk.py 가 본다)
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
DOMAIN="gui/$(id -u)"
PIDFILE="$DIR/state/kiosk.pid"                # 예전 방식(pid 파일)의 흔적(코드 폴더의 옛 자리) — 남아 있으면 끈다
OLD_PROFILE="$HOME/.urgent-board-chrome"      # 더 예전 방식(Chrome 전체 화면)의 프로필 — 남아 있으면 끈다

esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

plist() {
  d="$(esc "$DIR")"
  l="$(esc "$LOGS_DIR")"
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$d/.venv/bin/python</string>
    <string>$d/tools/kiosk_window.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$d</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>StandardOutPath</key>
  <string>$l/kiosk.log</string>
  <key>StandardErrorPath</key>
  <string>$l/kiosk.log</string>
</dict>
</plist>
EOF
}

loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }
# launchd 가 띄운 것 말고 따로 떠 있는 창 프로세스의 pid 들(손으로 띄운 것 — 창이 겹친다). 프로세스 이름으로 찾는다:
# 명령 글자만 보면 그 글자가 든 셸까지 잡힌다. 이름 칸은 ucomm(짧은 이름) — comm 은 뒤에 칸이 있으면 16자로 잘려 "Python" 이 안 보인다(실측)
strays() {
  mine="$(pid_of)"
  ps -axo pid=,ucomm=,args= | LC_ALL=C awk -v mine="$mine" '$2 ~ /[Pp]ython/ && $0 ~ /tools\/kiosk_window\.py/ && $1 != mine {print $1}'
}
pid_of() { launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk '$1 == "pid" && $2 == "=" {print $3; exit}'; }

stop_legacy() {
  pkill -f "user-data-dir=$OLD_PROFILE" 2>/dev/null
  if [ -f "$PIDFILE" ]; then
    old="$(cat "$PIDFILE" 2>/dev/null)"
    if [ -n "$old" ] && ps -p "$old" -o command= 2>/dev/null | grep -q "kiosk_window"; then kill "$old" 2>/dev/null; sleep 1; fi
    rm -f "$PIDFILE"
  fi
}

start() {
  stop_legacy
  mkdir -p "$AGENTS" "$LOGS_DIR" "$STATE_DIR"
  new="$(plist)"
  if [ ! -f "$PLIST" ] || [ "$new" != "$(cat "$PLIST")" ]; then          # 등록 내용이 바뀌었으면 내렸다가 다시 올린다
    if loaded; then
      launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
      # 내리기는 비동기다 — 다 내려갈 때까지 기다린다. 기다리지 않으면 아래의 "올라가 있지 않으면 올린다"가 아직 남은 옛 작업을 보고
      # 건너뛰어 창이 내려간 채 남는다(2026-10-15 바꿔 끼울 때 실제로 겪음 — 탭5 가 꺼졌다)
      i=0; while loaded && [ $i -lt 40 ]; do sleep 0.25; i=$((i+1)); done
    fi
    printf '%s\n' "$new" > "$PLIST"
  fi
  if ! loaded; then
    ok=1
    for try in 1 2 3 4 5; do                   # 내린 직후에는 올리기가 잠깐 실패할 수 있다
      if launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null || loaded; then ok=0; break; fi
      sleep 1
    done
    [ $ok -eq 0 ] || { echo "launchd 에 올리지 못했습니다 — $PLIST"; return 1; }
  fi
  i=0; while [ -z "$(pid_of)" ] && [ $i -lt 20 ]; do sleep 0.5; i=$((i+1)); done
  status
}

status() {
  if loaded; then
    p="$(pid_of)"
    if [ -n "$p" ]; then
      echo "떠 있습니다 (pid $p, 로그인하면 스스로 뜨고 죽으면 되살아납니다)"
      extra="$(strays | tr '\n' ' ')"
      [ -n "$extra" ] && echo "주의: 따로 떠 있는 창 프로세스가 있습니다(pid $extra) — 창이 겹칩니다. 끄려면: kill $extra"
      return 0
    fi
    echo "등록돼 있지만 지금은 떠 있지 않습니다 — $LOGS_DIR/kiosk.log 를 보세요"; return 1
  fi
  echo "떠 있지 않습니다(등록 안 됨)"; return 1
}

# 다음 로그인 때 창이 스스로 뜰 조건을 본다. 읽기만 한다 — 로그아웃·재부팅을 해 보지 않고 확인할 수 있는 데까지
doctor() {
  bad=0
  good() { printf '좋음  %s\n' "$1"; }
  warn() { printf '주의  %s\n' "$1"; bad=1; }
  py="$DIR/.venv/bin/python"
  if [ -f "$PLIST" ]; then
    if [ "$(plist)" = "$(cat "$PLIST")" ]; then good "등록 파일이 지금 코드와 같다($PLIST)"; else warn "등록 파일이 지금 코드와 다르다 — tools/kiosk.sh 를 한 번 실행"; fi
    plutil -lint "$PLIST" >/dev/null 2>&1 || warn "등록 파일의 꼴이 깨졌다(plutil -lint)"
  else
    warn "등록 파일이 없다 — 로그인해도 뜨지 않는다. tools/kiosk.sh 를 실행"
  fi
  if launchctl print-disabled "$DOMAIN" 2>/dev/null | grep -Eq "\"$LABEL\" => (disabled|true)"; then
    warn "launchd 에서 꺼져 있다 — launchctl enable $DOMAIN/$LABEL"
  else good "launchd 에서 꺼져 있지 않다"; fi
  if [ -x "$py" ] && "$py" -c 'import AppKit, WebKit, serial' >/dev/null 2>&1; then good "파이썬과 화면 모듈(pyobjc) · 직렬 모듈(pyserial)을 불러온다"
  else warn "파이썬이나 화면 · 직렬 모듈을 불러오지 못한다($py) — brew 가 python 을 올렸으면 .venv 를 다시 만든다(requirements.txt)"; fi
  if [ -d "$LOGS_DIR" ] && [ -w "$LOGS_DIR" ]; then good "기록 폴더에 쓸 수 있다"; else warn "기록 폴더($LOGS_DIR)에 쓸 수 없다"; fi
  if loaded; then
    p="$(pid_of)"
    runs="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk '$1 == "runs" && $2 == "=" {print $3; exit}')"
    if [ -n "$p" ]; then good "지금 떠 있다(pid $p, 이번 등록 뒤 ${runs:-?}번째 실행)"; else warn "등록돼 있지만 떠 있지 않다 — $LOGS_DIR/kiosk.log 를 본다"; fi
    extra="$(strays | tr '\n' ' ')"
    [ -n "$extra" ] && warn "따로 떠 있는 창 프로세스가 있다(pid $extra) — 창이 겹친다"
  else
    warn "launchd 에 올라가 있지 않다 — tools/kiosk.sh 를 실행"
  fi
  port="$("$DIR/.venv/bin/python" -m urgent_board.paths port 2>/dev/null)"       # 설정 파일의 포트(못 읽으면 기본값)
  code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${port:-0}/healthz" 2>/dev/null)"
  if [ "$code" = "200" ]; then good "현황판 서버가 답한다"; else warn "현황판 서버가 답하지 않는다(창은 '서버를 기다리는 중'을 보이며 기다린다)"; fi
  st="$STATE_DIR/tab5-link.json"                 # 탭5 연결이 30초마다 · 바뀔 때 적는다 — 포트는 열지 않는다(읽기만)
  if [ -f "$st" ]; then
    /usr/bin/python3 - "$st" <<'PY'
import json, sys, time
s = json.load(open(sys.argv[1], encoding="utf-8"))
age = time.time() - float(s.get("updated_at") or 0)
t = s.get("tab5") or {}
ok = lambda c: "좋음" if c else "주의"
print(f"{ok(age < 120)}  탭5 연결 상태 파일 — {age:.0f}초 전에 적힘" + ("" if age < 120 else "(창이 멈췄거나 꺼져 있다)"))
if s.get("paused"):
    print("참고  탭5 포트를 잠시 놓아 둔 상태다(tools/kiosk.sh tab5 resume 으로 돌려준다)")
else:
    print(f"{ok(s.get('connected'))}  탭5 연결: " + (f"{s.get('port')} · 다시 연결 {s.get('reconnects')}번" if s.get("connected") else f"끊김 — {s.get('last_error') or '?'}"))
last = s.get("last_ok_at")
print(f"{ok(last and time.time() - last < 300)}  보낸 그림 {s.get('frames_ok', 0)}장 · 실패 {s.get('frames_err', 0)}" + (f" · 마지막 성공 {time.time() - last:.0f}초 전" if last else " · 아직 없음"))
if t:
    print(f"{ok(t.get('board') in ('live', 'sleep'))}  탭5: board={t.get('board')} · 그린 그림 {t.get('bseq')} · 화면 {'켬' if t.get('scr') else '끔'} · 그림 나이 {t.get('bage')}초 · 온도 {t.get('temp')}°C")
o = s.get("orient")                            # 탭5 가 STAT 으로 알린 방향(세로 화면 — 설계 2026-10-18 D3). 없으면 방향을 알리지 않는 펌웨어 = 가로
name = {0: "가로", 1: "세로", 2: "가로 — 뒤집힘", 3: "세로 — 뒤집힘"}.get(o)
print(f"참고  탭5 방향: {name} (orient={o})" if name else "참고  탭5 방향: 모름(방향을 알리지 않는 펌웨어면 가로로 그린다)")
PY
    /usr/bin/python3 -c 'import json,sys,time; s=json.load(open(sys.argv[1])); t=s.get("tab5") or {}; sys.exit(0 if time.time()-float(s.get("updated_at") or 0)<120 and (s.get("paused") or (s.get("connected") and t.get("board") in ("live","sleep"))) else 1)' "$st" 2>/dev/null || bad=1
  else
    warn "탭5 연결 상태 파일이 없다($st) — 창이 한 번도 돌지 않았다"
  fi
  printf '사람이 볼 것  시스템 설정 > 일반 > 로그인 항목 및 확장 프로그램 > "백그라운드에서 허용"의 python3 가 켜져 있어야 한다(끄면 이 창이 로그인 때 뜨지 않는다).\n'
  printf '             재부팅 뒤에는 누가 로그인해야 뜬다(FileVault — 이 맥의 모든 서비스가 그렇다).\n'
  return $bad
}

# 창 작업 이름(LABEL)은 운영 하나뿐이다 — 시험 이름 · 다른 자료 폴더로 켜고 끄면 운영 창의 등록이 그 사본을 가리키게 바뀐다
# (작업 8 검토 3: 시험 설치의 메뉴 막대 '탭5 연결 시작'이 그 길이었다). 그때는 launchctl 을 부르지 않고 한 줄로 멈춘다.
production_names() {
  { [ -z "${URGENT_BOARD_LABEL_PREFIX:-}" ] || [ "$URGENT_BOARD_LABEL_PREFIX" = "$DEFAULT_PREFIX" ]; } &&
  { [ -z "${URGENT_BOARD_HOME:-}" ] || [ "$URGENT_BOARD_HOME" = "$HOME/Library/Application Support/UrgentBoard" ]; }
}
case "${1:-start}" in
  start|restart|stop|uninstall)
    production_names || { echo "탭5 창은 기본 이름 · 기본 자료 폴더에서만 켜고 꺼요 — 시험용 이름이나 다른 자료 폴더로는 건드리지 않아요."; exit 1; } ;;
esac

case "${1:-start}" in
  print) plist ;;
  doctor) doctor ;;
  start) start ;;
  restart)
    if loaded; then stop_legacy; launchctl kickstart -k "$DOMAIN/$LABEL" >/dev/null 2>&1; sleep 1; status; else start; fi ;;
  stop)
    stop_legacy
    if loaded; then launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null; echo "껐습니다(다음 로그인 때 다시 뜹니다 — 아예 빼려면 uninstall)"; else echo "떠 있지 않습니다"; fi ;;
  uninstall)
    stop_legacy
    loaded && launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
    if [ -f "$PLIST" ]; then mkdir -p "$AGENTS/_disabled"; mv "$PLIST" "$AGENTS/_disabled/$LABEL.plist.disabled-$(date +%Y-%m-%d)"; fi
    echo "등록을 지웠습니다" ;;
  status) status ;;
  shot)
    out="${2:-$LOGS_DIR/kiosk-shot.png}"
    case "$out" in /*) ;; *) out="$(pwd)/$out" ;; esac
    if [ -z "$(pid_of)" ]; then echo "창이 떠 있지 않습니다"; exit 1; fi
    mkdir -p "$STATE_DIR"; rm -f "$out"
    req="$STATE_DIR/kiosk-shot.$(pid_of).request"       # 그 창(pid)만 읽는다
    printf '%s\n' "$out" > "$req"
    i=0; while [ ! -s "$out" ] && [ $i -lt 30 ]; do sleep 0.5; i=$((i+1)); done
    if [ -s "$out" ]; then echo "$out"; else rm -f "$req"; echo "그림을 받지 못했습니다 — 경로는 $LOGS_DIR 나 임시 폴더(/tmp) 아래의 .png 여야 합니다. $LOGS_DIR/kiosk.log 를 보세요"; exit 1; fi ;;
  tab5)
    shift
    SDIR="${TAB5_STATE_DIR:-$STATE_DIR}"                 # 시험은 임시 폴더를 준다(실제 창의 놓기 파일을 건드리지 않게)
    PAUSE="$SDIR/tab5-link.pause"; STATE="$SDIR/tab5-link.json"
    MONCTL="${TAB5_MONCTL:-$DIR/firmware/tools/mon_ctl.py}"
    MONCTL_ARGS="${TAB5_MONCTL_ARGS:-}"           # 탭5 도구에 넘길 인자(탭5 가 여러 대일 때 고르는 번호 등)
    mkdir -p "$SDIR"
    # 창이 포트를 놓았다는 확인을 기다린다(10초까지): 상태 파일의 paused 가 이번 놓기 뒤에 적힌 것이어야 믿는다(낡은 "paused" 를 믿지 않게).
    #   상태 파일이 없거나 그 창이 살아 있지 않으면 기다리지 않는다 — 포트를 쥔 것이 없다
    wait_paused() {
      i=0
      while [ $i -lt 40 ]; do
        r="$(/usr/bin/python3 - "$STATE" "$1" <<'PY' 2>/dev/null
import json, os, sys
try:
    s = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    print("none"); sys.exit()
pid = int(s.get("pid") or 0)
try:
    os.kill(pid, 0); alive = pid > 0
except ProcessLookupError:
    alive = False
except OSError:
    alive = True
fresh = s.get("paused") and float(s.get("updated_at") or 0) >= float(sys.argv[2])
print("ok" if fresh else "none" if not alive else "wait")
PY
)"
        case "$r" in ok|none) return 0 ;; esac
        /bin/sleep 0.25; i=$((i+1))
      done
      echo "창이 포트를 놓았다는 확인이 없습니다 — 그래도 진행합니다(도구가 포트를 열지 못하면 잠시 뒤 다시)"
    }
    # mon_ctl 을 돌릴 파이썬: 포트(pyserial)와 화면 찍기 shot(PIL)이 둘 다 있는 것 — 맥마다 다르다(2026-10-18 실측: 맥북은 /usr/bin/python3,
    #   맥미니는 brew). 둘 다 있는 것이 없으면 pyserial 만이라도 있는 것(shot 만 못 한다)
    tool_python() {
      for py in /opt/homebrew/bin/python3 /usr/bin/python3 "$DIR/.venv/bin/python"; do
        [ -x "$py" ] && "$py" -c "import serial, PIL" 2>/dev/null && { echo "$py"; return; }
      done
      for py in "$DIR/.venv/bin/python" /opt/homebrew/bin/python3 /usr/bin/python3; do
        [ -x "$py" ] && "$py" -c "import serial" 2>/dev/null && { echo "$py"; return; }
      done
      echo /opt/homebrew/bin/python3
    }
    later() { a="$(printf '%s' "$1" | awk '{print int($1)}')"; b="$2"; [ -n "$a" ] && [ "$a" -gt "$b" ] && echo "$a" || echo "$b"; }
    case "${1:-}" in
      pause)
        mins="${2:-10}"
        case "$mins" in ''|*[!0-9]*) echo "분은 숫자로: tools/kiosk.sh tab5 pause 30"; exit 2 ;; esac
        t0="$(date +%s)"; echo "$(( t0 + mins * 60 ))" > "$PAUSE"; wait_paused "$t0"
        echo "탭5 포트를 ${mins}분 동안 놓았습니다 — 끝나면 tools/kiosk.sh tab5 resume" ;;
      resume)
        rm -f "$PAUSE"; echo "탭5 포트를 창에 돌려줬습니다" ;;
      --)
        shift
        prev=""; [ -f "$PAUSE" ] && prev="$(cat "$PAUSE" 2>/dev/null)"     # 앞서 누가 걸어 둔 놓기(더 길 수 있다 — 펌웨어 올리는 중)
        restore() {                                      # 끝나면 앞의 놓기로 되돌린다 — 아직 살아 있으면 남기고, 없었거나 지났으면 지운다
          if [ -n "$prev" ] && [ "$(later "$prev" 0)" -gt "$(date +%s)" ]; then printf '%s\n' "$prev" > "$PAUSE"; else rm -f "$PAUSE"; fi
        }
        lend() { echo "$(later "$prev" $(( $(date +%s) + 600 )))" > "$PAUSE"; }   # 10분 — 더 긴 놓기를 줄이지 않는다
        t0="$(date +%s)"; lend; child=""; keeper=""
        stop_all() { [ -n "$keeper" ] && { pkill -P "$keeper" 2>/dev/null; kill "$keeper" 2>/dev/null; }; [ -n "$child" ] && kill "$child" 2>/dev/null; }
        trap 'restore' EXIT; trap 'stop_all; exit 130' INT TERM   # 중간에 끊겨도 도구를 멈추고 돌려준다(도구가 포트를 쥔 채 남지 않게 — 놓기 파일도 10분이면 저절로 풀린다)
        wait_paused "$t0"
        "$(tool_python)" "$MONCTL" $MONCTL_ARGS "$@" &      # MONCTL_ARGS 는 낱말로 나뉘어야 한다(따옴표 없음)
        child=$!
        ( while kill -0 "$child" 2>/dev/null; do /bin/sleep 30; kill -0 "$child" 2>/dev/null && lend; done ) >/dev/null 2>&1 &   # 10분 넘게 도는 도구에도 놓기를 이어 준다
        keeper=$!                                        #   (출력을 버린다 — 쉬는 sleep 이 부른 쪽의 파이프를 쥐고 있지 않게)
        wait "$child"; rc=$?
        pkill -P "$keeper" 2>/dev/null; kill "$keeper" 2>/dev/null
        exit $rc ;;
      *) echo "쓰기: tools/kiosk.sh tab5 pause [분] | resume | -- <mon_ctl 인자…>   (예: tab5 -- run stat sleep:6 · tab5 -- run shot:/tmp/t.png)"; exit 2 ;;
    esac ;;
  *) echo "쓰기: tools/kiosk.sh [start|restart|stop|status|shot|tab5|doctor|uninstall|print]"; exit 2 ;;
esac
