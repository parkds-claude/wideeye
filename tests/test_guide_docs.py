"""안내 글 — GUIDE.md(AI) · START.md(사람의 0단계) · AGENTS.md · SETUP.md 와 길 정의의 사람 말(설계 2026-10-18 공개판 G3 · G4 · G6 ·
G8 · G9 · G12, 계획 1부 작업 7).

- 사람이 읽는 글(START.md · roadmap.yaml 의 사람 말 칸)에는 개발자 낱말을 쓰지 않는다 — tests/forbidden_words.txt. 화면의 실제 이름은
  괄호 · 따옴표 안에서만 예외.
- 안내 글이 부르는 `wideeye …` 는 모두 실제 하위 명령이다(어느 AI 든 그대로 따라 한다 — 1판 test_every_command_in_the_guide_exists 의 꼴).
- START.md 의 첫 한마디는 '길 명령의 출력만 따른다'로 시작한다(G4 — 제미나이 검수).
- 공개 글은 메신저 상표를 '메신저'보다 앞세우지 않는다(사용자 결정 2026-10-18 "메신저라고 카톡을 두리웅실 이야기할 것").
- START.md 는 GUIDE.md 의 앞부분(지킬 것 · 설치 전 · 설치 뒤)을 글자 그대로 싣는다 — 주소를 못 여는 AI 에게 사람이 붙여 넣는다(G9).
- AGENTS.md 는 둘이다(검토 R10): 이 작업장의 개발 지도(맨 위가 GUIDE.md 를 가리킨다)와, 내보낼 때 덮어쓰는 공개판 public/AGENTS.md(설정 AI 규칙).
- 길 명령 · 확인기가 사람에게 건네는 글(tools/guide_cli.py · urgent_board/guide_checks.py 의 한글 문자열)도 금지 낱말을 쓰지 않는다(검토 10)."""
import ast
import re
import shlex
from pathlib import Path

import pytest

from urgent_board import guide

ROOT = Path(__file__).resolve().parents[1]
WORDS_FILE = Path(__file__).with_name("forbidden_words.txt")
GUIDE_MD, START_MD, AGENTS_MD, SETUP_MD = (ROOT / n for n in ("GUIDE.md", "START.md", "AGENTS.md", "SETUP.md"))
# 공개판 전용 글(작업 9 가 내보낼 때 맨 위로 덮어쓴다 — 검토 R10). 내보낸 공개판에는 public/ 이 없고 그 글이 맨 위에 있다.
PUBLIC_DIR = ROOT / "public" if (ROOT / "public").is_dir() else ROOT
PUBLIC_AGENTS = PUBLIC_DIR / "AGENTS.md"
PUBLIC_README = PUBLIC_DIR / "README.md"
CLI_SOURCES = (ROOT / "tools" / "guide_cli.py", ROOT / "urgent_board" / "guide_checks.py")    # 사람에게 건네는 글이 든 코드
INSTALL_LINE = "curl -fsSL https://github.com/parkds-claude/wideeye/raw/v0.2.0/install/wideeye-install.sh | sh"    # 결정 R2 — 작업 8 의 설치 입구
FIRST_RULE = "길 명령의 출력만 따라 주세요"
PASTE_START, PASTE_END = "<!-- 붙여넣기: 시작", "<!-- 붙여넣기: 끝"
DESIGN_WORDS = ("엔진", "수집기", "출처", "판정", "TCC", "launchd", "plist", "venv", "api_hash", "토큰", "일련번호", "펌웨어", "서버")
BRANDS = ("카톡", "카카오", "텔레그램", "Telegram", "팀즈", "Teams", "Gmail", "지메일", "왓츠앱", "WhatsApp", "슬랙", "Slack", "iMessage",
          "FaceTime", "라인 메신저", "LINE")
ENCLOSED = re.compile(r"\([^()\n]*\)|'[^'\n]*'|\"[^\"\n]*\"|\[[^\[\]\n]*\]")
FIRST_SCREEN = 40                                        # 공개 README 의 '첫 화면'(줄)


