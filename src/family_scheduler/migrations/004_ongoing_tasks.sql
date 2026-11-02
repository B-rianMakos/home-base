-- WO-FS-009 · Ongoing tasks (OT-11). A task's due date can be empty (''): an ongoing task, never overdue and not on
-- the Calendar. An ongoing task can't repeat (OT-2), since repeats need a date. Every existing task keeps its date.
--
-- The rule is a table check, so the table is rebuilt with the same columns, data and indexes (as in 003); db.migrate
-- turns foreign keys off around this and checks them afterwards.

CREATE TABLE task_new (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    priority         TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('high', 'medium', 'low')),
    due_date         TEXT NOT NULL,                  -- YYYY-MM-DD; '' for an ongoing task
    repeat           TEXT NOT NULL DEFAULT 'none'
                     CHECK (repeat IN ('none', 'daily', 'weekly', 'monthly', 'yearly')),
    completed_at     TEXT,
    completed_by     TEXT REFERENCES family_member(email),
    deleted_at       TEXT,
    deleted_by       TEXT REFERENCES family_member(email),
    previous_task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
    created_by       TEXT NOT NULL REFERENCES family_member(email),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    CHECK (due_date <> '' OR repeat = 'none')
);

INSERT INTO task_new (id, title, priority, due_date, repeat, completed_at, completed_by, deleted_at, deleted_by,
                      previous_task_id, created_by, created_at, updated_at)
SELECT id, title, priority, due_date, repeat, completed_at, completed_by, deleted_at, deleted_by,
       previous_task_id, created_by, created_at, updated_at
FROM task;

DROP TABLE task;

ALTER TABLE task_new RENAME TO task;

CREATE INDEX task_open_idx  ON task (due_date) WHERE completed_at IS NULL AND deleted_at IS NULL;
CREATE INDEX task_done_idx  ON task (completed_at) WHERE completed_at IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX task_trash_idx ON task (deleted_at) WHERE deleted_at IS NOT NULL;
