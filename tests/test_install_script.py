"""설치 스크립트 2판(설계 2026-10-18 공개판 G10, 계획 1부 작업 8 · 결정 R11) — Homebrew 없이, 관리자 암호 없이, 받는 것마다 지문(sha256).

시험은 인터넷에 닿지 않는다: `WIDEEYE_FETCH=file://<폴더>` 로 같은 이름의 가짜 파일을 받게 하고, 스크립트 사본의 **고정 판 묶음만**
가짜 파일의 지문으로 바꾼다(그 밖의 줄은 글자 그대로 — `with_pins` 가 확인한다). 가짜 uv 는 받은 명령을 적기만 하고, 코드 폴더의
`bin/wideeye` 도 가짜라 실제 launchd 작업을 만들지 않는다. HOME · TMPDIR · 글꼴 폴더 · 자료 폴더는 모두 임시 폴더다."""
import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from tests.test_guide_docs import forbidden_hits, word_lists

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install" / "wideeye-install.sh"
WRAPPER = ROOT / "install.sh"
LOCK = ROOT / "requirements.lock"
BUILD_LOCK = ROOT / "install" / "requirements-build.lock"
PIN_START, PIN_END = "# ==== 고정 판 · 지문", "# ==== 고정 판 끝"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PIN_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)='([^']*)'")
NEXT_LINE = "다음:"
SHELLS = [s for s in ("/bin/sh", "/bin/dash") if Path(s).exists()]
FONT_NAMES = ("Pretendard-Regular.otf", "Pretendard-Bold.otf")
SYSTEM_PRETENDARD = any(Path("/Library/Fonts").glob("Pretendard-*.otf"))    # 맥 전체 글꼴에 있으면 스크립트가 늘 건너뛴다
needs_no_system_font = pytest.mark.skipif(SYSTEM_PRETENDARD, reason="이 맥의 /Library/Fonts 에 Pretendard 가 있다")


# ---- 고정 판 묶음 ----------------------------------------------------------------------------------------------------

def pin_block(text: str) -> tuple[int, int, dict[str, str]]:
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(PIN_START))
    end = next(i for i, l in enumerate(lines) if l.startswith(PIN_END))
    pins = {}
    for line in lines[start + 1:end]:
        m = PIN_LINE.match(line)
        if m:
            assert m.group(1).startswith("PIN_"), m.group(1)         # 사람의 UV_* 를 지울 때 함께 지워지지 않게
            pins[m.group(1)[4:]] = m.group(2)
    return start, end, pins


def with_pins(dest: Path, **over: str) -> Path:
    """스크립트 사본 — 고정 판 묶음 안의 값만 바꾼다. 바뀐 줄이 묶음 밖에 있으면 실패."""
    text = SCRIPT.read_text(encoding="utf-8")
    start, end, pins = pin_block(text)
    lines = text.splitlines(keepends=True)
    for name, value in over.items():
        assert name in pins, name
        idx = next(i for i in range(start, end) if lines[i].startswith(f"PIN_{name}="))
        lines[idx] = re.sub(r"^([A-Z0-9_]+)='[^']*'", lambda m: f"{m.group(1)}='{value}'", lines[idx])
    out = "".join(lines)
    changed = [i for i, (a, b) in enumerate(zip(text.splitlines(), out.splitlines())) if a != b]
    assert all(start < i < end for i in changed)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(out, encoding="utf-8")
    return dest


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- 가짜 내려받기 ----------------------------------------------------------------------------------------------------