def word_lists() -> tuple[list[str], list[str]]:
    forbid, allow, cur = [], [], None
    for raw in WORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in ("[금지]", "[예외]"):
            cur = forbid if line == "[금지]" else allow
            continue
        assert cur is not None, f"구역 머리([금지] · [예외]) 앞의 줄: {line}"
        cur.append(line)
    return forbid, allow


def blank_allowed(text: str, allow: list[str]) -> tuple[str, set[str]]:
    """괄호 · 따옴표 안에 그대로 쓴 예외 이름을 빈칸으로 — (지운 글, 쓰인 예외 이름)."""
    spans = [m.span() for m in ENCLOSED.finditer(text)]
    chars, used = list(text), set()
    for name in allow:
        for m in re.finditer(re.escape(name), text):
            if any(a <= m.start() and m.end() <= b for a, b in spans):
                chars[m.start():m.end()] = " " * (m.end() - m.start())
                used.add(name)
    return "".join(chars), used


def forbidden_hits(text: str, forbid: list[str], allow: list[str]) -> tuple[list[tuple[str, str]], set[str]]:
    clean, used = blank_allowed(text, allow)
    low = clean.lower()
    hits = []
    for w in forbid:
        i = low.find(w.lower())
        if i >= 0:
            hits.append((w, clean[max(0, i - 25): i + len(w) + 25]))
    return hits, used


def roadmap_texts() -> list[tuple[str, str, str]]:
    return guide.human_texts(guide.load())


def paste_part(text: str) -> str:
    """GUIDE.md 의 붙여넣기 표시 사이 — 표시 줄은 빼고, 줄 끝 빈칸은 지운다."""
    a, b = text.index(PASTE_START), text.index(PASTE_END)
    body = text[text.index("\n", a) + 1:b]
    return "\n".join(line.rstrip() for line in body.strip("\n").splitlines())


def fenced_blocks(text: str) -> list[str]:
    """``` 또는 ```` 로 둘러싼 상자의 글(바깥 울타리의 길이에 맞춰 닫는다 — 안의 ``` 를 품는다)."""
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        m = re.match(r"^(`{3,})", lines[i])
        if not m:
            i += 1
            continue
        fence, body, i = m.group(1), [], i + 1
        while i < len(lines) and lines[i].rstrip() != fence:
            body.append(lines[i].rstrip())
            i += 1
        out.append("\n".join(body))
        i += 1
    return out


def _outside_fences(text: str) -> list[tuple[str, bool]]:
    """(줄, 상자 밖인가) — 상자 안의 '## ' 줄은 제목이 아니다(START.md 의 붙여넣기 상자는 GUIDE.md 의 제목을 품는다)."""
    out, fence = [], None
    for line in text.splitlines():
        m = re.match(r"^(`{3,})", line)
        if fence is None and m:
            fence = m.group(1)
            out.append((line, False))
        elif fence is not None and line.rstrip() == fence:
            fence = None
            out.append((line, False))
        else:
            out.append((line, fence is None))
    return out


def headings(text: str) -> list[str]:
    return [line[3:] for line, outside in _outside_fences(text) if outside and line.startswith("## ")]


def section(text: str, heading_word: str) -> str:
    """'## ' 제목에 그 말이 든 첫 구역(다음 '## ' 제목 앞까지 — 상자 안의 줄은 제목으로 치지 않는다)."""
    lines, body, inside = _outside_fences(text), [], False
    for line, outside in lines:
        is_head = outside and line.startswith("## ")
        if inside and is_head:
            break
        if inside:
            body.append(line)
        elif is_head and heading_word in line:
            inside = True
    assert inside, f"'{heading_word}' 가 든 ## 제목이 없다"
    return "\n".join(body) + "\n"


# ---- 금지 낱말 --------------------------------------------------------------------------------------------------------

