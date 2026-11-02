-- WO-FS-014 CD-1 · Tasks and reminders in Trash can be deleted for good: one, the ticked ones, or all of them. They
-- are marked first with a batch id, so Undo can bring them back, and deleted for good once the Undo time has run out.
-- Upgrades a 1.11.0 database with nothing lost; everything in Trash stays there.

ALTER TABLE task ADD COLUMN purge_at TEXT;
ALTER TABLE task ADD COLUMN purge_batch TEXT;
ALTER TABLE reminder ADD COLUMN purge_at TEXT;
ALTER TABLE reminder ADD COLUMN purge_batch TEXT;
