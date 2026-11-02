"""Installs the Git pre-push hook (WO-FS-010 PC-c, WO-FS-015 CK-6): before every push, the personal-information
check scans every tracked file and every commit, and the push is stopped if it finds anything.

    python tools/install_hooks.py
"""

from __future__ import annotations

import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = """#!/bin/sh
# Installed by tools/install_hooks.py: nothing personal can be pushed.
PY=.venv/Scripts/python
[ -x "$PY" ] || PY=.venv/bin/python
[ -x "$PY" ] || PY=python
exec "$PY" tools/check_public.py --history
"""


def main() -> None:
    hooks = ROOT / ".git" / "hooks"
    if not hooks.parent.exists():
        raise SystemExit("Not a Git repository: run this from a clone of Home Base.")
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-push"
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed {hook.relative_to(ROOT).as_posix()}: every push is checked for personal information first.")


if __name__ == "__main__":
    main()
