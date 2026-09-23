"""Text preparation: email cleanup, tokenisation, vocabulary matching.

The matcher is span-aware -- every hit knows *where* in the email it came
from. Positions are what let the extractor attach "black" to the tumblers
rather than to the hats mentioned two sentences later.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .vocab import CONTRACTIONS

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

_QUOTE_HEADERS = (
    re.compile(r"^\s*on .{0,120}?wrote:\s*$", re.I | re.M),
    re.compile(r"^\s*-{2,}\s*original message\s*-{2,}\s*$", re.I | re.M),
    re.compile(r"^\s*_{5,}\s*$", re.M),
    re.compile(r"^\s*from:\s.+$", re.I | re.M),
    re.compile(r"^\s*sent from my \w+.*$", re.I | re.M),
)

_SIGNATURE_MARKERS = (
    re.compile(r"^\s*--\s*$", re.M),
    re.compile(r"^\s*(thanks|thank you|regards|best|cheers|sincerely|talk soon)[,!.]?\s*$", re.I | re.M),
)

_WHITESPACE = re.compile(r"[ \t ]+")
_DASHES = re.compile(r"[‐-―−]")
_QUOTES = re.compile(r"[‘’‛]")
_DQUOTES = re.compile(r"[“”]")


def strip_quoted_reply(body: str) -> str:
    """Remove quoted history and trailing signature blocks from a reply body.

    Quoted history is *not* discarded by the pipeline -- `thread.py` keeps it
    as explicit context. Removing it here stops the current-message extraction
    from re-reading last month's numbers as if they were new requirements.
    """
    if not body:
        return ""
    text = body.replace("\r\n", "\n")

    cut = len(text)
    for pattern in _QUOTE_HEADERS:
        match = pattern.search(text)
        if match and match.start() < cut:
            cut = match.start()
    text = text[:cut]

    # Drop ">"-prefixed quote lines wherever they survived.
    lines = [ln for ln in text.split("\n") if not ln.lstrip().startswith(">")]
    text = "\n".join(lines)

    # Trim a signature block, but only if real content precedes it.
    for pattern in _SIGNATURE_MARKERS:
        for match in pattern.finditer(text):
            head = text[: match.start()].strip()
            if len(head) >= 40:
                text = head
                break
    return text.strip()


def normalize_text(text: str) -> str:
    """Lowercase, expand contractions, and flatten punctuation variants.

    Returned text is the same *length semantics* as the caller needs for
    matching; it is not span-aligned with the original, so all downstream
    span work happens against this normalised string.
    """
    if not text:
        return ""
    text = _DASHES.sub("-", text)
    text = _QUOTES.sub("'", text)
    text = _DQUOTES.sub('"', text)
    text = text.lower()
    for short, long in CONTRACTIONS.items():
        text = re.sub(rf"\b{re.escape(short)}\b", long, text)
    text = _WHITESPACE.sub(" ", text)
    return text


_PLURAL_EXCEPTIONS = {
    "glass": "glass", "dress": "dress", "class": "class", "address": "address",
    "business": "business", "bus": "bus", "gas": "gas", "less": "less",
    "socks": "socks", "sunglasses": "sunglasses", "specs": "specs",
    "sales": "sales", "thanks": "thanks", "pcs": "pcs", "this": "this",
    "yes": "yes", "us": "us", "as": "as", "is": "is", "has": "has", "was": "was",
    "prices": "price", "boxes": "box", "watches": "watch", "sizes": "size",
}


def singularize(word: str) -> str:
    """Cheap, domain-appropriate singulariser (no external dependency)."""
    lower = word.lower()
    if lower in _PLURAL_EXCEPTIONS:
        return _PLURAL_EXCEPTIONS[lower]
    if len(lower) > 4 and lower.endswith("ies"):
        return lower[:-3] + "y"
    if len(lower) > 4 and lower.endswith(("ches", "shes", "sses", "xes")):
        return lower[:-2]
    if len(lower) > 3 and lower.endswith("s") and not lower.endswith("ss"):
        return lower[:-1]
    return lower


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'./\-]*")


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    return [Token(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Vocabulary matching
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Match:
    """A vocabulary hit located in the normalised text."""

    canonical: str
    matched_text: str
    start: int
    end: int
    fuzzy: bool = False
    score: float = 1.0

    def overlaps(self, other: Match) -> bool:
        return self.start < other.end and other.start < self.end


def _alias_index(vocab: dict[str, tuple[str, ...]]) -> list[tuple[str, str]]:
    """Flatten {canonical: aliases} into [(alias, canonical)], longest first."""
    pairs = [(alias, canonical) for canonical, aliases in vocab.items() for alias in aliases]
    pairs.sort(key=lambda p: (-len(p[0]), p[0]))
    return pairs


_INDEX_CACHE: dict[int, list[tuple[str, str]]] = {}


def alias_index(vocab: dict[str, tuple[str, ...]]) -> list[tuple[str, str]]:
    key = id(vocab)
    cached = _INDEX_CACHE.get(key)
    if cached is None:
        cached = _alias_index(vocab)
        _INDEX_CACHE[key] = cached
    return cached


def find_matches(
    text: str,
    vocab: dict[str, tuple[str, ...]],
    *,
    fuzzy: bool = False,
    fuzzy_threshold: float = 0.80,
    min_fuzzy_len: int = 5,
) -> list[Match]:
    """Find all vocabulary hits in `text`, longest-first, non-overlapping.

    With `fuzzy=True`, unmatched word tokens are compared against the alias
    list so misspellings ("tumbelers", "hoodys", "backpaks") still resolve.
    """
    matches: list[Match] = []
    for alias, canonical in alias_index(vocab):
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])")
        for hit in pattern.finditer(text):
            candidate = Match(canonical, hit.group(0), hit.start(), hit.end())
            if not any(candidate.overlaps(existing) for existing in matches):
                matches.append(candidate)

    if fuzzy:
        matches.extend(
            _fuzzy_matches(text, vocab, matches, fuzzy_threshold, min_fuzzy_len)
        )

    matches.sort(key=lambda m: m.start)
    return matches


def _fuzzy_matches(
    text: str,
    vocab: dict[str, tuple[str, ...]],
    existing: list[Match],
    threshold: float,
    min_len: int,
) -> list[Match]:
    aliases = alias_index(vocab)
    single_word = [(a, c) for a, c in aliases if " " not in a and len(a) >= 3]
    found: list[Match] = []
    for token in tokenize(text):
        word = token.text.strip(".-'")
        if len(word) < min_len or word.isdigit():
            continue
        probe = Match(canonical="", matched_text=word, start=token.start, end=token.end)
        if any(probe.overlaps(m) for m in existing) or any(probe.overlaps(m) for m in found):
            continue
        best_score, best_canonical, = 0.0, None
        stem = singularize(word)
        for alias, canonical in single_word:
            score = max(
                difflib.SequenceMatcher(None, word, alias).ratio(),
                difflib.SequenceMatcher(None, stem, singularize(alias)).ratio(),
            )
            if score > best_score:
                best_score, best_canonical = score, canonical
        if best_canonical and best_score >= threshold:
            found.append(
                Match(best_canonical, word, token.start, token.end, fuzzy=True, score=best_score)
            )
    return found


def contains_any(text: str, phrases: tuple[str, ...] | list[str]) -> str | None:
    """Return the first phrase present in `text` as a whole word, else None."""
    for phrase in phrases:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text):
            return phrase
    return None


def sentences(text: str) -> list[tuple[str, int, int]]:
    """Split into (sentence, start, end). Newlines count as breaks."""
    out: list[tuple[str, int, int]] = []
    start = 0
    for match in re.finditer(r"[.!?\n]+", text):
        end = match.end()
        chunk = text[start:end].strip()
        if chunk:
            out.append((chunk, start, end))
        start = end
    tail = text[start:].strip()
    if tail:
        out.append((tail, start, len(text)))
    return out


def snippet(text: str, start: int, end: int, pad: int = 40) -> str:
    """A readable evidence window around a span, for audit trails."""
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    prefix = "..." if left > 0 else ""
    suffix = "..." if right < len(text) else ""
    return f"{prefix}{text[left:right].strip()}{suffix}"