def test_word_list_keeps_the_design_words_and_has_both_parts():
    forbid, allow = word_lists()
    assert set(DESIGN_WORDS) <= set(forbid), set(DESIGN_WORDS) - set(forbid)
    assert allow and len(forbid) == len(set(forbid)) and len(allow) == len(set(allow))
    for name in allow:                                                   # 예외 이름은 금지 낱말을 품을 때만 뜻이 있다
        assert any(w.lower() in name.lower() for w in forbid), name


def test_start_and_roadmap_text_have_no_forbidden_words():
    forbid, allow = word_lists()
    texts = [("START.md", "전체", START_MD.read_text(encoding="utf-8"))]
    texts += [(f"roadmap:{sid}", field, t) for sid, field, t in roadmap_texts()]
    used_all, bad = set(), []
    for where, field, text in texts:
        hits, used = forbidden_hits(text, forbid, allow)
        used_all |= used
        bad += [(where, field, w, around) for w, around in hits]
    assert not bad, "\n".join(map(str, bad))
    assert set(allow) == used_all, f"쓰이지 않는 예외 이름(지울 것): {set(allow) - used_all}"


def test_allowed_screen_names_pass_only_inside_brackets_or_quotes():
    forbid, allow = word_lists()
    assert not forbidden_hits("'API 및 서비스'를 눌러요.", forbid, allow)[0]
    assert not forbidden_hits("(사용자 인증 정보 › OAuth 클라이언트 ID 만들기)", forbid, allow)[0]
    assert forbidden_hits("API 및 서비스를 눌러요.", forbid, allow)[0]               # 괄호 · 따옴표 밖이면 걸린다
    assert forbidden_hits("탭5 펌웨어를 넣어요", forbid, allow)[0]


# ---- 명령 -------------------------------------------------------------------------------------------------------------

def _commands(text: str) -> tuple[set[tuple[str, ...]], list[str]]:
    """글 속 `wideeye 낱말 [낱말]` 들과, 백틱 · 상자 안의 채울 자리 없는 전체 명령 줄들."""
    words = {tuple(w for w in m.groups() if w) for m in re.finditer(r"\bwideeye ([a-z][a-z0-9-]*)(?: ([a-z][a-z0-9-]*))?", text)}
    lines = re.findall(r"`(wideeye [^`\n]+)`", text)
    lines += [ln.strip() for b in fenced_blocks(text) for ln in b.splitlines() if ln.strip().startswith("wideeye ")]
    return words, [ln for ln in lines if not any(c in ln for c in "<>…|")]


def check_command(line: str) -> None:
    """채울 자리 없는 `wideeye …` 한 줄을 실제 파서로 읽는다 — 틀리면 SystemExit(2). --help 를 붙여 보는 꼴은 모르는 선택지를 놓친다
    (`wideeye doctor --bundel x` 가 0 으로 끝났다 — 검토 7)."""
    from tools import guide_cli, setup_cli, tab5_cli, ub
    args = shlex.split(line)
    assert args[0] == "wideeye" and len(args) > 1, line
    rest = args[1:]
    if rest[0] == "guide":
        guide_cli.parser().parse_args(rest[1:])
    elif rest[0] == "setup":
        setup_cli.parser().parse_args(rest[1:])
    elif rest[0] == "tab5":
        tab5_cli.parser().parse_args(rest[1:])
    else:
        ub.parser().parse_args(rest)


@pytest.mark.parametrize("line", ["wideeye open settingz", "wideeye restart engin", "wideeye doctor --bundel x", "wideeye guide nxt",
                                  "wideeye setup who --limt 5", "wideeye frobnicate"])
def test_command_checker_rejects_wrong_arguments(line, capsys):
    with pytest.raises(SystemExit) as e:
        check_command(line)
    assert e.value.code == 2, line
    capsys.readouterr()


