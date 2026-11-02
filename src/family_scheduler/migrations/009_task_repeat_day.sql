-- WO-FS-013 ET-1 · A monthly or yearly task remembers its repeat day (and, yearly, its month), so a task on the 31st
-- comes back to the 31st after a short month instead of drifting to the 28th. Upgrades a 1.10.1 database with nothing
-- lost. The days are filled in once by the hub when it starts (Organizer.restore_repeat_days, ET-2 to ET-4), since
-- that follows each task's chain of earlier occurrences; non-repeating, daily and weekly tasks leave them empty.

ALTER TABLE task ADD COLUMN repeat_day INTEGER CHECK (repeat_day BETWEEN 1 AND 31);
ALTER TABLE task ADD COLUMN repeat_month INTEGER CHECK (repeat_month BETWEEN 1 AND 12);
