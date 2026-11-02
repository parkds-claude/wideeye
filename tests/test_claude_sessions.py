"""다른 Claude 세션이 이미 다룬 메일 — 세션 기록을 읽어 알아낸다. 실제 기록은 건드리지 않는다(지어낸 줄로 시험)."""
import json
from datetime import datetime, timedelta, timezone

from urgent_board.collectors import claude_sessions as cs
from urgent_board.models import Grade, Item, Source
from urgent_board.store import Store

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
MID, TID, OTHER = "19a1b2c3d4e5f607", "19a1b2c3d4e5f600", "0123456789abcdef"

def line(parts, entry="claude-vscode", at="2026-10-13T06:54:10.123Z", role="assistant"):
    return json.dumps({"type": role, "entrypoint": entry, "timestamp": at, "message": {"role": role, "content": parts}}, ensure_ascii=False)

def use(name, **inp): return {"type": "tool_use", "id": "toolu_x", "name": name, "input": inp}
def result(text): return {"type": "tool_result", "tool_use_id": "toolu_x", "content": text}

def test_tool_call_on_a_mail_counts_as_handled():
    at, ids = cs.acted(line([use("mcp__claude_ai_Gmail__get_thread", thread_id=TID)]))
    assert ids == {TID} and at == datetime(2026, 10, 13, 6, 54, 10, 123000, tzinfo=timezone.utc)
    assert cs.acted(line([use("Bash", command=f"python3 tally.py --message {MID} --thread {TID}")]))[1] == {MID, TID}
    assert cs.acted(line([use("mcp__sheets__update_values", values=[["배정", MID]])], entry="cli"))[1] == {MID}       # 터미널에서 쓰는 세션도 사람 세션

def test_what_does_not_count():
    assert cs.acted(line([use("Bash", command=f"cat {MID}")], entry="sdk-cli")) is None        # 봇의 claude -p — 아침 브리핑 봇은 모든 메일을 넣는다
    assert cs.acted(line([use("Bash", command=f"cat {MID}")], entry="")) is None               # 표식이 없으면 사람 세션인지 모른다 → 치지 않는다(보이는 쪽)
    assert cs.acted(line([result(f"목록: {MID}, {TID}")], role="user")) is None                 # 결과에만 나온 것 = 목록에 보였을 뿐
    assert cs.acted(line([{"type": "text", "text": f"{MID} 메일은 내일 답하죠"}])) is None      # 말로만 언급
    assert cs.acted(line([use("Bash", command="ls -la")])) is None                              # id 가 없다
    assert cs.acted("{깨진 줄") is None and cs.acted("") is None
    assert cs.acted(line([use("Bash", command="git show 0123456789abcdef0123456789abcdef01234567")])) is None    # 40자리 커밋 해시의 일부는 16자리 id 가 아니다

def write(path, lines, tail=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(x + "\n" for x in lines) + tail)

def test_scan_reads_only_what_was_added_and_keeps_the_latest_time(tmp_path):
    st = Store(tmp_path / "t.db"); root = tmp_path / "projects"; f = root / "proj-a" / "s1.jsonl"
    write(f, [line([use("Bash", command=f"x {MID}")], at="2026-10-13T06:00:00Z"), line([result(OTHER)], role="user")])
    assert cs.scan(root, st, NOW) == 1 and st.touched_at([MID, OTHER]) == {MID: datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)}
    assert cs.scan(root, st, NOW) == 0                                                           # 늘어난 것이 없으면 읽지 않는다
    write(f, [line([use("Bash", command=f"y {MID} {TID}")], at="2026-10-13T07:30:00Z")], tail=line([use("Bash", command=f"z {OTHER}")])[:40])
    assert cs.scan(root, st, NOW) == 2                                                           # 덧붙은 줄만, 쓰다 만 마지막 줄은 다음에
    got = st.touched_at([MID, TID, OTHER])
    assert got[MID] == datetime(2026, 10, 13, 7, 30, tzinfo=timezone.utc) and TID in got and OTHER not in got
    write(root / "proj-a" / "s1" / "subagents" / "agent-1.jsonl", [line([use("Bash", command=f"w {OTHER}")], at="2026-10-13T08:00:00Z")])
    assert cs.scan(root, st, NOW) == 1 and OTHER in st.touched_at([OTHER])                       # 그 세션이 시킨 하위 에이전트의 도구 호출도 친다

