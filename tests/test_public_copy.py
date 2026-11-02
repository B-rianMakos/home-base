"""WO-FS-015: the public copy's demo mode (RN-3), the author credit allowed by the personal-information check (PI-g),
and the commit scan failing instead of only reporting (CK-2)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import FakeClock

from family_scheduler import demo
from family_scheduler.hub.context import build_context
from family_scheduler.secrets_store import StaticKeyProvider

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_public  # noqa: E402


def test_demo_mode_fills_an_empty_hub_once(tmp_path: Path, clock: FakeClock) -> None:  # RN-2, RN-3
    ctx = build_context(tmp_path, keys=StaticKeyProvider(), clock=clock, fake_calendar=True, fake_mail=True)
    org = ctx.organizer
    assert org.needs_setup()  # RN-2: a new hub starts empty
    assert demo.seed(org, date(2026, 10, 27)) is True
    members = [m["email"] for m in org.list_members()]
    assert members == sorted([demo.ADMIN, demo.SECOND, *demo.MEMBERS])
    assert all(e.endswith("@example.com") for e in members)  # PI-a, PI-b: made up
    events = org.list_events()
    assert len(events["upcoming"]) >= 5
    assert any(e.get("car") for e in events["upcoming"])
    tasks = org.list_tasks()
    assert tasks["overdue"] and tasks["high"] and tasks["ongoing"]
    assert [r["message"] for r in org.list_reminders()]
    assert [i["label"] for i in org.list_trash()] == ["Buy a new garden hose"]
    assert len(org.list_cars()) == 2
    assert demo.seed(org, date(2026, 10, 27)) is False  # never twice, never over a real family
    ctx.conn.close()


def test_the_hub_has_a_demo_command() -> None:
    source = (ROOT / "src/family_scheduler/hub/__main__.py").read_text(encoding="utf-8")
    assert 'sub.add_parser("demo"' in source
    assert "zc = None if demo else announce(port)" in source  # not announced on the network
    assert 'home-base-demo' in source  # its own data folder, never the real hub's


def test_the_author_credit_is_the_one_allowed_phrase(monkeypatch: pytest.MonkeyPatch) -> None:  # PI-g
    monkeypatch.setattr(check_public, "author_credit", lambda: "Ada Lovelace and Alan Turing")
    private = ["Lovelace"]
    assert check_public.scan_text("Copyright (c) 2026 Ada Lovelace and Alan Turing", "LICENSE", private) == []
    found = check_public.scan_text("Ask Lovelace about it", "README.md", private)
    assert [f.kind for f in found] == ["a word from the private list"]


def test_this_copys_licence_names_its_authors() -> None:
    credit = check_public.author_credit()
    assert credit and " and " in credit and "@" not in credit  # names only, no contact details


def test_a_commit_with_personal_details_fails(monkeypatch: pytest.MonkeyPatch) -> None:  # CK-2
    monkeypatch.setattr(check_public, "load_blocklist", lambda: None)
    monkeypatch.setattr(check_public, "scan_files", lambda private: (1, []))
    monkeypatch.setattr(check_public, "scan_history", lambda private: (1, 1))
    monkeypatch.setattr(sys, "argv", ["check_public.py", "--history"])
    assert check_public.main() == 1
    monkeypatch.setattr(check_public, "scan_history", lambda private: (1, 0))
    assert check_public.main() == 0


def test_the_hook_installer_writes_a_pre_push_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # CK-6
    import install_hooks

    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(install_hooks, "ROOT", tmp_path)
    install_hooks.main()
    hook = (tmp_path / ".git" / "hooks" / "pre-push").read_text(encoding="utf-8")
    assert hook.startswith("#!/bin/sh") and "tools/check_public.py --history" in hook


def test_no_private_file_is_in_the_copy() -> None:  # PB-2, PI-e, CK-5
    import subprocess

    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                            capture_output=True, check=True).stdout.decode("utf-8")
    names = {Path(f).name for f in listed.split("\0") if f}  # everything Git would commit
    assert "LICENSE" in names
    assert "config.ini" not in names and not any(n.endswith((".db", ".log", ".key")) for n in names)
    assert not any("blocklist" in n for n in names)
    assert not any(n.startswith("client_secret") for n in names)
