# ruff: noqa: E501  (an HTML page template; its lines follow the HTML, not the code style)
"""Writes the style guide page (WO-FS-004 Part A 1) from the values in style.py, so the two can't drift apart.

    python tools/style_guide.py OUTPUT.html
"""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path

from family_scheduler.app import style as s
from family_scheduler.app.icons import VIEW_ICONS
from family_scheduler.app.widgets import ACTION_ICONS

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "style-guide.html").resolve()


def rel(path: Path) -> str:
    return Path(os.path.relpath(path, OUT.parent)).as_posix()


def svg(name: str, colour: str = s.TEXT, size: int = s.ICON) -> str:
    text = (s.ASSETS / "icons" / f"{name}.svg").read_text(encoding="utf-8")
    text = text.split("\n", 1)[1] if text.startswith("<!--") else text
    return (text.replace('width="24"', f'width="{size}"').replace('height="24"', f'height="{size}"')
            .replace("currentColor", colour).replace('class="', 'aria-hidden="true" class="'))


def swatch(name: str, value: str, note: str, text: str = s.TEXT) -> str:
    return (f'<div class="sw"><div class="chip" style="background:{value};color:{text}">Aa</div>'
            f'<b>{html.escape(name)}</b><code>{value}</code><span>{html.escape(note)}</span></div>')


def tag(text: str, colour: str) -> str:
    bg = s.TINT.get(colour, s.RAISED_HOVER)
    return f'<span class="tag" style="background:{bg};color:{colour}">{html.escape(text)}</span>'


def row(title: str, sub: str, accent: str, *, title_colour: str = s.TEXT, tags: str = "", state: str = "",
        tick: bool = False, actions: bool = False) -> str:
    acts = "".join(f'<span class="act">{svg(ACTION_ICONS[a], s.SELECT_TEXT if state == "sel" else s.TEXT)}</span>'
                   for a in ("edit", "remove")) if actions else ""
    t = '<span class="tick"></span>' if tick else ""
    colour = s.SELECT_TEXT if state == "sel" else title_colour
    return (f'<div class="row {state}"><span class="bar" style="background:'
            f'{s.SELECT_TEXT if state == "sel" else accent}"></span>{t}<div class="txt"><b style="color:{colour}">'
            f'{html.escape(title)}</b><span>{html.escape(sub)}</span></div><div class="tags">{tags}</div>{acts}</div>')


def main() -> None:
    fonts = s.ASSETS / "fonts"
    faces = "".join(
        f'@font-face {{ font-family: "Inter"; src: url("{rel(fonts / f"Inter-{w}.ttf")}"); font-weight: {n}; }}\n'
        for w, n in (("Regular", 400), ("Medium", 500), ("SemiBold", 600), ("Bold", 700)))
    contrast = "".join(
        f"<tr><td>{html.escape(name)}</td><td><span class='mini' style='background:{bg};color:{fg}'>Aa</span></td>"
        f"<td><code>{fg}</code> on <code>{bg}</code></td><td>{s.contrast_ratio(fg, bg):.1f} : 1</td></tr>"
        for name, fg, bg in s.CONTRAST_PAIRS)
    nav = "".join(
        f'<div class="nav {"sel" if t == "Events" else ""}">{svg(i, s.SELECT_TEXT if t == "Events" else s.TEXT)}'
        f'<span>{html.escape(t)}</span>'
        + (f'<b class="badge" style="background:{s.TODAY_EVENT}">1</b>' if t == "Events" else "")
        + (f'<b class="badge" style="background:{s.OVERDUE}">2</b>' if t == "Tasks" else "") + "</div>"
        for t, i in VIEW_ICONS.items())
    icon_names = ["calendar-days", "calendar-clock", "list-checks", "sticky-note", "history", "trash-2", "settings", "plus",
                  "pencil", "refresh-cw", "send", "check", "circle-check", "undo-2", "rotate-ccw", "x",
                  "arrow-left-right", "wifi-off", "triangle-alert"]
    icon_grid = "".join(f'<div class="ic">{svg(n, s.TEXT, 24)}<code>{n}</code></div>' for n in icon_names)
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Home Base Style Guide</title>
<style>
{faces}
:root {{ --base:{s.BASE}; --surface:{s.SURFACE}; --raised:{s.RAISED}; --raised-hover:{s.RAISED_HOVER};
  --divider:{s.DIVIDER}; --text:{s.TEXT}; --muted:{s.MUTED}; --past:{s.PAST}; --select:{s.SELECT};
  --select-text:{s.SELECT_TEXT}; --select-tag:{s.SELECT_TAG}; --accent:{s.ACCENT}; --red:{s.OVERDUE};
  --green:{s.TODAY_EVENT}; --blue:{s.HIGH}; --lav:{s.REM}; --r-card:{s.R_CARD}px; --r-ctl:{s.R_CONTROL}px; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--base); color: var(--text); font: 400 17px/1.45 "Inter", "Segoe UI", sans-serif; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 32px 16px 64px; }}
