"""Hub service entry point.

    HomeBaseHub.exe                 run the hub (started at boot by the scheduled task)
    HomeBaseHub.exe set-admin EMAIL change the admin account (architecture 9.2)
    python -m family_scheduler.hub demo   a demo hub with a made-up family (WO-FS-015 RN-3)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from family_scheduler import __version__
from family_scheduler.config import data_dir, hub_port


def _setup_logging() -> None:
    # A windowed PyInstaller build has no console; uvicorn and logging must not write to it.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")  # noqa: SIM115
    folder = data_dir() / "logs"
    folder.mkdir(exist_ok=True)
    handler = RotatingFileHandler(folder / "hub.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])


def run(demo: bool = False) -> None:
    import uvicorn

    from family_scheduler.hub.api import create_app
    from family_scheduler.hub.context import build_context
    from family_scheduler.hub.discovery import announce
    from family_scheduler.hub.worker import Worker

    _setup_logging()
    log = logging.getLogger("family_scheduler.hub")
    if demo:
        from datetime import date

        from family_scheduler import demo as demo_data
        from family_scheduler.secrets_store import KeyFileProvider

        # Its own key, never one taken from this PC's Credential Manager, so a demo hub shares nothing with a real one.
        ctx = build_context(data_dir(), keys=KeyFileProvider(data_dir() / "keys" / "hub.key"), fake_calendar=True,
                            fake_mail=True)
        if demo_data.seed(ctx.organizer, date.today()):
            log.info("Demo family added")
    else:
        ctx = build_context(data_dir())
    worker = Worker(ctx)
    worker.start()  # commits any Clear all left open by a restart on its first tick
    port = hub_port()
    zc = None if demo else announce(port)  # a demo hub isn't announced: no other PC on the network finds it
    log.info("Home Base hub %s starting on port %s, data in %s", __version__, port, ctx.data_dir)
    try:
        uvicorn.run(create_app(ctx), host="0.0.0.0", port=port, log_config=None, access_log=False)
    finally:
        worker.stop()
        if zc is not None:
            zc.close()


def _tell(message: str) -> None:
    """The hub exe has no console, so also show a Windows message box."""
    print(message)
    if sys.platform == "win32" and (sys.stdout is None or not sys.stdout.isatty()):
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Home Base", 0)


def set_admin(email: str) -> None:
    from family_scheduler.hub.context import build_context

    ctx = build_context(data_dir())
    if ctx.google_connected():
        _tell("Disconnect Google Calendar first (Family & Settings on the hub PC).")
        raise SystemExit(1)
    admin = ctx.organizer.admin_email()
    ctx.organizer.set_admin_email(admin, email, google_connected=False)
    _tell(f"Admin account is now {ctx.organizer.admin_email()}. Restart the hub, then reconnect Google Calendar.")


TASK = "Home Base Hub"  # WO-FS-011 HB-3: was "Family Scheduler Hub"


def import_from(source: str) -> None:
    """WO-FS-008 PS-2, MH-2: bring another hub's data into this one. Run as an administrator; the installer offers it.
    The hub is stopped while its data is replaced, then started again."""
    import subprocess
    import time

    from family_scheduler.hub.move import ImportFailed, import_data, source_key

    hidden = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    if sys.platform == "win32":
        subprocess.run(["schtasks", "/End", "/TN", TASK], capture_output=True, creationflags=hidden, check=False)
        for exe in ("HomeBaseHub.exe", "FamilySchedulerHub.exe"):  # the hub, by either name (HB-3)
            subprocess.run(["taskkill", "/F", "/IM", exe, "/FI", f"PID ne {os.getpid()}"],
                           capture_output=True, creationflags=hidden, check=False)
        time.sleep(2)  # let Windows release the stopped hub's files
    folder = Path(source)
    try:
        report = import_data(folder, data_dir(), source_key(folder))
    except (ImportFailed, OSError) as err:
        _tell(f"The data wasn't brought in. {err}")
        raise SystemExit(1) from err
    finally:
        if sys.platform == "win32":
            subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, creationflags=hidden, check=False)
    _tell(f"Home Base data brought in from {folder}.\n\n{report.summary()}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="HomeBaseHub")
    sub = parser.add_subparsers(dest="command")
    admin = sub.add_parser("set-admin", help="change the admin Google account")
    admin.add_argument("email")
    move = sub.add_parser("import", help="bring another hub's data (a folder with family.db) into this hub")
    move.add_argument("folder")
    sub.add_parser("demo", help="run a demo hub with a made-up family, in test mode, apart from any real data")
    args = parser.parse_args()
    if args.command == "set-admin":
        set_admin(args.email)
    elif args.command == "import":
        import_from(args.folder)
    elif args.command == "demo":
        if not os.environ.get("FS_DATA_DIR"):  # never the real hub's data folder
            os.environ["FS_DATA_DIR"] = str(Path(os.environ.get("TEMP", Path.home())) / "home-base-demo")
        run(demo=True)
    else:
        run()


if __name__ == "__main__":
    main()
