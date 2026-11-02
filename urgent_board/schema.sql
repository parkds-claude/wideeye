-- urgent-board 저장소 스키마 v1
CREATE TABLE IF NOT EXISTS events (
  key TEXT PRIMARY KEY, source TEXT NOT NULL, chat_id TEXT NOT NULL, message_id TEXT NOT NULL,
  chat_name TEXT, sender_id TEXT, sender_name TEXT, text TEXT, received_at TEXT NOT NULL,
  is_from_me INTEGER NOT NULL, is_read INTEGER NOT NULL, is_group INTEGER NOT NULL,
  mentioned_me INTEGER DEFAULT 0, reply_to_me INTEGER DEFAULT 0, thread_id TEXT DEFAULT '',
  link TEXT DEFAULT '', account TEXT DEFAULT '', raw_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_received ON events(received_at);
CREATE INDEX IF NOT EXISTS idx_events_chat ON events(source, chat_id, received_at);
CREATE TABLE IF NOT EXISTS dismissed (key TEXT PRIMARY KEY, until TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS summaries (key TEXT PRIMARY KEY, text_hash TEXT NOT NULL, summary TEXT NOT NULL, at TEXT NOT NULL);
-- 볼 필요가 있는가(요약과 같은 호출에서 Claude Haiku 가 판단) — 대기 항목을 거를 때만 쓴다. text_hash 는 summaries 와 같은 값
CREATE TABLE IF NOT EXISTS verdicts (key TEXT PRIMARY KEY, text_hash TEXT NOT NULL, needed INTEGER NOT NULL, at TEXT NOT NULL);
-- 다른 Claude 세션이 도구로 다룬 메일 id → 마지막으로 다룬 때(collectors/claude_sessions.py). 본문·대화는 넣지 않는다
CREATE TABLE IF NOT EXISTS touched (ref TEXT PRIMARY KEY, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scan_pos (path TEXT PRIMARY KEY, pos INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sources (source TEXT PRIMARY KEY, ok INTEGER NOT NULL, note TEXT, at TEXT NOT NULL);
-- 할 일 문구와 기한(요약과 같은 호출에서 Claude Haiku 가 뽑음 — 2026-10-16 할 일 현황판). text_hash 는 summaries 와 같은 값
CREATE TABLE IF NOT EXISTS task_lines (key TEXT PRIMARY KEY, text_hash TEXT NOT NULL, what TEXT NOT NULL, due TEXT DEFAULT '', at TEXT NOT NULL);
-- 할 일 장부(2026-10-16 할 일 현황판 — urgent_board/tasks.py). 대화 하나가 한 줄, 끝나면 state='done' 과 까닭(reply · dismiss · read · not_needed · handled)
CREATE TABLE IF NOT EXISTS tasks (conv TEXT PRIMARY KEY, state TEXT NOT NULL, key TEXT NOT NULL, business TEXT, source TEXT, chat_id TEXT,
  chat_name TEXT, sender_name TEXT, what TEXT, summary TEXT, due TEXT DEFAULT '', link TEXT, unread INTEGER, count INTEGER DEFAULT 0,
  opened_at TEXT NOT NULL, last_at TEXT NOT NULL, closed_at TEXT DEFAULT '', closed_why TEXT DEFAULT '');
-- Claude 가 고른 사업(2026-10-16 분류 키우기 — 규칙이 아무 데도 못 건 항목에만 쓴다). 글의 표식이 맞을 때만 쓴다
CREATE TABLE IF NOT EXISTS picks (key TEXT PRIMARY KEY, text_hash TEXT NOT NULL, business TEXT NOT NULL, at TEXT NOT NULL);
