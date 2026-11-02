# PyInstaller build: two programs in one folder (architecture section 11).
#   HomeBase.exe     the PC app (windowed)
#   HomeBaseHub.exe  the hub service (no window; started at boot by Task Scheduler)
# One-folder mode keeps PySide6's LGPL Qt libraries as separate, replaceable DLLs (FR-3).
# Build with: python tools/build_release.py

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SRC = "../src"
datas = (
    collect_data_files("family_scheduler")       # SQL migrations
    + collect_data_files("tzdata")               # America/Toronto on Windows
    + collect_data_files("googleapiclient")      # Calendar API discovery document
)
hidden = collect_submodules("uvicorn") + collect_submodules("zeroconf") + ["keyring.backends.Windows"]
# zeroconf is LGPL-2.1: ship it as plain .py files in the folder so it can be replaced, as the LGPL requires.
lgpl_as_files = {"zeroconf": "py"}
# Dev tools that hooks would otherwise drag in (pydantic has a mypy plugin).
dev_only = ["tkinter", "mypy", "mypyc", "pytest", "_pytest", "ruff", "PyInstaller", "pip", "setuptools"]

app_a = Analysis(["../src/family_scheduler/app/__main__.py"], pathex=[SRC], datas=datas, hiddenimports=hidden,
                 excludes=dev_only, module_collection_mode=lgpl_as_files)
hub_a = Analysis(["../src/family_scheduler/hub/__main__.py"], pathex=[SRC], datas=datas, hiddenimports=hidden,
                 excludes=[*dev_only, "PySide6"], module_collection_mode=lgpl_as_files)

app_exe = EXE(PYZ(app_a.pure), app_a.scripts, [], exclude_binaries=True, name="HomeBase",
              console=False, icon="app.ico")
hub_exe = EXE(PYZ(hub_a.pure), hub_a.scripts, [], exclude_binaries=True, name="HomeBaseHub",
              console=False, icon="app.ico")

COLLECT(app_exe, app_a.binaries, app_a.datas, hub_exe, hub_a.binaries, hub_a.datas, name="HomeBase")