def test_scan_starts_over_when_a_file_shrinks_and_ignores_old_lines(tmp_path):
    st = Store(tmp_path / "t.db"); root = tmp_path / "projects"; f = root / "p" / "s.jsonl"
    write(f, [line([use("Bash", command=f"x {MID}")], at="2026-10-13T06:00:00Z")] * 3)
    cs.scan(root, st, NOW)
    f.write_text(line([use("Bash", command=f"x {TID}")], at="2026-10-13T08:00:00Z") + "\n", encoding="utf-8")       # 파일이 새로 쓰였다(더 짧다)
    assert cs.scan(root, st, NOW) == 1 and TID in st.touched_at([TID])
    write(root / "p" / "old.jsonl", [line([use("Bash", command=f"x {OTHER}")], at="2026-09-01T00:00:00Z")])
    assert cs.scan(root, st, NOW, days=7) == 0 and st.touched_at([OTHER]) == {}                  # 기간 밖의 줄은 적지 않는다
    assert cs.scan(tmp_path / "없는 폴더", st, NOW) == 0                                          # 폴더가 없어도 죽지 않는다

def item(key, refs, received, grade=Grade.URGENT):
    return Item(grade=grade, business="other", source=Source.GMAIL, chat_name="견적", sender_name="김과장", summary="요약",
                received_at=received, waited_minutes=10, deadline=None, reasons=["unread"], key=key, refs=refs)

def test_handled_means_touched_after_the_message_arrived(tmp_path):
    st = Store(tmp_path / "t.db")
    st.touch_many({MID: NOW - timedelta(minutes=5), TID: NOW - timedelta(hours=3)})
    arrived = NOW - timedelta(minutes=30)
    assert cs.handled(st, [item("k1", [MID, TID], arrived)]) == {"k1": NOW - timedelta(minutes=5)}       # 도착 뒤에 다뤘다
    assert cs.handled(st, [item("k2", [TID], arrived)]) == {}                # 다룬 것이 도착 전이다 — 같은 대화에 새로 온 메일은 아직 안 챙긴 것
    assert cs.handled(st, [item("k3", [], arrived), item("k4", [OTHER], arrived)]) == {}                 # 메일이 아니거나(카톡·문자) 다룬 적 없다
    st.touch_many({MID: NOW - timedelta(hours=9)})                            # 옛 시각이 다시 들어와도 최신을 지킨다
    assert st.touched_at([MID])[MID] == NOW - timedelta(minutes=5)


# ===== 품질 반복 2회차 — 검수: "다룬 것"의 기준이 넓다 =====
def test_writing_a_file_or_searching_does_not_count_and_this_project_is_ignored():
    for tool in ("Write", "Edit", "NotebookEdit", "Read", "Grep", "Glob"):          # 문서에 id 를 적거나 찾아본 것은 메일을 다룬 것이 아니다
        assert cs.acted(line([use(tool, file_path="/x/notes.md", content=f"{MID} 는 나중에")])) is None, tool
    assert cs.acted(line([use("Bash", command=f"python3 tally.py {MID}")]))[1] == {MID}
    assert cs.acted(line([use("mcp__claude_ai_Gmail__create_draft", threadId=TID)]))[1] == {TID}
    here = json.loads(line([use("Bash", command=f"sqlite3 state/urgent.db 'select * from events where message_id=\"{MID}\"'")]))
    here["cwd"] = "/Users/tester/urgent-board"
    assert cs.acted(json.dumps(here), ignore_dirs=("/Users/tester/urgent-board",)) is None        # 현황판 자체를 고치며 id 를 다룬 것은 치지 않는다
    here["cwd"] = "/Users/tester/urgent-board/tools"
    assert cs.acted(json.dumps(here), ignore_dirs=("/Users/tester/urgent-board",)) is None
    here["cwd"] = "/Users/tester/urgent-board-other"
    assert cs.acted(json.dumps(here), ignore_dirs=("/Users/tester/urgent-board",)) is not None    # 이름만 비슷한 다른 폴더는 친다

