"""Checks that no tracked file holds personal information or a secret (WO-FS-010 section 5).

    python tools/check_public.py              scan every tracked file; exit 1 on any finding
    python tools/check_public.py --history    also scan every commit (author, email, message and files); exit 1 if
                                              any holds personal details (WO-FS-015 CK-2: this copy fails on them)

Two kinds of check:
  * generic patterns, safe to keep in public code: real-looking email addresses, home network addresses, Windows
    user and OneDrive paths, Google client IDs and secrets, OAuth tokens, private keys;
  * a private list of the family's own words (names, addresses, places), read from a file that is never committed:
    FS_BLOCKLIST, or %USERPROFILE%\\.family-scheduler-blocklist.txt. One word or phrase per line; # starts a comment.
    Missing file: the check says so and still runs the generic patterns.

A finding is reported by file, line and kind only: the matched text is never printed, so the output can be saved.

The authors' names, exactly as they appear in the copyright line of LICENSE ("Copyright (c) YEAR NAMES"), are the
one phrase allowed even if the private list holds part of it (WO-FS-015 PI-g).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINARY = {".ttf", ".otf", ".ico", ".png", ".jpg", ".jpeg", ".gif", ".exe", ".zip", ".db", ".pyc"}

# Placeholders that are fine in public code (PI section 2): made-up domains and documented examples.
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com")
ALLOWED_EMAILS = {"name@gmail.com", "new@gmail.com", "you@gmail.com", "noreply@github.com"}
ALLOWED_ADDRESSES = {"192.168.1.20", "10.0.0.5", "127.0.0.1", "0.0.0.0"}

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PRIVATE_IP = re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b(?!/)")
PATTERNS = [
    ("Windows user folder path", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<|\{|%|\*|Public\b|name\b|you\b)\w",
                                            re.IGNORECASE)),
    ("OneDrive path", re.compile(r"OneDrive(?:[\\/]| - )", re.IGNORECASE)),
    ("Google client ID", re.compile(r"\b\d{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com")),
    ("Google client secret", re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}")),
    ("OAuth token", re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}|\b1//0[A-Za-z0-9_-]{20,}")),
    ("Private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


@dataclass
class Finding:
    where: str
    line: int
    kind: str

    def __str__(self) -> str:
        return f"{self.where}:{self.line}: {self.kind}"


def blocklist_path() -> Path:
    return Path(os.environ.get("FS_BLOCKLIST") or Path.home() / ".family-scheduler-blocklist.txt")


def load_blocklist(path: Path | None = None) -> list[str] | None:
    """The private words, or None when the file doesn't exist."""
    path = path or blocklist_path()
    if not path.exists():
        return None
    words = [w.strip() for w in path.read_text(encoding="utf-8-sig").splitlines()]
    return [w for w in words if w and not w.startswith("#")]


def author_credit() -> str:
    """PI-g: the authors' names from LICENSE's copyright line, or "" if there's none."""
    licence = ROOT / "LICENSE"
    if not licence.exists():
        return ""
    match = re.search(r"^Copyright \(c\) \d{4} (.+)$", licence.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1).strip() if match else ""


def _word(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)


def scan_text(text: str, where: str, private: list[str] | None) -> list[Finding]:
    """Every finding in one file's text."""
    found: list[Finding] = []
    words = [_word(t) for t in private or []]
    credit = author_credit()
    for number, line in enumerate(text.splitlines(), start=1):
        if credit:
            line = line.replace(credit, "")  # PI-g: the author credit is allowed
        for match in EMAIL.finditer(line):
            email = match.group(0).lower()
            if email.split("@", 1)[1] not in ALLOWED_EMAIL_DOMAINS and email not in ALLOWED_EMAILS:
                found.append(Finding(where, number, "email address that isn't a placeholder"))
        for match in PRIVATE_IP.finditer(line):
            if match.group(0) not in ALLOWED_ADDRESSES:
                found.append(Finding(where, number, "home network address"))
        found.extend(Finding(where, number, kind) for kind, pattern in PATTERNS if pattern.search(line))
        if any(w.search(line) for w in words):
            found.append(Finding(where, number, "a word from the private list"))
    return found


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [f for f in out.decode("utf-8").split("\0") if f]


def scan_files(private: list[str] | None) -> tuple[int, list[Finding]]:
    files = [f for f in tracked_files() if Path(f).suffix.lower() not in BINARY]
    found: list[Finding] = []
    for name in files:
        path = ROOT / name
        if not path.exists():  # deleted but not yet committed
            continue
        found.extend(scan_text(path.read_text(encoding="utf-8", errors="replace"), name, private))
        found.extend(scan_text(name, f"{name} (file name)", private))
    return len(files), found


def scan_history(private: list[str] | None) -> tuple[int, int]:
    """PC-b: (commits, commits whose author, message or files hold personal information)."""
    log = subprocess.run(["git", "log", "--format=%H%x1f%an%x1f%ae%x1f%B%x1e"], cwd=ROOT, capture_output=True,
                         check=True).stdout.decode("utf-8", errors="replace")
    commits = [c.strip() for c in log.split("\x1e") if c.strip()]
    flagged = 0
    for commit in commits:
        sha, name, email, message = (commit.split("\x1f") + ["", "", "", ""])[:4]
        meta = f"{name}\n{email}\n{message}"
        bad = bool(scan_text(meta, sha, private))
        if not bad:
            for path in subprocess.run(["git", "ls-tree", "-r", "--name-only", sha], cwd=ROOT, capture_output=True,
                                       check=True).stdout.decode("utf-8").splitlines():
                if Path(path).suffix.lower() in BINARY:
                    continue
                blob = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=ROOT, capture_output=True,
                                      check=False).stdout.decode("utf-8", errors="replace")
                if scan_text(blob, path, private):
                    bad = True
                    break
        flagged += bad
    return len(commits), flagged


def main() -> int:
    private = load_blocklist()
    if private is None:
        print(f"Private list not found ({blocklist_path()}): checking the generic patterns only.")
    else:
        print(f"Private list: {len(private)} entries (from outside the repository).")
    count, found = scan_files(private)
    print(f"Tracked files scanned: {count}")
    for finding in found:
        print(f"  {finding}")
    print(f"Findings: {len(found)}")
    flagged = 0
    if "--history" in sys.argv:
        commits, flagged = scan_history(private)
        print(f"History: {flagged} of {commits} commits contain personal information.")
    return 1 if found or flagged else 0  # CK-2: in this copy a commit with personal details fails too


if __name__ == "__main__":
    sys.exit(main())
