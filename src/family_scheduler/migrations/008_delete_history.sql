-- 1.10.1 · History entries can be deleted one at a time, or all at once with Clear all. A deleted entry is marked
-- first, so Undo can bring it back, and deleted for good once the Undo time has run out. Upgrades a 1.10.0 database
-- with nothing lost; every existing entry stays shown.

ALTER TABLE activity_entry ADD COLUMN deleted_at TEXT;
ALTER TABLE activity_entry ADD COLUMN deleted_batch TEXT;