h1 {{ font-size: 29px; margin: 0 0 4px; font-weight: 700; }}
h2 {{ font-size: 21px; margin: 0 0 4px; font-weight: 600; }}
p.lede, section > p {{ color: var(--muted); margin: 0 0 16px; max-width: 780px; }}
section {{ background: var(--surface); border-radius: var(--r-card); padding: 24px; margin: 0 0 16px; }}
code {{ font: 13px Consolas, monospace; color: var(--muted); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }}
.sw {{ display: flex; flex-direction: column; gap: 2px; font-size: 14px; }}
.sw span {{ color: var(--muted); }}
.chip {{ height: 64px; border-radius: var(--r-ctl); display: grid; place-items: center; font-weight: 600;
  font-size: 20px; margin-bottom: 6px; border: 1px solid var(--divider); }}
.layers {{ background: var(--base); border-radius: var(--r-card); padding: 16px; display: flex; gap: 8px; }}
.layers .l2 {{ background: var(--surface); border-radius: var(--r-card); padding: 16px; flex: 1; }}
.layers .l3 {{ background: var(--raised); border-radius: var(--r-card); padding: 16px; margin-top: 8px; }}
.type div {{ display: flex; align-items: baseline; gap: 16px; padding: 6px 0; }}
.type code {{ width: 190px; flex: none; }}
.space {{ display: flex; align-items: flex-end; gap: 24px; flex-wrap: wrap; }}
.space div {{ display: flex; flex-direction: column; align-items: center; gap: 6px; font-size: 14px; }}
.space i {{ display: block; background: var(--accent); border-radius: 2px; }}
.radii {{ display: flex; gap: 24px; flex-wrap: wrap; }}
.radii div {{ width: 120px; height: 72px; background: var(--raised); display: grid; place-items: center;
  font-size: 14px; color: var(--muted); }}
.btns {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
.btn {{ font: 600 17px "Inter", sans-serif; border: none; border-radius: var(--r-ctl); padding: 8px 16px;
  display: inline-flex; gap: 6px; align-items: center; background: var(--raised); color: var(--text); }}
.btn.primary {{ background: var(--text); color: var(--base); }}
.btn.danger {{ color: var(--red); }}
.btn.danger.hover {{ background: var(--red); color: var(--base); }}
.btn.hover {{ background: var(--raised-hover); }}
.btn.icon {{ background: transparent; padding: 8px; }}
.btn.icon.hover {{ background: var(--raised-hover); }}
.btn.off {{ color: {s.DISABLED}; }}
.fields {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }}
.fld label {{ display: block; color: var(--muted); font-weight: 600; margin-bottom: 6px; }}
.fld div {{ background: var(--raised-hover); border-radius: var(--r-ctl); padding: 8px 10px;
  border: 2px solid transparent; }}
.fld div.focus {{ border-color: var(--accent); }}
.fld div.ph {{ color: var(--past); }}
.tag {{ display: inline-block; font: 600 14px "Inter", sans-serif; border-radius: 999px; padding: 2px 10px;
  margin: 2px 4px 2px 0; white-space: nowrap; }}
.rows {{ display: flex; flex-direction: column; gap: 4px; }}
.hdr {{ font-size: 21px; font-weight: 600; margin: 8px 0 4px 4px; }}
.row {{ display: flex; align-items: center; gap: 12px; padding: 10px 8px; border-radius: var(--r-ctl);
  min-height: 60px; }}
