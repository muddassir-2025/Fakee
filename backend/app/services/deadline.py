"""Detect an application/registration deadline and whether it has passed.

An expired posting is **not** the same as a fake one: the opportunity may have
been genuine and simply closed. The risk engine surfaces ``EXPIRED`` separately
so the user is told "too late" rather than "fraud".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MONTHS: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_MON = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

# 19th July 2026 / 19-Jul-2026 / 19 July 2026
DAY_FIRST_RE = re.compile(
    rf"(?P<day>\d{{1,2}})(?:st|nd|rd|th)?[\s\-\.]+(?P<mon>{_MON})[a-z]*\.?,?[\s\-\.]+(?P<year>\d{{4}})",
    re.IGNORECASE,
)
# July 14, 2026 / Jul 14 2026
MONTH_FIRST_RE = re.compile(
    rf"(?P<mon>{_MON})[a-z]*\.?[\s\-\.]+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?[\s\-\.]+(?P<year>\d{{4}})",
    re.IGNORECASE,
)

DEADLINE_KEYWORDS: tuple[str, ...] = (
    "registration last date",
    "registration deadline",
    "last date to apply",
    "last date to register",
    "last date",
    "last day",
    "deadline",
    "apply before",
    "apply by",
    "closes on",
    "registration closes",
    "closing date",
    "register by",
)

# How far after a deadline keyword we look for the date.
WINDOW = 160


@dataclass
class DeadlineInfo:
    raw: str | None = None
    deadline: date | None = None
    expired: bool = False


def _parse(day: str, mon: str, year: str) -> date | None:
    key = mon.lower()
    month = MONTHS.get(key) or MONTHS.get(key[:3])
    if not month:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def _all_dates(text: str) -> list[tuple[int, str, date]]:
    found: list[tuple[int, str, date]] = []
    for regex in (DAY_FIRST_RE, MONTH_FIRST_RE):
        for match in regex.finditer(text):
            parsed = _parse(match.group("day"), match.group("mon"), match.group("year"))
            if parsed:
                found.append((match.start(), match.group(0), parsed))
    found.sort(key=lambda item: item[0])
    return found


def detect_deadline(text: str, today: date | None = None) -> DeadlineInfo:
    """Return the application deadline (if stated) and whether it has passed."""
    reference = today or date.today()
    if not text:
        return DeadlineInfo()

    lower = text.lower()
    anchors = [lower.find(kw) for kw in DEADLINE_KEYWORDS]
    anchors = [pos for pos in anchors if pos != -1]
    if not anchors:
        return DeadlineInfo()
    anchors.sort()

    dates = _all_dates(text)
    for anchor in anchors:
        for pos, raw, parsed in dates:
            if anchor <= pos <= anchor + WINDOW:
                return DeadlineInfo(
                    raw=raw.strip(), deadline=parsed, expired=parsed < reference
                )
    return DeadlineInfo()
