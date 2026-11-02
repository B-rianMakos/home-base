"""Look and formatting shared by the app's screens.

Every colour, font size, corner radius and spacing step in the app is defined here, once (VS-1). WO-FS-004 builds
the look from three dark layers (base, surface, raised) on an 8 px grid with rounded corners, and keeps the
WO-FS-003 rules: black base, white text, light green only for the selection, and the meaning colours (green
today's events, red overdue, blue high priority, lavender reminders). CONTRAST_PAIRS lists every text colour against
its background for the WCAG AA check (BK-9).
"""

from __future__ import annotations

import ctypes
import re
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QWidget

from family_scheduler.app.months import MONTH_NAMES
from family_scheduler.digest import fmt_day, fmt_time
from family_scheduler.palette import PRIORITY_COLOURS
from family_scheduler.recurrence import ordinal
from family_scheduler.timeutil import parse_iso, to_local

ASSETS = Path(__file__).resolve().parent / "assets"

# --- layers (MS-1) ---------------------------------------------------------------------------------

BASE = "#000000"         # the window itself, showing between the panels
SURFACE = "#121212"      # sidebar and content panel
RAISED = "#1E1E1E"       # cards, dialogs, menus, fields, hovered rows
RAISED_HOVER = "#2A2A2A"  # hovered buttons and fields on the raised layer, neutral tags
PRIMARY_HOVER = "#E6E6E6"
CARD_BUTTON_HOVER = "#363636"  # a secondary button on a card, hovered
CARD_PRESSED = "#424242"  # WO-FS-013 WC-3: a car card being pressed
DIVIDER = "#262626"      # the rare faint line (MS-2)
BACKDROP = QColor(0, 0, 0, 170)  # dims the window behind a dialog (MS-5)

# --- text --------------------------------------------------------------------------------------------

TEXT = "#FFFFFF"
MUTED = "#BFBFBF"        # second lines, hints, column headers
PAST = "#A6A6A6"         # past events kept for 30 days (EM-4), done tasks
DISABLED = "#999999"
TAG_TEXT = "#E6E6E6"     # text on neutral tags

# --- selection, accents and meaning colours ----------------------------------------------------------

SELECT = "#A8E6A3"        # selected row and sidebar item: light green, used for nothing else (SL-5)
SELECT_TEXT = "#000000"   # all text on a selected row (SL-2)
SELECT_TAG = "#8DC489"    # tags on a selected row: a darker shade of the selection
ACCENT = "#FFB547"        # Home Base's own amber: focus rings and the app icon
TODAY_EVENT = "#4CD964"   # today's events: strong green, unlike the pale selection (TD-3, TD-9)
OVERDUE = "#FF5C5C"       # overdue tasks, errors, danger (TD-4)
HIGH = PRIORITY_COLOURS["high"]      # high priority (TD-5)
MEDIUM = PRIORITY_COLOURS["medium"]  # WO-FS-013 TP-a: medium priority (yellow)
LOW = PRIORITY_COLOURS["low"]        # low priority (grey-green)
REM = "#C3A6FF"           # reminders
EVENT_ACCENT = "#8C8C8C"  # accent bar of an ordinary event or task
WARN = OVERDUE
BANNER_BG = "#3A1212"
BANNER_TEXT = "#FF8A8A"
TINT = {  # dark tag backgrounds for each meaning colour (LC-3)
    OVERDUE: "#3A1717", HIGH: "#122A45", TODAY_EVENT: "#0F3019", REM: "#2A2142", TAG_TEXT: RAISED_HOVER,
    MEDIUM: "#2E2A10", LOW: "#1B2A20",  # WO-FS-013 TP-d
}
SCROLL = "#5C5C5C"
AM = "#FFDD55"            # WO-FS-013 EC-3: am in the time pickers, a warm morning yellow (clear of the amber accent)
PM = "#7F8CFF"            # pm, an evening indigo (clear of the reminder lavender and the High blue)
WEEKEND = "#9CCBFF"       # WO-FS-008 DP-1: Saturday and Sunday in the date pickers; red stays for overdue and errors

# --- month colours (WO-FS-009 section 5): only on month labels and headers, never on blocks ------------------------
# Northern-hemisphere seasons; within each season light to dark. Tuned from the work order's suggestions so that each
# one is at least 12 (CIEDE2000) from every meaning colour and at least 11 from the months beside it (MC-2, MC-3).

MONTH_COLOURS = {
    1: "#3D63D1",   # January: mid blue
    2: "#1F3A8A",   # February: deep blue
    3: "#D9E24A",   # March: fresh yellow-green
    4: "#6FA83A",   # April: leaf green
    5: "#2E8B57",   # May: deep green
    6: "#FFF3A0",   # June: pale yellow
    7: "#FFE433",   # July: sunny yellow
    8: "#C99700",   # August: golden yellow
    9: "#EE7E1E",   # September: light orange
    10: "#C9471A",  # October: burnt orange
    11: "#8C1C3A",  # November: burgundy
    12: "#C6ECFA",  # December: light ice blue
}

