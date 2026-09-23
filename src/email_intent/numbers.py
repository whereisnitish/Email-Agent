"""Quantity and budget parsing.

This module is the single source of truth for turning customer wording such as
"around 100-150 pcs" or "under 20 bucks each" into numbers. Both the rule
engine and the LLM path funnel through it, so an LLM never has to do
arithmetic and the two paths can never disagree on how a number is read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .vocab import CAPACITY_UNITS

# ---------------------------------------------------------------------------
# Cue words
# ---------------------------------------------------------------------------

APPROX_CUES = (
    "about", "around", "approximately", "approx", "roughly", "maybe", "somewhere around",
    "in the range of", "close to", "ballpark", "or so", "give or take", "~", "circa",
    "something like", "ish", "a little over", "a bit over", "no more than",
)

MAX_CUES = (
    "under", "below", "less than", "no more than", "not more than", "not over",
    "max", "maximum", "up to", "at most", "within", "cap", "capped at", "keep it to",
    "stay under", "keep these under", "keep them under", "nothing over", "or less",
)

BUDGET_CUES = (
    "budget", "price", "pricing", "cost", "spend", "$", "bucks", "dollars", "usd",
    "per unit", "per piece", "each", "apiece", "a piece", "unit price", "quote",
    "cheap", "expensive", "afford",
)

PER_UNIT_CUES = (
    "each", "/each", "ea.", " ea ", "apiece", "a piece", "per piece", "per unit",
    "/unit", "/pc", "per pc", "per item", "a pop", "per shirt", "per bottle", "per tumbler",
)

TOTAL_CUES = (
    "total", "in total", "all in", "altogether", "overall", "for the whole",
    "for everything", "in all", "all together", "grand total",
)

QUANTITY_UNITS = (
    "pcs", "pc", "pieces", "piece", "units", "unit", "qty", "quantity", "ea",
    "sets", "set", "cases", "case", "boxes", "box", "packs", "pack",
)

#: Bulk units that carry an implicit multiplier.
BULK_MULTIPLIERS = {"dozen": 12, "dozens": 12, "gross": 144, "case": 1, "cases": 1}

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
SCALE_WORDS = {"hundred": 100, "thousand": 1000, "k": 1000}


# ---------------------------------------------------------------------------
# Number scanning
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NumberHit:
    value: float
    start: int
    end: int
    raw: str
    role: str  # quantity | money | capacity | size | date | reference | unknown


#: Leading "." is excluded so the ".50" of "15.50" is not read as its own
#: number; "$" must stay allowed so "$15" is found at all.
_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")
_WORD_NUM_RE = re.compile(
    r"(?<![a-z])(?:a |an |couple of |couple )?"
    r"(?:" + "|".join(NUMBER_WORDS) + r"|hundred|thousand)"
    r"(?:[ -](?:" + "|".join(NUMBER_WORDS) + r"|hundred|thousand))*"
    r"(?![a-z])"
)

_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
)

_ORDINAL_RE = re.compile(r"\d+(st|nd|rd|th)\b")


def _words_to_number(phrase: str) -> float | None:
    """Convert a small English number phrase ("two hundred") to a value."""
    phrase = phrase.replace("-", " ").strip()
    phrase = re.sub(r"^(a|an) ", "one ", phrase)
    phrase = re.sub(r"^couple( of)? ", "two ", phrase)
    total, current, seen = 0, 0, False
    for word in phrase.split():
        if word in NUMBER_WORDS:
            current += NUMBER_WORDS[word]
            seen = True
        elif word in SCALE_WORDS:
            current = (current or 1) * SCALE_WORDS[word]
            total += current
            current = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return float(total + current)


def _context(text: str, start: int, end: int, before: int = 28, after: int = 22) -> tuple[str, str]:
    return text[max(0, start - before):start], text[end:end + after]


def classify_numbers(text: str) -> list[NumberHit]:
    """Find every number in `text` and label what it is actually counting."""
    hits: list[NumberHit] = []

    for match in _NUM_RE.finditer(text):
        raw = match.group(1)
        value = float(raw.replace(",", ""))
        start, end = match.start(1), match.end(1)
        left, right = _context(text, start, end)
        hits.append(NumberHit(value, start, end, raw, _role_for(text, start, end, left, right, value)))

    for match in _WORD_NUM_RE.finditer(text):
        raw = match.group(0).strip()
        if raw in ("a", "an"):
            continue
        value = _words_to_number(raw)
        if value is None or value == 0:
            continue
        start, end = match.start(), match.end()
        if any(h.start < end and start < h.end for h in hits):
            continue
        left, right = _context(text, start, end)
        role = _role_for(text, start, end, left, right, value)
        hits.append(NumberHit(value, start, end, raw, role))

    hits.sort(key=lambda h: h.start)
    return hits


def _role_for(text: str, start: int, end: int, left: str, right: str, value: float) -> str:
    right_word = right.lstrip()
    left_strip = left.rstrip()

    # Money: "$15", "15 bucks", "usd 15"
    if left_strip.endswith("$") or re.search(r"(usd|us\$)\s*$", left_strip):
        return "money"
    if re.match(r"^(bucks|dollars|usd|cents)\b", right_word):
        return "money"
    if re.match(r"^(k)\b", right_word) and re.search(r"(budget|spend|cost)", left):
        return "money"

    # Capacity / dimensions: "20 oz", "16-oz", "10000 mah"
    if re.match(r"^-?\s*(" + "|".join(CAPACITY_UNITS) + r")\b", right_word):
        return "capacity"

    # Apparel size: "2xl", "size 10"
    if re.match(r"^\s*x?l\b", right_word) and value in (2, 3, 4):
        return "size"
    if re.search(r"\bsize[s]?\s*$", left):
        return "size"

    # Dates: "oct 20", "20th", "october 20", "2026"
    if _ORDINAL_RE.match(text[start:end + 2]):
        return "date"
    if re.search(r"(" + "|".join(_MONTHS) + r")\.?\s*$", left):
        return "date"
    if re.match(r"^\s*(st|nd|rd|th)\b", right_word):
        return "date"
    if re.match(r"^[/-]\d", right_word) and value <= 31:
        return "date"
    if 1900 <= value <= 2100 and re.search(r"[/-]\s*$", left_strip):
        return "date"
    if re.match(r"^\s*(" + "|".join(_MONTHS) + r")\b", right_word):
        return "date"

    # Identifiers: "po 4471", "order #1002", "invoice 7781".
    # "order" and "quote" are also verbs ("can you quote 300 tumblers"), so
    # they only mark an identifier when a # or "number" makes that explicit.
    if re.search(r"(?:^|\s)#\s*$", left_strip):
        return "reference"
    if re.search(r"\b(po|p\.o\.|purchase order|invoice|tracking|ref|reference)\s*(?:#|no\.?|number)?\s*$", left_strip):
        return "reference"
    if re.search(r"\b(order|quote|job)\s*(?:#|no\.|number)\s*$", left_strip):
        return "reference"

    # Quantity: explicit unit, or followed by a noun, or a plain round count
    if re.match(r"^-?\s*(" + "|".join(QUANTITY_UNITS) + r")\b", right_word):
        return "quantity"
    if re.match(r"^\s*(" + "|".join(BULK_MULTIPLIERS) + r")\b", right_word):
        return "quantity"
    if re.search(r"(qty|quantity|need|want|order|get|about|around|roughly|approximately|maybe)\s*$", left_strip):
        return "quantity"
    if re.match(r"^\s*[a-z]", right_word) and value >= 2:
        return "quantity"
    if value >= 10:
        return "quantity"
    return "unknown"


# ---------------------------------------------------------------------------
# Quantity
# ---------------------------------------------------------------------------

@dataclass
class Quantity:
    value: int | None = None
    minimum: int | None = None
    maximum: int | None = None
    approximate: bool = False
    raw: str | None = None
    unit: str | None = None
    warnings: list[str] = field(default_factory=list)


_RANGE_RE = re.compile(
    r"(?:between\s+)?(?P<a>\d[\d,]*)\s*(?:-|to|or|and)\s*(?P<b>\d[\d,]*)\s*"
    r"(?P<unit>pcs|pc|pieces|piece|units|unit|ea)?"
)


def parse_quantity(text: str, *, prefer_span: tuple[int, int] | None = None) -> Quantity:
    """Parse "how many" out of a text segment.

    A range ("100-150") reports both bounds; `value` carries the upper bound so
    a sourcing bot never under-quotes, and `approximate` flags that the number
    is soft. That rule is fixed and documented rather than model-decided.
    """
    quantity = Quantity()
    if not text:
        return quantity

    range_match = _RANGE_RE.search(text)
    if range_match:
        low = int(range_match.group("a").replace(",", ""))
        high = int(range_match.group("b").replace(",", ""))
        # Guard against matching a date range or a price range.
        span = (range_match.start(), range_match.end())
        looks_like_money = "$" in text[max(0, span[0] - 2):span[1] + 6]
        if low < high and not looks_like_money and high >= 10:
            quantity.minimum, quantity.maximum = low, high
            quantity.value = high
            quantity.approximate = True
            quantity.raw = range_match.group(0).strip()
            quantity.unit = range_match.group("unit")
            return quantity

    candidates = [h for h in classify_numbers(text) if h.role == "quantity"]
    if not candidates:
        return quantity

    if prefer_span:
        lo, hi = prefer_span
        inside = [h for h in candidates if lo <= h.start <= hi]
        if inside:
            candidates = inside

    hit = max(candidates, key=lambda h: h.value)
    value = hit.value

    tail = text[hit.end:hit.end + 18]
    multiplier_match = re.match(r"^\s*(" + "|".join(BULK_MULTIPLIERS) + r")\b", tail.lstrip())
    if multiplier_match:
        word = multiplier_match.group(1)
        value *= BULK_MULTIPLIERS[word]
        quantity.unit = word

    lead = text[max(0, hit.start - 30):hit.start]
    quantity.approximate = any(cue in lead for cue in APPROX_CUES)
    quantity.value = round(value)
    quantity.raw = _raw_window(text, hit.start, hit.end)
    unit_match = re.match(r"^\s*(" + "|".join(QUANTITY_UNITS) + r")\b", tail.lstrip())
    if unit_match and not quantity.unit:
        quantity.unit = unit_match.group(1)
    return quantity


def _raw_window(text: str, start: int, end: int) -> str:
    """Capture the customer's own wording around a number, e.g. "around 150"."""
    lead_start = start
    window = text[max(0, start - 22):start]
    for cue in sorted(APPROX_CUES + MAX_CUES, key=len, reverse=True):
        idx = window.rfind(cue)
        if idx != -1 and window[idx + len(cue):].strip(" ,") == "":
            lead_start = max(0, start - 22) + idx
            break
    tail_match = re.match(
        r"^\s*(" + "|".join(QUANTITY_UNITS + tuple(BULK_MULTIPLIERS)) + r")\b", text[end:end + 14]
    )
    tail_end = end + (tail_match.end() if tail_match else 0)
    return text[lead_start:tail_end].strip()


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

