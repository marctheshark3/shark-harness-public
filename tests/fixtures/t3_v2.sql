PRAGMA journal_mode=WAL;
CREATE TABLE projection_projects(project_id TEXT, title TEXT, workspace_root TEXT,
    deleted_at TEXT, default_model_selection_json TEXT, default_thread_env_mode TEXT);
CREATE TABLE projection_threads(thread_id TEXT, project_id TEXT, title TEXT,
    updated_at TEXT, deleted_at TEXT, archived_at TEXT,
    pending_approval_count INTEGER, pending_user_input_count INTEGER,
    runtime_mode TEXT, interaction_mode TEXT, model_selection_json TEXT);
CREATE TABLE projection_thread_sessions(thread_id TEXT, status TEXT);
CREATE TABLE orchestration_v2_projection_threads(thread_id TEXT PRIMARY KEY,
    project_id TEXT, title TEXT, runtime_mode TEXT, interaction_mode TEXT,
    updated_at TEXT, archived_at TEXT, deleted_at TEXT, payload_json TEXT);
CREATE TABLE orchestration_v2_projection_runs(run_id TEXT PRIMARY KEY, thread_id TEXT,
    ordinal INTEGER, status TEXT, requested_at TEXT, completed_at TEXT, payload_json TEXT);
CREATE TABLE orchestration_v2_projection_runtime_requests(runtime_request_id TEXT PRIMARY KEY,
    thread_id TEXT, kind TEXT, status TEXT);
INSERT INTO projection_projects VALUES ('p', 'Example', '/projects/example', NULL,
    '{"instanceId":"codex","model":"project-model"}', NULL);
INSERT INTO projection_threads VALUES ('stale', 'p', 'Leftover legacy session',
    '2026-10-04T16:00:00Z', NULL, NULL, 0, 0, 'approval-required', 'plan',
    '{"instanceId":"codex","model":"old-model"}');
INSERT INTO projection_thread_sessions VALUES ('stale', 'running');
INSERT INTO orchestration_v2_projection_threads VALUES ('t', 'p', 'Current desktop session',
    'approval-required', 'plan', '2026-10-04T16:00:00Z', NULL, NULL,
    '{"modelSelection":{"instanceId":"codex","model":"session-model","options":[{"id":"reasoningEffort","value":"max"}]}}');
INSERT INTO orchestration_v2_projection_runs VALUES ('old', 't', 1, 'completed',
    '2026-10-04T15:55:00Z', '2026-10-04T15:59:00Z',
    '{"startedAt":"2026-10-04T15:55:00Z"}');
INSERT INTO orchestration_v2_projection_runs VALUES ('current', 't', 2, 'running',
    '2026-10-04T16:00:00Z', NULL, '{"startedAt":"2026-10-04T16:00:00Z"}');