def test_command_checker_accepts_real_commands(capsys):
    for line in ("wideeye open settings", "wideeye restart engine", "wideeye doctor --bundle x.zip", "wideeye guide next --for-chat",
                 "wideeye setup who --unsorted --limit 5", "wideeye install --no-open", "wideeye guide confirm try-tab5 yes",
                 "wideeye tab5 flash", "wideeye tab5 flash --yes", "wideeye tab5 restore x.bin --yes"):
        check_command(line)


def test_guide_names_only_commands_that_exist(capsys):
    from tools import setup_cli, ub
    setup_choices = set(setup_cli.parser()._subparsers._group_actions[0].choices)
    rm = guide.load()
    sources = {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in (GUIDE_MD, START_MD, AGENTS_MD, PUBLIC_AGENTS, PUBLIC_README)}
    sources["roadmap"] = "\n".join([f"`{c}`" for s in rm.steps for c in (*s.run, *s.after)]
                                   + [f"`{b['then']}`" for s in rm.steps for b in s.stuck if b.get("then", "").startswith("wideeye ")]
                                   + [t for _, _, t in guide.human_texts(rm)])
    seen = set()
    for name, text in sources.items():
        words, lines = _commands(text)
        if name in ("GUIDE.md", "START.md"):
            assert ("guide", "next") in words, name                       # 길의 입구를 반드시 말한다
        for w in words:
            seen.add((name, w))
            with pytest.raises(SystemExit) as e:
                ub.main([w[0], "--help"])
            assert e.value.code == 0, (name, w)
            if w[0] == "guide" and len(w) > 1:
                assert w[1] in guide.GUIDE_COMMANDS, (name, w)
            if w[0] == "setup" and len(w) > 1:
                assert w[1] in setup_choices, (name, w)
        for ln in lines:                                                  # 채울 자리 없는 줄은 인자까지 실제 파서로(검토 7)
            check_command(ln)
    assert seen
    capsys.readouterr()


def test_install_line_is_written_the_same_in_guide_and_start():
    for p in (GUIDE_MD, START_MD):
        text = p.read_text(encoding="utf-8")
        assert INSTALL_LINE in text, p.name
        assert "sudo" not in text, p.name                                 # 설치는 관리자 암호 없이(G10)
    assert "https://github.com/parkds-claude/wideeye/raw/main/GUIDE.md" in START_MD.read_text(encoding="utf-8")


# ---- GUIDE.md -------------------------------------------------------------------------------------------------------

def test_guide_starts_with_the_rules_the_ai_obeys_first():
    text = GUIDE_MD.read_text(encoding="utf-8")
    heads = headings(text)
    assert len(heads) >= 3 and "지킬 것" in heads[0] and "설치 전" in heads[1] and "설치 뒤" in heads[2], heads
    rules = section(text, "지킬 것")
    for must in ("출력만 따른다", "비밀", "읽거나", "자료", "한 번에 질문 하나", "허락", "--detail", "사람의 말"):
        assert must in rules, must
    assert "needs_person" in rules                                        # 사람 손이 없는 단계(install)에서 멈추지 않게(검토 2)
    loop = section(text, "설치 뒤")
    for must in ("wideeye guide next", "wideeye guide check", "끝", "<…>", "needs_person", "한 줄씩", "'막히면'", "'이렇게 해요'"):
        assert must in loop, must
    assert "run_forms" in text and "기계:" in text                         # --json 의 채울 자리 명령은 그대로 부르지 않는다(작업 6)
    out = section(text, "출력 읽는 법")
    for must in ("needs_person", "건너뜀", "state", "note", "next"):          # check 의 기계 줄 키 · 종료값 0 에 건너뜀(검토 8)
        assert must in out, must
    pre = section(text, "설치 전")
    assert "믿고" in pre and "브라우저" in pre                              # curl | sh 는 설치 프로그램을 믿고 받는다 — 먼저 읽어 볼 수 있다(검토 4)
    assert "실제 이름" in pre                                              # 허락 창의 단추는 화면의 글자로(검토 6b)


