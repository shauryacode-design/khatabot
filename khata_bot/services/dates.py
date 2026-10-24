"""Date parsing and formatting in Indian Standard Time."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def today_ist() -> date:
    return datetime.now(IST).date()


def parse_date(text: str, today: date | None = None) -> date | None:
    current = today or today_ist()
    value = text.translate(_DEVANAGARI_DIGITS).strip().lower()
    if re.search(r"\b(aaj|today)\b", value):
        return current
    if re.search(r"\bkal\b", value):
        return current - timedelta(days=1)
    if re.search(r"\btomorrow\b", value):
        return current + timedelta(days=1)
    if re.search(r"\b(parso|day after tomorrow)\b", value):
        return current + timedelta(days=2)
    iso = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", value)
    if iso:
        try:
            return date(int(iso[1]), int(iso[2]), int(iso[3]))
        except ValueError:
            return None
    numeric = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", value)
    if numeric:
        year = int(numeric[3]) if numeric[3] else current.year
        if year < 100:
            year += 2000
        try:
            return date(year, int(numeric[2]), int(numeric[1]))
        except ValueError:
            return None
    named = re.search(r"\b(\d{1,2})\s+([a-z]+)(?:\s+(20\d{2}))?\b", value)
    if named and named[2] in _MONTHS:
        try:
            return date(
                int(named[3]) if named[3] else current.year,
                _MONTHS[named[2]],
                int(named[1]),
            )
        except ValueError:
            return None
    return None


def month_bounds(year: int, month: int) -> tuple[date, date]:
    if not 1 <= month <= 12:
        raise ValueError("Month must be between 1 and 12.")
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return start, end
