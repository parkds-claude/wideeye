# WideEye 탭5 펌웨어 — M5Stack Tab5 를 WideEye 현황판 화면으로

맥의 WideEye 창(`tools/kiosk_window.py`)이 현황판을 그림(1280×720 JPEG)으로 떠서 **USB-C 직렬로** 보내면, 탭5 가 하드웨어로 풀어 90° 돌려 5" 화면에 그린다.
ESP-IDF **5.5.4**(정확히), M5Unified 0.2.23 · M5GFX 0.2.30. Wi-Fi 와 USB-A(호스트)는 쓰지 않는다.

- 설계: `../docs/exec-plans/2026-10-15-tab5-usb-frames-design.md`(그림 보내기) · `2026-10-15-tab5-board-mode.md`(현황판 모드 — 결정 MN-D31~40) · `2026-10-18-tab5-portrait-design.md`(세로 — MN-D41).
  코드 주석의 MN-D1~30 은 앞 판(HDMI 모니터)의 결정이다 — `2026-10-12-tab5-hdmi-monitor-decisions.md`(앞 판 저장소에서 옮긴 사본, 지운 기능의 기록도 함께 있다).
- 이 폴더는 2026-11-02 에 앞 판 저장소(비공개 작업 저장소)에서 옮겨 왔고, 그때 **HDMI 모니터 기능을 지웠다**(캡처 동글 · USB 호스트 · 띠 방식). 지우기 전의 판은 그 저장소의 태그 `tab5_monitor-hdmi-last`.

## 탭5 에 넣기 (WideEye 를 쓰는 사람)

ESP-IDF 없이 릴리스의 미리 빌드한 이미지를 넣는다 — WideEye 를 깐 폴더에서(설정 길의 `tab5-flash` 단계가 이것을 부른다):

```
bin/wideeye tab5 flash          # 확인만 — 어느 탭5 · 어느 이미지(받아서 지문 확인) · 할 일. 기기에는 아무것도 하지 않는다
bin/wideeye tab5 flash --yes --serial <탭5 번호>    # 실제로(번호는 위 출력에 — 설정의 탭5 면 생략): 칩 확인 → 플래시 16MB 통째 백업 → 굽기 → 새 펌웨어의 답 확인 → 설정에 이 탭5
bin/wideeye tab5 restore <백업 파일> --yes    # 넣기 전으로(공장 펌웨어로) 되돌리기
```

- 이미지는 릴리스 파일 `wideeye-tab5-v<판>.bin`(부트로더 · 파티션표 · 앱을 0x2000 부터 합친 것 — 빈 곳은 0xFF). 크기 · sha256 은 `release.json` 에 고정했고, 받은 뒤 그 값과 같을 때만 굽는다.
- 굽기 전에 막는 것: ESP32-P4 가 아님 · 칩 판이 v1.x 가 아님(이 빌드는 v1.0~v1.99 만 — v3 칩은 다른 빌드가 있어야 한다) · 플래시 16MB 미만 · 보안 부팅이나 플래시 암호화가 켜짐 · MAC 이 USB 일련번호와 다름.
- 백업은 자료 폴더의 `tab5-backups/`(0600) — `<이름>.bin` · `.bin.sha256` · `.json`(MAC · 칩 판 · 시각). 공장 배치(앱이 0x10000)는 우리 배치와 겹쳐 한 번 구우면 깨지므로 되돌리기는 16MB 를 통째로 0x0 에 쓴다.
- 합친 이미지가 NVS(0x11000) 자리도 0xFF 로 덮는다 — 공장 펌웨어의 설정 조각이 남지 않는 대신, 다시 구우면 방향 · 밝기 설정이 처음으로 돌아간다.
- 확인을 마친 기기: 칩 v1.3 · 화면 ST7121 인 탭5 한 대(2026-11-03, 공장 상태로 되돌렸다가 굽기 · 되돌리기 · 다시 굽기). M5GFX 0.2.30 은 ILI9881C · ST7123 · ST7121 화면을 스스로 알아보지만 앞의 둘에서는 시험하지 못했다.
- 굽는 도구는 esptool 5.4.0(설치기가 `requirements.lock` 의 지문으로 깐다). GPL-2.0-or-later 라 WideEye 코드에서 import 하지 않고 `python -m esptool` 로만 부른다.

