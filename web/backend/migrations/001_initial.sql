-- 0.3.0 baseline. Applied migrations are immutable.

CREATE TABLE ai_requests (
                    id TEXT PRIMARY KEY, session_id TEXT, owner TEXT NOT NULL,
                    kind TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                    location TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
                    status TEXT NOT NULL, seconds REAL, audio_seconds REAL,
                    input_tokens INTEGER, output_tokens INTEGER, source_id TEXT NOT NULL DEFAULT '');

CREATE TABLE app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    language TEXT NOT NULL DEFAULT 'ko',
    updated_at TEXT NOT NULL
);

CREATE TABLE lecture_notes (
                    id TEXT PRIMARY KEY, course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
                    title TEXT NOT NULL, fingerprint TEXT NOT NULL, originals TEXT NOT NULL,
                    revision_id TEXT, deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);

CREATE TABLE note_details (
                    revision_id TEXT NOT NULL REFERENCES note_revisions(id), number INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(revision_id, number));

CREATE TABLE note_pages (
                    revision_id TEXT NOT NULL REFERENCES note_revisions(id), number INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(revision_id, number));

CREATE TABLE note_revisions (
                    id TEXT PRIMARY KEY, note_id TEXT NOT NULL REFERENCES lecture_notes(id),
                    analyzer TEXT NOT NULL, status TEXT NOT NULL, study_status TEXT NOT NULL DEFAULT 'pending',
                    keywords TEXT NOT NULL DEFAULT '{"format": "keywords", "format_version": 1, "generator_version": "glossary-v2", "data": []}', page_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, seconds REAL NOT NULL DEFAULT 0, overview TEXT NOT NULL DEFAULT '{"format": "overview", "format_version": 1, "generator_version": "overview-1", "data": []}', ai_settings TEXT NOT NULL DEFAULT '{}', glossary_data TEXT NOT NULL DEFAULT '{"format": "glossary", "format_version": 1, "generator_version": "glossary-v2", "data": {}}');

CREATE TABLE recording_chunks (
                    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,
                    PRIMARY KEY(recording_id,number));

CREATE TABLE recordings (
                    id TEXT PRIMARY KEY,
                    course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
                    course_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    status TEXT NOT NULL,
                    language TEXT NOT NULL,
                    prompt TEXT NOT NULL DEFAULT '',
                    corrections TEXT NOT NULL DEFAULT '',
                    format_transcript INTEGER NOT NULL DEFAULT 1,
                    llm_enabled INTEGER NOT NULL DEFAULT 1,
                    llm_model TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    source_path TEXT NOT NULL DEFAULT '',
                    audio_path TEXT NOT NULL DEFAULT '',
                    note_path TEXT NOT NULL DEFAULT '',
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    processing_seconds REAL NOT NULL DEFAULT 0,
                    transcript_text TEXT NOT NULL DEFAULT '',
                    segments_json TEXT NOT NULL DEFAULT '{"format": "segments", "format_version": 1, "generator_version": "speech-1", "data": []}',
                    breaks_json TEXT NOT NULL DEFAULT '{"format": "breaks", "format_version": 1, "generator_version": "speech-1", "data": []}',
                    quality_json TEXT NOT NULL DEFAULT '{"format": "quality", "format_version": 1, "generator_version": "speech-1", "data": {}}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT ''
                , note_revision_id TEXT, note_snapshot_json TEXT NOT NULL DEFAULT '{"format": "note-snapshot", "format_version": 1, "generator_version": "speech-1", "data": {}}', review_status TEXT NOT NULL DEFAULT 'none', review_error TEXT NOT NULL DEFAULT '', last_operation TEXT NOT NULL DEFAULT 'transcribe', ai_settings_json TEXT NOT NULL DEFAULT '{}');

CREATE TABLE recovery_sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    acknowledged INTEGER NOT NULL DEFAULT 0);

CREATE TABLE request_checkpoints (
                    session_id TEXT NOT NULL REFERENCES recovery_sessions(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, result TEXT NOT NULL, seconds REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(session_id, fingerprint));

CREATE TABLE review_runs (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                settings TEXT NOT NULL, metrics TEXT NOT NULL DEFAULT '{"format": "metrics", "format_version": 1, "generator_version": "speech-1", "data": {}}', error TEXT NOT NULL DEFAULT '');

CREATE TABLE search_documents (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
                    course_id INTEGER, title TEXT NOT NULL, course TEXT NOT NULL,
                    tab TEXT NOT NULL, page INTEGER, seconds REAL,
                    body TEXT NOT NULL, normalized TEXT NOT NULL, title_normalized TEXT NOT NULL);

CREATE TABLE search_revision (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, indexed INTEGER NOT NULL);

CREATE TABLE suggestion_history (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL,
                approved_at TEXT NOT NULL, payload TEXT NOT NULL);

CREATE TABLE suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
                    sentence_id TEXT NOT NULL,
                    original TEXT NOT NULL,
                    replacement TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    decided_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(recording_id, sentence_id, original, replacement)
                );

CREATE TABLE transcription_runs (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                settings TEXT NOT NULL, metrics TEXT NOT NULL DEFAULT '{"format": "metrics", "format_version": 1, "generator_version": "speech-1", "data": {}}', error TEXT NOT NULL DEFAULT '');

CREATE INDEX recordings_created_idx
                    ON recordings(created_at DESC);

CREATE INDEX recordings_status_idx
                    ON recordings(status, created_at);

CREATE INDEX suggestion_history_date ON suggestion_history(approved_at DESC, id);

CREATE INDEX suggestions_recording_idx
                    ON suggestions(recording_id, status, id);

CREATE TRIGGER search_courses_DELETE AFTER DELETE ON courses
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_courses_INSERT AFTER INSERT ON courses
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_courses_UPDATE AFTER UPDATE ON courses
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_lecture_notes_DELETE AFTER DELETE ON lecture_notes
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_lecture_notes_INSERT AFTER INSERT ON lecture_notes
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_lecture_notes_UPDATE AFTER UPDATE ON lecture_notes
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_note_details_DELETE AFTER DELETE ON note_details
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_note_details_INSERT AFTER INSERT ON note_details
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_note_details_UPDATE AFTER UPDATE ON note_details
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_note_pages_DELETE AFTER DELETE ON note_pages
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_note_pages_INSERT AFTER INSERT ON note_pages
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_note_pages_UPDATE AFTER UPDATE ON note_pages
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_note_revisions_DELETE AFTER DELETE ON note_revisions
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_note_revisions_INSERT AFTER INSERT ON note_revisions
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_note_revisions_UPDATE AFTER UPDATE ON note_revisions
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_recordings_DELETE AFTER DELETE ON recordings
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_recordings_INSERT AFTER INSERT ON recordings
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

CREATE TRIGGER search_recordings_UPDATE AFTER UPDATE ON recordings
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END;

INSERT INTO search_revision VALUES (1,0,-1);

INSERT INTO app_state VALUES ('active_course_id','0');