FAKE_UV = r"""#!/bin/sh
# 가짜 uv — 받은 명령을 적고, 할 일을 흉내 낸다(파이썬 설치 · 찾기 · 가상 환경). pip 은 적기만.
printf '%s\n' "$*" >> "$FAKE_UV_LOG"
last=''
for a in "$@"; do last=$a; done
case "$1" in
  --version) echo "uv 0.12.23 (fake)" ;;
  python)
    d="$UV_PYTHON_INSTALL_DIR/cpython-$last-macos-aarch64-none/bin"
    case "$2" in
      install)
        mkdir -p "$d"
        printf '#!/bin/sh\nprintf "%%s\\n" "py $*" >> "$FAKE_UV_LOG"\nexit 0\n' > "$d/python3.12"
        chmod 755 "$d/python3.12" ;;
      find) [ -x "$d/python3.12" ] || exit 2; echo "$d/python3.12" ;;
    esac ;;
  venv)
    py='' clear=''
    while [ $# -gt 0 ]; do
      case "$1" in --python) py=$2; shift ;; --clear) clear=1 ;; esac
      shift
    done
    if [ -e "$last" ] && [ -z "$clear" ]; then echo "already exists" >&2; exit 2; fi
    rm -rf "$last"; mkdir -p "$last/bin"; ln -s "$py" "$last/bin/python"
    echo "version_info = 3.12.15" > "$last/pyvenv.cfg" ;;
  pip)
    printf 'env UV_INDEX_URL=%s UV_NO_CONFIG=%s\n' "${UV_INDEX_URL:-}" "${UV_NO_CONFIG:-}" >> "$FAKE_UV_LOG"
    printf 'tmp %s %s\n' "$TMPDIR" "$UV_CACHE_DIR" >> "$FAKE_UV_LOG"
    [ -z "${FAKE_UV_PIP_FAIL:-}" ] || exit 1 ;;
esac
exit 0
"""

FAKE_WIDEEYE = """#!/bin/sh
# 가짜 wideeye — 부른 인자만 적는다(시험이 실제 launchd 작업을 만들지 않게)
printf '%s\\n' "$*" >> "$FAKE_WIDEEYE_LOG"
if [ -n "${FAKE_WIDEEYE_FAIL:-}" ]; then echo "등록 실패 까닭(시험)"; exit 1; fi
echo "등록했다(시험)"
"""


