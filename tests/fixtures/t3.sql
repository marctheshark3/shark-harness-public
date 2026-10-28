PRAGMA journal_mode=WAL;
CREATE TABLE projection_projects(project_id TEXT, title TEXT, workspace_root TEXT, deleted_at TEXT);
CREATE TABLE projection_threads(thread_id TEXT, project_id TEXT, title TEXT,
    updated_at TEXT, deleted_at TEXT, archived_at TEXT,
    pending_approval_count INTEGER, pending_user_input_count INTEGER);
CREATE TABLE projection_thread_sessions(thread_id TEXT, status TEXT);
CREATE TABLE projection_turns(row_id INTEGER PRIMARY KEY, thread_id TEXT,
    turn_id TEXT, state TEXT, requested_at TEXT, started_at TEXT, completed_at TEXT);
INSERT INTO projection_projects VALUES ('p', 'Example', '/projects/example', NULL);
INSERT INTO projection_threads VALUES ('t','p','An idea','2026-10-04T16:00:00Z',NULL,NULL,0,0);
INSERT INTO projection_thread_sessions VALUES ('t','running');
INSERT INTO projection_turns VALUES (1,'t','one','completed',
    '2026-10-04T15:55:00Z','2026-10-04T15:55:00Z','2026-10-04T15:59:00Z');