def test_guide_tells_the_ai_to_fill_its_own_kind_and_says_b_is_not_ready():
    pre = section(GUIDE_MD.read_text(encoding="utf-8"), "설치 전")
    assert "ai=" in pre and "스스로" in pre
    assert "다음 판" in pre                                               # B(Claude 앱 도우미)는 2부 — 있다고 말하지 않는다


# ---- START.md -------------------------------------------------------------------------------------------------------

def test_start_first_message_puts_the_follow_the_guide_rule_first():
    first = section(START_MD.read_text(encoding="utf-8"), "첫 한마디")
    blocks = fenced_blocks(first)
    assert blocks, "첫 한마디에 복사할 상자가 없다"
    msg = blocks[0]
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    assert lines[0].startswith(FIRST_RULE), lines[0]
    assert "https://github.com/parkds-claude/wideeye/raw/main/GUIDE.md" in msg and "비밀번호" in msg and "한 번에 하나" in msg


def test_start_carries_the_guide_paste_part_word_for_word():
    guide_part = paste_part(GUIDE_MD.read_text(encoding="utf-8"))
    assert guide_part.startswith("## ") and INSTALL_LINE in guide_part and "wideeye guide next" in guide_part
    blocks = fenced_blocks(section(START_MD.read_text(encoding="utf-8"), "열지 못할 때"))
    assert blocks and blocks[0].strip("\n") == guide_part


def test_start_has_the_zero_step_parts_in_order():
    text = START_MD.read_text(encoding="utf-8")
    heads = headings(text)
    order = ["준비물", "터미널", "AI 고르기", "로그인", "탭5", "첫 한마디", "열지 못할 때"]
    at = [next(i for i, h in enumerate(heads) if w in h) for w in order]
    assert at == sorted(at), heads
    prep = section(text, "준비물")
    for must in ("맥", "탭5", "USB-C", "관리자 암호"):
        assert must in prep, must
    term = section(text, "터미널")
    for must in ("⌘", "스페이스", "터미널", "Return"):
        assert must in term, must
    pick = section(text, "AI 고르기")
    for must in ("A", "B", "C", "Claude Code", "Codex", "Gemini", "https://"):
        assert must in pick, must


def test_start_clears_the_beginner_blockers_from_the_review():
    """검토 4 · 5 · 6 · 9 — 처음 쓰는 사람이 막히는 곳."""
    text = START_MD.read_text(encoding="utf-8")
    term = section(text, "터미널")
    assert "terminal" in term and "응용 프로그램 › 유틸리티 › 터미널" in term          # 영어로 쳐도 · 검색이 안 열릴 때의 길
    pick = section(text, "AI 고르기")
    for must in ("claude", "codex", "gemini", "Yes", "Allow", "npm", "새 폴더", "바탕화면", "Local", "문서"):
        assert must in pick, must
    assert "2026년 6월 18일" in pick and "Google One" in pick                     # 공식 저장소가 말한 범위 그대로
    first = section(text, "첫 한마디")
    assert "복사 단추" in first and first.index("복사 단추") < first.index("끌어")      # 단추를 먼저, 끌어 고르기는 그다음
    assert "믿고" in first and "다시 보낸 뒤" in first and "이어서" in first
    for t in (text, " ".join(x for x in guide.load().step("hello").tell if isinstance(x, str))):
        assert "메시지 본문은 기본으로 가지 않아요" in t and "그때마다 먼저 물어요" in t   # --detail 은 그때마다 허락(검토 5)


# ---- 메신저 상표 -------------------------------------------------------------------------------------------------------

def _brand_before_messenger(text: str) -> list[str]:
    first = text.find("메신저")
    return [b for b in BRANDS if b in text and (first < 0 or text.find(b) < first)]