.row.hover {{ background: var(--raised); }}
.row.sel {{ background: var(--select); color: var(--select-text); }}
.row.sel .txt span {{ color: var(--select-text); }}
.row.sel .tag {{ background: var(--select-tag) !important; color: var(--select-text) !important; }}
.bar {{ width: 4px; align-self: stretch; border-radius: 2px; margin: 4px 0; flex: none; }}
.tick {{ width: 22px; height: 22px; border-radius: 50%; border: 1.5px solid var(--muted); flex: none; }}
.txt {{ display: flex; flex-direction: column; min-width: 0; flex: 1; }}
.txt b {{ font-weight: 600; }}
.txt span {{ color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.tags {{ display: flex; flex-wrap: wrap; justify-content: flex-end; }}
.act {{ width: 34px; height: 34px; display: grid; place-items: center; border-radius: var(--r-ctl); flex: none; }}
.toast {{ background: var(--raised-hover); border-radius: var(--r-ctl); padding: 10px 10px 10px 16px;
  display: inline-flex; gap: 12px; align-items: center; margin: 0 8px 8px 0; }}
.banner {{ background: {s.BANNER_BG}; color: {s.BANNER_TEXT}; border-radius: var(--r-ctl); padding: 8px 16px;
  font-weight: 600; display: flex; gap: 10px; align-items: center; }}
.icons {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }}
.ic {{ background: var(--raised); border-radius: var(--r-ctl); padding: 12px; display: flex; flex-direction: column;
  align-items: center; gap: 6px; }}
.ic code {{ font-size: 12px; }}
.side {{ background: var(--surface); width: {s.SIDEBAR_WIDTH}px; border-radius: var(--r-card); padding: 8px;
  border: 1px solid var(--divider); }}
.nav {{ display: flex; gap: 12px; align-items: center; padding: 10px 12px; border-radius: var(--r-ctl);
  font-weight: 500; }}
.nav.sel {{ background: var(--select); color: var(--select-text); font-weight: 600; }}
.badge {{ margin-left: auto; color: var(--base); border-radius: 999px; min-width: 24px; height: 24px;
  display: grid; place-items: center; font-size: 14px; padding: 0 7px; }}
.member {{ background: var(--raised); border-radius: var(--r-card); padding: 8px; display: flex; gap: 8px;
  align-items: center; margin-top: 12px; }}
.avatar {{ width: {s.AVATAR}px; height: {s.AVATAR}px; border-radius: 50%; background: var(--accent);
  color: var(--base); display: grid; place-items: center; font-weight: 700; }}
.split {{ display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }}
.appicon {{ display: flex; gap: 24px; align-items: flex-end; }}
table {{ border-collapse: collapse; width: 100%; font-size: 15px; }}
td {{ padding: 6px 8px; border-top: 1px solid var(--divider); }}
.mini {{ display: inline-block; padding: 2px 8px; border-radius: 6px; font-weight: 600; }}
@media (max-width: 640px) {{ .type code {{ width: auto; }} .type div {{ flex-direction: column; gap: 0; }}
  .side {{ width: 100%; }} }}
</style>
</head>
<body>
<main>
<h1>Home Base style guide</h1>
<p class="lede">WO-FS-004 Part A. Every value on this page is read from <code>src/family_scheduler/app/style.py</code>,
the one place the app's look is defined. The look takes the feel of modern chat and productivity apps (layered dark
surfaces, rounded shapes, generous spacing, clean icons) but nothing of theirs: the colours, icon, layout and amber
accent are Home Base's own.</p>

<section><h2>1. Three dark layers</h2>
<p>Areas are told apart by shade, not lines. The window is black; the sidebar and content sit on it as rounded
panels; cards, dialogs, menus, fields and hovered rows are one step lighter.</p>
<div class="layers"><div class="l2"><b>Surface</b> <code>{s.SURFACE}</code> (sidebar, content)
<div class="l3"><b>Raised</b> <code>{s.RAISED}</code> (cards, dialogs, fields, hovered rows)</div></div>
<div class="l2" style="flex:.5"><b>Base</b> <code>{s.BASE}</code> shows in the 8 px gutters</div></div>
<div class="grid" style="margin-top:16px">
{swatch("Base", s.BASE, "window")}{swatch("Surface", s.SURFACE, "sidebar, content panel")}
{swatch("Raised", s.RAISED, "cards, dialogs, menus")}{swatch("Raised, hover", s.RAISED_HOVER, "fields, buttons, tags")}
{swatch("Divider", s.DIVIDER, "the rare faint line")}
</div></section>