## 맥과 주고받는 것

포트의 주인은 맥의 탭5 창이다(TIOCEXCL 로 독점). 다른 도구를 쓰기 전에 `tools/kiosk.sh tab5 pause [분]`, 끝나면 `tools/kiosk.sh tab5 resume`. 한 번만 쓸 때는 `tools/kiosk.sh tab5 -- run stat sleep:6`(빌렸다가 돌려준다).

| 맥 → 탭5 | 탭5 → 맥 |
|---|---|
| `screen <id> 0\|1` | `OK screen <id> 0\|1` |
| `frame <id> <len> <crc32 16진> <나이 ms>` | `OK frame-begin <id>` · `ERR frame <id> too-big\|bad-args\|busy` |
| `+<base64>`(원본 1,500B) — 4줄마다 확인을 기다린다 | `OK + <id> <받은 바이트>` · `ERR + <id> broken` |
| `frameend <id>` | `OK frame <id> <len> crc=<crc>` · `ERR frame <id> short\|crc\|wrong-id\|not-receiving\|timeout` |

- 탭5 는 5초마다 `STAT …` 한 줄을 스스로 낸다. 맥은 꼬리의 `board=<none|live|lost|sleep> bseq=<그린 번호> scr=<0|1> bage=<그린 내용의 나이 초>` 를 꼭 읽고, `temp=` · `orient=<0..3>` 을 읽는다. 나머지(`in out decfail dec_ms rot_ms … heap_int blk psram bdec`)는 진단용이다.
- 부팅하면 `MON BOOT fw=wideeye …` 한 줄(맥은 이것을 보고 그림과 screen 을 다시 보낸다). 시리얼 `boot` 가 그 줄을 다시 찍는다.

## 상태와 화면

- **LIVE** — 화면에 그린 그림의 내용이 180초 안(줄이 왔다는 것이 아니라 그림이 싱싱한가 — MN-D33).
- **SLEEP** — 맥이 `screen 0` 을 90초 안에 보냈다(맥 화면이 잠듦 · 잠김). 백라이트를 끈다. 90초 끊기면 그림이 싱싱해도 LOST — `screen 1` 이 와야 켠다(잠긴 맥 옆에 메시지를 켜지 않게).
- **LOST** — "현황판이 오지 않습니다"(밝기 60, 밤에도). **NONE** — 켠 뒤 아직 그림이 없다("현황판을 기다리는 중", 3분 뒤부터 LOST 와 같은 알림).
- 화면을 치면 아래에 조작 띠: 어둡게 · 밝게 · **방향**(90°씩 — 가로 → 세로 → 가로 뒤집힘 → 세로 뒤집힘, 맥이 STAT 의 `orient=` 를 보고 세로 그림을 보낸다) · 흑백/컬러 · 닫기. 기본은 컬러.
- 꺼진(SLEEP) 동안 화면을 쳐도 켜지지 않는다 — 잠긴 맥의 메시지가 보이지 않게.

## 스스로 하는 일 (복구 · 발열)

판단은 `main/recovery.h`(순수 로직 — 호스트 시험), 실행은 `main/main.cpp` 의 감시 태스크 · panel 태스크와 `main/board_session.cpp`.

