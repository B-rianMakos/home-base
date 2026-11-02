"""WO-FS-003: events grouped by month (EM-1 to EM-6) and the black theme's rules (VS-1, BK-9, SL-5, TD-9)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from conftest import ALEX, FakeClock, event

from family_scheduler.app import style
from family_scheduler.app.months import group_by_month
from family_scheduler.organizer import Organizer


def ev(title: str, day: str, start: str = "16:00", repeat: str = "none",
       next_date: str | None = None) -> dict[str, Any]:
    return {"title": title, "date": day, "start_time": start, "repeat": repeat, "next_date": next_date or day}


def months(groups: list[Any]) -> list[str]:
    """Each month and how many rows it has. The title itself has no count (WO-FS-008 ST-1)."""
    assert all(not re.search(r"\(\d+\)", g.label) for g in groups)
    return [f"{g.label} ({len(g.rows)})" for g in groups]


def titles(group: Any) -> list[str]:
    return [r.event["title"] for r in group.rows]


def test_months_in_date_order_with_counts() -> None:
    upcoming = [ev("Dentist", "2026-11-03"), ev("Concert", "2026-12-10"), ev("Swim", "2026-11-04")]
    groups = group_by_month(upcoming, [])
    assert months(groups) == ["November 2026 (2)", "December 2026 (1)"]  # EM-1, EM-6


def test_sorted_by_date_then_start_time_within_a_month() -> None:
    upcoming = [ev("Late", "2026-11-03", "18:00"), ev("Early", "2026-11-03", "09:00"), ev("First", "2026-11-01")]
    assert titles(group_by_month(upcoming, [])[0]) == ["First", "Early", "Late"]  # EM-2


def test_sync_errors_no_longer_jump_the_month_order() -> None:
    # The hub lists failed events first; inside a month they take their place by date.
    upcoming = [ev("Failed", "2026-11-20"), ev("Dentist", "2026-11-03")]
    assert titles(group_by_month(upcoming, [])[0]) == ["Dentist", "Failed"]


def test_repeating_event_once_under_its_next_occurrence() -> None:
    swim = ev("Swim", "2026-09-01", repeat="weekly", next_date="2026-11-03")
    groups = group_by_month([swim], [])
    assert months(groups) == ["November 2026 (1)"]  # EM-3: not under September, and only once
    assert groups[0].rows[0].day == "2026-11-03"


def test_past_events_in_their_own_month_above_the_current_one() -> None:
    past = [ev("Back-to-school night", "2026-09-29"), ev("Soccer", "2026-10-20")]
    upcoming = [ev("Dentist", "2026-10-30"), ev("Concert", "2026-11-10")]
    groups = group_by_month(upcoming, past)
    assert months(groups) == ["September 2026 (1)", "October 2026 (2)", "November 2026 (1)"]  # EM-4
    assert [r.past for r in groups[1].rows] == [True, False]


def test_months_with_no_events_are_left_out() -> None:
    groups = group_by_month([ev("Dentist", "2026-11-03"), ev("Ski weekend", "2027-01-09")], [])
    assert months(groups) == ["November 2026 (1)", "January 2027 (1)"]  # EM-5: no December


def test_no_events_at_all() -> None:
    assert group_by_month([], []) == []  # the view then shows "No events yet." (EM-5)


def test_grouping_what_the_hub_sends(org: Organizer, clock: FakeClock) -> None:
    """End to end with the Organizer: Tuesday Oct 27, 2026."""
    org.add_event(ALEX, event("Back-to-school night", day="2026-09-29", start="18:30", end="20:00"))
    org.add_event(ALEX, event("Swim practice", day="2026-10-06", start="18:00", end="19:00", repeat="weekly"))
    org.add_event(ALEX, event("Dentist", day="2026-11-03"))
    org.add_event(ALEX, event("Ski weekend", day="2027-01-09", start="09:00", end="17:00"))
    data = org.list_events()
    groups = group_by_month(data["upcoming"], data["past"])
    assert months(groups) == ["September 2026 (1)", "October 2026 (1)", "November 2026 (1)", "January 2027 (1)"]
    swim = groups[1].rows[0]
    assert (swim.event["title"], swim.day, swim.past) == ("Swim practice", "2026-10-27", False)
    assert groups[0].rows[0].past

    clock.advance(days=7)  # a week on, Swim's next occurrence is in November: still shown once
    data = org.list_events()
    groups = group_by_month(data["upcoming"], data["past"])
    assert months(groups) == ["September 2026 (1)", "November 2026 (2)", "January 2027 (1)"]
    assert [r.event["title"] for g in groups for r in g.rows].count("Swim practice") == 1


@pytest.mark.parametrize(("name", "fg", "bg"), style.CONTRAST_PAIRS, ids=[p[0] for p in style.CONTRAST_PAIRS])
def test_every_text_colour_passes_wcag_aa(name: str, fg: str, bg: str) -> None:
    assert style.contrast_ratio(fg, bg) >= 4.5, name  # BK-9


def test_contrast_ratio_matches_known_values() -> None:
    assert style.contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0)
    assert style.contrast_ratio("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)


def test_light_green_is_only_used_for_selection() -> None:
    lines = [line for line in style.STYLESHEET.splitlines() if style.SELECT in line]
    assert lines
    assert all("selected" in line or "selection" in line for line in lines)  # SL-5, SL-6
    assert style.SELECT != style.TODAY_EVENT  # TD-9: a pale fill, and a strong green used only for text
    assert all(fg != style.SELECT for _, fg, _ in style.CONTRAST_PAIRS)  # never a text colour


APP_FILES = ("views.py", "main_window.py", "dialogs.py", "widgets.py", "icons.py", "badges.py", "months.py", "week.py",
             "calview.py", "accounts.py", "today.py",
             "__main__.py")
# Calls that set a size, a gap, a corner or a timing: their numbers must come from style.py (VS-1, WO-FS-004).
SIZING_CALLS = re.compile(r"\b(setContentsMargins|setSpacing|setHorizontalSpacing|setVerticalSpacing|setFixedWidth|"
                          r"setFixedHeight|setFixedSize|setMinimumWidth|setMinimumHeight|setMinimumSize|resize|"
                          r"setBlurRadius|setOffset|drawRoundedRect|setDuration|adjusted)\(([^()]*(?:\([^()]*\))?[^()]*)\)")


def test_colours_and_sizes_are_only_defined_in_style_py() -> None:
    app = Path(style.__file__).parent
    for name in APP_FILES:
        source = (app / name).read_text(encoding="utf-8")
        assert not re.findall(r"#[0-9a-fA-F]{6}\b", source), name  # no colours
        assert not re.findall(r"\d+\s*(pt|px)\b", source), name  # no font sizes or pixel sizes
        assert "setStyleSheet(" not in source, name  # no stylesheets outside style.py
        if name == "icons.py":
            continue  # the app icon's drawing is artwork on its own 64-unit grid, like the paths in an SVG
        for call, args in SIZING_CALLS.findall(source):
            literals = [int(n) for n in re.findall(r"(?<![\w.])\d+(?![\w.])", args)]
            assert all(n <= 2 for n in literals), f"{name}: {call}({args})"  # 0, 1 and 2 are hairlines


def test_style_defines_the_grid_and_corners() -> None:
    assert (style.S1, style.S2, style.S3, style.S4) == (8, 16, 24, 32)  # MS-4
    assert style.R_CARD == 12 and style.R_CONTROL == 8  # MS-3
    assert (style.BASE, style.SURFACE, style.RAISED) == ("#000000", "#121212", "#1E1E1E")  # MS-1
    assert style.ACCENT == "#FFB547" and style.FONT_FAMILY == "Inter"  # approved in Part A


def test_blend_fades_between_two_colours() -> None:
    assert style.blend("#000000", "#FFFFFF", 0.0) == "#000000"
    assert style.blend("#000000", "#FFFFFF", 1.0) == "#FFFFFF"
    assert style.blend("#121212", "#1E1E1E", 0.5) == "#181818"