# --- cars and groceries (WO-FS-010 sections 9 and 10) ----------------------------------------------------------
# CB-c: the Cars today bar reads like a traffic light. The green is calm, clearly unlike today's-events green and the
# light green selection; each colour carries black or white text at 4.5 : 1 or more (CB-i).

CAR_FREE = "#3E9E6A"      # no car in use
CAR_SOME = "#E8C547"      # some cars in use, at least one free
CAR_ALL = "#D9363E"       # every car in use, or a car double-booked
CAR_INK = {CAR_FREE: BASE, CAR_SOME: BASE, CAR_ALL: TEXT}
GROCERY = "#4FC3B7"       # GR-11: the groceries badge
FOOD = TODAY_EVENT        # WO-FS-011 GG-1, GG-2: the Food group's title and accent bars (green)
ITEMS = HIGH              # the Items group's (blue)

# --- the Today week calendar (WO-FS-005 section 3) ---------------------------------------------------------

EVENT_BLOCK = TODAY_EVENT  # WB-1: events are green rounded blocks
TASK_BLOCK = HIGH          # WB-2: tasks are blue rounded blocks
BLOCK_TEXT = BASE          # WB-3: black text on both
BLOCK_SELECTED = TEXT      # WU-a: a white outline, the one exception to the light-green selection rule
TODAY_COLUMN = "#181818"   # WV-1: today's column, a shade lighter than the surface

# --- spacing and corners (MS-3, MS-4) --------------------------------------------------------------------

S1, S2, S3, S4 = 8, 16, 24, 32   # the 8 px grid
TOAST_WIDTH = 40 * S1           # narrowest toast
HALF = S1 // 2                   # 4 px, for hairline gaps inside a row
R_CARD = 12
R_CONTROL = 8
ACCENT_BAR = 4                   # width of a row's coloured accent (LC-2)
SIDEBAR_WIDTH = 240
WINDOW_MIN = (1024, 700)         # PC-6: nothing fixed is cut off at this size
WINDOW_START = (1180, 760)
ROW_HEIGHT = 64                  # two-line rows
HEADER_HEIGHT = 44
ICON = 18                        # icon size in buttons and rows
AVATAR = 36
TAG_HEIGHT = 24
TEXT_BOX_HEIGHT = 5 * 24          # the reminder message box
DAY_HEADER = 52                  # day names and dates
STACK_BLOCK = 9 * S1             # SB-3: every week block is this tall; WO-FS-008 FX-3: room for two title lines
MONTH_BAR = 22                   # MV-3: an event or task bar in a month cell
WEEKDAY_ROW = 24                 # the Mon … Sun row above the month grid
TASK_LIST_ROWS = 6               # rows shown at once in the "+N more" list
MEMBER_LIST_HEIGHT = 3 * ROW_HEIGHT  # the family list in Settings, before it scrolls
OUTLINE = 2                      # WU-a, WB-6: selected and overdue outlines
SCROLLBAR = 10                   # the thin scroll bar's width (CF-5), kept free above the calendar
REPLIES_PANEL = 40 * 8           # WT-2: the Replies side panel
WHEEL_ROW = 28                   # WO-FS-008 TM-2: one value on a time wheel
WHEEL_WIDTH = 7 * S1             # an hour or minute wheel
WHEEL_ROWS = 3                   # the chosen value in the middle, one dimmed value above and below
PICKER_CELL = 5 * S1             # a day in the date picker
MONTH_BAND = HALF                # MC-5 to MC-7: a month's underline, and the band across the top of the month grid
CAR_BAR = 5 * S1                 # CB-b: the Cars today bar's height
CAR_BAR_LABELS = 6               # CB-b: an hour label every 6 hours (12 am, 6 am, 12 pm, 6 pm, 12 am)
CAR_POPUP_WIDTH = 50 * S1        # the Which car? pop-up
CAR_CARDS_SHOWN = 4              # WO-FS-013 WC-10: more cars than this scroll inside Which car?
PILL_RADIUS = 10                 # the Free / In use pill on a car card
HIDDEN_POPUP_WIDTH = 65 * S1     # the Hidden list: Show again and Delete from Home Base on each row (WO-FS-013)
EVENT_POPUP_WIDTH = 75 * S1      # Add and Edit event: the sender line fits on one line (WO-FS-013 EC-7)
SWITCH = (36, 20)                # WO-FS-013 EC-2: the All day switch's track
CHIP_RADIUS = 10                 # EC-6: invitee chips are pills
TICK = 22                        # the round tick on a task row (LC-8)
EMPTY_ICON = 40                  # the icon of an empty list (LC-7)
TAG_MAX_WIDTH = 28 * S1           # longer tags are shortened, full text on hover

# --- fonts (IF-3) -------------------------------------------------------------------------------------

