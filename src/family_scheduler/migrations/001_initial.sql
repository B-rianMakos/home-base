-- WO-FS-002 · Deliverable 2: Database schema (SQLite)
-- Family Scheduler, Windows PC app with a home hub.
-- Converted from Deliverables/WO-FS-001/02 Database Schema.sql (PostgreSQL 16).
-- Changes from WO-FS-001 are marked "changed in WO-FS-002:".
--
-- PostgreSQL features replaced:
--   ENUM types          -> TEXT columns with CHECK (... IN (...))
--   uuid / gen_random_uuid() -> TEXT ids generated in Python (uuid4 hex)
--   timestamptz         -> TEXT, UTC ISO 8601 "YYYY-MM-DDTHH:MM:SS+00:00" (sorts as text)
--   date / time         -> TEXT "YYYY-MM-DD" / "HH:MM"
--   jsonb               -> TEXT holding JSON
--   uuid[] arrays       -> TEXT holding a JSON array
--   bytea               -> BLOB
--   GENERATED ... STORED starts_at/ends_at -> plain columns calculated in Python on every write
--   regex CHECK on gcal_event_id -> length CHECK + GLOB CHECK
--   bigint IDENTITY     -> INTEGER PRIMARY KEY AUTOINCREMENT
--   partial indexes     -> kept (SQLite supports WHERE on indexes)
--   admin-only CHECK on family_member -> admin email kept in the setting table
--     (changed in WO-FS-002: so the admin account can be changed later, correction 4)

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Settings (changed in WO-FS-002: new). Key/value pairs:
--   admin_email, change_seq, rsvp_refreshed_at, last_maintenance_date
-- ---------------------------------------------------------------------------
CREATE TABLE setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- changed in WO-FS-010: no admin is written here. A new hub asks for it on first start (AC-4).
INSERT INTO setting (key, value) VALUES
    ('change_seq', '0');

-- ---------------------------------------------------------------------------
-- FamilyMember
-- changed in WO-FS-002: gets_digest removed (DG-9); role removed (admin email is a setting);
-- no sign-in or session tables (WU-1).
-- ---------------------------------------------------------------------------
CREATE TABLE family_member (
    email      TEXT PRIMARY KEY CHECK (email = lower(email) AND email LIKE '%_@_%'),
    active     INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL
);


