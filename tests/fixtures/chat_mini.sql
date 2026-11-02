CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB, handle_id INTEGER, date INTEGER,
  is_from_me INTEGER, is_read INTEGER, item_type INTEGER DEFAULT 0, associated_message_type INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0, service TEXT);
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT, is_filtered INTEGER DEFAULT 0, last_read_message_timestamp INTEGER DEFAULT 0);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
INSERT INTO handle VALUES (1, '+821000000000');
INSERT INTO chat VALUES (1, 'g1', '+821000000000', NULL, 0, 0);
INSERT INTO chat_handle_join VALUES (1, 1);