@dataclass
class Budget:
    per_unit: float | None = None
    total: float | None = None
    currency: str | None = None
    is_maximum: bool = False
    approximate: bool = False
    raw: str | None = None
    basis: str | None = None  # per_unit | total | assumed_per_unit
    warnings: list[str] = field(default_factory=list)


_CURRENCY_HINTS = (
    ("USD", ("$", "usd", "us$", "dollar", "dollars", "bucks")),
    ("EUR", ("€", "eur", "euro", "euros")),
    ("GBP", ("£", "gbp", "pound", "pounds", "quid")),
    ("CAD", ("cad", "canadian dollar")),
)


def detect_currency(text: str) -> str | None:
    for code, hints in _CURRENCY_HINTS:
        for hint in hints:
            if hint in text:
                return code
    return None


def parse_budget(text: str, *, quantity: int | None = None) -> Budget:
    """Parse a price ceiling / target out of a text segment."""
    budget = Budget()
    if not text:
        return budget

    money = [h for h in classify_numbers(text) if h.role == "money"]
    if not money:
        # Allow a bare number when a budget cue makes the reading unambiguous,
        # e.g. "keep it under 15 each".
        if any(cue in text for cue in PER_UNIT_CUES) and any(
            cue in text for cue in ("budget", "under", "keep", "stay", "price", "cost", "spend")
        ):
            money = [
                h for h in classify_numbers(text)
                if h.role in ("quantity", "unknown") and h.value <= 500
                and re.search(r"(" + "|".join(map(re.escape, PER_UNIT_CUES)) + r")", text[h.end:h.end + 16])
            ]
        if not money:
            return budget

    hit = min(money, key=lambda h: h.value) if len(money) > 1 else money[0]
    lead = text[max(0, hit.start - 40):hit.start]
    tail = text[hit.end:hit.end + 26]
    window = lead + text[hit.start:hit.end] + tail

    budget.currency = detect_currency(window) or detect_currency(text) or "USD"
    budget.is_maximum = any(cue in lead for cue in MAX_CUES) or any(
        cue in tail for cue in ("or less", "max", "or under")
    )
    budget.approximate = any(cue in lead for cue in APPROX_CUES)
    budget.raw = _budget_raw(text, hit.start, hit.end)

    per_unit = any(cue in tail for cue in PER_UNIT_CUES) or any(cue in lead for cue in ("per unit", "unit price"))
    is_total = any(cue in tail for cue in TOTAL_CUES) or any(cue in lead for cue in TOTAL_CUES)

    if per_unit and not is_total:
        budget.per_unit, budget.basis = hit.value, "per_unit"
    elif is_total:
        budget.total, budget.basis = hit.value, "total"
        if quantity:
            budget.warnings.append("budget stated as a total, not per unit")
    else:
        # Ambiguous. Use magnitude, and always say which way we read it.
        if hit.value >= 1000 or (quantity and hit.value >= quantity * 5):
            budget.total, budget.basis = hit.value, "total"
        else:
            budget.per_unit, budget.basis = hit.value, "assumed_per_unit"
            budget.warnings.append(
                "budget basis not stated; read as per-unit based on magnitude"
            )
    return budget


def _budget_raw(text: str, start: int, end: int) -> str:
    lead_start = start
    window = text[max(0, start - 26):start]
    for cue in sorted(APPROX_CUES + MAX_CUES, key=len, reverse=True):
        idx = window.rfind(cue)
        if idx != -1 and re.fullmatch(r"[ ,]*\$?", window[idx + len(cue):]):
            lead_start = max(0, start - 26) + idx
            break
    if text[max(0, lead_start - 1):lead_start] == "$":
        lead_start -= 1
    tail_match = re.match(
        r"^\s*(bucks|dollars|usd|each|apiece|a piece|per piece|per unit|/unit|ea)\b",
        text[end:end + 14],
    )
    tail_end = end + (tail_match.end() if tail_match else 0)
    return text[lead_start:tail_end].strip()
