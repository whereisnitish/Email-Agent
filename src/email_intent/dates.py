"""Relative-date resolution.

Customers write "by Oct 20", "before the 20th", "next month" or "ASAP". All of
those are resolved here against the email's own received date -- never against
"now" -- so re-running an extraction months later reproduces the same answer.

Every result carries a `precision` so downstream automation can tell a hard
committed date from "sometime next month".
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

# ---------------------------------------------------------------------------

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2,
    "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4,
    "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

#: Wording that makes a date a *delivery* commitment rather than, say, the
#: date of the event itself.
DELIVERY_CUES = (
    "by", "before", "no later than", "not later than", "due", "deadline",
    "delivered", "delivery", "deliver", "need them", "need these", "need it",
    "needed", "in hand", "in-hands", "in hands", "arrive", "arrival", "ship",
    "shipped", "on site", "onsite", "receive", "have them", "get them", "want them",
    # Bare request verbs count too -- "need 100 mugs in 2 weeks" is a deadline.
    # The occasion guard above keeps them from capturing event dates.
    "need", "want", "require", "looking for",
)

RUSH_CUES = ("asap", "as soon as possible", "rush", "urgently", "right away", "immediately")

PRECISION_DAY = "day"
PRECISION_MONTH = "month"
PRECISION_WEEK = "week"
PRECISION_QUARTER = "quarter"
PRECISION_RUSH = "asap"


@dataclass
class ResolvedDate:
    iso: str | None
    precision: str
    raw: str
    approximate: bool = False
    start: int = -1
    end: int = -1
    has_delivery_cue: bool = False
    note: str | None = None


# ---------------------------------------------------------------------------
# US holidays that drive promotional-product deadlines
# ---------------------------------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    last = date(year, month, last_day)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _holidays(year: int) -> dict[str, date]:
    return {
        "new year": date(year, 1, 1),
        "valentines": date(year, 2, 14),
        "st patricks": date(year, 3, 17),
        "memorial day": _last_weekday(year, 5, 0),
        "fourth of july": date(year, 7, 4),
        "july 4th": date(year, 7, 4),
        "independence day": date(year, 7, 4),
        "labor day": _nth_weekday(year, 9, 0, 1),
        "halloween": date(year, 10, 31),
        "thanksgiving": _nth_weekday(year, 11, 3, 4),
        "black friday": _nth_weekday(year, 11, 3, 4) + timedelta(days=1),
        "christmas": date(year, 12, 25),
        "new years": date(year, 1, 1),
    }


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_WEEKDAY_ALT = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_HOLIDAY_ALT = "|".join(sorted(_holidays(2000), key=len, reverse=True))

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("iso", re.compile(r"\b(?P<y>20\d{2})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b")),
    ("numeric", re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?\b")),
    ("month_day", re.compile(
        rf"\b(?P<mon>{_MONTH_ALT})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(?P<y>20\d{{2}}))?\b")),
    ("day_month", re.compile(
        rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{_MONTH_ALT})\.?(?:,?\s*(?P<y>20\d{{2}}))?\b")),
    ("month_part", re.compile(
        rf"\b(?P<part>early|mid|middle of|late|end of|beginning of|first week of|last week of)\s+"
        rf"(?:the\s+)?(?P<mon>{_MONTH_ALT})\b")),
    ("bare_ordinal", re.compile(r"\b(?:the\s+)?(?P<d>\d{1,2})(?:st|nd|rd|th)\b")),
    ("in_n_units", re.compile(
        r"\b(?:in|within|inside of)\s+(?:the\s+next\s+)?(?P<n>\d{1,2}|a|two|three|four|five|six)\s+"
        r"(?P<unit>day|days|week|weeks|month|months|business day|business days)\b")),
    ("n_units_out", re.compile(
        r"\b(?P<n>\d{1,2}|two|three|four)\s+(?P<unit>week|weeks|month|months)\s+(?:from now|out)\b")),
    ("relative_period", re.compile(
        r"\b(?P<rel>next|this|following|coming|end of the|end of next|start of next|beginning of next)\s+"
        r"(?P<unit>week|month|quarter)\b")),
    ("weekday", re.compile(rf"\b(?:this|next|on|by|before)?\s*(?P<wd>{_WEEKDAY_ALT})\b")),
    ("holiday", re.compile(rf"\b(?P<hol>{_HOLIDAY_ALT})\b")),
    ("month_only", re.compile(rf"\b(?:in|by|before|during|for)\s+(?P<mon>{_MONTH_ALT})\b")),
    ("rush", re.compile(r"\b(" + "|".join(map(re.escape, RUSH_CUES)) + r")\b")),
]

_WORD_COUNTS = {"a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


# ---------------------------------------------------------------------------

def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _roll_forward(candidate: date, reference: date) -> date:
    """Push a bare month/day into the future the way a reader would read it."""
    if candidate >= reference:
        return candidate
    try:
        return candidate.replace(year=candidate.year + 1)
    except ValueError:  # Feb 29
        return candidate.replace(year=candidate.year + 1, day=28)


def _shift_months(base: date, months: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def find_date_expressions(text: str, reference: date) -> list[ResolvedDate]:
    """Resolve every date-ish expression in `text` against `reference`."""
    found: list[ResolvedDate] = []

    def claimed(start: int, end: int) -> bool:
        return any(d.start < end and start < d.end for d in found)

    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if claimed(match.start(), match.end()):
                continue
            resolved = _resolve(kind, match, text, reference)
            if resolved:
                resolved.has_delivery_cue = _has_delivery_cue(text, resolved.start)
                found.append(resolved)

    found.sort(key=lambda d: d.start)
    return found


def _resolve(kind: str, match: re.Match[str], text: str, reference: date) -> ResolvedDate | None:
    raw = match.group(0).strip()
    start, end = match.start(), match.end()
    groups = match.groupdict()

    try:
        if kind == "iso":
            value = date(int(groups["y"]), int(groups["m"]), int(groups["d"]))
            return ResolvedDate(value.isoformat(), PRECISION_DAY, raw, False, start, end)

        if kind == "numeric":
            month, day = int(groups["m"]), int(groups["d"])
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None
            year_text = groups.get("y")
            if year_text:
                year = int(year_text)
                year += 2000 if year < 100 else 0
                value = date(year, month, day)
            else:
                value = _roll_forward(date(reference.year, month, day), reference)
            return ResolvedDate(value.isoformat(), PRECISION_DAY, raw, False, start, end)

        if kind in ("month_day", "day_month"):
            month = MONTHS[groups["mon"]]
            day = int(groups["d"])
            if not 1 <= day <= calendar.monthrange(reference.year, month)[1]:
                return None
            year_text = groups.get("y")
            if year_text:
                value = date(int(year_text), month, day)
            else:
                value = _roll_forward(date(reference.year, month, day), reference)
            return ResolvedDate(value.isoformat(), PRECISION_DAY, raw, False, start, end)

        if kind == "month_part":
            month = MONTHS[groups["mon"]]
            base = _roll_forward(date(reference.year, month, 1), reference.replace(day=1))
            part = groups["part"]
            if part in ("early", "beginning of", "first week of"):
                value = base.replace(day=10)
            elif part in ("mid", "middle of"):
                value = base.replace(day=15)
            else:
                value = _end_of_month(base.year, base.month)
            return ResolvedDate(value.isoformat(), PRECISION_DAY, raw, True, start, end,
                                note=f"resolved from {part} {groups['mon']}")

        if kind == "bare_ordinal":
            day = int(groups["d"])
            if not 1 <= day <= 31:
                return None
            # Only read a bare ordinal as a date when something nearby says so.
            lead = text[max(0, start - 30):start]
            if not any(cue in lead for cue in ("by the", "before the", "on the", "of the", "by", "before")):
                return None
            # A bare day-of-month rolls to the *next month*, not the next year:
            # on Sep 23, "before the 20th" means Oct 20.
            month, year = reference.month, reference.year
            for _ in range(13):
                try:
                    value = date(year, month, day)
                except ValueError:
                    month, year = (1, year + 1) if month == 12 else (month + 1, year)
                    continue
                if value >= reference:
                    break
                month, year = (1, year + 1) if month == 12 else (month + 1, year)
            else:
                return None
            return ResolvedDate(value.isoformat(), PRECISION_DAY, raw, False, start, end)

        if kind in ("in_n_units", "n_units_out"):
            token = groups["n"]
            count = int(token) if token.isdigit() else _WORD_COUNTS.get(token, 0)
            if not count:
                return None
            unit = groups["unit"]
            if unit.startswith("business day"):
                value, added = reference, 0
                while added < count:
                    value += timedelta(days=1)
                    if value.weekday() < 5:
                        added += 1
            elif unit.startswith("day"):
                value = reference + timedelta(days=count)
            elif unit.startswith("week"):
                value = reference + timedelta(weeks=count)
            else:
                value = _shift_months(reference, count)
            return ResolvedDate(value.isoformat(), PRECISION_DAY, raw, True, start, end)

        if kind == "relative_period":
            rel, unit = groups["rel"], groups["unit"]
            if unit == "week":
                offset = 0 if rel in ("this", "end of the") else 1
                monday = reference - timedelta(days=reference.weekday()) + timedelta(weeks=offset)
                value = monday + timedelta(days=4)  # Friday: the business end of week
                if value < reference:
                    value += timedelta(weeks=1)
                return ResolvedDate(value.isoformat(), PRECISION_WEEK, raw, True, start, end,
                                    note="end of the referenced week")
            if unit == "month":
                offset = 0 if rel in ("this", "end of the") else 1
                shifted = _shift_months(reference.replace(day=1), offset)
                if rel in ("start of next", "beginning of next"):
                    value = shifted.replace(day=10)
                else:
                    value = _end_of_month(shifted.year, shifted.month)
                return ResolvedDate(value.isoformat(), PRECISION_MONTH, raw, True, start, end,
                                    note="month-level precision only")
            quarter = (reference.month - 1) // 3 + (0 if rel == "this" else 1)
            year = reference.year + quarter // 4
            quarter %= 4
            value = _end_of_month(year, quarter * 3 + 3)
            return ResolvedDate(value.isoformat(), PRECISION_QUARTER, raw, True, start, end)

        if kind == "weekday":
            weekday = WEEKDAYS[groups["wd"]]
            lead = text[max(0, start - 14):start]
            if not any(cue in lead for cue in ("this", "next", "on", "by", "before")):
                return None
            delta = (weekday - reference.weekday()) % 7 or 7
            if "next" in lead:
                delta += 7 if delta < 7 else 0
            return ResolvedDate((reference + timedelta(days=delta)).isoformat(),
                                PRECISION_DAY, raw, False, start, end)

        if kind == "holiday":
            name = groups["hol"]
            value = _holidays(reference.year).get(name)
            if value is None:
                return None
            if value < reference:
                value = _holidays(reference.year + 1)[name]
            return ResolvedDate(value.isoformat(), PRECISION_DAY, raw, False, start, end,
                                note=f"resolved US holiday: {name}")

        if kind == "month_only":
            month = MONTHS[groups["mon"]]
            base = _roll_forward(date(reference.year, month, 1), reference.replace(day=1))
            value = _end_of_month(base.year, base.month)
            return ResolvedDate(value.isoformat(), PRECISION_MONTH, raw, True, start, end,
                                note="month-level precision only")

        if kind == "rush":
            return ResolvedDate(None, PRECISION_RUSH, raw, True, start, end,
                                note="customer asked for the earliest possible date")
    except (ValueError, KeyError):
        return None
    return None


#: Wording *after* a date that still makes it a delivery commitment.
TRAILING_DELIVERY_CUES = ("deadline", "in hand", "in-hands", "in hands", "delivery date", "drop dead")


#: A date sitting right after an occasion noun is when the *event* happens, not
#: when goods are due -- "for our event next month", "the gala is Nov 14".
_OCCASION_DATE_RE = re.compile(
    r"\b(event|show|expo|conference|convention|summit|gala|party|meeting|picnic|"
    r"tournament|outing|celebration|banquet|wedding|fundraiser|opening)\b[^.!?]{0,12}$"
)


def _has_delivery_cue(text: str, start: int) -> bool:
    lead = text[max(0, start - 60):start]
    if _OCCASION_DATE_RE.search(text[max(0, start - 30):start]):
        return False
    if any(re.search(rf"(?<![a-z]){re.escape(cue)}(?![a-z])", lead) for cue in DELIVERY_CUES):
        return True
    trail = text[start:start + 45]
    return any(cue in trail for cue in TRAILING_DELIVERY_CUES)


def find_delivery_deadline(text: str, reference: date) -> ResolvedDate | None:
    """Pick the date that is actually a *delivery* commitment.

    "We have an event next month ... need them delivered by Oct 20" must return
    Oct 20, not the event month. A date only counts when the customer framed it
    as a delivery: an event date mentioned in passing is not a deadline, and
    guessing one would be inventing a commitment they never made.
    """
    expressions = find_date_expressions(text, reference)
    if not expressions:
        return None

    pool = [d for d in expressions if d.has_delivery_cue]
    if not pool:
        return None

    hard = [d for d in pool if d.iso and d.precision == PRECISION_DAY]
    if hard:
        return min(hard, key=lambda d: d.iso or "")

    dated = [d for d in pool if d.iso]
    if dated:
        return min(dated, key=lambda d: d.iso or "")

    rush = [d for d in pool if d.precision == PRECISION_RUSH]
    return rush[0] if rush else None


def resolve_phrase(phrase: str, reference: date) -> ResolvedDate | None:
    """Resolve a standalone phrase (used for LLM-extracted raw deadline text)."""
    if not phrase:
        return None
    text = phrase.strip().lower()
    direct = find_delivery_deadline(text, reference)
    if direct:
        return direct
    # Retry with a delivery cue prefixed so bare fragments ("the 20th") resolve.
    return find_delivery_deadline(f"by {text}", reference)
