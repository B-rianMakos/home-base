"""WO-FS-014: delete one Trash item for good, the ticked ones, or everything with Clear all, each with a 10-second Undo
(DT-1 to DT-5, CT-1 to CT-4, TK-a, RL-1 to RL-6, CD-1 to CD-3)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from conftest import ALEX, SAM, FakeClock, task
from fastapi.testclient import TestClient
from test_today import open_app, page_text

from family_scheduler import db
from family_scheduler.app.views import describe_change
from family_scheduler.hub.api import create_app
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import NotFound, Organizer, TooLate


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def trashed_task(org: Organizer, title: str, **kw: Any) -> str:
    t = org.add_task(ALEX, task(title, **kw))
    org.remove_task(ALEX, t["id"])
    return str(t["id"])


def trashed_reminder(org: Organizer, text: str) -> str:
    r = org.add_reminder(ALEX, text)
    org.remove_reminder(ALEX, r["id"])
    return str(r["id"])


def trash(org: Organizer) -> list[str]:
    return sorted(i["label"] for i in org.list_trash())


def rows(org: Organizer, table: str) -> int:
    return int(org.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_delete_one_task_and_one_reminder_then_undo(org: Organizer) -> None:  # DT-1, DT-3, RL-1, RL-2
    t = trashed_task(org, "Shed")
    r = trashed_reminder(org, "Guest Wi-Fi is on the fridge.")
    one = org.delete_from_trash(ALEX, [("task", t)])
    assert trash(org) == ["Guest Wi-Fi is on the fridge."]
    entry = org.list_history()[0]
    assert (entry["label"], describe_change(entry)) == ("Shed", "Deleted from Trash for good")
    assert org.undo_trash_delete(SAM, one["id"]) == 1  # anyone can undo
    assert trash(org) == ["Guest Wi-Fi is on the fridge.", "Shed"]
    assert all(h["after"] is None or "batch" not in h["after"] for h in org.list_history())  # its entry is gone
    two = org.delete_from_trash(SAM, [("reminder", r)])
    assert trash(org) == ["Shed"]
    org.undo_trash_delete(ALEX, two["id"])
    assert len(org.list_trash()) == 2


def test_deleted_for_good_after_the_undo_time(org: Organizer, clock: FakeClock) -> None:  # DT-4
    t = trashed_task(org, "Shed")
    done = org.delete_from_trash(ALEX, [("task", t)])
    clock.advance(seconds=10)
    assert org.commit_trash_deletes() == 0 and rows(org, "task") == 1  # still undoable in the grace time
    clock.advance(seconds=6)
    assert org.commit_trash_deletes() == 1 and rows(org, "task") == 0
    with pytest.raises(TooLate, match="Too late to undo"):
        org.undo_trash_delete(ALEX, done["id"])


def test_undo_and_the_final_delete_never_both_happen(org: Organizer, clock: FakeClock) -> None:  # CD-3
    t = trashed_task(org, "Shed")
    done = org.delete_from_trash(ALEX, [("task", t)])
    org.undo_trash_delete(ALEX, done["id"])
    clock.advance(seconds=60)
    assert org.commit_trash_deletes() == 0 and trash(org) == ["Shed"]  # undone: never deleted
    again = org.delete_from_trash(ALEX, [("task", t)])
    clock.advance(seconds=60)
    org.commit_trash_deletes()
    with pytest.raises(TooLate):
        org.undo_trash_delete(ALEX, again["id"])  # deleted: never undone
    with pytest.raises(NotFound):
        org.delete_from_trash(ALEX, [("task", t)])


def test_clear_all_with_undo_and_its_count(org: Organizer, clock: FakeClock) -> None:  # CT-2 to CT-4
    for title in ("Shed", "Attic"):
        trashed_task(org, title)
    trashed_reminder(org, "Garage code changed.")
    done = org.clear_trash(SAM)
    assert done["count"] == 3 and org.list_trash() == []
    entry = org.list_history()[0]
    assert (entry["action"], entry["label"], describe_change(entry)) == ("cleared", "Trash (3 items)",
                                                                       "Cleared Trash (3 items)")
    org.undo_trash_delete(ALEX, done["id"])
    assert len(org.list_trash()) == 3 and org.list_history()[0]["action"] != "cleared"
    org.clear_trash(SAM)
    clock.advance(seconds=16)
    assert org.commit_trash_deletes() == 3
    with pytest.raises(NotFound, match="already empty"):
        org.clear_trash(SAM)


def test_delete_ticked(org: Organizer) -> None:  # TK-a
    a, b = trashed_task(org, "Shed"), trashed_task(org, "Attic")
    trashed_reminder(org, "Keep")
    done = org.delete_from_trash(ALEX, [("task", a), ("task", b)])
    assert done["count"] == 2 and trash(org) == ["Keep"]
    assert describe_change(org.list_history()[0]) == "Cleared Trash (2 items)"


def test_a_repeating_tasks_next_occurrence_is_untouched(org: Organizer, clock: FakeClock) -> None:  # DT-5
    t = org.add_task(ALEX, task("Rent", due="2026-10-31", repeat="monthly"))
    org.complete_task(ALEX, t["id"])
    nxt = next(x for x in org.list_tasks()["low"] + org.list_tasks()["medium"] + org.list_tasks()["high"]
               if x["title"] == "Rent")
    org.remove_task(ALEX, t["id"])  # the done one, to Trash
    org.delete_from_trash(ALEX, [("task", t["id"])])
    clock.advance(seconds=16)
    org.commit_trash_deletes()
    row = org.conn.execute("SELECT * FROM task WHERE id = ?", (nxt["id"],)).fetchone()
    assert row["previous_task_id"] is None and row["repeat"] == "monthly" and row["repeat_day"] == 31


def test_nothing_outside_trash_changes(org: Organizer, clock: FakeClock) -> None:  # RL-3, RL-4
    keep = org.add_task(ALEX, task("Keep me"))
    org.add_reminder(ALEX, "Keep this note")
    trashed_task(org, "Shed")
    before = {t: rows(org, t) for t in ("event", "reminder", "grocery_item")}
    org.clear_trash(ALEX)
    clock.advance(seconds=16)
    org.commit_trash_deletes()
    assert {t: rows(org, t) for t in before} == before
    assert org.conn.execute("SELECT title FROM task").fetchall()[0][0] == keep["title"]
    assert any(h["label"] == "Keep me" for h in org.list_history())  # History isn't touched
    org.purge()  # the nightly clean-up still works
    assert rows(org, "task") == 1


def test_restore_ignores_items_being_deleted(org: Organizer) -> None:
    t = trashed_task(org, "Shed")
    org.delete_from_trash(ALEX, [("task", t)])
    assert org.restore(ALEX, [("task", t)]) == 0


def test_the_migration(tmp_path: Path) -> None:  # CD-1
    conn = db.connect(str(tmp_path / "family.db"))
    assert db.migrate(conn) == 10
    for table in ("task", "reminder"):
        assert {"purge_at", "purge_batch"} <= {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()


def test_the_hub_endpoints(ctx: HubContext) -> None:  # CD-2
    web = TestClient(create_app(ctx), client=("127.0.0.1", 50000))
    h = {"X-Member": ALEX}
    a, b = trashed_task(ctx.organizer, "Shed"), trashed_task(ctx.organizer, "Attic")
    one = web.post("/api/trash/delete", json={"items": [{"type": "task", "id": a}]}, headers=h).json()
    assert [i["label"] for i in web.get("/api/trash", headers=h).json()] == ["Attic"]
    assert web.post(f"/api/trash/undo/{one['id']}", headers=h).json() == {"restored": 1}
    every = web.post("/api/trash/clear", headers=h).json()
    assert every["count"] == 2 and web.get("/api/trash", headers=h).json() == []
    assert web.post(f"/api/trash/undo/{every['id']}", headers=h).json() == {"restored": 2}
    assert web.post("/api/trash/delete", json={"items": [{"type": "task", "id": "nope"}]},
                    headers=h).status_code == 404
    assert b


def test_the_trash_page(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock, tmp_path: Path,
                        monkeypatch: pytest.MonkeyPatch) -> None:  # DT-1 to DT-3, CT-1, TK-a, RL-5
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from family_scheduler.app import dialogs
    from family_scheduler.app.widgets import ACTION_TIPS, DANGER_ACTIONS

    trashed_task(org, "Shed")
    trashed_task(org, "Attic")
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    window.show_view("Trash")
    view = window.current()
    assert sorted(page_text(view)) == ["Attic", "Shed"]
    row = view.list.row_delegate.entry(view.list.model().index(0, 0))
    assert row.actions == ["restore", "delete_forever"] and "delete_forever" in DANGER_ACTIONS
    assert ACTION_TIPS["delete_forever"] == "Delete forever. It can't be restored after 10 seconds."
    assert view.clear_b.isEnabled() and not view.delete_b.isEnabled()
    first = page_text(view)[0]
    view.on_action(row.id, "delete_forever")  # no confirmation
    assert first not in page_text(view)
    assert window.toast.text.text().startswith("Deleted forever. 10 s to undo.")
    window.undo()
    assert sorted(page_text(view)) == ["Attic", "Shed"]
    view.list.setCurrentRow(0)
    view.list.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert len(page_text(view)) == 1  # DT-2: the Delete key
    window.undo()
    view.select_all()
    assert view.delete_b.isEnabled()
    asked: list[str] = []
    monkeypatch.setattr(dialogs, "confirm", lambda _p, _t, text, *a, **k: asked.append(text) or True)
    view.delete_ticked()
    assert asked == ["Delete 2 items forever?"] and page_text(view) == []
    window.undo()
    view.clear()
    assert asked[-1] == "Clear all Trash?" and page_text(view) == []
    assert window.toast.text.text().startswith("Trash cleared (2 items).")
    assert not view.clear_b.isEnabled()  # CT-1: disabled when empty; RL-5: the empty state at once
    assert view.list.count() == 0 or "Trash is empty." in str(page_text(view))