<section><h2>2. Text, selection and meaning colours</h2>
<p>Kept from WO-FS-003. Light green is only ever the selection. Amber is the app's own accent, used for focus rings,
the avatar and the app icon, never for meaning.</p>
<div class="grid">
{swatch("Text", s.TEXT, "main text", s.BASE)}{swatch("Muted", s.MUTED, "second lines, hints", s.BASE)}
{swatch("Past", s.PAST, "past events, done tasks", s.BASE)}{swatch("Selection", s.SELECT, "selected row only", s.BASE)}
{swatch("Today's events", s.TODAY_EVENT, "green", s.BASE)}{swatch("Overdue, errors", s.OVERDUE, "red", s.BASE)}
{swatch("High priority", s.HIGH, "blue", s.BASE)}{swatch("Reminders", s.REM, "lavender", s.BASE)}
{swatch("Accent", s.ACCENT, "focus, avatar, app icon", s.BASE)}
</div></section>

<section><h2>3. Type</h2>
<p>Inter (bundled, SIL Open Font License), falling back to Segoe UI. Sizes are the WO-FS-003 sizes; Qt points are
shown here at their on-screen size.</p>
<div class="type">
<div><code>View title · {s.TITLE_PT} pt · 600</code><span style="font-size:29px;font-weight:600">Events</span></div>
<div><code>Today section · {s.TODAY_SECTION_PT} pt · 700</code><span style="font-size:24px;font-weight:700;color:var(--green)">Today's events (2)</span></div>
<div><code>Section, month · {s.SECTION_PT} pt · 600</code><span style="font-size:21px;font-weight:600">November 2026 (5)</span></div>
<div><code>Row title · {s.BASE_PT} pt · 600</code><span style="font-weight:600">Parent-teacher interview</span></div>
<div><code>Body, second line · {s.BASE_PT} pt</code><span style="color:var(--muted)">Wed Nov 4 · 4:30 pm–5:00 pm · Riverdale Public School</span></div>
<div><code>Tags, badges · {s.SMALL_PT} pt · 600</code><span class="tag" style="background:var(--raised-hover);color:{s.TAG_TEXT}">Every week</span></div>
</div></section>

<section><h2>4. Spacing and corners</h2>
<p>One 8 px grid. Corners: cards and dialogs {s.R_CARD} px, buttons and fields {s.R_CONTROL} px, tags fully round.</p>
<div class="space">{"".join(f'<div><i style="width:{v}px;height:{v}px"></i>{v} px</div>' for v in (s.S1, s.S2, s.S3, s.S4))}</div>
<div class="radii" style="margin-top:24px"><div style="border-radius:{s.R_CARD}px">card {s.R_CARD} px</div>
<div style="border-radius:{s.R_CONTROL}px">control {s.R_CONTROL} px</div>
<div style="border-radius:999px;height:32px;align-self:center">tag: round</div></div></section>

<section><h2>5. Buttons</h2>
<p>Primary filled white; secondary a subtle dark fill; danger red text that fills red on hover; icon buttons with no
border until hovered, each with a tooltip.</p>
<div class="btns">
<span class="btn primary">{svg("plus", s.BASE)} Add event</span><span class="btn">Cancel</span>
<span class="btn hover">Secondary, hovered</span><span class="btn danger">Remove</span>
<span class="btn danger hover">Remove, hovered</span><span class="btn off">Disabled</span>
<span class="btn icon">{svg("refresh-cw")}</span><span class="btn icon hover">{svg("refresh-cw")}</span>
<span class="btn icon">{svg("trash-2", s.OVERDUE)}</span>
</div></section>

<section><h2>6. Fields</h2>
<p>Labels above, a raised fill, no heavy border, and an amber focus ring while typing.</p>
<div class="fields">
<div class="fld"><label>Title</label><div>Haircut</div></div>
<div class="fld"><label>Location (focused)</label><div class="focus">Main St Barbers</div></div>
<div class="fld"><label>Repeats</label><div>Every week <span style="float:right;color:var(--muted)">⌄</span></div></div>
<div class="fld"><label>Message</label><div class="ph">Something everyone should know</div></div>
</div></section>