def tgz(files: dict[str, tuple[bytes, int]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, (data, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), mode, 1_700_000_000
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def font_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in FONT_NAMES:
            z.writestr(f"public/static/{n}", b"OTTO fake " + n.encode())
        z.writestr("public/variable/PretendardVariable.ttf", b"not this one")
        z.writestr("LICENSE.txt", b"OFL")
    return buf.getvalue()


def write_source(d: Path, with_wrapper: bool = False) -> Path:
    """가짜 코드 폴더 — bin/wideeye(가짜) · 잠금 파일 둘. with_wrapper 면 진짜 install.sh 도."""
    (d / "bin").mkdir(parents=True, exist_ok=True)
    (d / "install").mkdir(parents=True, exist_ok=True)
    (d / "bin" / "wideeye").write_text(FAKE_WIDEEYE, encoding="utf-8")
    (d / "bin" / "wideeye").chmod(0o755)
    (d / "requirements.lock").write_text("flask==3.1.3 \\\n    --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")
    (d / "install" / "requirements-build.lock").write_text("setuptools==84.0.0 \\\n    --hash=sha256:" + "1" * 64 + "\n",
                                                           encoding="utf-8")
    if with_wrapper:
        shutil.copy2(WRAPPER, d / "install.sh")
    return d


class Rig:
    """임시 맥 한 대 — HOME · TMPDIR · 받을 곳 · 글꼴 폴더 · 가짜 코드 폴더와 그 지문을 맞춘 스크립트 사본."""

    def __init__(self, tmp: Path, **over: str):
        self.tmp = tmp
        self.home = Path(os.path.realpath(tmp)) / "home"          # 스크립트는 깔 곳을 실제 경로(pwd -P)로 적는다
        self.app = self.home / "WideEye"
        self.fonts = self.home / "Library" / "Fonts"
        self.tmpdir = tmp / "t"
        self.fetch = tmp / "fetch"
        self.src = write_source(tmp / "src")
        for p in (self.home, self.tmpdir, self.fetch):
            p.mkdir(parents=True, exist_ok=True)
        self.uv_log, self.we_log = tmp / "uv.log", tmp / "wideeye.log"
        uv = FAKE_UV.encode()
        arm = tgz({"uv-aarch64-apple-darwin/uv": (uv, 0o755), "uv-aarch64-apple-darwin/uvx": (b"#!/bin/sh\n", 0o755)})
        intel = tgz({"uv-x86_64-apple-darwin/uv": (uv, 0o755), "uv-x86_64-apple-darwin/uvx": (b"#!/bin/sh\n", 0o755)})
        font = font_zip()
        app = tgz({f"wideeye-0.1.0/{k}": v for k, v in {
            "bin/wideeye": (FAKE_WIDEEYE.encode(), 0o755),
            "requirements.lock": ((self.src / "requirements.lock").read_bytes(), 0o644),
            "install/requirements-build.lock": ((self.src / "install" / "requirements-build.lock").read_bytes(), 0o644),
            "README.md": (b"# WideEye\n", 0o644)}.items()})
        (self.fetch / "uv-aarch64-apple-darwin.tar.gz").write_bytes(arm)
        (self.fetch / "uv-x86_64-apple-darwin.tar.gz").write_bytes(intel)
        (self.fetch / "Pretendard-1.3.9.zip").write_bytes(font)
        (self.fetch / "v0.1.0.tar.gz").write_bytes(app)
        pins = {"UV_ARM_SHA256": sha(arm), "UV_INTEL_SHA256": sha(intel), "FONT_SHA256": sha(font)}
        pins.update(over)
        self.script = with_pins(tmp / "script" / "wideeye-install.sh", **pins)
        self.app_sha = sha(app)

    def env(self, **extra: str) -> dict:
        e = {"HOME": str(self.home), "TMPDIR": str(self.tmpdir), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
             "WIDEEYE_HOME": str(self.app), "WIDEEYE_FETCH": self.fetch.as_uri(), "WIDEEYE_FONTS_DIR": str(self.fonts),
             "WIDEEYE_SOURCE": str(self.src), "FAKE_UV_LOG": str(self.uv_log), "FAKE_WIDEEYE_LOG": str(self.we_log),
             "URGENT_BOARD_HOME": str(self.tmp / "data"), "URGENT_BOARD_LABEL_PREFIX": "io.examplecorp.install-test",
             "UV_INDEX_URL": "https://example.invalid/should-be-dropped", "LANG": "ko_KR.UTF-8"}
        e.update(extra)
        return {k: v for k, v in e.items() if v is not None}

    def run(self, shell: str = "/bin/sh", script: Path | None = None, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([shell, str(script or self.script)], env=self.env(**extra), capture_output=True, text=True,
                              timeout=60, stdin=subprocess.DEVNULL)

    def uv_calls(self) -> list[str]:
        return self.uv_log.read_text(encoding="utf-8").splitlines() if self.uv_log.exists() else []

    def wideeye_calls(self) -> list[str]:
        return self.we_log.read_text(encoding="utf-8").splitlines() if self.we_log.exists() else []


def last_line(text: str) -> str:
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1] if lines else ""


def fail_text(r: subprocess.CompletedProcess) -> str:
    return last_line(r.stderr)


# ---- 글 · 꼴 ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("path", [SCRIPT, WRAPPER])
def test_scripts_are_valid_posix_sh(shell, path):
    r = subprocess.run([shell, "-n", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_half_downloaded_script_cannot_run_half_its_steps():
    """본문은 모두 main() 안에 있고 마지막 줄이 그것을 부른다 — 받다 끊긴 스크립트는 아무 단계도 돌리지 않는다."""
    lines = [l for l in SCRIPT.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lines[-1] == 'main "$@"'
    assert sum(1 for l in lines if l.startswith("main ")) == 1
    top = [l for l in lines if not l.startswith((" ", "\t", "#", "}")) and l != 'main "$@"']
    assert all(re.match(r"^([A-Z][A-Z0-9_]*='[^']*'|[a-z_]+\(\) \{|#!/bin/sh)", l) for l in top), top


def test_a_cut_off_download_does_nothing(tmp_path):
    """받다 끊긴 스크립트(어디서 끊겨도)는 아무것도 만들거나 받지 않는다."""
    rig = Rig(tmp_path)
    text = rig.script.read_bytes()
    body = text.rstrip(b"\n")
    cuts = [text[: int(len(text) * f)] for f in (0.05, 0.2, 0.4, 0.6, 0.8, 0.95)] + [body[: body.rfind(b"\n") + 1]]
    for i, part in enumerate(cuts):
        cut = tmp_path / f"cut-{i}.sh"
        cut.write_bytes(part)
        rig.run(script=cut)
        assert not rig.app.exists() and rig.uv_calls() == [] and rig.wideeye_calls() == [], i


def test_install_never_asks_for_admin_rights():
    for path in (SCRIPT, WRAPPER):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bsudo\b", text, re.I), path
        assert "administrator privileges" not in text and "osascript" not in text, path


def test_versions_pin_every_download_with_a_sha256():
    _, _, pins = pin_block(SCRIPT.read_text(encoding="utf-8"))
    urls = {k[:-4]: v for k, v in pins.items() if k.endswith("_URL")}
    assert set(urls) == {"UV_ARM", "UV_INTEL", "FONT", "APP"}
    for name, url in urls.items():
        assert f"{name}_SHA256" in pins, name
        if name == "APP":                                          # 릴리스 묶음: 작업장 · 릴리스 전 공개판은 둘 다 빈 값, 릴리스 판은
            if url == "":                                          # GitHub 릴리스 파일 주소(결정 R12 — archive 가 아님)와 그 지문
                assert pins["APP_SHA256"] == ""
            else:
                m = re.fullmatch(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/releases/download/(v\d+\.\d+\.\d+)/wideeye-(v\d+\.\d+\.\d+)\.tar\.gz", url)
                assert m and m.group(1) == m.group(2), url
                assert HEX64.match(pins["APP_SHA256"])
            continue
        assert url.startswith("https://github.com/"), url
        assert HEX64.match(pins[f"{name}_SHA256"]), name
    v = pins["UV_VERSION"]
    assert urls["UV_ARM"] == f"https://github.com/astral-sh/uv/releases/download/{v}/uv-aarch64-apple-darwin.tar.gz"
    assert urls["UV_INTEL"] == f"https://github.com/astral-sh/uv/releases/download/{v}/uv-x86_64-apple-darwin.tar.gz"
    assert urls["FONT"].startswith("https://github.com/orioncactus/pretendard/releases/download/")
    assert re.match(r"^3\.12\.\d+$", pins["PY_VERSION"])
    downloads = re.findall(r"https://[A-Za-z0-9][^\s'\"]*", SCRIPT.read_text(encoding="utf-8").split(PIN_END, 1)[1])
    assert downloads == [], "고정 판 묶음 밖에 내려받는 주소를 두지 않는다"


def test_failure_lines_are_plain_words():
    """실패마다 쉬운 말 한 줄 — 사람에게 그대로 전해지는 글이라 개발자 낱말을 쓰지 않는다(tests/forbidden_words.txt)."""
    forbid, allow = word_lists()
    text = SCRIPT.read_text(encoding="utf-8")
    msgs = re.findall(r'\b(?:fail|say) "((?:[^"\\]|\\.)*)"', text)
    fails = re.findall(r'\bfail "((?:[^"\\]|\\.)*)"', text)
    assert len(fails) >= 10
    for m in msgs:
        plain = re.sub(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*", "", m)
        hits, _ = forbidden_hits(plain, forbid, allow)
        assert not hits, (m, hits)
    for m in fails:
        assert "\\n" not in m and re.search(r"[가-힣]", m), m


def test_lock_files_pin_every_package_with_hashes():
    text = LOCK.read_text(encoding="utf-8")
    assert "--universal" in text and "--generate-hashes" in text           # 두 칩 모두 같은 잠금으로 확인한다
    reqs = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", text, re.M)
    names = {n.lower() for n, _ in reqs}
    assert {"flask", "pyyaml", "telethon", "pyobjc-framework-cocoa", "pyserial", "cryptography"} <= names
    assert "pytest" not in names                                              # 시험 부품은 requirements-dev.txt
    blocks = re.split(r"^(?=[A-Za-z0-9_.-]+==)", text, flags=re.M)[1:]
    assert len(blocks) == len(reqs)
    for b in blocks:
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", b), b.splitlines()[0]
    assert re.search(r"^setuptools==\S+ \\\n\s+--hash=sha256:[0-9a-f]{64}", BUILD_LOCK.read_text(encoding="utf-8"), re.M)
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in dev and "pytest" in dev
    assert "pytest" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_wrapper_runs_the_installer_on_its_own_folder():
    text = WRAPPER.read_text(encoding="utf-8")
    assert "install/wideeye-install.sh" in text and "WIDEEYE_SOURCE=" in text and "WIDEEYE_HOME=" in text


def test_tool_folders_are_never_committed():
    """코드 폴더 안에서 install.sh 를 돌리면 .tools(uv) · .python 이 생긴다 — 자동 커밋에 실리지 않게(홈 CLAUDE.md 산출물 규칙)."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {".tools/", ".python/"} <= set(ignore)


# ---- 돌려 보기 --------------------------------------------------------------------------------------------------------

@needs_no_system_font
@pytest.mark.parametrize("shell", SHELLS)
def test_local_install_ends_with_the_next_step_and_the_full_path(tmp_path, shell):
    rig = Rig(tmp_path)
    r = rig.run(shell)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert lines[-1].startswith(NEXT_LINE) and "wideeye guide next" in lines[-1]
    assert lines[-2].endswith("/bin/wideeye")
    full = lines[-2].split()[-1]
    assert full == str(rig.app / "bin" / "wideeye") and os.access(full, os.X_OK)
    real = rig.app / ".python" / "cpython-3.12.15-macos-aarch64-none" / "bin" / "python3.12"
    assert any(str(real) in l and "전체 디스크 접근" in l for l in lines[-5:]), lines[-5:]
    assert rig.wideeye_calls() == ["install --no-open"]
    assert "등록했다(시험)" not in r.stdout + r.stderr                               # 등록 출력(1판 안내 · '지금 허락' 글)은 삼킨다(검토 1)
    calls = rig.uv_calls()
    pip = [c for c in calls if c.startswith("pip install")]
    assert len(pip) == 1
    assert "env UV_INDEX_URL= UV_NO_CONFIG=1" in calls                                    # 사람의 uv 설정은 쓰지 않는다
    tmp_line = next(c for c in calls if c.startswith("tmp ")).split()
    assert all(x.startswith(str(rig.tmpdir) + "/wideeye-install.") for x in tmp_line[1:])   # uv 의 임시 · 받은 사본도 지울 곳에
    assert "--require-hashes" in pip[0] and f"-r {rig.app}/requirements.lock" in pip[0]
    assert f"-b {rig.app}/install/requirements-build.lock" in pip[0] and "--only-binary :all:" in pip[0]
    assert f"--python {rig.app}/.venv/bin/python" in pip[0]
    assert any(c.startswith("python install") and "3.12.15" in c and "--no-bin" in c for c in calls)
    assert sorted(p.name for p in rig.fonts.iterdir()) == sorted(FONT_NAMES)          # 고정 꼴 글꼴만(가변 글꼴 · 라이선스 글은 빼고)
    assert (rig.app / ".tools" / "uv").exists() and (rig.app / "bin" / "wideeye").exists()
    assert list(rig.tmpdir.iterdir()) == []                                            # 임시 폴더는 지웠다
    made = sorted(str(p.relative_to(rig.home)) for p in rig.home.rglob("*") if p.is_file()
                  and not str(p).startswith((str(rig.app) + "/", str(rig.fonts) + "/")))
    assert made == []                                                                  # 깔 곳 · 글꼴 폴더 밖에는 쓰지 않는다


def test_install_refuses_a_download_with_the_wrong_checksum(tmp_path):
    rig = Rig(tmp_path, UV_ARM_SHA256="a" * 64, UV_INTEL_SHA256="a" * 64)
    r = rig.run()
    assert r.returncode == 1
    assert "지문" in fail_text(r)
    assert not (rig.app / ".tools" / "uv").exists() and not (rig.app / ".venv").exists()
    assert not (rig.app / ".python").exists() and rig.uv_calls() == [] and rig.wideeye_calls() == []
    assert not rig.fonts.exists()
    assert list(rig.tmpdir.iterdir()) == []                                            # 받은 것은 남기지 않는다


@needs_no_system_font
def test_a_font_with_the_wrong_checksum_is_not_installed(tmp_path):
    rig = Rig(tmp_path, FONT_SHA256="b" * 64)
    r = rig.run()
    assert r.returncode == 1 and "지문" in fail_text(r)
    assert not rig.fonts.exists() or not any(rig.fonts.iterdir())
    assert rig.wideeye_calls() == []                                                   # 등록 전에 멈춘다


@needs_no_system_font
def test_install_is_idempotent(tmp_path):
    rig = Rig(tmp_path)
    first = rig.run()
    assert first.returncode == 0, first.stderr
    font_times = {p.name: p.stat().st_mtime_ns for p in rig.fonts.iterdir()}
    for f in rig.fetch.iterdir():                                                      # 두 번째에는 아무것도 받지 못한다
        f.unlink()
    n = len(rig.uv_calls())
    second = rig.run()
    assert second.returncode == 0, second.stdout + second.stderr
    assert [l for l in second.stdout.splitlines() if l.strip()][-2:] == [l for l in first.stdout.splitlines() if l.strip()][-2:]
    again = rig.uv_calls()[n:]
    assert not any(c.startswith(("python install", "venv")) for c in again), again        # 고정 판 파이썬 · 가상 환경은 그대로
    assert any(c.startswith("pip install") for c in again)
    assert {p.name: p.stat().st_mtime_ns for p in rig.fonts.iterdir()} == font_times    # 글꼴은 건너뜀
    assert rig.wideeye_calls() == ["install --no-open", "install --no-open"]


def test_an_installed_pretendard_is_kept_and_not_downloaded(tmp_path):
    rig = Rig(tmp_path)
    rig.fonts.mkdir(parents=True)
    mine = rig.fonts / "Pretendard-Regular.otf"
    mine.write_bytes(b"the person's own font")
    (rig.fetch / "Pretendard-1.3.9.zip").unlink()
    r = rig.run()
    assert r.returncode == 0, r.stderr
    assert mine.read_bytes() == b"the person's own font" and [p.name for p in rig.fonts.iterdir()] == [mine.name]


def test_remote_mode_refuses_while_no_release_is_pinned(tmp_path):
    """릴리스 묶음의 지문이 비어 있으면(아직 판이 없다) 아무것도 받지 않고 쉬운 말로 멈춘다 — 진짜 스크립트 그대로(릴리스 판이면
    그 두 고정값만 비운 사본으로)."""
    rig = Rig(tmp_path)
    if pin_block(SCRIPT.read_text(encoding="utf-8"))[2]["APP_SHA256"]:
        with_pins(tmp_path / "real.sh", APP_URL="", APP_SHA256="")
    else:
        shutil.copy2(SCRIPT, tmp_path / "real.sh")
    r = rig.run(script=tmp_path / "real.sh", WIDEEYE_SOURCE=None)
    assert r.returncode == 1
    assert fail_text(r).startswith("아직 내려받을 판이 없어요")
    assert not rig.app.exists() and rig.uv_calls() == []


def test_remote_mode_downloads_the_pinned_release(tmp_path):
    rig = Rig(tmp_path)
    url = "https://example.invalid/wideeye/archive/refs/tags/v0.1.0.tar.gz"
    script = with_pins(tmp_path / "remote.sh", **_shas(rig), APP_URL=url, APP_SHA256=rig.app_sha)
    r = rig.run(script=script, WIDEEYE_SOURCE=None)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (rig.app / "README.md").read_text() == "# WideEye\n" and rig.wideeye_calls() == ["install --no-open"]
    assert last_line(r.stdout).startswith(NEXT_LINE)


def test_remote_mode_refuses_a_release_with_the_wrong_checksum(tmp_path):
    rig = Rig(tmp_path)
    url = "https://example.invalid/wideeye/archive/refs/tags/v0.1.0.tar.gz"
    script = with_pins(tmp_path / "remote.sh", **_shas(rig), APP_URL=url, APP_SHA256="c" * 64)
    r = rig.run(script=script, WIDEEYE_SOURCE=None)
    assert r.returncode == 1 and "지문" in fail_text(r)
    assert not (rig.app / "bin").exists() and not (rig.app / "README.md").exists()


def _shas(rig: Rig) -> dict[str, str]:
    """rig 가 맞춘 지문(uv · 글꼴) — 릴리스 묶음 것은 빼고."""
    pins = pin_block(rig.script.read_text(encoding="utf-8"))[2]
    return {k: v for k, v in pins.items() if k.endswith("_SHA256") and k != "APP_SHA256"}


def test_a_missing_download_says_to_check_the_connection(tmp_path):
    rig = Rig(tmp_path)
    for f in rig.fetch.glob("uv-*"):
        f.unlink()
    r = rig.run()
    assert r.returncode == 1 and "인터넷" in fail_text(r)


def test_a_folder_holding_something_else_is_refused(tmp_path):
    rig = Rig(tmp_path)
    rig.app.mkdir(parents=True)
    (rig.app / "notes.txt").write_text("mine")
    r = rig.run()
    assert r.returncode == 1 and "다른 것" in fail_text(r)
    assert sorted(p.name for p in rig.app.iterdir()) == ["notes.txt"] and rig.uv_calls() == []


def test_parts_that_fail_to_install_stop_before_registering(tmp_path):
    rig = Rig(tmp_path)
    r = rig.run(FAKE_UV_PIP_FAIL="1")
    assert r.returncode == 1 and "부품" in fail_text(r) and rig.wideeye_calls() == []


def test_install_sh_installs_in_place(tmp_path):
    """코드 폴더 안의 install.sh — 그 폴더를 깔 곳이자 코드로 쓴다(복사하지 않는다)."""
    rig = Rig(tmp_path)
    code = write_source(Path(os.path.realpath(tmp_path)) / "code", with_wrapper=True)
    shutil.copy2(rig.script, code / "install" / "wideeye-install.sh")
    r = subprocess.run(["/bin/sh", str(code / "install.sh")], env=rig.env(WIDEEYE_SOURCE=None, WIDEEYE_HOME=None),
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (code / ".tools" / "uv").exists() and (code / ".venv" / "bin" / "python").exists()
    assert not rig.app.exists()
    assert [l for l in r.stdout.splitlines() if l.strip()][-2].endswith(f"{code}/bin/wideeye")


LEGACY_PY = """#!/bin/sh
# 다른 파이썬으로 만든 .venv 의 흉내 — FAKE_LEGACY_BROKEN 이면 부품 불러오기만 실패한다
printf '%s\\n' "legacy $*" >> "$FAKE_UV_LOG"
case "$*" in *"import flask"*) [ -z "${FAKE_LEGACY_BROKEN:-}" ] || exit 1 ;; esac
exit 0
"""


def legacy_code(tmp_path: Path, rig: Rig, link: bool) -> tuple[Path, Path]:
    """다른 파이썬으로 만든 .venv 가 있는 코드 폴더(이 작업장의 맥들 — 전체 디스크 접근 허락이 그 파이썬에 걸려 있다).
    link 면 .venv 가 다른 폴더를 가리키는 심볼릭 링크(개발용 작업 폴더 — 운영 .venv 로 이어진다)."""
    real = Path(os.path.realpath(tmp_path))
    code = write_source(real / "code", with_wrapper=True)
    shutil.copy2(rig.script, code / "install" / "wideeye-install.sh")
    venv = real / "other-venv" if link else code / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python3").write_text(LEGACY_PY)
    (venv / "bin" / "python3").chmod(0o755)
    (venv / "bin" / "python").symlink_to(venv / "bin" / "python3")
    if link:
        (code / ".venv").symlink_to(venv)
    return code, venv


def run_wrapper(code: Path, rig: Rig, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/sh", str(code / "install.sh")], env=rig.env(WIDEEYE_SOURCE=None, WIDEEYE_HOME=None, **extra),
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("link", [True, False])
def test_an_existing_environment_built_from_another_python_is_left_untouched(tmp_path, link):
    """이미 돌고 있는 .venv(다른 파이썬)는 지우지도, 부품을 바꾸지도 않는다 — 다시 돌려도 판이 내려가지 않게(검토 2).
    고정 판 파이썬도 받지 않는다. 전체 디스크 접근 줄은 그 파이썬의 실제 경로."""
    rig = Rig(tmp_path)
    code, venv = legacy_code(tmp_path, rig, link)
    r = run_wrapper(code, rig)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = rig.uv_calls()
    assert not any(c.startswith(("python install", "venv", "pip")) for c in calls), calls
    assert "부품은 그대로 둬요" in r.stdout and "부품마다 지문 확인" not in r.stdout
    assert (code / ".venv").is_symlink() == link and (venv / "bin" / "python3").exists()
    assert any(str(venv / "bin" / "python3") in l and "전체 디스크 접근" in l for l in r.stdout.splitlines())
    assert last_line(r.stdout).startswith(NEXT_LINE)


def test_parts_are_synced_to_the_lock_only_when_asked(tmp_path):
    rig = Rig(tmp_path)
    code, venv = legacy_code(tmp_path, rig, link=False)
    r = run_wrapper(code, rig, WIDEEYE_SYNC_PARTS="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert any(c.startswith("pip install") and f"--python {code}/.venv/bin/python" in c and "--require-hashes" in c
               for c in rig.uv_calls())


def test_an_existing_environment_missing_parts_gets_them_from_the_lock(tmp_path):
    """다른 파이썬의 .venv 라도 부품을 불러오지 못하면(빠졌다) 잠금으로 채운다 — 그 환경은 지우지 않는다."""
    rig = Rig(tmp_path)
    code, venv = legacy_code(tmp_path, rig, link=False)
    r = run_wrapper(code, rig, FAKE_LEGACY_BROKEN="1")
    assert any(c.startswith("pip install") for c in rig.uv_calls())
    assert (venv / "bin" / "python3").exists()
    assert r.returncode == 1 and "불러오지 못했어요" in fail_text(r)                         # 가짜 pip 은 고치지 못한다 — 쉬운 말로 멈춤


@pytest.mark.parametrize("extra", [{"WIDEEYE_SYNC_PARTS": "1"}, {"FAKE_LEGACY_BROKEN": "1"}])
def test_a_linked_environment_is_never_changed(tmp_path, extra):
    """.venv 가 다른 폴더를 가리키는 링크면(개발용 작업 폴더) 맞추라고 해도 · 부품이 빠졌어도 바꾸지 않고 멈춘다."""
    rig = Rig(tmp_path)
    code, venv = legacy_code(tmp_path, rig, link=True)
    r = run_wrapper(code, rig, **extra)
    assert r.returncode == 1 and "바꾸지 않아요" in fail_text(r)
    assert not any(c.startswith(("pip", "venv", "python install")) for c in rig.uv_calls())
    assert rig.wideeye_calls() == []


def test_a_broken_environment_is_rebuilt_by_running_again(tmp_path):
    """고정 판 파이썬 폴더가 지워져 .venv 가 끊겼으면 같은 한 줄을 다시 돌리는 것으로 고쳐진다(사람에게 '다시 돌려 주세요'만 말한다)."""
    rig = Rig(tmp_path)
    assert rig.run().returncode == 0
    shutil.rmtree(rig.app / ".python")
    n = len(rig.uv_calls())
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    again = rig.uv_calls()[n:]
    assert any(c.startswith("python install") for c in again)
    assert any(c.startswith("venv") and "--clear" in c for c in again)
    assert os.access(os.path.realpath(rig.app / ".venv" / "bin" / "python"), os.X_OK)


def test_a_failed_registration_shows_why_and_never_says_next(tmp_path):
    """등록이 실패하면 그 출력을 보이고 쉬운 말로 멈춘다 — '다음:' 줄은 나오지 않는다(`|| true` 를 넣으면 이 시험이 잡는다, 검토 5)."""
    rig = Rig(tmp_path)
    r = rig.run(FAKE_WIDEEYE_FAIL="1")
    assert r.returncode == 1
    assert fail_text(r).startswith("WideEye 를 이 맥에 등록하지 못했어요")
    assert "등록 실패 까닭(시험)" in r.stderr                                       # 삼킨 출력은 실패할 때만 보인다
    assert NEXT_LINE not in r.stdout + r.stderr and "/bin/wideeye\n" not in r.stdout


def test_too_little_disk_space_stops_before_any_download(tmp_path):
    rig = Rig(tmp_path, NEED_KB="999999999999")
    r = rig.run()
    assert r.returncode == 1 and "디스크 공간" in fail_text(r)
    assert not (rig.app / ".tools" / "uv").exists() and rig.uv_calls() == [] and not rig.fonts.exists()


def test_downloads_give_up_on_a_stalled_connection():
    """멈춘 내려받기가 끝없이 기다리지 않게 — 연결 20초 · 30초 동안 초당 1KB 아래면 끊고 쉬운 말로 멈춘다(검토 4)."""
    text = SCRIPT.read_text(encoding="utf-8")
    https = [l for l in text.splitlines() if "curl " in l and "--proto '=https'" in l]
    assert len(https) == 1
    for flag in ("--connect-timeout 20", "--speed-limit 1024", "--speed-time 30", "--proto-redir '=https'", "--tlsv1.2"):
        assert flag in https[0], flag