| 일어난 일 | 하는 일 | 시리얼에 찍히는 줄 |
|---|---|---|
| 파이프라인 5초 · 세션 30초 · 화면 주사 3초 · panel 태스크(터치 · 밝기 · 온도 보호) 10초 돌지 않음 | 재시작 | `MON RECOVER pipeline-stuck / session-stuck / panel-stuck / ui-stuck -> restart n/4` |
| JPEG 머리가 깨진 그림 | 그 장을 버리고 앞 그림을 그대로 둔다(하드웨어 · IDF 파서에 넣지 않는다 — 부품 패치 J6) | `STAT rej=` · `rejwhy=` · `bdec=` |
| 앞의 그리기가 0.3초 안에 안 끝남 | 그 장을 포기한다 | `draw token timeout` · `STAT tokto=` |
| 칩 65 / 75 / 85°C | 밝기 150 / 70 / 그림 멈춤("기기가 뜨거워 잠시 쉽니다"). 문턱보다 5°C 식으면 한 단계씩 되돌린다 | `MON HEAT warm / hot / critical / normal temp=…` |
| 패닉 · 워치독으로 다시 켜짐 | 부팅 때 센다. **켜진 뒤 3분 안에** 4번 잇달아 죽으면 그리기를 시작하지 않고 안내를 띄운 채 선다 → **15분 뒤 스스로 한 번 더 해 본다**(RL-D2 — 시리얼 `reboot` 도 듣는다. 다만 선 동안 워치독 리셋이 또 나면 15분을 처음부터 다시 센다 — 아래 '아직 안 된 것'). 3분 넘게 돈 뒤의 리셋은 잇달음의 첫 번째로 센다(RL-D1) | 부팅 줄 `panics=` · `hw=` · `prev=` · `MON FAIL crash-loop (retry in 900 s)` · `MON RETRY` |
| 부팅 때 버퍼 · 태스크 · 디코더를 못 만듦 | 재시작(3번까지) → "화면을 시작하지 못했습니다" | `MON RECOVER sys-init / video-init -> restart n/3` |

- 재시작에는 상한이 있다(NVS 에 센다 — 멈춤 4번, 부팅 실패 3번). 현황판이 1분 이어지면 멈춤 횟수를, **켜진 뒤 3분이 지나면 패닉 횟수를 지운다**(RL-D1 — 전에는 현황판 30분이었다). **전원을 껐다 켜면 모두 지워진다.**
- 왜 바꿨나(2026-11-03): 맥북에 꽂힌 탭5 가 오전 동안 워치독 리셋(리셋 까닭 7)을 띄엄띄엄 여섯 번 겪었다 — 맥북 덮개 닫기(배터리 사용 중 · USB 전원이 끊김)와 맥북 다시 켜기 무렵이었다(때가 겹쳤을 뿐 까닭으로 확인하지는 못했다 — 2026-11-04 시험에서는 맥북이 켜진 채 선 탭5 에서도 두 번 났다). 맥북은 자주 잠기고 닫혀 현황판 30분이 이어지지 않으니 횟수가 지워지지 않았고, 4 를 넘자 탭5 가 사람이 손댈 때까지 섰다(맥 쪽 창은 30초마다 다시 연결하며 `frame … busy` 만 받았다). 리셋 자체의 까닭은 아직 모른다 — 그래서 부팅 기록(RL-D3)을 남긴다.
- **부팅 기록**(RL-D3): 시리얼 `boot` 가 `BOOTINFO …` 다음 줄에 `BOOTHIST n=<부팅 수> [hw=<하드웨어 리셋 까닭 16진> why=<IDF 까닭> prev=<그 전 실행 길이 — 0: 3분 안 · 1: 3분 · 2: 30분 · 3: 3시간 · 4: 하루>] …` 를 새것부터 8개 찍는다. 부팅 줄에도 `hw=` · `prev=` 가 있다.
- 맥 쪽 창(`tools/tab5_link.py` — RL-D4)은 그림이 잇달아 `busy` 로 거절되면 `boot` 를 물어 서 있는 탭5 를 알아보고, 상태 파일에 `halted` 를 적고(`tools/kiosk.sh doctor` 가 알린다) 곧바로 `reboot` 을 보낸다(그 뒤로는 17분에 한 번까지) — 이 판보다 옛 펌웨어(스스로 다시 해 보지 않는다)도 풀린다. 2026-11-04 실물 시험: 창이 탭5 에 다시 붙은 지 6초 만에 알아채 보냈고, 15초 만에 그림을 다시 받았다.
- 재시작 직전에 그리기 태스크를 세운다(MN-D20 — 다른 코어가 캐시 작업 중에 리셋되면 재시작 도중에 죽었다).
- 펌웨어는 NVS 를 지우지 않는다. 이름 공간 `tab5mon` 만 쓴다(앞 판과 같은 이름 — `write_flash @flash_args` 로 앱만 바꾸면 밝기 · 방향 설정이 이어진다. `wideeye tab5 flash` 의 합친 이미지는 NVS 자리를 지운다).

