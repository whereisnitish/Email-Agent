"""Anti-hallucination: every extracted value must trace back to the email.

The brief's hardest requirement is "do not invent information the customer did
not provide". Prompting alone does not guarantee that, so each value is checked
against the source text before it is allowed into the output:

* vocabulary values (colors, materials, product types, decoration) are grounded
  through their aliases -- "tees" grounds "t-shirt";
* numbers are grounded on their digits actually occurring in the email;
* free text is grounded on content-word overlap.

Ungrounded values are dropped (default) or flagged, and always lower confidence.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from .config import Config
from .schema import EmailIntent
from .textutil import normalize_text, singularize, tokenize
from .vocab import (
    COLORS,
    DECORATIONS,
    MATERIALS,
    PREVIOUS_ORDER_REQUIREMENT,
    PRODUCT_TYPES,
    references_previous_order,
)

STOPWORDS = frozenset({
    "a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "with", "be",
    "is", "are", "as", "at", "by", "from", "that", "this", "it", "our", "we",
    "us", "you", "your", "please", "need", "want", "would", "like", "similar",
    "product", "products", "item", "items", "required", "requirement",
})


@dataclass
class GroundingReport:
    checked: int = 0
    grounded: int = 0
    dropped: list[tuple[str, str]] = field(default_factory=list)
    flagged: list[tuple[str, str]] = field(default_factory=list)

    @property
    def score(self) -> float:
        if not self.checked:
            return 1.0
        return self.grounded / self.checked

    def summary(self) -> list[str]:
        return [
            f"dropped ungrounded {location} value {value!r} (not found in the email)"
            for location, value in self.dropped
        ] + [
            f"unverified {location} value {value!r}" for location, value in self.flagged
        ]


# ---------------------------------------------------------------------------

def _present(text: str, phrase: str, threshold: float) -> bool:
    """True if `phrase` occurs in `text` exactly or as a near-spelling."""
    phrase = phrase.strip().lower()
    if not phrase:
        return False
    if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text):
        return True
    if " " in phrase:
        # Multi-word: every word must appear somewhere (order-free).
        return all(_present(text, word, threshold) for word in phrase.split())
    stem = singularize(phrase)
    for token in tokenize(text):
        word = token.text
        if word == phrase or singularize(word) == stem:
            return True
        if len(phrase) >= 5 and len(word) >= 5:
            if difflib.SequenceMatcher(None, word, phrase).ratio() >= threshold:
                return True
            if difflib.SequenceMatcher(None, singularize(word), stem).ratio() >= threshold:
                return True
    return False


def _grounded_by_vocab(
    value: str, text: str, vocab: dict[str, tuple[str, ...]], threshold: float
) -> bool:
    """Ground a canonical value through any of its aliases ("tees" -> t-shirt)."""
    aliases = vocab.get(value.lower())
    if aliases:
        return any(_present(text, alias, threshold) for alias in aliases)
    return _present(text, value, threshold)


def _grounded_free_text(value: str, text: str, threshold: float) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", value.lower()) if w not in STOPWORDS]
    if not words:
        return True
    return any(_present(text, word, threshold) for word in words)


def _digits_present(value: float, text: str) -> bool:
    rendered = f"{int(value)}" if float(value) == int(value) else f"{value}"
    compact = text.replace(",", "")
    return bool(re.search(rf"(?<![\d.]){re.escape(rendered)}(?![\d])", compact))


# ---------------------------------------------------------------------------

def verify(intent: EmailIntent, source_text: str, config: Config | None = None) -> GroundingReport:
    """Check every value in `intent` against `source_text`, in place."""
    config = config or Config()
    text = normalize_text(source_text)
    threshold = config.grounding_similarity
    report = GroundingReport()
    drop = config.drop_ungrounded_values

    def check(value: str, location: str, vocab: dict[str, tuple[str, ...]] | None) -> bool:
        report.checked += 1
        if vocab is not None:
            ok = _grounded_by_vocab(value, text, vocab, threshold)
        else:
            ok = _grounded_free_text(value, text, threshold)
        if ok:
            report.grounded += 1
        else:
            (report.dropped if drop else report.flagged).append((location, value))
        return ok

    for index, product in enumerate(intent.products):
        where = f"products[{index}]"

        if product.product_type and drop and not check(
            product.product_type, f"{where}.product_type", PRODUCT_TYPES
        ):
            product.product_type = None

        product.colors = [
            c for c in product.colors if check(c, f"{where}.colors", COLORS) or not drop
        ]
        product.materials = [
            m for m in product.materials if check(m, f"{where}.materials", MATERIALS) or not drop
        ]
        product.sizes = [
            s for s in product.sizes if check(s, f"{where}.sizes", None) or not drop
        ]

        if product.decoration and drop and not check(
            product.decoration, f"{where}.decoration", DECORATIONS
        ):
            product.decoration = None

        kept_requirements = []
        for requirement in product.special_requirements:
            report.checked += 1
            if requirement == PREVIOUS_ORDER_REQUIREMENT:
                ok = references_previous_order(text)
            else:
                ok = _grounded_free_text(requirement, text, threshold)
            if ok:
                report.grounded += 1
                kept_requirements.append(requirement)
            else:
                (report.dropped if drop else report.flagged).append(
                    (f"{where}.special_requirements", requirement)
                )
                if not drop:
                    kept_requirements.append(requirement)
        product.special_requirements = kept_requirements

        for attribute in ("quantity", "budget_per_unit", "budget_total"):
            value = getattr(product, attribute)
            if value is None:
                continue
            report.checked += 1
            if _digits_present(value, text):
                report.grounded += 1
            else:
                (report.dropped if drop else report.flagged).append(
                    (f"{where}.{attribute}", str(value))
                )
                if drop:
                    setattr(product, attribute, None)
                    product.warnings.append(
                        f"{attribute} was discarded: no matching number in the email"
                    )

    preferences = intent.customer_preferences
    if preferences.preferred_brand and drop and not check(
        preferences.preferred_brand, "customer_preferences.preferred_brand", None
    ):
        preferences.preferred_brand = None
    preferences.preferred_suppliers = [
        s for s in preferences.preferred_suppliers
        if check(s, "customer_preferences.preferred_suppliers", None) or not drop
    ]
    if preferences.previous_order_reference:
        report.checked += 1
        if references_previous_order(text):
            report.grounded += 1
        else:
            (report.dropped if drop else report.flagged).append(
                ("customer_preferences.previous_order_reference", "true")
            )
            if drop:
                preferences.previous_order_reference = False

    intent.extraction.grounding_score = report.score
    intent.extraction.warnings.extend(report.summary())
    return report
