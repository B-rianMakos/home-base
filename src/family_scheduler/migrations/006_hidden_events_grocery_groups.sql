-- WO-FS-011 · Hidden events (HE-11) and grocery groups and order (GO-8). Upgrades a 1.8.0 database with nothing lost.

-- An event hidden from the app stays in Google Calendar; it's hidden for the whole family until shown again.
ALTER TABLE event ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1));
ALTER TABLE event ADD COLUMN hidden_by TEXT;
ALTER TABLE event ADD COLUMN hidden_at TEXT;

-- Groceries: Food or Items, and each item's place in its group's to-buy list. Every existing item goes into Food
-- (GG-9), keeping the order it was added in.
ALTER TABLE grocery_item ADD COLUMN category TEXT NOT NULL DEFAULT 'food' CHECK (category IN ('food', 'item'));
ALTER TABLE grocery_item ADD COLUMN position INTEGER NOT NULL DEFAULT 0;
UPDATE grocery_item SET position = (SELECT COUNT(*) FROM grocery_item AS earlier
                                    WHERE earlier.added_at < grocery_item.added_at
                                       OR (earlier.added_at = grocery_item.added_at
                                           AND earlier.rowid < grocery_item.rowid)) + 1;