def test_scan_passes_the_ignored_folders(tmp_path):
    st = Store(tmp_path / "t.db"); root = tmp_path / "projects"
    d = json.loads(line([use("Bash", command=f"x {MID}")], at="2026-10-13T06:00:00Z")); d["cwd"] = "/work/board"
    write(root / "p" / "s.jsonl", [json.dumps(d)])
    assert cs.scan(root, st, NOW, ignore_dirs=("/work/board",)) == 0 and st.touched_at([MID]) == {}


def test_commands_that_look_into_the_board_itself_do_not_count_from_any_folder():
    # 3회차(검수): 현황판 폴더가 아닌 곳(홈)에서 현황판의 저장소를 들여다본 명령도 "다룬 것"이 됐다
    marks = ("urgent-board", "urgent.db", ":5091")
    for cmd in (f"sqlite3 ~/urgent-board/state/urgent.db \"select * from events where message_id='{MID}'\"",
                f"curl -s http://127.0.0.1:5091/api/items | grep {MID}", f"cd /Users/tester/urgent-board && python tools/replay.py {MID}"):
        assert cs.acted(line([use("Bash", command=cmd)]), ignore_marks=marks) is None, cmd
    assert cs.acted(line([use("Bash", command=f"python3 ~/mail-helper/tools/fetch.py {MID}")]), ignore_marks=marks)[1] == {MID}
    mixed = line([use("Bash", command=f"curl 127.0.0.1:5091/api/items | grep {MID}"), use("mcp__claude_ai_Gmail__get_thread", thread_id=TID)])
    assert cs.acted(mixed, ignore_marks=marks)[1] == {TID}                       # 같은 줄의 다른 도구 호출은 그대로 친다


def test_python_one_liners_that_import_the_board_package_do_not_count():
    # 4회차(검수가 재현): 홈 폴더에서 `python3 -c "from urgent_board.store import …<id>"` 로 저장소를 들여다본 것이 "다룬 것"이 됐다 — 패키지 이름도 표식이다
    from pathlib import Path
    import run_board
    from urgent_board import config
    s = config.load(Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "home")
    marks = run_board.board_marks(s)
    # 폴더 이름은 실제 코드 폴더의 이름으로 본다 — 작업 사본(worktree) · 공개판 폴더에서도 같은 시험이 돈다(검토 R14)
    assert s.root.name == Path(__file__).resolve().parents[1].name
    assert "urgent_board" in marks and s.root.name in marks and "urgent.db" in marks and ":5091" in marks
    cmd = f"python3 -c \"from urgent_board.store import Store; print(Store('x').touched_at(['{MID}']))\""
    assert cs.acted(line([use("Bash", command=cmd)]), ignore_marks=marks) is None
    assert cs.acted(line([use("Bash", command=f"python3 ~/mail-helper/tools/fetch.py {MID}")]), ignore_marks=marks)[1] == {MID}
    assert cs.RULE == 4                                                          # 표식이 늘었으니 적어 둔 것을 버리고 다시 읽는다


def test_an_empty_mark_does_not_switch_the_whole_thing_off():
    # 검수: 표식에 빈 글자가 끼면('' 은 어느 글에나 들어 있다) 모든 명령이 "현황판을 들여다본 것"이 돼 '다룬 것'이 조용히 꺼진다
    assert cs.acted(line([use("Bash", command=f"python3 ~/mail-helper/tools/fetch.py {MID}")]), ignore_marks=("", "urgent-board"))[1] == {MID}
    import run_board
    from types import SimpleNamespace
    from pathlib import Path
    marks = run_board.board_marks(SimpleNamespace(root=Path("."), db_path=Path("state/urgent.db"), port=5091))
    assert "" not in marks and all(len(m) >= 4 for m in marks) and "urgent_board" in marks and ":5091" in marks