def test_public_text_does_not_lead_with_a_specific_messenger_brand():
    start = START_MD.read_text(encoding="utf-8")
    assert "메신저" in start
    assert not _brand_before_messenger(start)
    readme = PUBLIC_README.read_text(encoding="utf-8")                       # 공개 README(작업 9) — 첫 화면에 상표를 앞세우지 않는다
    assert not _brand_before_messenger("\n".join(readme.splitlines()[:FIRST_SCREEN]))
    assert _brand_before_messenger("카톡과 메신저") == ["카톡"]                 # 시험 자체


# ---- 다른 글이 가리키는 곳 ---------------------------------------------------------------------------------------------

def test_setup_and_agents_send_first_time_setup_to_the_guide():
    setup = SETUP_MD.read_text(encoding="utf-8").splitlines()
    assert setup[0].startswith("# 긴급현황판 설정 안내서")                  # 1판 setup guide 가 이 글을 그대로 찍는다
    assert any("GUIDE.md" in ln for ln in setup[:5])
    private_top = "\n".join(AGENTS_MD.read_text(encoding="utf-8").splitlines()[:10])
    assert "GUIDE.md" in private_top                                                     # 이 작업장의 개발 지도 — 맨 위가 GUIDE.md 로(R10)
    public = PUBLIC_AGENTS.read_text(encoding="utf-8")
    top = "\n".join(public.splitlines()[:20])
    assert "GUIDE.md" in top and "고치지" in top and "비밀" in top
    assert not (ROOT / "docs" / "dev-map.md").exists()
    text = "\n".join(setup)                                                             # 마지막 검토 I2 — 연결은 처음이면 GUIDE 로
    connect = text[text.index("## 2단계"):text.index("## 3단계")]
    head, question = connect.split("질문(그대로):", 1)
    assert "GUIDE.md" in head and "guide next" in head
    first_q = question.strip().splitlines()[0]
    assert "카카오" not in first_q and "카톡" not in first_q
    assert sum("카카오톡" in ln for ln in connect.splitlines()) == 1 and "고급 길" in connect   # 고급 길 한 줄


# ---- 길 정의의 사람 말(작업 4 검토에서 넘어온 것) ----------------------------------------------------------------------

def test_roadmap_carry_overs_from_the_review():
    rm = guide.load()
    assert rm.step("tab5-show").who == "both"
    assert any("스스로" in t for t in rm.step("hello").tell if isinstance(t, str))       # 어떤 AI 인지는 AI 가 채운다
    assert any("따옴표" in t for t in rm.step("link-gmail").tell if isinstance(t, str))   # 구글 화면의 영어 이름은 따옴표 안의 말을 찾는다
    assert any("맨 앞" in (t if isinstance(t, str) else t["say"]) for t in rm.step("try").do)  # 시험 낱말은 글의 맨 앞에(결정 R7)


# ---- 길 명령 · 확인기가 사람에게 건네는 글(검토 10) -------------------------------------------------------------------------