## 빌드 · 올리기

```
cd firmware
source ~/esp/esp-idf-5.5.4/export.sh               # 이 맥북: IDF_PYTHON_ENV_PATH=~/.espressif/python_env/idf5.5_py3.11_env 를 먼저(brew 파이썬 3.14 로는 IDF 환경을 못 찾는다)
idf.py build
../tools/kiosk.sh tab5 pause 30                     # 창이 포트를 놓게(끝나면 tab5 resume)
PORT=$(/usr/bin/python3 tools/mon_ctl.py --serial <탭5 일련번호> port)   # 설정의 display.tab5_usb_serial 값
python -m esptool --chip esp32p4 -p "$PORT" read_mac     # 올릴 기기가 맞는지 본다
python -m esptool --chip esp32p4 -p "$PORT" read_flash 0x20000 0x600000 backup-app.bin   # 지금 앱 영역 백업(되돌리기용)
cd build && python -m esptool --chip esp32p4 -p "$PORT" -b 460800 --before default_reset --after watchdog_reset write_flash "@flash_args"
../../tools/kiosk.sh tab5 resume
```

- `idf.py flash` 대신 `write_flash @flash_args` 를 쓴다 — `idf.py flash` 는 그 자리에서 다시 빌드할 수 있다(검토하고 시험한 바이너리와 달라진다).
- **굽고 나서는 `--after watchdog_reset`** — `hard_reset` 이면 이 칩(USB 직렬)은 내려받기 모드에 머물러 화면이 꺼진 채 시리얼에 답하지 않는다(2026-11-02 겪음: USB 에는 보이는데 `screen` 에 답이 없음).
  그렇게 됐으면 `python -m esptool --chip esp32p4 -p "$PORT" --before no_reset --after watchdog_reset read_mac` 으로 꺼낸다. 이 리셋은 부팅 줄의 `panics=` 를 하나 올린다(현황판 30분이면 지워진다).
- `build*/` · `managed_components/` · `sdkconfig` 는 git 제외, `dependencies.lock` 은 git 에 둔다(같은 부품 판 — TB-D7). `sdkconfig.defaults` 를 고쳤으면 `sdkconfig` 를 지우고 다시 빌드한다.
- **버퍼 3장**: `CMakeLists.txt` 가 `tools/patch_m5gfx_fbs.py` 를 불러 M5GFX 의 패널 버퍼를 2장 → 3장으로 고친다(멱등). 고칠 파일이 없거나 모양이 바뀌었으면 구성이 실패한다. 부팅 줄의 `fbs=3`.
- `sdkconfig.defaults` 는 실험 기능을 켠다: `CONFIG_SPIRAM_XIP_FROM_PSRAM=y`(코드를 PSRAM 에서 실행 — 플래시에 쓰는 동안에도 화면 주사 · 받기 인터럽트가 멈추지 않는 까닭일 수 있다, 추정).
- **ESP-IDF 판을 올리면** `components/jpeg_decode_enhanced/PATCHES.md` 의 주의를 먼저 읽는다(IDF 의 비공개 구조체에 기댄다 — 정확히 5.5.4 가 아니면 빌드를 막아 둠).
- 화면 글자를 바꾸려면 `tools/gen_ui_assets.py` 의 `LABELS` 를 고치고 brew 파이썬(Pillow 12.3.0 + raqm)으로 다시 굽는다. 글꼴은 Pretendard 1.3.9 의 `Pretendard-Bold.otf`(`~/Library/Fonts` 또는 `fonts/`).

## 릴리스 이미지 · 재현 빌드