FONT_FAMILY = "Inter"          # bundled, SIL Open Font License
FONT_FALLBACK = "Segoe UI"
BASE_PT = 13
SMALL_PT = 11                  # tags and badges only; never body text
TITLE_PT = 22
SECTION_PT = 16
TODAY_SECTION_PT = 18

ANIMATION_MS = 150             # CF-4
SYNC_ON_OPEN_MS = 1500           # WO-FS-010 GY-2: the one Sync with Google once the app has opened
GROCERY_UNDO_S = 8             # GR-4, GR-5: seconds the Undo button stays
DIMMED = 0.6                   # WO-FS-011 GO-4: how far a disabled action fades into its background
MINUTE_MS = 60_000             # how often Today checks whether it is a new day (WO-FS-007 TP-5)

CONTRAST_PAIRS: list[tuple[str, str, str]] = [
    ("Main text on the panels", TEXT, SURFACE),
    ("Main text on cards, dialogs and fields", TEXT, RAISED),
    ("Main text on a hovered row or button", TEXT, RAISED_HOVER),
    ("Second lines and hints", MUTED, SURFACE),
    ("Second lines on cards and hovered rows", MUTED, RAISED),
    ("Hints on a hovered button", MUTED, RAISED_HOVER),
    ("Button on a card, hovered", TEXT, CARD_BUTTON_HOVER),
    ("Past events, done tasks", PAST, SURFACE),
    ("Past events, hovered", PAST, RAISED),
    ("Disabled text", DISABLED, RAISED),
    ("Neutral tags", TAG_TEXT, RAISED_HOVER),
    ("Selected row and sidebar item", SELECT_TEXT, SELECT),
    ("Tags on a selected row", SELECT_TEXT, SELECT_TAG),
    ("Primary button", BASE, TEXT),
    ("Danger button, hovered", BASE, OVERDUE),
    ("Today's events (green)", TODAY_EVENT, SURFACE),
    ("Today's events, hovered", TODAY_EVENT, RAISED),
    ("Green tags and badges", TODAY_EVENT, TINT[TODAY_EVENT]),
    ("Green badge", BASE, TODAY_EVENT),
    ("Overdue and errors (red)", OVERDUE, SURFACE),
    ("Overdue, hovered", OVERDUE, RAISED),
    ("Red text on a hovered button", OVERDUE, RAISED_HOVER),
    ("Red tags", OVERDUE, TINT[OVERDUE]),
    ("Red badge", BASE, OVERDUE),
    ("High priority (blue)", HIGH, SURFACE),
    ("High priority, hovered", HIGH, RAISED),
    ("Blue tags", HIGH, TINT[HIGH]),
    ("Reminders (lavender)", REM, SURFACE),
    ("Lavender tags", REM, TINT[REM]),
    ("Can't reach the home hub banner", BANNER_TEXT, BANNER_BG),
    ("Avatar letter", BASE, ACCENT),
    ("Event blocks: black on green", BLOCK_TEXT, EVENT_BLOCK),
    ("Task blocks: black on blue", BLOCK_TEXT, TASK_BLOCK),
    ("Past events, faded", EVENT_BLOCK, TINT[EVENT_BLOCK]),
    ("Done tasks, faded", TASK_BLOCK, TINT[TASK_BLOCK]),
    ("High marker on a task block", HIGH, BASE),
    ("Overdue and Not sent markers", OVERDUE, BASE),
    ("Day names over today's column", TEXT, TODAY_COLUMN),
    ("Weekday names and dates over today", MUTED, TODAY_COLUMN),
    ("Days of the next or last month (month view)", PAST, SURFACE),
    ("Today's date in its circle", BASE, TEXT),
    ("Date picker: weekdays", TEXT, RAISED),
    ("Date picker: Saturday and Sunday (light blue)", WEEKEND, RAISED),
    ("Date picker: days of another month", PAST, RAISED),
    ("Date picker: the selected day", SELECT_TEXT, SELECT),
    ("Time wheels: the chosen value", TEXT, RAISED_HOVER),
    ("Time wheels: values above and below", PAST, RAISED),
    ("All day label on an event block", BLOCK_TEXT, EVENT_BLOCK),
    ("Month labels over their underline or band (white on the panel)", TEXT, SURFACE),
    ("Setup guide: links (amber) on a card", ACCENT, RAISED),
    ("Cars today bar: none in use (black on green)", BASE, CAR_FREE),
    ("Cars today bar: some in use (black on yellow)", BASE, CAR_SOME),
    ("Cars today bar: all in use (white on red)", TEXT, CAR_ALL),
    ("Cars today: hour labels", MUTED, SURFACE),
    ("Car tag and Groceries accent text (teal)", GROCERY, SURFACE),
    ("Groceries: Food title (green)", FOOD, SURFACE),
    ("Groceries: Items title (blue)", ITEMS, SURFACE),
    ("Tasks: High priority header (blue)", HIGH, SURFACE),
    ("Tasks: Medium and Low priority headers", TEXT, SURFACE),
    ("Due soon tag (blue on its tint)", HIGH, TINT[HIGH]),
    ("Medium tag (yellow on its tint)", MEDIUM, TINT[MEDIUM]),
    ("Low tag (grey-green on its tint)", LOW, TINT[LOW]),
    ("Priority switch: High chosen (black on blue)", BASE, HIGH),
    ("Priority switch: Medium chosen (black on yellow)", BASE, MEDIUM),
    ("Priority switch: Low chosen (black on grey-green)", BASE, LOW),
    ("Priority switch: High not chosen (blue on the card)", HIGH, RAISED),
    ("Priority switch: Medium not chosen (yellow on the card)", MEDIUM, RAISED),
    ("Priority switch: Low not chosen (grey-green on the card)", LOW, RAISED),
    ("Due within 7 days line (blue on the card)", HIGH, RAISED),
    ("am chosen (black on morning yellow)", BASE, AM),
    ("pm chosen (black on evening indigo)", BASE, PM),
    ("am not chosen, and am in the summary line (on the card)", AM, RAISED),
    ("pm not chosen, and pm in the summary line (on the card)", PM, RAISED),
    ("Invitee chip, invited (black on green)", BASE, TODAY_EVENT),
    ("Invitee chip, not invited", TEXT, RAISED_HOVER),
    ("Section labels in the event pop-up", TEXT, RAISED),
    ("Car card: the car's name", TEXT, RAISED_HOVER),
    ("Car card, hovered", TEXT, CARD_BUTTON_HOVER),
    ("Car card, pressed", TEXT, CARD_PRESSED),
    ("Car card: what it's used for", MUTED, RAISED_HOVER),
    ("Car card, hovered: what it's used for", MUTED, CARD_BUTTON_HOVER),
    ("Free pill (black on green)", BASE, CAR_FREE),
    ("In use pill (black on yellow)", BASE, CAR_SOME),
    ("Will be double-booked (red on a car card)", OVERDUE, RAISED_HOVER),
    ("Trash: Delete forever icon, hovered (red on the hover shade)", OVERDUE, RAISED_HOVER),
    ("Trash: Delete ticked and Clear all (red on the panel)", OVERDUE, SURFACE),
    ("Digest: priority pills (black on blue, yellow, grey-green)", BASE, HIGH),
    ("Due soon tag on a selected row", SELECT_TEXT, SELECT_TAG),
    ("Setup guide: Back link", MUTED, SURFACE),
]