def person_strings(path: Path) -> list[tuple[int, str]]:
    """한글이 든 문자열 상수(f-글의 조각 포함) — 독스트링은 빼고(개발자 글). AI 가 사람에게 그대로 옮기는 글이 여기 있다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and re.search(r"[가-힣]", n.value)]


def test_guide_cli_and_checker_messages_have_no_forbidden_words():
    forbid, allow = word_lists()
    bad = []
    for path in CLI_SOURCES:
        strings = person_strings(path)
        assert len(strings) > 20, path                                   # 훑을 글을 실제로 찾았다
        for line, s in strings:
            hits, _ = forbidden_hits(s, forbid, allow)
            bad += [(path.name, line, w, around) for w, around in hits]
    assert not bad, "\n".join(map(str, bad))


# ---- 설정 한눈에의 'AI 에게 줄 말'(마지막 검토 I2) ---------------------------------------------------------------------

def test_the_setup_page_prompt_starts_the_same_path_as_start_md():
    """메뉴 막대 '설정…' → 설정 한눈에의 'AI 에게 줄 말'도 START 의 첫 한마디와 같은 길(GUIDE.md → wideeye guide next)로 보낸다 —
    처음 설정 입구가 둘이면 AI 가 두 안내서 사이에서 헷갈린다. 설정을 고칠 때만 setup guide(SETUP.md)."""
    from urgent_board import setup_steps
    text = setup_steps.prompt_text("/Users/me/WideEye")
    assert text.startswith(FIRST_RULE + ".")
    assert "/Users/me/WideEye/GUIDE.md" in text and "/Users/me/WideEye/bin/wideeye guide next" in text and "--for-chat" in text
    assert text.index("guide next") < text.index("setup guide")                      # 처음 설정이 먼저, 고치기는 그 뒤
    assert "긴급현황판" not in text and "urgent-board" not in text and "urgentboard" not in text
    assert "비밀번호" in text and "묻지" in text
    spaced = setup_steps.prompt_text("/Users/me/My Apps/WideEye")
    assert '"/Users/me/My Apps/WideEye/bin/wideeye" guide next' in spaced and '"/Users/me/My Apps/WideEye/GUIDE.md"' in spaced
    for line in text.splitlines():
        for w in ("카톡", "카카오"):
            assert w not in line


# ---- 무엇을 이을 수 있는지 먼저(마지막 검토 I3 · M7 · M2) ----------------------------------------------------------------

LINKABLE = {"sms": "문자", "calls": "부재중 전화", "teams": "Microsoft Teams", "gmail": "Gmail", "telegram": "텔레그램", "other": "그 밖의 메신저"}


def _linkable_block(text: str) -> str:
    i = text.index("# 이번 판에서 이을 수 있는 것\n")                                    # 제목(README 머리의 영어 글이 가리키는 곳)
    return text[i:text.index("\n\n", text.index("\n- ", i) + 1)]


def test_start_and_readme_say_what_can_be_linked_before_install():
    start = START_MD.read_text(encoding="utf-8")
    readme = PUBLIC_README.read_text(encoding="utf-8")
    for text, before in ((start, "## 2."), (readme, "## 무엇을 읽고")):
        block = _linkable_block(text)
        assert text.index("# 이번 판에서 이을 수 있는 것\n") < text.index(before)       # 깔기 전에 안다
        for name in ("문자", "부재중 전화", "아이폰", "Microsoft Teams", "Gmail", "텔레그램", "어려운 길", "그 밖의 메신저"):
            assert name in block, name
        assert "아직" in block and "카톡" not in block and "카카오" not in block      # 카톡을 내세우지 않는다(G12)
    assert "~/WideEye" in start and "바탕화면" in start[start.index("~/WideEye") - 200:start.index("~/WideEye") + 50]   # 폴더가 둘(M7)


def test_hello_question_names_the_same_services_the_profile_takes():
    from tools import guide_cli
    hello = guide.load().step("hello")
    q = next(x for x in hello.do if isinstance(x, str) and x.startswith("쓰는 것을 모두 골라요"))
    assert set(LINKABLE) == set(guide.PROFILE["messengers"])                            # 답을 받는 코드와 같은 갈래
    for key, name in LINKABLE.items():
        assert name in q, key
        assert name in guide_cli.PROFILE_ASK["messengers"], key
    assert "카톡" not in q and "카카오" not in q


def test_readme_commands_are_runnable_as_written():
    """M2 — wideeye 는 PATH 에 없다: 표의 명령은 설치 끝에 나온 전체 위치(보통 ~/WideEye/bin/wideeye)로 적는다."""
    readme = PUBLIC_README.read_text(encoding="utf-8")
    table = readme[readme.index("## 자주 쓰는 명령"):readme.index("## 코드를 고치는 사람에게")]
    cmds = re.findall(r"`([^`]*wideeye [^`]*)`", table)
    assert cmds and all(c.startswith("~/WideEye/bin/wideeye ") for c in cmds), cmds
    assert "전체 위치" in table

