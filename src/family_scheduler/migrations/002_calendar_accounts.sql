-- WO-FS-005 · Google Calendar invites from two accounts, and automatic retries.
-- Upgrades a 1.2.0 database; nothing is removed (DB-4).

-- ---------------------------------------------------------------------------
-- Calendar accounts (GA-1, DB-2): the only Google accounts that can send invites.
-- Each account's token is a separate encrypted secret named "google_token:<email>".
-- position: the admin's account is first, the one used for anyone without their own account (GS-1).
-- ---------------------------------------------------------------------------
CREATE TABLE calendar_account (
    email        TEXT PRIMARY KEY CHECK (email = lower(email) AND email LIKE '%_@_%'),
    position     INTEGER NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'not_connected'
                 CHECK (status IN ('connected', 'not_connected', 'needs_reconnect')),
    last_error   TEXT,
    connected_at TEXT
);

-- changed in WO-FS-010: the accounts come from the hub's own settings, never from this file (AC-1, AC-2). The
-- admin, if one is set, becomes the first account; a second one is chosen in Family & Settings (AC-5).
INSERT INTO calendar_account (email, position)
    SELECT value, 1 FROM setting WHERE key = 'admin_email' AND value <> '';

-- The 1.2.0 token belonged to the admin account.
UPDATE secret SET name = 'google_token:' || (SELECT value FROM setting WHERE key = 'admin_email')
 WHERE name = 'google_token' AND EXISTS (SELECT value FROM setting WHERE key = 'admin_email');
UPDATE calendar_account SET status = 'connected', connected_at = (SELECT updated_at FROM secret
       WHERE name = 'google_token:' || calendar_account.email)
 WHERE EXISTS (SELECT 1 FROM secret WHERE name = 'google_token:' || calendar_account.email);

-- ---------------------------------------------------------------------------
-- Event organizer (GS-4, DB-1): the account that created the event in Google.
-- Every event made before 1.3.0 was created by the admin's account (GS-5).
-- ---------------------------------------------------------------------------
ALTER TABLE event ADD COLUMN organizer_email TEXT NOT NULL DEFAULT '';
UPDATE event SET organizer_email = coalesce((SELECT value FROM setting WHERE key = 'admin_email'), '');

-- ---------------------------------------------------------------------------
-- Automatic retry (AS-2, AS-3, DB-3), kept in the database so it survives a hub restart.
--   retry_attempts  failed sends so far
--   first_failed_at when the first send failed; retries stop 24 hours later
--   next_retry_at   when the hub tries again; NULL once it has given up
-- ---------------------------------------------------------------------------
ALTER TABLE event ADD COLUMN retry_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE event ADD COLUMN first_failed_at TEXT;
ALTER TABLE event ADD COLUMN next_retry_at TEXT;

CREATE INDEX event_retry_idx ON event (next_retry_at) WHERE next_retry_at IS NOT NULL;

-- Events already failed in 1.2.0 get the retry schedule from the next hub start.
UPDATE event SET next_retry_at = '1970-01-01T00:00:00+00:00' WHERE sync_status = 'error';