def month_colour(month: int) -> str:
    """MC-1: the colour of a month, 1 to 12."""
    return MONTH_COLOURS[month]


def month_ink(month: int) -> str:
    """MC-8: black or white text on a month's colour, whichever contrasts more (both are checked to pass 4.5 : 1)."""
    colour = MONTH_COLOURS[month]
    return BASE if contrast_ratio(BASE, colour) >= contrast_ratio(TEXT, colour) else TEXT


def show_month(widget: QWidget, month: int) -> None:
    """MC-5 to MC-7: underline a label or button in a month's colour (the rules are in MONTH_RULES); its text stays
    white on the dark panel."""
    widget.setProperty("month", month)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def blend(start: str, end: str, amount: float) -> str:
    """A colour part of the way from one colour to another, for fades (CF-4)."""
    a = [int(start.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(end.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02X}" for x, y in zip(a, b, strict=True))


def colour_am_pm(text: str) -> str:
    """EC-3: "4:15 pm to 5:00 pm (45 min)" with each am and pm in its own colour (rich text for a QLabel)."""
    return re.sub(r"\b(am|pm)\b", lambda m: f'<span style="color:{AM if m[1] == "am" else PM}">{m[1]}</span>',
                  escape(text))


DIALOG_ACCENTS = {"high": HIGH, "medium": MEDIUM, "low": LOW, "event": TODAY_EVENT}  # WO-FS-013 TP-e, EC-1, WC-6


def _accent_rules() -> str:
    return "\n".join(f'#DialogCard[accent="{key}"] {{ border-top: {HALF}px solid {colour}; }}'
                     for key, colour in DIALOG_ACCENTS.items())


def font(pt: int = BASE_PT, bold: bool = False, weight: QFont.Weight | None = None) -> QFont:
    f = QFont(FONT_FAMILY, pt)
    f.setFamilies([FONT_FAMILY, FONT_FALLBACK])
    f.setWeight(weight if weight is not None else (QFont.Weight.Bold if bold else QFont.Weight.Normal))
    return f


STYLESHEET = f"""
QWidget {{ font-family: "{FONT_FAMILY}", "{FONT_FALLBACK}"; font-size: {BASE_PT}pt; color: {TEXT}; }}
QMainWindow, #Base {{ background: {BASE}; }}
QDialog, QMessageBox {{ background: {RAISED}; }}
#Panel, #Sidebar {{ background: {SURFACE}; border-radius: {R_CARD}px; }}
#CardHost {{ background: transparent; }}
#DialogCard {{ background: {RAISED}; border-radius: {R_CARD}px; border: 1px solid {DIVIDER}; }}
{_accent_rules()}
#Card {{ background: {RAISED}; border-radius: {R_CARD}px; }}
#MemberDomain {{ color: {MUTED}; }}
QLabel {{ background: transparent; }}
#ViewTitle {{ font-size: {TITLE_PT}pt; font-weight: 600; }}
#DialogTitle {{ font-size: {SECTION_PT}pt; font-weight: 600; }}
#Section {{ font-size: {SECTION_PT}pt; font-weight: 600; }}
#Muted, QLabel[muted="true"] {{ color: {MUTED}; }}
#FieldLabel {{ color: {MUTED}; font-weight: 600; }}
#MemberEmail {{ font-weight: 600; }}
#Banner {{ background: {BANNER_BG}; color: {BANNER_TEXT}; border-radius: {R_CONTROL}px; padding: {S1}px {S2}px;
           font-weight: 600; }}
#Toast {{ background: {RAISED_HOVER}; border-radius: {R_CONTROL}px; }}
#Toast QLabel {{ background: transparent; }}
QPushButton {{ background: {RAISED}; color: {TEXT}; border: none; border-radius: {R_CONTROL}px;
           padding: {S1}px {S2}px; font-weight: 600; }}
QPushButton:hover {{ background: {RAISED_HOVER}; }}
QPushButton:focus {{ outline: none; }}
QPushButton:disabled {{ color: {DISABLED}; }}
QPushButton[primary="true"] {{ background: {TEXT}; color: {BASE}; }}
QPushButton[badged="true"] {{ padding-right: {S4 + S1}px; }}
#Segment {{ background: {RAISED}; border-radius: {R_CONTROL}px; }}
#Segment QPushButton {{ background: transparent; padding: {HALF + 2}px {S2}px; }}
#Segment QPushButton:checked {{ background: {TEXT}; color: {BASE}; }}
#PrioritySwitch QPushButton {{ background: transparent; border: 2px solid; padding: {HALF}px {S2}px; }}
#PrioritySwitch QPushButton[key="high"] {{ color: {HIGH}; border-color: {HIGH}; }}
#PrioritySwitch QPushButton[key="medium"] {{ color: {MEDIUM}; border-color: {MEDIUM}; }}
#PrioritySwitch QPushButton[key="low"] {{ color: {LOW}; border-color: {LOW}; }}
#PrioritySwitch QPushButton:hover {{ background: {RAISED_HOVER}; }}
#PrioritySwitch QPushButton[key="high"]:checked {{ background: {HIGH}; color: {BASE}; }}
#PrioritySwitch QPushButton[key="medium"]:checked {{ background: {MEDIUM}; color: {BASE}; }}
#PrioritySwitch QPushButton[key="low"]:checked {{ background: {LOW}; color: {BASE}; }}
#PrioritySwitch QPushButton:focus {{ border-color: {ACCENT}; }}
#DueSoonNote {{ color: {HIGH}; }}
#AmPm {{ background: {RAISED_HOVER}; border-radius: {R_CONTROL}px; }}
#AmPm QPushButton {{ background: transparent; padding: {HALF + 2}px {S2}px; }}
#AmPm QPushButton[side="am"] {{ color: {AM}; }}
#AmPm QPushButton[side="pm"] {{ color: {PM}; }}
#AmPm QPushButton[side="am"]:checked {{ background: {AM}; color: {BASE}; }}
#AmPm QPushButton[side="pm"]:checked {{ background: {PM}; color: {BASE}; }}
#FormSection {{ font-weight: 700; color: {TEXT}; }}
#Chip {{ background: {RAISED_HOVER}; border-radius: {CHIP_RADIUS}px; padding: 2px {S1 + HALF}px; text-align: left; }}
#Chip:hover {{ background: {CARD_BUTTON_HOVER}; }}
#Chip:checked {{ background: {TODAY_EVENT}; color: {BASE}; }}
#Chip:focus {{ border: 2px solid {ACCENT}; }}
#SenderNote {{ color: {MUTED}; }}
#PlainScroll, #PlainScroll > QWidget, #PlainPage {{ background: transparent; }}
#TodaySection {{ font-size: {TODAY_SECTION_PT}pt; font-weight: 700; }}
#CarSwatch {{ border-radius: {HALF}px; background: {CAR_FREE}; }}
#CarSwatch[level="some"] {{ background: {CAR_SOME}; }}
#CarSwatch[level="all"] {{ background: {CAR_ALL}; }}
#CarCard {{ background: {RAISED_HOVER}; border: 2px solid transparent; border-radius: {R_CARD}px; padding: 0;
           text-align: left; }}
#CarCard:hover {{ background: {CARD_BUTTON_HOVER}; }}
#CarCard:pressed {{ background: {CARD_PRESSED}; }}
#CarCard[selected="true"] {{ border-color: {SELECT}; }}
#CarCard:focus {{ border-color: {ACCENT}; }}
#CarCard[compact="true"] {{ border-radius: {R_CONTROL}px; }}
#CarCard:disabled {{ background: {RAISED}; }}
#CarName {{ font-weight: 700; }}
#CarDetail {{ color: {MUTED}; }}
#CarClash {{ color: {OVERDUE}; font-weight: 600; }}
#CarPill {{ border-radius: {PILL_RADIUS}px; padding: 2px {S1}px; font-weight: 700; color: {BASE};
           background: {CAR_FREE}; }}
#CarPill[level="busy"] {{ background: {CAR_SOME}; }}
#CarEvent {{ color: {MUTED}; }}
#Status {{ border-radius: {R_CONTROL}px; padding: 2px {S1}px; font-weight: 600; color: {TAG_TEXT};
           background: {RAISED_HOVER}; }}
#Status[ready="yes"] {{ color: {TODAY_EVENT}; background: {TINT[TODAY_EVENT]}; }}
#Status[ready="no"] {{ color: {OVERDUE}; background: {TINT[OVERDUE]}; }}
#StepNumber {{ color: {MUTED}; min-width: {S2}px; }}
#LinkButton {{ background: transparent; color: {MUTED}; padding: {HALF}px 0; }}
#LinkButton:hover {{ color: {TEXT}; }}
#Notice {{ background: {RAISED_HOVER}; border-radius: {R_CONTROL}px; padding: {S1}px {S2}px; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton[danger="true"] {{ color: {OVERDUE}; }}
QPushButton[danger="true"]:hover {{ background: {OVERDUE}; color: {BASE}; }}
QPushButton[primary="true"]:disabled, QPushButton[danger="true"]:disabled {{ background: {RAISED}; color: {DISABLED}; }}
#Card QPushButton {{ background: {RAISED_HOVER}; }}
#Card QPushButton:hover {{ background: {CARD_BUTTON_HOVER}; }}
#Card QPushButton[primary="true"] {{ background: {TEXT}; color: {BASE}; }}
#Card QPushButton[primary="true"]:hover {{ background: {PRIMARY_HOVER}; }}
#Card QPushButton[danger="true"]:hover {{ background: {OVERDUE}; color: {BASE}; }}
#Card QPushButton:disabled {{ background: {RAISED}; color: {DISABLED}; }}
#Card QPushButton[iconOnly="true"] {{ background: transparent; }}
#Card QPushButton[iconOnly="true"]:hover {{ background: {RAISED_HOVER}; }}
QPushButton[iconOnly="true"] {{ background: transparent; padding: {S1}px; }}
QPushButton[iconOnly="true"]:hover {{ background: {RAISED_HOVER}; }}
QListView, QTreeView {{ background: transparent; border: none; outline: 0; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; border: none; padding: {S1}px; color: {MUTED}; font-weight: 600; }}
QTreeView::item {{ padding: {S1}px {HALF}px; border: none; }}
QTreeView::item:hover {{ background: {RAISED}; }}
QTreeView::item:selected, QTreeView::item:selected:!active {{ background: {SELECT}; color: {SELECT_TEXT}; }}
QListWidget::item:selected, QListWidget::item:selected:!active {{ background: {SELECT}; color: {SELECT_TEXT}; }}
QLineEdit, QPlainTextEdit, QDateEdit, QTimeEdit, QComboBox {{ background: {RAISED_HOVER}; color: {TEXT};
           border: 2px solid transparent; border-radius: {R_CONTROL}px; padding: {HALF + 2}px {S1}px;
           selection-background-color: {TEXT}; selection-color: {BASE}; }}
QLineEdit:focus, QPlainTextEdit:focus, QDateEdit:focus, QTimeEdit:focus, QComboBox:focus {{
           border-color: {ACCENT}; }}
QLineEdit:disabled, QDateEdit:disabled, QTimeEdit:disabled, QComboBox:disabled {{ color: {DISABLED}; }}
QComboBox QAbstractItemView {{ background: {RAISED}; color: {TEXT}; border: 1px solid {DIVIDER}; border-radius:
           {R_CONTROL}px; padding: {HALF}px; selection-background-color: {SELECT}; selection-color: {SELECT_TEXT};
           outline: 0; }}
QCalendarWidget QWidget {{ background: {RAISED}; color: {TEXT}; }}
QCalendarWidget QToolButton {{ background: {RAISED}; color: {TEXT}; font-weight: 600; padding: {HALF}px {S1}px;
           border-radius: {R_CONTROL}px; }}
QCalendarWidget QToolButton:hover {{ background: {RAISED_HOVER}; }}
QCalendarWidget QAbstractItemView {{ background: {RAISED}; color: {TEXT}; selection-background-color: {SELECT};
           selection-color: {SELECT_TEXT}; }}
QCalendarWidget QAbstractItemView:disabled {{ color: {DISABLED}; }}
QCheckBox, QRadioButton {{ background: transparent; spacing: {S1}px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: {HALF}px 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px {HALF}px; }}
QScrollBar::handle {{ background: {RAISED_HOVER}; border-radius: 3px; }}
QScrollBar::handle:hover {{ background: {SCROLL}; }}
QScrollBar::handle:vertical {{ min-height: {S4}px; }}
QScrollBar::handle:horizontal {{ min-width: {S4}px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QToolTip {{ background: {RAISED_HOVER}; color: {TEXT}; border: 1px solid {DIVIDER}; border-radius: {R_CONTROL}px;
           padding: {HALF}px {S1}px; }}
""" + "".join(  # WO-FS-009 MC-5 to MC-7: show_month() underlines a label in its month's colour
    f'*[month="{m}"] {{ border: none; border-bottom: {MONTH_BAND}px solid {c}; padding-bottom: {HALF // 2}px; }}\n'
    for m, c in MONTH_COLOURS.items())


def palette() -> QPalette:
    """The same colours for the parts Qt draws itself (message boxes, menus, focus)."""
    p = QPalette()
    for role, colour in ((QPalette.ColorRole.Window, RAISED), (QPalette.ColorRole.Base, RAISED),
                         (QPalette.ColorRole.AlternateBase, RAISED), (QPalette.ColorRole.Button, RAISED),
                         (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Text, TEXT),
                         (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.BrightText, TEXT),
                         (QPalette.ColorRole.ToolTipBase, RAISED_HOVER), (QPalette.ColorRole.ToolTipText, TEXT),
                         (QPalette.ColorRole.PlaceholderText, PAST), (QPalette.ColorRole.Link, ACCENT),
                         (QPalette.ColorRole.Highlight, SELECT), (QPalette.ColorRole.HighlightedText, SELECT_TEXT)):
        p.setColor(role, QColor(colour))  # all groups, so the selection looks the same without focus (SL-3)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED))
    return p


def _draw_images(folder: Path) -> None:
    """Tick boxes, radio buttons and arrows that show on dark layers (BK-7), drawn once at twice the size."""
    folder.mkdir(parents=True, exist_ok=True)
    size = 36

    def save(name: str, paint: Callable[[QPainter], None], width: int = size, height: int = size) -> None:
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint(p)
        p.end()
        image.save(str(folder / f"{name}.png"))

    def box(p: QPainter, fill: str, round_: bool) -> None:
        p.setPen(QPen(QColor(MUTED if fill == RAISED_HOVER else TEXT), 3))
        p.setBrush(QColor(fill))
        if round_:
            p.drawEllipse(3, 3, size - 6, size - 6)
        else:
            p.drawRoundedRect(3, 3, size - 6, size - 6, 8, 8)

    def tick(p: QPainter) -> None:
        box(p, TEXT, False)
        p.setPen(QPen(QColor(BASE), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline([QPointF(9, 18), QPointF(15, 25), QPointF(27, 11)])

    def dot(p: QPainter) -> None:
        box(p, RAISED_HOVER, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TEXT))
        p.drawEllipse(10, 10, size - 20, size - 20)

    def arrow(p: QPainter, down: bool) -> None:
        p.setPen(QPen(QColor(MUTED), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        y1, y2 = (14, 23) if down else (23, 14)
        p.drawPolyline([QPointF(9, y1), QPointF(18, y2), QPointF(27, y1)])

    save("check-off", lambda p: box(p, RAISED_HOVER, False))
    save("check-on", tick)
    save("radio-off", lambda p: box(p, RAISED_HOVER, True))
    save("radio-on", dot)
    def switch(p: QPainter, on: bool) -> None:  # WO-FS-013 EC-2: green when on
        w, h = SWITCH[0] * 2, SWITCH[1] * 2
        p.setPen(QPen(QColor(TODAY_EVENT if on else MUTED), 3))
        p.setBrush(QColor(TODAY_EVENT if on else RAISED_HOVER))
        p.drawRoundedRect(2, 2, w - 4, h - 4, (h - 4) / 2, (h - 4) / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(BASE if on else MUTED))
        knob = h - 14
        p.drawEllipse(w - knob - 7 if on else 7, 7, knob, knob)

    save("switch-off", lambda p: switch(p, False), SWITCH[0] * 2, SWITCH[1] * 2)
    save("switch-on", lambda p: switch(p, True), SWITCH[0] * 2, SWITCH[1] * 2)
    save("arrow-down", lambda p: arrow(p, True))
    save("arrow-up", lambda p: arrow(p, False))


def _image_rules(folder: Path) -> str:
    def url(name: str) -> str:
        return f'url("{(folder / name).as_posix()}.png")'

    return f"""
QCheckBox::indicator, QTreeView::indicator {{ width: {ICON}px; height: {ICON}px; image: {url("check-off")}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked {{ image: {url("check-on")}; }}
QCheckBox#Switch::indicator {{ width: {SWITCH[0]}px; height: {SWITCH[1]}px; image: {url("switch-off")}; }}
QCheckBox#Switch::indicator:checked {{ image: {url("switch-on")}; }}
QRadioButton::indicator {{ width: {ICON}px; height: {ICON}px; image: {url("radio-off")}; }}
QRadioButton::indicator:checked {{ image: {url("radio-on")}; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: {S3 + HALF}px; }}
QComboBox::down-arrow, QDateEdit::down-arrow, QTimeEdit::down-arrow {{ image: {url("arrow-down")};
           width: {S1 + HALF}px; height: {S1 + HALF}px; }}
QTimeEdit::up-arrow {{ image: {url("arrow-up")}; width: {S1 + HALF}px; height: {S1 + HALF}px; }}
QTimeEdit::up-button, QTimeEdit::down-button {{ border: none; width: {S3}px; background: transparent; }}
"""


def dark_title_bar(widget: QWidget) -> None:
    """FX-2: ask Windows for a dark title bar, whatever the Windows colour mode."""
    if sys.platform != "win32":
        return
    hwnd = int(widget.winId())
    value = ctypes.c_int(1)
    for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE; 19 on Windows 10 before 20H1
        if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                      ctypes.sizeof(value)) == 0:
            return


class _TitleBars(QObject):
    """Applies the dark title bar to every window as it first appears."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow() \
                and not obj.property("darkTitle"):
            obj.setProperty("darkTitle", True)
            dark_title_bar(obj)
        return False


_title_bars = _TitleBars()


def animations_on() -> bool:
    """CF-4: follow Windows' "Animation effects" setting (SPI_GETCLIENTAREAANIMATION)."""
    if sys.platform != "win32":
        return True
    value = ctypes.c_int(1)
    if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
        return bool(value.value)
    return True


def load_fonts() -> None:
    for path in sorted((ASSETS / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))


def apply_theme(app: QApplication) -> None:
    load_fonts()
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    app.setStyle("Fusion")  # draws message boxes and focus from the palette, unlike the Windows style
    app.setPalette(palette())
    app.setFont(font(BASE_PT))
    app.installEventFilter(_title_bars)
    from family_scheduler import __version__
    from family_scheduler.app.icons import app_icon

    folder = Path(tempfile.gettempdir()) / f"family-scheduler-theme-{__version__}"
    _draw_images(folder)
    app.setStyleSheet(STYLESHEET + _image_rules(folder))
    app.setWindowIcon(app_icon())


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2 contrast ratio between two #RRGGBB colours."""

    def luminance(colour: str) -> float:
        channels = [int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def time_range(start: str, end: str) -> str:
    """"4:15 pm–5:00 pm", or "All day" for an all-day event, which has no times (WO-FS-008 AD-6)."""
    if not start:
        return "All day"
    return f"{fmt_time(start)}–{fmt_time(end)}"


def repeat_note(t: dict[str, Any]) -> str:
    """WO-FS-013 MR-9: "Repeats on the 31st" for a monthly task on the 29th to 31st, "Repeats on February 29" for a
    yearly one on the 29th; otherwise ""."""
    day = t.get("repeat_day")
    if t.get("repeat") == "monthly" and day and day >= 29:
        return f"Repeats on the {ordinal(day)}"
    if t.get("repeat") == "yearly" and day == 29 and t.get("repeat_month") == 2:
        return "Repeats on February 29"
    return ""


def day_label(iso_date: str) -> str:
    return fmt_day(iso_date)


def when(stamp: str | None) -> str:
    """A stored UTC time shown in Toronto time (PC-10)."""
    if not stamp:
        return ""
    local = to_local(parse_iso(stamp))
    return f"{local.strftime('%a %b')} {local.day}, {fmt_time(local.strftime('%H:%M'))}"


def synced_ago(stamp: str | None, now: datetime | None = None) -> str:
    """WO-FS-010 GY-2: "Synced 10 minutes ago"."""
    return ago(stamp, now, what="Synced", never="Not synced with Google yet")


def ago(stamp: str | None, now: datetime | None = None, *, what: str = "Replies updated",
        never: str = "Replies not refreshed yet") -> str:
    """RV-2: "Replies updated 10 minutes ago"."""
    if not stamp:
        return never
    now = now or datetime.now(UTC)
    minutes = int((now - parse_iso(stamp)).total_seconds() // 60)
    if minutes < 1:
        return f"{what} just now"
    if minutes < 60:
        return f"{what} {minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{what} {hours} hour{'s' if hours != 1 else ''} ago"
    return f"{what} {when(stamp)}"


def short_email(email: str) -> str:
    return email.split("@", 1)[0] + "@"


REPEAT_LABELS = {"none": "Doesn't repeat", "daily": "Every day", "weekly": "Every week",
                 "monthly": "Every month", "yearly": "Every year"}


def today_iso() -> str:
    return to_local(datetime.now(UTC)).date().isoformat()


def is_past(iso_date: str) -> bool:
    return iso_date < date.fromisoformat(today_iso()).isoformat()


# MC-8: text on each month colour, in black or white (month_ink), for the contrast check and the colour sheet.
CONTRAST_PAIRS += [(f"{MONTH_NAMES[m - 1]} label on its colour", month_ink(m), MONTH_COLOURS[m])
                   for m in range(1, 13)]