- `tools/make_release_image.sh <나올 폴더>` — 빈 임시 폴더에 소스만 옮겨 빌드하고(부품은 `dependencies.lock` 대로 새로 받는다) `flash_args` 대로 합쳐 `wideeye-tab5-v<판>.bin` 과 `.sha256` 을 만든 뒤 `release.json` 과 견준다. 펌웨어를 바꿨으면 `--write` 로 `release.json` 을 새로 쓴다(판 이름은 `version.txt`).
- `CONFIG_APP_REPRODUCIBLE_BUILD=y` — 빌드 시각 · 폴더 경로가 바이너리에 들어가지 않는다. 같은 ESP-IDF(5.5.4)와 같은 esptool(합치기)이면 어느 폴더 · 어느 맥에서 빌드해도 바이트까지 같다(2026-11-03 세 폴더에서 확인). 그래서 누구나 이 소스에서 릴리스 이미지를 다시 만들어 지문을 대조할 수 있다.
- 스크립트는 이미지 안에 `/Users/` 같은 개인 경로가 있으면 멈춘다.

## 되돌리기

- `wideeye tab5 flash` 로 넣었으면: `bin/wideeye tab5 restore <tab5-backups 의 .bin> --yes`(16MB 통째).
- 위 '빌드 · 올리기'로 앱만 바꿨으면: `python -m esptool --chip esp32p4 -p "$PORT" write_flash 0x20000 backup-app.bin`(위 백업). NVS 는 건드리지 않는다.
- HDMI 모니터 겸용 판은 공개하지 않았다(앞 판 저장소의 태그 `tab5_monitor-hdmi-last`).

## 시험 · 진단

- 기기 없이: `tools/run_host_tests.sh` — `recovery.h`(감시 · 발열 · 현황판 상태 · 방향) · JPEG 머리 검사(40만 가지 변형) · 그림 받기(`board_rx.h`).
- 기기에서(`tools/mon_ctl.py run …` 또는 `tools/kiosk.sh tab5 -- run …`): `stat` · `shot:<파일.png>`(패널을 찍어 방향대로 돌려 저장) · `frame:<jpg>` · `screen:<0|1>` · `put:<jpg>` 뒤 `play <fps> <초>` · `bench`.
- 시리얼 명령(기기에서 `help`): `stat tasks heap pwr boot cfg shot bench orient 0..3 flip mono 0|1 range 0|1 bri <5..255> osd 0|1 reboot board stale <초>`, 시험용 `fault stuck|sess|ui|dec|tok|temp|budget|boot|panic|notice|clear`.
- `tools/make_test_jpeg.py` · `tools/make_bad_jpegs.py` — 자가 시험용 그림과 깨진 그림.

## 구조

| 파일 | 하는 일 |
|---|---|
| `main/main.cpp` | 부팅 · panel 태스크(터치 · 밝기 · 방향 · 온도) · 감시 태스크 |
| `main/board_session.cpp` | USB-A 전원 끄기 · 세션 박동 · 재시작 횟수 지우기 |
| `main/sercmd.cpp` · `main/board_rx.h` | 시리얼 명령 · 현황판 그림 받기(조각 · 확인 · CRC) |
| `main/pipeline.cpp` | 현황판 → 화면(그리는 것은 이 태스크뿐) · STAT |
| `main/video.cpp` | 풀기(하드웨어 JPEG) → 돌리기(PPA) → 패널 버퍼 3장 교체 |
| `main/ui.cpp` · `main/ui_assets.*` | 안내 화면 · 조작 띠 · 미리 구운 글자 |
| `main/recovery.h` | 복구 · 발열 · 현황판 상태의 판단(순수 로직) |
| `main/settings.cpp` | NVS 설정 · 재시작 횟수 |

## 들여온 것 · 참고한 것