-- ---------------------------------------------------------------------------
-- Secrets (changed in WO-FS-002: replaces google_connection's token columns).
-- Fernet ciphertext. Names: google_token, google_client, gmail_app_password.
-- ---------------------------------------------------------------------------
CREATE TABLE secret (
    name       TEXT PRIMARY KEY,
    value_enc  BLOB NOT NULL,
    updated_at TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Event
-- ---------------------------------------------------------------------------
CREATE TABLE event (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
    date                  TEXT NOT NULL,                 -- local date, YYYY-MM-DD
    start_time            TEXT NOT NULL,                 -- local wall clock, HH:MM
    end_time              TEXT NOT NULL,
    time_zone             TEXT NOT NULL DEFAULT 'America/Toronto',
    location              TEXT NOT NULL DEFAULT '' CHECK (length(location) <= 200),
    repeat                TEXT NOT NULL DEFAULT 'none'
                          CHECK (repeat IN ('none', 'daily', 'weekly', 'monthly', 'yearly')),
    gcal_event_id         TEXT NOT NULL UNIQUE
                          CHECK (length(gcal_event_id) BETWEEN 5 AND 1024
                                 AND gcal_event_id NOT GLOB '*[^a-v0-9]*'),
    sync_status           TEXT NOT NULL DEFAULT 'pending'
                          CHECK (sync_status IN ('pending', 'synced', 'error')),
    sync_error            TEXT,
    -- changed in WO-FS-002: which Google call Retry re-runs (a failed create, or a failed Clear all cancel)
    sync_op               TEXT CHECK (sync_op IN ('create', 'delete')),
    conflict_acknowledged INTEGER NOT NULL DEFAULT 0 CHECK (conflict_acknowledged IN (0, 1)),
    created_by            TEXT NOT NULL REFERENCES family_member(email),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    starts_at             TEXT NOT NULL,                 -- UTC, calculated in Python
    ends_at               TEXT NOT NULL,                 -- UTC, calculated in Python
    CHECK (end_time > start_time)
);

CREATE INDEX event_starts_at_idx ON event (starts_at);
CREATE INDEX event_unsynced_idx  ON event (sync_status) WHERE sync_status <> 'synced';

CREATE TABLE event_invitee (
    event_id TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    email    TEXT NOT NULL REFERENCES family_member(email),
    rsvp     TEXT NOT NULL DEFAULT 'no_reply'
             CHECK (rsvp IN ('accepted', 'declined', 'tentative', 'no_reply', 'organizer')),
    PRIMARY KEY (event_id, email)
);

-- ---------------------------------------------------------------------------
-- Task
-- changed in WO-FS-002: deleted_by added for single removals (TR-1).
-- ---------------------------------------------------------------------------
CREATE TABLE task (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    priority         TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('high', 'medium', 'low')),
    due_date         TEXT NOT NULL,
    repeat           TEXT NOT NULL DEFAULT 'none'
                     CHECK (repeat IN ('none', 'daily', 'weekly', 'monthly', 'yearly')),
    completed_at     TEXT,
    completed_by     TEXT REFERENCES family_member(email),
    deleted_at       TEXT,
    deleted_by       TEXT REFERENCES family_member(email),
    previous_task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
    created_by       TEXT NOT NULL REFERENCES family_member(email),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX task_open_idx  ON task (due_date) WHERE completed_at IS NULL AND deleted_at IS NULL;
CREATE INDEX task_done_idx  ON task (completed_at) WHERE completed_at IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX task_trash_idx ON task (deleted_at) WHERE deleted_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Reminder
-- changed in WO-FS-002: created_at removed; order kept with seq (correction 1);
-- deleted_by added (TR-1). No date is ever shown.
-- ---------------------------------------------------------------------------
CREATE TABLE reminder (
    id         TEXT PRIMARY KEY,
    seq        INTEGER NOT NULL UNIQUE,
    message    TEXT NOT NULL CHECK (length(message) BETWEEN 1 AND 500),
    deleted_at TEXT,
    deleted_by TEXT REFERENCES family_member(email),
    created_by TEXT NOT NULL REFERENCES family_member(email)
);

CREATE INDEX reminder_trash_idx ON reminder (deleted_at) WHERE deleted_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Pending Clear all (10-second undo window, CA-3)
-- changed in WO-FS-002: 'committing' state added so undo and commit can't both win.
-- ---------------------------------------------------------------------------
CREATE TABLE pending_clear (
    id           TEXT PRIMARY KEY,
    list         TEXT NOT NULL CHECK (list IN ('events', 'tasks', 'reminders')),
    item_ids     TEXT NOT NULL,                 -- JSON array of ids
    requested_by TEXT NOT NULL REFERENCES family_member(email),
    requested_at TEXT NOT NULL,
    commit_after TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'pending'
                 CHECK (state IN ('pending', 'committing', 'undone', 'committed', 'partial')),
    resolved_at  TEXT,
    failed_ids   TEXT NOT NULL DEFAULT '[]'     -- events whose Google cancel failed
);

CREATE INDEX pending_clear_open_idx ON pending_clear (commit_after) WHERE state = 'pending';

-- ---------------------------------------------------------------------------
-- ActivityEntry
-- changed in WO-FS-002: 'digest sent' action (DG-10); kept 30 days (RT-4).
-- ---------------------------------------------------------------------------
CREATE TABLE activity_entry (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    who        TEXT NOT NULL REFERENCES family_member(email),
    action     TEXT NOT NULL CHECK (action IN ('added', 'edited', 'removed', 'completed', 'reopened',
                                               'cleared', 'undone', 'restored', 'digest sent')),
    item_type  TEXT NOT NULL CHECK (item_type IN ('event', 'task', 'reminder', 'list', 'digest')),
    item_id    TEXT,
    list       TEXT CHECK (list IN ('events', 'tasks', 'reminders')),
    item_label TEXT NOT NULL,
    at         TEXT NOT NULL,
    before     TEXT,                            -- JSON, previous values for edits (AL-3)
    after      TEXT                             -- JSON, new values for edits
);

CREATE INDEX activity_at_idx ON activity_entry (at);

-- ---------------------------------------------------------------------------
-- Digest sends (changed in WO-FS-002: per request; drives the 1-minute rule and daily cap)
-- ---------------------------------------------------------------------------
CREATE TABLE digest_send (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL REFERENCES family_member(email),
    sent_at    TEXT NOT NULL,
    local_date TEXT NOT NULL                    -- Toronto date, for the daily cap (FR-8)
);

CREATE INDEX digest_send_date_idx ON digest_send (local_date);

-- ---------------------------------------------------------------------------
-- Retention (RT-1 to RT-6), run nightly by the hub worker. Cutoff = now - 30 days.
--   DELETE FROM task WHERE completed_at < :cutoff AND deleted_at IS NULL;
--   DELETE FROM task WHERE deleted_at < :cutoff;
--   DELETE FROM reminder WHERE deleted_at < :cutoff;
--   DELETE FROM event WHERE repeat = 'none' AND ends_at < :cutoff;
--   DELETE FROM activity_entry WHERE at < :cutoff;
--   DELETE FROM pending_clear WHERE resolved_at < :cutoff;
--   DELETE FROM digest_send WHERE sent_at < :cutoff;
-- ---------------------------------------------------------------------------
