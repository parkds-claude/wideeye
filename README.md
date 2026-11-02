# WideEye

**WideEye gathers the messages that reach your Mac — texts, mail, messengers, missed calls, work-chat notifications — into one calm board, so nothing waiting for you slips by.**
You set it up by talking with an AI you already use. Start with [`START.md`](START.md) (Korean, step by step).

- **macOS only.** Everything runs on your own Mac. There is no WideEye server.
- **What it can read in this release:** texts, missed calls (with an iPhone) and a few mail and work-chat services — see '이번 판에서 이을 수 있는 것' below. Other messengers are not supported yet.
- **The Tab5 screen is optional.** WideEye can show the board on an M5Stack Tab5 plugged in by USB-C. The program for the Tab5 is still being prepared and is not part of this release — until then you see the board in your Mac's browser.
- **Privacy:** what WideEye reads (including your Mac's contacts, to show names instead of numbers) stays in a folder on your Mac. The AI that helps you set up sees your name and mail address, your box names and the names of the people and rooms that contacted you — but not message text unless you allow it each time. Passwords and login codes never go through the AI. An optional summary feature (off by default) sends the text of messages shown on the board, with the sender and room names and the date received, to Claude — together with your box names, keywords and descriptions, the names of not-yet-confirmed work and your 'no task' rule. Details below.
- **License:** MIT (see [`LICENSE`](LICENSE)). Third-party notices: [`NOTICE`](NOTICE).

---

## WideEye 는 무엇인가요

맥에 온 연락 — 문자 · 메일 · 메신저 · 부재중 전화 · 회사 메신저 알림 — 을 한 화면에 모아 보여 주는 알림판이에요.
아직 읽지 않은 것, 읽었지만 답하지 않은 부탁을 놓치지 않게 해 줘요.

- 화면은 맥의 브라우저로 봐요. 책상 위에 둘 작은 화면(M5Stack **탭5**)을 USB-C 로 꽂으면 그 화면에도 보여요.
- 연락을 사업 · 모임 · 가족처럼 **상자**로 나눠 보여요. 어느 상자인지는 당신이 정한 기준으로 가려요.
- 소리를 내지 않아요. 보기만 하는 알림판이에요.

## 시작하기

**[`START.md`](START.md) 를 위에서부터 따라 하세요.** 준비물, 터미널 여는 법, AI 고르기, AI 에게 보낼 첫 한마디가 차례로 있어요.

설정은 당신이 쓰는 **AI 와 대화하면서** 한 단계씩 해요.

- AI 는 [`GUIDE.md`](GUIDE.md) 를 읽고, 설치한 뒤에는 `wideeye guide next` 가 알려 주는 차례만 따라요.
- 비밀번호 · 로그인 코드는 AI 에게 말하지 않아요. WideEye 가 이 맥에 여는 입력 화면에 직접 넣어요.
- 중간에 그만둬도 괜찮아요. 다음에 AI 에게 "이어서"라고 하면 그 자리부터 해요.

### 준비물

1. **맥** 한 대 — macOS 13 이상.
2. **AI 하나** — 맥에서 명령을 직접 실행하는 AI, 또는 대화만 하는 AI(당신이 터미널에 한 줄씩 붙여 넣어요). 자세한 것은 `START.md` 의 3.
3. 이 맥의 **관리자 암호를 아는 사람** — 맥이 문자를 읽게 허락할 때 한 번 필요해요.
4. (고르기) **탭5**와 데이터가 되는 **USB-C 선**.

#### 이번 판에서 이을 수 있는 것

깔기 전에 확인하세요. 아래에 없는 것만 쓴다면, 이번 판으로는 아직 알림을 받을 수 없어요.

- **문자** — 이 맥의 메시지 앱에 오는 문자예요. 아이폰 문자가 이 맥에도 오게 해 두면 그것도 보여요.
- **부재중 전화** — 아이폰이 있어야 해요. 아이폰으로 온 전화가 이 맥에서도 울려야 보여요. 글이 없어서 이것만으로는 첫 알림 시험을 할 수 없어요.
- **Microsoft Teams 알림** — 이 맥에 뜨는 Teams 알림이에요.
- **Gmail** — 어려운 길이에요. 구글 개발자 화면에서 열쇠 파일을 한 번 만들어야 해요(처음이면 30분쯤).
- **텔레그램** — 어려운 길이에요. 텔레그램 개발자 화면에서 앱을 하나 만들어야 해요(처음이면 20분쯤).
- **그 밖의 메신저**와 Gmail 이 아닌 메일은 **아직** 읽지 못해요. 다음 판들에서 늘려 가요.

### 탭5 에 대하여

탭5 에 넣을 WideEye 프로그램은 **아직 준비 중이에요**(이번 판에는 들어 있지 않아요). 그동안은 맥의 브라우저로 봐요 — 설정 길이 탭5 단계를 건너뛰어 줘요. 프로그램이 나오면 같은 길에서 탭5 를 이어요.

## 무엇을 읽고, 무엇이 밖으로 나가나요

**모두 이 맥에서 돌아요.** WideEye 를 만든 쪽의 서버는 없고, 사용 기록을 어디에도 보내지 않아요.

- **읽는 곳**: 이 맥에 이미 있는 것(문자 · 통화 기록 · 알림 · 연락처 — 연락처는 번호 대신 이름을 보이려고 읽어요)과, 당신이 이어 준 메일 · 메신저 계정. 계정은 그 회사의 서버와만, 당신의 로그인으로 직접 이야기해요.
- **두는 곳**: 읽은 것과 설정은 이 맥의 자료 폴더(`~/Library/Application Support/UrgentBoard`)에만 둬요.
- **설정을 돕는 AI 에게 가는 것**: 당신의 이름 · 메일 주소 · 상자 이름 · 당신에게 연락한 사람과 방의 이름. **메시지 본문은 기본으로 가지 않아요** — 제목이나 앞부분을 보여야 할 때는 그때마다 먼저 물어요.
- **비밀번호 · 로그인 코드**는 AI 를 거치지 않아요. 이 맥에만 열리는 입력 화면에 당신이 직접 넣고, 이 맥의 키체인이나 당신만 읽을 수 있는 파일에 둬요.
- **요약(고르기 — 처음에는 꺼져 있어요)**: 켜면 알림판에 뜰 메시지의 글과 보낸 사람 · 방 이름 · 받은 날짜가 짧은 요약과 할 일을 만들려고 Claude 로 가요(이 맥의 Claude Code 로그인으로). 같은 때에 상자 이름 · 낱말 · 설명, 확정 전 일의 이름, '할 일이 아닌 것'의 기준 글도 함께 가요. 그 밖에는 아무것도 보내지 않아요.
- **남는 위험**: 이 맥에서 명령을 실행할 수 있는 AI 는 마음만 먹으면 이 맥의 다른 파일도 읽을 수 있어요. 믿을 수 있는 AI 만 쓰세요.

## 자주 쓰는 명령

설치한 뒤 AI 가 대신 불러 주지만, 직접 써도 돼요.
`wideeye` 는 터미널이 이름만으로 찾는 곳에 있지 않아요 — 설치 끝에 나온 **전체 위치**로 불러요(보통 `~/WideEye/bin/wideeye`, 다른 곳에 깔았다면 그 위치).

| 명령 | 하는 일 |
|---|---|
| `~/WideEye/bin/wideeye guide next` | 설정 길의 다음 한 단계 |
| `~/WideEye/bin/wideeye guide status` | 어디까지 했는지 |
| `~/WideEye/bin/wideeye open board` | 알림판을 브라우저로 |
| `~/WideEye/bin/wideeye open list` | 전체 목록(원문 열기 · 처리됨) |
| `~/WideEye/bin/wideeye open settings` | 설정 화면 |
| `~/WideEye/bin/wideeye doctor` | 무엇이 안 되는지 살펴보기 |

## 코드를 고치는 사람에게

- AI 로 코드를 고친다면 [`AGENTS.md`](AGENTS.md) 를 먼저 읽게 하세요. 설정을 돕는 AI 는 코드를 고치지 않아요.
- 파이썬 패키지 이름은 `urgent_board` 예요(WideEye 의 처음 이름에서 왔어요). 명령은 `bin/wideeye`(`bin/urgentboard` 는 같은 명령의 옛 이름).
- 시험: 시험 부품은 설치가 깔지 않아요 — 코드 폴더에서 `./install.sh` 를 한 번 돌린 뒤 `.tools/uv pip install --python .venv/bin/python -r requirements-dev.txt` 로 깔고(설치가 만든 `.venv` 에는 pip 가 없어요), `.venv/bin/python -m pytest -q`. 창을 실제로 띄우는 시험이 들어 있어 1~2분 걸려요(화면이 없는 세션에서는 건너뛰어요).
- 시험 자료와 화면 그림의 사람 · 방 이름과 전화 · 계정 번호 · 메일 주소는 모두 지어낸 것이에요(알림 메일을 흉내 내는 시험 몇 개만 그 서비스의 알림 주소 꼴을 써요). 실제 사람 · 회사 · 방 이름이나 기기 고유 번호를 코드 · 시험 · 글에 넣지 마세요.

## 라이선스와 고마운 것들

- WideEye 는 **MIT 라이선스**예요 — [`LICENSE`](LICENSE).
- 글꼴 **Pretendard**(길형진, SIL Open Font License 1.1) — `urgent_board/static/fonts/OFL.txt`.
- 함께 쓰는 파이썬 부품과 그 라이선스는 [`NOTICE`](NOTICE) 에 있어요.
- M5Stack Tab5 는 M5Stack 의 제품이에요. WideEye 는 M5Stack 과 관계가 없어요.
