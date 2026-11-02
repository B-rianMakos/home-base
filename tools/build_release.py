r"""Builds the release: PyInstaller folder, then the Inno Setup installer (dist/HomeBaseSetup-X.Y.Z.exe).

    python tools/build_release.py [OUTPUT_FOLDER]

OUTPUT_FOLDER defaults to dist. Use another folder while an app from dist\HomeBase is still open, since
Windows won't let its files be replaced.

Needs the dev dependencies (pip install -e .[dev]) and Inno Setup 6 (free) for the installer step.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from family_scheduler import __version__  # noqa: E402

ISCC_PATHS = [
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
]


def app_icon() -> None:
    """packaging/app.ico, drawn from the same code as the window icon (IF-2)."""
    from PySide6.QtGui import QGuiApplication

    from family_scheduler.app.icons import write_ico

    _app = QGuiApplication.instance() or QGuiApplication([])
    write_ico(ROOT / "packaging" / "app.ico")


def main() -> None:
    dist = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist"
    subprocess.run([sys.executable, str(ROOT / "tools" / "licences.py"), str(ROOT / "THIRD-PARTY-LICENSES.md")],
                   check=True)
    app_icon()
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(dist / "HomeBase", ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(dist),
                    "--workpath", str(ROOT / "build"), str(ROOT / "packaging" / "HomeBase.spec")],
                   check=True, cwd=ROOT / "packaging")
    iscc = next((p for p in ISCC_PATHS if p.exists()), None) or shutil.which("iscc")
    if iscc is None:
        print("Inno Setup 6 not found: built dist/HomeBase only. Install Inno Setup to build the installer.")
        return
    subprocess.run([str(iscc), f"/DAppVersion={__version__}", f"/DDistDir={dist}",
                    str(ROOT / "packaging" / "installer.iss")], check=True)
    print(f"Built {dist / f'HomeBaseSetup-{__version__}.exe'}")


if __name__ == "__main__":
    main()