- `components/jpeg_decode_enhanced/` — [Hiroki-Kawakami/Tab5-UVC-Display](https://github.com/Hiroki-Kawakami/Tab5-UVC-Display) 의 부품, **MIT**(Copyright (c) 2025 Hiroki Kawakami, 그 폴더의 `LICENSE`). 고친 곳과 뺀 것은 `PATCHES.md`.
- 화면 주사의 메모리 버스 우선권 값 — [Hiroki-Kawakami/Tab5-Screen-Streamer](https://github.com/Hiroki-Kawakami/Tab5-Screen-Streamer) `bsp_tab5.c`(MIT).
- 글꼴 — [Pretendard](https://github.com/orioncactus/pretendard) Bold, **SIL OFL 1.1**(`fonts/OFL-Pretendard.txt`). 글자를 미리 구운 그림으로 펌웨어에 넣는다.
- 빌드 때 받는 것 — ESP-IDF 5.5.4(Apache-2.0), M5Unified · M5GFX(MIT, M5Stack — M5GFX 안의 LovyanGFX 는 FreeBSD 라이선스).
- 미리 빌드한 이미지에 들어간 것의 라이선스 전문 — `THIRD_PARTY_NOTICES.txt`(릴리스에도 함께 싣는다).
- 굽는 도구 esptool(Espressif, GPL-2.0-or-later) — 저장소에 넣지 않고 설치기가 PyPI 에서 받는다. 따로 된 프로그램으로만 부른다.
- **참고한 프로젝트** — [nnn112358/Tab5_UVC_Camera](https://github.com/nnn112358/Tab5_UVC_Camera): Tab5 에서 하드웨어 JPEG 와 PPA 로 화면을 그리는 길. 출처 감사(2026-11-02 — 그 프로젝트의 코드 파일을 줄 단위로 견줌)에서 이 펌웨어에 그 프로젝트의 코드는 남아 있지 않다고 보았다(같은 것은 ESP-IDF API 쓰임새뿐).

## 아직 안 된 것

- 기기 회귀 시험 도구가 없다 — HDMI 판의 `dev_regress.py` 는 지웠다. 현황판 기기 시험은 위의 `mon_ctl` 단계로 손으로 한다.
- 다른 칩 판(v3) · 다른 화면(ILI9881C · ST7123)의 탭5 에서 해 보지 않았다. 굽기 · 되돌리기는 한 대(칩 v1.3 · ST7121)를 공장 상태로 되돌려 대신 시험했다.
- v3 칩 탭5 용 빌드는 없다(명령이 굽기 전에 멈춘다).
- 공장 펌웨어로 되돌린 직후(켜진 지 8초쯤) 탭5 가 리셋 신호에 답하지 않은 적이 한 번 있다(2026-11-03 — esptool `No serial data received`, USB 에는 보임, 몇 분 뒤에도 그대로). USB-C 선을 뽑았다 다시 꽂으니 풀렸고, 그 뒤 굽기는 정상이었다. 명령은 이때 "선을 뽑았다 다시 꽂고 다시 해 본다"고 안내한다. 같은 날 다른 때(공장 펌웨어가 5분 넘게 돈 뒤)에는 선 없이 붙었다 — 까닭은 확인 못 함.
- **선 탭5 의 '15분 뒤 스스로 다시'(RL-D2)가 실물에서 끝까지 되지 않았다**(2026-11-04). 시험으로 세운 탭5 가 선 동안 워치독 리셋(`hw=0x7 why=7` — 고장 처리기가 기록을 남기기 전에 감시 타이머가 리셋한 꼴)을 두 번 더 겪었고, 리셋마다 다시 서며 15분을 처음부터 셌다. 맥 쪽 창(RL-D4)이 붙어 있으면 몇 초 안에 `reboot` 으로 풀므로 맥에 꽂아 쓰는 동안은 서 있지 않는다. 이 리셋의 까닭은 모른다 — 맥이 포트를 여닫는 것만으로는 나지 않았다(같은 방식으로 70번 여닫아 0번).
- 굽기 도구의 `--after watchdog-reset`(LP 워치독)도 이 칩의 부팅 기록에 `hw=0x7` 로 남는다. 그래서 `hw=0x7` 만으로는 어느 감시 타이머인지 가를 수 없다.
