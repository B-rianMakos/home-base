"""Background jobs on the hub: Clear all commits, automatic Google retries (WO-FS-005 AS-2), nightly purge and
daily backup (section 8)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from family_scheduler import db
from family_scheduler.hub.context import HubContext

log = logging.getLogger(__name__)
BACKUPS_KEPT = 7


def run_maintenance(ctx: HubContext) -> None:
    counts = ctx.organizer.purge()
    log.info("30-day purge: %s", counts)
    backup_dir = ctx.data_dir / "backups"
    target = backup_dir / f"family-{ctx.organizer.today().isoformat()}.db"
    with ctx.organizer._read():
        db.backup(ctx.conn, target)
    prune_backups(backup_dir)
    ctx.organizer.mark_maintenance_done()


def prune_backups(folder: Path, keep: int = BACKUPS_KEPT) -> None:
    copies = sorted(folder.glob("family-*.db"))
    for old in copies[:-keep]:
        old.unlink(missing_ok=True)


class Worker(threading.Thread):
    def __init__(self, ctx: HubContext, tick: float = 0.5) -> None:
        super().__init__(name="hub-worker", daemon=True)
        self.ctx = ctx
        self.tick = tick
        self.stopping = threading.Event()

    def run(self) -> None:
        ticks = 0
        try:
            self.ctx.organizer.retry_on_start()  # AS-2: whenever the hub starts
        except Exception:
            log.exception("Couldn't schedule retries at start")
        self.sync_on_start()
        while not self.stopping.is_set():
            try:
                self.ctx.organizer.commit_due_clears()
                self.ctx.organizer.commit_history_deletes()
                self.ctx.organizer.commit_trash_deletes()  # WO-FS-014 DT-4
                if ticks % 20 == 0:  # every 10 seconds
                    sent = self.ctx.organizer.retry_due()
                    if sent:
                        log.info("Automatic retry sent %s event(s) to Google", sent)
                if ticks % 120 == 0 and self.ctx.organizer.maintenance_due():
                    run_maintenance(self.ctx)
            except Exception:  # keep the hub alive; the next tick retries
                log.exception("Background job failed")
            ticks += 1
            self.stopping.wait(self.tick)

    def sync_on_start(self) -> None:
        """WO-FS-010 GY-2: one Sync with Google whenever the hub starts (there's no timed background sync)."""
        org = self.ctx.organizer
        if org.needs_setup() or not org.connected_accounts():
            return
        try:
            log.info("Sync with Google at start: %s", org.sync_google(org.admin_email())["message"])
        except Exception:  # offline at boot: the next Sync button or app start tries again
            log.exception("Sync with Google at start failed")

    def stop(self) -> None:
        self.stopping.set()
