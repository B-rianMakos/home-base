-- 1.9.1 · An event that came from Google Calendar can be deleted from Home Base without touching Google. Its row is
-- kept, marked, and never shown again, so Sync with Google doesn't bring it back; if it's removed in Google, the row
-- goes as usual. Upgrades a 1.9.0 database with nothing lost; every existing event stays shown.

ALTER TABLE event ADD COLUMN app_deleted INTEGER NOT NULL DEFAULT 0 CHECK (app_deleted IN (0, 1));
ALTER TABLE event ADD COLUMN app_deleted_by TEXT;
ALTER TABLE event ADD COLUMN app_deleted_at TEXT;
