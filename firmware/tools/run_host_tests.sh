#!/bin/sh
# 기기 없이 도는 순수 로직 시험. ESP-IDF export 를 하지 않은 셸에서 실행.
# 이 맥북은 기본 SDK(27)와 링커가 맞지 않아 -isysroot 로 Xcode SDK 를 짚어야 링크된다(메모리 mac-host-cpp-toolchain).
set -e
cd "$(dirname "$0")"
OUT="${TMPDIR:-/tmp}/wideeye_tab5_host_tests"
mkdir -p "$OUT"
SDK="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)"
FLAGS="-std=c++17 -O2 -Wall -Wextra -Werror"
[ -n "$SDK" ] && FLAGS="$FLAGS -isysroot $SDK"
rc=0
for t in test_recovery test_jpeg_header test_board_rx; do
    clang++ $FLAGS "$t.cpp" -o "$OUT/$t"
    "$OUT/$t" || rc=1
done
exit $rc
