-- WO-FS-008 · All-day events (AD-10). Upgrades a 1.5.0 database; nothing is removed, and every existing event
-- stays timed. An all-day event keeps start_time and end_time empty (''); its starts_at and ends_at are the local
-- midnights at the start and end of its day, so ordering, "past" and the 30-day retention work unchanged.
--
-- The 1.0.0 table checks end_time > start_time, which an all-day event can't meet, and SQLite can't change a
-- check in place: the table is rebuilt with the same columns, data and indexes (SQLite's documented procedure).
-- db.migrate turns foreign keys off around this, so event_invitee rows are kept, and checks them afterwards.

CREATE TABLE event_new (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
    date                  TEXT NOT NULL,                 -- local date, YYYY-MM-DD
    start_time            TEXT NOT NULL,                 -- local wall clock, HH:MM; '' for an all-day event
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
    sync_op               TEXT CHECK (sync_op IN ('create', 'delete')),
    conflict_acknowledged INTEGER NOT NULL DEFAULT 0 CHECK (conflict_acknowledged IN (0, 1)),
    created_by            TEXT NOT NULL REFERENCES family_member(email),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    starts_at             TEXT NOT NULL,                 -- UTC, calculated in Python
    ends_at               TEXT NOT NULL,                 -- UTC, calculated in Python
    organizer_email       TEXT NOT NULL DEFAULT '',  -- the app always sets it (WO-FS-010 AC-2)
    retry_attempts        INTEGER NOT NULL DEFAULT 0,
    first_failed_at       TEXT,
    next_retry_at         TEXT,
    all_day               INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0, 1)),
    CHECK ((all_day = 0 AND end_time > start_time) OR (all_day = 1 AND start_time = '' AND end_time = ''))
);

INSERT INTO event_new (id, title, date, start_time, end_time, time_zone, location, repeat, gcal_event_id,
                       sync_status, sync_error, sync_op, conflict_acknowledged, created_by, created_at, updated_at,
                       starts_at, ends_at, organizer_email, retry_attempts, first_failed_at, next_retry_at, all_day)
SELECT id, title, date, start_time, end_time, time_zone, location, repeat, gcal_event_id,
       sync_status, sync_error, sync_op, conflict_acknowledged, created_by, created_at, updated_at,
       starts_at, ends_at, organizer_email, retry_attempts, first_failed_at, next_retry_at, 0
FROM event;

DROP TABLE event;

ALTER TABLE event_new RENAME TO event;

CREATE INDEX event_starts_at_idx ON event (starts_at);
CREATE INDEX event_unsynced_idx  ON event (sync_status) WHERE sync_status <> 'synced';
CREATE INDEX event_retry_idx ON event (next_retry_at) WHERE next_retry_at IS NOT NULL;
