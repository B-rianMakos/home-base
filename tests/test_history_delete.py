"""1.10.1: History entries can be deleted one at a time, or all at once with Clear all, each with 10 seconds to undo."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from conftest import ALEX, SAM, FakeClock, task
from fastapi.testclient import TestClient
from test_today import open_app, page_text

from family_scheduler import db
from family_scheduler.hub.api import create_app
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import NotFound, Organizer, TooLate


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def labels(org: Organizer) -> list[str]:
    return [h["label"] for h in org.list_history()]


def rows_in_db(org: Organizer) -> int:
    return int(org.conn.execute("SELECT COUNT(*) FROM activity_entry").fetchone()[0])


def test_delete_one_entry_then_undo(org: Organizer, clock: FakeClock) -> None:
    org.add_task(ALEX, task("Recycling"))
    org.add_task(SAM, task("Shed"))
    shed = org.list_history()[0]
    done = org.delete_history(ALEX, shed["id"])
    assert done["count"] == 1 and labels(org) == ["Recycling"]  # gone for everyone
    assert org.list_history(member=SAM) == []
    assert org.undo_history_delete(SAM, done["id"]) == 1  # anyone can undo it
    assert labels(org) == ["Shed", "Recycling"]  # back in place; undoing writes nothing new to History


def test_delete_is_for_good_after_the_undo_time(org: Organizer, clock: FakeClock) -> None:
    org.add_task(ALEX, task("Recycling"))
    org.add_task(ALEX, task("Shed"))
    done = org.delete_history(ALEX, org.list_history()[0]["id"])
    clock.advance(seconds=5)
    assert org.commit_history_deletes() == 0 and rows_in_db(org) == 2  # still undoable
    clock.advance(seconds=11)
    assert org.commit_history_deletes() == 1 and rows_in_db(org) == 1
    with pytest.raises(TooLate):
        org.undo_history_delete(ALEX, done["id"])


def test_an_entry_already_gone(org: Organizer) -> None:
    org.add_task(ALEX, task("Recycling"))
    entry = org.list_history()[0]["id"]
    org.delete_history(ALEX, entry)
    with pytest.raises(NotFound, match="already gone"):
        org.delete_history(SAM, entry)
    with pytest.raises(NotFound):
        org.delete_history(ALEX, 99999)


def test_clear_all_history_then_undo(org: Organizer, clock: FakeClock) -> None:
    for t in ("Recycling", "Shed", "Paint"):
        org.add_task(ALEX, task(t))
    org.add_task(SAM, task("Attic"))
    done = org.clear_history(SAM)
    assert done["count"] == 4 and org.list_history() == []
    assert len(org.list_tasks()["open"]) == 4  # the tasks themselves are untouched
    org.undo_history_delete(ALEX, done["id"])
    assert labels(org) == ["Attic", "Paint", "Shed", "Recycling"]
    org.clear_history(ALEX)
    with pytest.raises(NotFound, match="already empty"):
        org.clear_history(ALEX)


def test_clear_all_doesnt_bring_back_a_deleted_entry_on_undo(org: Organizer) -> None:
    org.add_task(ALEX, task("Recycling"))
    org.add_task(ALEX, task("Shed"))
    single = org.delete_history(ALEX, org.list_history()[0]["id"])  # Shed
    cleared = org.clear_history(ALEX)  # Recycling only
    assert cleared["count"] == 1
    org.undo_history_delete(ALEX, cleared["id"])
    assert labels(org) == ["Recycling"]
    org.undo_history_delete(ALEX, single["id"])
    assert labels(org) == ["Shed", "Recycling"]


def test_new_entries_after_clear_all_show(org: Organizer) -> None:
    org.add_task(ALEX, task("Recycling"))
    org.clear_history(ALEX)
    org.add_task(ALEX, task("Shed"))
    assert labels(org) == ["Shed"]


def test_the_api(ctx: HubContext) -> None:
    web = TestClient(create_app(ctx), client=("127.0.0.1", 50000))
    head = {"X-Member": ALEX}
    ctx.organizer.add_task(ALEX, task("Recycling"))
    ctx.organizer.add_task(ALEX, task("Shed"))
    entry = web.get("/api/history", headers=head).json()[0]["id"]
    one = web.delete(f"/api/history/{entry}", headers=head).json()
    assert [h["label"] for h in web.get("/api/history", headers=head).json()] == ["Recycling"]
    assert web.post(f"/api/history/undo/{one['id']}", headers=head).json() == {"restored": 1}
    every = web.post("/api/history/clear", headers=head).json()
    assert every["count"] == 2 and web.get("/api/history", headers=head).json() == []
    assert web.delete(f"/api/history/{entry}", headers=head).status_code == 404


def test_migration_keeps_every_entry_shown(tmp_path: Path) -> None:
    conn = db.connect(str(tmp_path / "family.db"))
    assert db.migrate(conn) == 10
    cols = {r[1] for r in conn.execute("PRAGMA table_info(activity_entry)")}
    assert {"deleted_at", "deleted_batch"} <= cols
    conn.close()


def test_the_history_page(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock, tmp_path: Path,
                          monkeypatch: pytest.MonkeyPatch) -> None:
    from family_scheduler.app import dialogs
    from family_scheduler.app.widgets import ACTION_TIPS

    org.add_task(ALEX, task("Recycling"))
    org.add_task(ALEX, task("Shed"))
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    window.show_view("History")
    view = window.current()
    assert page_text(view) == ["Shed", "Recycling"]
    row = view.list.row_delegate.entry(view.list.model().index(0, 0))
    assert row.actions == ["delete_history"] and ACTION_TIPS["delete_history"] == "Delete this entry from History"
    view.on_action(row.id, "delete_history")  # the trash button on Shed: no confirmation
    assert page_text(view) == ["Recycling"]
    assert "Deleted from History" in window.toast.text.text()
    window.undo()
    assert page_text(view) == ["Shed", "Recycling"]
    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: False)
    view.clear()  # Cancel on the confirmation: nothing happens
    assert page_text(view) == ["Shed", "Recycling"]
    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: True)
    view.clear()
    assert page_text(view) == ["- Nothing has changed in the last 30 days."]
    assert "History cleared (2 entries)" in window.toast.text.text()
    window.undo()
    assert page_text(view) == ["Shed", "Recycling"]