<section><h2>7. Tags</h2>
<p>Short facts as round tags, tinted with the meaning colour.</p>
{tag("Every week", s.TAG_TEXT)}{tag("✓ 2  ✗ 0  ? 1", s.TAG_TEXT)}{tag("Medium", s.TAG_TEXT)}{tag("High", s.HIGH)}
{tag("Not sent to Google", s.OVERDUE)}{tag("Overlaps Dentist", s.OVERDUE)}{tag("Today", s.TODAY_EVENT)}
{tag("Reminder", s.REM)}</section>

<section><h2>8. Rows</h2>
<p>Lists are rows, not table cells: a coloured accent, a main line, a second line and tags. Hovered rows lighten
and show Edit and Remove; the selected row is light green with black text. Tasks get a round tick.</p>
<div class="hdr">November 2026 (5)</div>
<div class="rows">
{row("Sunday dinner", "Sun Nov 1 · 6:00 pm–8:00 pm · 14 Elm Ave", s.EVENT_ACCENT, tags=tag("Every week", s.TAG_TEXT) + tag("✓ 1  ✗ 0  ? 2", s.TAG_TEXT))}
{row("Dentist", "Wed Nov 4 · 4:00 pm–5:00 pm · Smile Dental", s.EVENT_ACCENT, tags=tag("✓ 1  ✗ 1  ? 1", s.TAG_TEXT), state="sel", actions=True)}
{row("Swim practice", "Thu Nov 5 · 5:30 pm–6:30 pm · Pan Am Centre", s.EVENT_ACCENT, tags=tag("Every week", s.TAG_TEXT), state="hover", actions=True)}
{row("Piano recital", "Sun Nov 8 · 7:00 pm–8:30 pm · Royal Conservatory", s.OVERDUE, title_colour=s.OVERDUE, tags=tag("Not sent to Google", s.OVERDUE))}
{row("Renew passport photos", "Due Sun Oct 25", s.OVERDUE, title_colour=s.OVERDUE, tags=tag("High", s.HIGH), tick=True)}
{row("Sam is allergic to tree nuts.", "— alex@", s.REM)}
</div></section>

<section><h2>9. Sidebar, toasts and banner</h2>
<p>Sidebar: icon and label for each view, count badges (overdue tasks in red, today's events in green, hidden at
zero) and the current member at the bottom. Confirmations are toasts in the corner that fade out; the Clear all
countdown uses the same toast.</p>
<div class="split"><div class="side">{nav}
<div class="member"><span class="avatar">A</span><div style="flex:1;line-height:1.2"><b>alex@</b><br>
<span style="color:var(--muted)">example.com</span></div>{svg("arrow-left-right")}</div></div>
<div style="flex:1;min-width:260px">
<div class="toast">{svg("circle-check", s.TODAY_EVENT)} Digest sent to alex@example.com</div>
<div class="toast">{svg("undo-2")} 6 tasks cleared. 7 s to undo. <span class="btn primary">Undo · 7 s</span></div>
<div class="toast">{svg("triangle-alert", s.OVERDUE)} Couldn't save: the end time must be after the start time.</div>
<div class="banner" style="margin-top:8px">{svg("wifi-off", s.BANNER_TEXT)} Can't reach the home hub. Make sure the hub PC is on.</div>
</div></div></section>

<section><h2>10. Icons and app icon</h2>
<p>Lucide 0.460.0, ISC licence, bundled with the app and drawn in white or a meaning colour. The app icon is
original: a house whose windows are the days of a calendar, on the app's amber.</p>
<div class="icons">{icon_grid}</div>
<div class="appicon" style="margin-top:24px">
{"".join(f'<img src="Part A/app-icon-{n}.png" width="{n if n < 128 else 128}" height="{n if n < 128 else 128}" alt="App icon, {n} px">' for n in (256, 64, 32, 16))}
</div></section>

<section><h2>11. Contrast (BK-9)</h2>
<p>Every text colour against its background. All pass WCAG AA (4.5 : 1); the tests fail if one drops below.</p>
<table>{contrast}</table></section>
</main>
</body>
</html>
"""
    OUT.write_text(page, encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
