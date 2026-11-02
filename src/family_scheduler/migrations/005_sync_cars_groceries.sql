-- WO-FS-010 · Two-way Google sync (GY-16), cars (CR-13) and groceries (GR-10). Upgrades a 1.7.0 database with
-- nothing lost: every event, invitee, reply and History entry is copied across; every existing event is an app event.
-- db.migrate turns foreign keys off around the table rebuilds and checks them before committing.

-- ---------------------------------------------------------------------------
-- Cars (CR-1, CR-13): 1 to 10, each with its own name, in order. The family's two cars to start with.
-- ---------------------------------------------------------------------------
CREATE TABLE car (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 30),
    position INTEGER NOT NULL UNIQUE CHECK (position BETWEEN 1 AND 10)
);

INSERT INTO car (name, position) VALUES ('Subaru Crosstrek', 1), ('Chevrolet Cruze', 2);

-- ---------------------------------------------------------------------------
-- Events, rebuilt with:
--   source          'app' (made in the app) or 'google' (brought in by Sync with Google, GY-4)
--   calendar_email  the calendar account it was first synced from ('' for app events)
--   read_only       1 when Google won't let the family change it here (GY-8, GY-11, GY-12)
--   google_note     why it's read-only, shown on the event ("Organized by …", "Repeats in a way …")
--   html_link       the event in Google Calendar, for Open in Google Calendar
--   private         1 for a Private Google event, kept only as Busy (GY-12)
--   end_date        the last day of a multi-day event from Google ('' for one day, GY-11)
--   google_updated  Google's "updated" time when last synced
--   last_synced_at  when Sync with Google last brought it in or changed it
--   car_id          the car the event needs, or NULL (CR-4 to CR-13); car_chosen_by who chose it
-- gcal_event_id accepts any id Google gives (events made in Google aren't limited to the app's own ids).
-- ---------------------------------------------------------------------------
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
    gcal_event_id         TEXT NOT NULL UNIQUE CHECK (length(gcal_event_id) BETWEEN 1 AND 1024),
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
    organizer_email       TEXT NOT NULL DEFAULT '',
    retry_attempts        INTEGER NOT NULL DEFAULT 0,
    first_failed_at       TEXT,
    next_retry_at         TEXT,
    all_day               INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0, 1)),
    source                TEXT NOT NULL DEFAULT 'app' CHECK (source IN ('app', 'google')),
    calendar_email        TEXT NOT NULL DEFAULT '',
    read_only             INTEGER NOT NULL DEFAULT 0 CHECK (read_only IN (0, 1)),
    google_note           TEXT NOT NULL DEFAULT '',
    html_link             TEXT NOT NULL DEFAULT '',
    private               INTEGER NOT NULL DEFAULT 0 CHECK (private IN (0, 1)),
    end_date              TEXT NOT NULL DEFAULT '',
    google_updated        TEXT,
    last_synced_at        TEXT,
    car_id                INTEGER REFERENCES car(id) ON DELETE SET NULL,
    car_chosen_by         TEXT,
    CHECK ((all_day = 0 AND (end_time > start_time OR end_date <> ''))
           OR (all_day = 1 AND start_time = '' AND end_time = ''))
);

INSERT INTO event_new (id, title, date, start_time, end_time, time_zone, location, repeat, gcal_event_id,
                       sync_status, sync_error, sync_op, conflict_acknowledged, created_by, created_at, updated_at,
                       starts_at, ends_at, organizer_email, retry_attempts, first_failed_at, next_retry_at, all_day)
SELECT id, title, date, start_time, end_time, time_zone, location, repeat, gcal_event_id,
       sync_status, sync_error, sync_op, conflict_acknowledged, created_by, created_at, updated_at,
       starts_at, ends_at, organizer_email, retry_attempts, first_failed_at, next_retry_at, all_day
FROM event;

-- Invitees, rebuilt so an invitee from Google who isn't on the family list is kept by address (GY-13).
CREATE TABLE event_invitee_new (
    event_id TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    email    TEXT NOT NULL CHECK (email = lower(email) AND email LIKE '%_@_%'),
    rsvp     TEXT NOT NULL DEFAULT 'no_reply'
             CHECK (rsvp IN ('accepted', 'declined', 'tentative', 'no_reply', 'organizer')),
    PRIMARY KEY (event_id, email)
);

INSERT INTO event_invitee_new (event_id, email, rsvp) SELECT event_id, email, rsvp FROM event_invitee;

DROP TABLE event_invitee;
DROP TABLE event;
ALTER TABLE event_new RENAME TO event;
ALTER TABLE event_invitee_new RENAME TO event_invitee;

CREATE INDEX event_starts_at_idx ON event (starts_at);
CREATE INDEX event_unsynced_idx  ON event (sync_status) WHERE sync_status <> 'synced';
CREATE INDEX event_retry_idx ON event (next_retry_at) WHERE next_retry_at IS NOT NULL;
CREATE INDEX event_car_idx ON event (car_id) WHERE car_id IS NOT NULL;

-- History, rebuilt so changes to the Cars card can be recorded (CR-3) as item type 'car'.
CREATE TABLE activity_entry_new (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    who        TEXT NOT NULL REFERENCES family_member(email),
    action     TEXT NOT NULL CHECK (action IN ('added', 'edited', 'removed', 'completed', 'reopened',
                                               'cleared', 'undone', 'restored', 'digest sent')),
    item_type  TEXT NOT NULL CHECK (item_type IN ('event', 'task', 'reminder', 'list', 'digest', 'car')),
    item_id    TEXT,
    list       TEXT CHECK (list IN ('events', 'tasks', 'reminders')),
    item_label TEXT NOT NULL,
    at         TEXT NOT NULL,
    before     TEXT,
    after      TEXT
);

INSERT INTO activity_entry_new (id, who, action, item_type, item_id, list, item_label, at, before, after)
SELECT id, who, action, item_type, item_id, list, item_label, at, before, after FROM activity_entry;

DROP TABLE activity_entry;
ALTER TABLE activity_entry_new RENAME TO activity_entry;
CREATE INDEX activity_at_idx ON activity_entry (at);

-- Each calendar account keeps Google's sync token, so later syncs fetch only what changed (GY-3).
ALTER TABLE calendar_account ADD COLUMN sync_token TEXT;

-- ---------------------------------------------------------------------------
-- Groceries (GR-10): one shared list, in the order added. Not in History (GR-8).
-- ---------------------------------------------------------------------------
CREATE TABLE grocery_item (
    id        TEXT PRIMARY KEY,
    text      TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 100),
    added_by  TEXT NOT NULL,
    added_at  TEXT NOT NULL,
    bought    INTEGER NOT NULL DEFAULT 0 CHECK (bought IN (0, 1)),
    bought_at TEXT
);

CREATE INDEX grocery_added_idx ON grocery_item (added_at);
