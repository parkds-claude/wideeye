#!/bin/bash
# 공개 릴리스에 싣는 탭5 이미지를 만든다 — FL-D1(합친 이미지 하나) · FL-D3(재현 빌드) · FL-D4(지문은 firmware/release.json).
#
# 사용(ESP-IDF 5.5.4 환경을 연 셸에서 — `. $IDF_PATH/export.sh`):
#   firmware/tools/make_release_image.sh <나올 폴더>           빌드 → 합침 → release.json 과 견준다(다르면 1)
#   firmware/tools/make_release_image.sh <나올 폴더> --write   빌드 → 합침 → release.json 을 새 값으로 쓴다(펌웨어를 바꿨을 때)
#
# 빌드는 늘 빈 임시 폴더에서 한다 — 작업 폴더의 build · managed_components · sdkconfig 를 쓰지 않는다
# (부품은 dependencies.lock 대로 새로 받는다). 그래서 돌릴 때마다 "다른 폴더에서 빌드해도 같은 지문인가"를 함께 본다.
# 합치기는 이 앱의 .venv 에 깔린 esptool(requirements.lock 의 판)로 한다 — 판이 다르면 합친 파일이 달라질 수 있다.
# 나오는 것: <나올 폴더>/wideeye-tab5-v<판>.bin · .bin.sha256 · build.log
set -eu

FW=$(cd "$(dirname "$0")/.." && pwd)
ROOT=$(dirname "$FW")
PY=$ROOT/.venv/bin/python
OUT=${1:?"나올 폴더를 주세요"}
MODE=${2:-check}
die() { echo "멈춤: $*" >&2; exit 1; }

case "$MODE" in check|--write) ;; *) die "둘째 인자는 --write 만 받는다" ;; esac
command -v idf.py >/dev/null || die "ESP-IDF 환경이 열려 있지 않다 — . \$IDF_PATH/export.sh"
idf.py --version 2>/dev/null | grep -q "v5\.5\.4" || die "ESP-IDF 5.5.4 가 아니다($(idf.py --version 2>/dev/null)) — 판이 다르면 지문이 달라진다"
[ -x "$PY" ] && "$PY" -m esptool version >/dev/null 2>&1 || die "$PY 에 esptool 이 없다 — requirements.lock 대로 깔 것"
VER=$(tr -d ' \n' < "$FW/version.txt")
case "$VER" in [0-9]*.[0-9]*.[0-9]*) ;; *) die "version.txt 가 X.Y.Z 꼴이 아니다: $VER" ;; esac
NAME="wideeye-tab5-v$VER.bin"

mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/wideeye-fw.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# 소스만 옮긴다(빌드 결과 · 받은 부품 · 지역 설정 · 글꼴 원본 · 지문 파일은 뺀다)
rsync -a --exclude '/build*/' --exclude '/managed_components/' --exclude '/sdkconfig' --exclude '/sdkconfig.old' \
  --exclude '/fonts/*.otf' --exclude '/release.json' "$FW/" "$TMP/firmware/"
echo "빌드: $TMP/firmware (기록 $OUT/build.log)"
( cd "$TMP/firmware" && idf.py build ) > "$OUT/build.log" 2>&1 || die "빌드 실패 — $OUT/build.log"

# flash_args(첫 줄은 플래시 설정, 나머지는 '위치 파일')를 그대로 합친다 — 위치를 이 스크립트에 따로 적지 않는다
B=$TMP/firmware/build
set -- $(sed -n '1p' "$B/flash_args")
FLASH_OPTS="$*"
PAIRS=$(sed -n '2,$p' "$B/flash_args")
FIRST=$(echo "$PAIRS" | while read -r a _; do printf '%d\n' "$a"; done | sort -n | head -1)   # 16진수를 숫자로 견준다(글자 차례면 0x10000 이 앞선다)
[ "$FIRST" = 8192 ] || die "부트로더 위치가 0x2000 이 아니다(십진 $FIRST) — 명령(urgent_board/tab5_flash.py)도 함께 볼 것"
( cd "$B" && "$PY" -m esptool --chip esp32p4 merge-bin -o "$OUT/$NAME" --target-offset 0x2000 \
    $(echo "$FLASH_OPTS" | sed 's/--flash_mode/--flash-mode/; s/--flash_freq/--flash-freq/; s/--flash_size/--flash-size/') \
    $PAIRS ) >> "$OUT/build.log" 2>&1 || die "합치기 실패 — $OUT/build.log"

# 개인 경로가 들어가지 않았는가(재현 빌드가 /IDF 등으로 바꾼다)
for pat in '/Users/' '/home/' '/private/' 'scratchpad'; do
  n=$(strings -a "$OUT/$NAME" | grep -c -- "$pat" || true)
  [ "$n" = 0 ] || die "이미지 안에 '$pat' 가 ${n}번 — 재현 빌드 설정을 볼 것"
done

SIZE=$(stat -f %z "$OUT/$NAME")
SHA=$(shasum -a 256 "$OUT/$NAME" | cut -d' ' -f1)
( cd "$OUT" && shasum -a 256 "$NAME" > "$NAME.sha256" )
ESPTOOL=$("$PY" -m esptool version | tail -1)
echo "이미지: $OUT/$NAME · $SIZE 바이트 · sha256 $SHA"

"$PY" - "$FW/release.json" "$MODE" "$VER" "$NAME" "$SIZE" "$SHA" "$ESPTOOL" <<'EOF'
import json, sys
path, mode, ver, name, size, sha, esptool = sys.argv[1:8]
new = {"version": ver, "tag": f"v{ver}", "file": name, "offset": "0x2000", "size": int(size), "sha256": sha,
       "chip": "esp32p4", "chip_rev": [100, 199], "flash_size": "16MB", "idf": "5.5.4", "esptool": esptool}
if mode == "--write":
    with open(path, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"썼다: {path}")
    sys.exit(0)
try:
    old = json.load(open(path, encoding="utf-8"))
except FileNotFoundError:
    print(f"{path} 가 없다 — --write 로 처음 만든다"); sys.exit(1)
diff = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
if diff:
    print("release.json 과 다르다: " + ", ".join(f"{k} {old.get(k)!r} → {new.get(k)!r}" for k in diff)); sys.exit(1)
print("release.json 과 같다 — 같은 소스에서 같은 이미지(재현 빌드)")
EOF
