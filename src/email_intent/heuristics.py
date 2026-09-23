"""Deterministic rule engine.

Runs standalone (no API key, no network) and doubles as the cross-check for
the LLM path. It is span-aware: every attribute is attached to the product it
sits next to in the sentence, which is what keeps "200 polos and 100 hats"
from collapsing into one muddled line item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from . import dates as dateutil
from .config import Config
from .numbers import classify_numbers, parse_budget, parse_quantity
from .schema import CustomerPreferences, EmailIntent, ProductRequirement
from .textutil import (
    Match,
    contains_any,
    find_matches,
    normalize_text,
    sentences,
    snippet,
)
from .vocab import (
    AMBIGUOUS_SIZE_TOKENS,
    APPAREL_PRODUCTS,
    APPAREL_SIZES,
    COLORS,
    DECORATIONS,
    GENERIC_DECORATION,
    INTENT_CUES,
    KNOWN_BRANDS,
    MATERIALS,
    OCCASIONS,
    PREVIOUS_ORDER_REQUIREMENT,
    PRODUCT_TYPES,
    VAGUE_PRODUCT_TYPES,
    references_previous_order,
)

# ---------------------------------------------------------------------------

SPECIAL_REQUIREMENT_CUES: dict[str, tuple[str, ...]] = {
    "Delivery ASAP": ("asap", "as soon as possible", "right away", "urgently", "rush order", "rush"),
    "Individually packaged": ("individually packaged", "individually bagged", "polybag", "poly bag"),
    "Ship to multiple locations": (
        "multiple locations", "several addresses", "split shipment", "drop ship", "dropship",
    ),
    "Pre-production sample required": (
        "pre production sample", "pre-production sample", "sample first", "send a sample",
        "see a sample", "proof first", "virtual proof",
    ),
    "Made in USA": ("made in usa", "made in the usa", "domestic production", "us made"),
    "Union made": ("union made", "union-made"),
    "Eco-friendly / sustainable materials": (
        "eco friendly", "eco-friendly", "sustainable", "recycled content", "environmentally friendly",
    ),
    "Size breakdown to be confirmed": (
        "size breakdown", "sizes tbd", "assorted sizes", "mix of sizes", "size run",
    ),
    "Gift packaging required": ("gift box", "gift wrapped", "gift packaging", "presentation box"),
    "Setup/artwork already on file": ("on file", "you have our art", "same artwork"),
}

QUESTION_RE = re.compile(r"[^.!?\n]*\?")
ORDER_REF_RE = re.compile(
    r"(?:\b(?:po|p\.o\.|purchase order|order|invoice|quote|ref|reference|job)\s*#?\s*|#)"
    r"([a-z]{0,4}[-]?\d{3,}[a-z0-9-]*)"
)
REVISION_QUANTITY_RE = re.compile(
    r"(?:make it|change it to|changed to|bump (?:it|the quantity|the qty)? ?to|"
    r"increase (?:it |the quantity |the qty )?to|go (?:up )?to|update it to)\s+(?P<n>\d[\d,]*)"
)
#: Supplier names are read from the ORIGINAL-cased text: a run of up to three
#: capitalised words after a sourcing phrase ("we usually order from Summit
#: Supply but..." -> "Summit Supply").
SUPPLIER_RE = re.compile(
    r"(?:supplier|vendor|buy from|order from|source from|purchase from|we use|"
    r"we usually use|go through)\s+"
    r"((?:[A-Z][A-Za-z0-9&.\-]*)(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,2})"
)


@dataclass
class ProductMention:
    """A product word found in the email, with the region of text it owns."""

    canonical: str
    match: Match
    seg_start: int
    seg_end: int


# ---------------------------------------------------------------------------

def pluralize(word: str) -> str:
    if not word or word.endswith("s"):
        return word
    if word.endswith(("ch", "sh", "x", "z")):
        return word + "es"
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


class RuleEngine:
    """Extracts structured requirements without calling any model."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()

    # -- entry point --------------------------------------------------------

    def extract(
        self,
        body: str,
        *,
        subject: str = "",
        reference_date: date | None = None,
        context_text: str = "",
        original_text: str | None = None,
        email_id: str | None = None,
    ) -> EmailIntent:
        reference = reference_date or date.today()
        raw = f"{subject}\n{body}" if subject else body
        text = normalize_text(raw)
        context = normalize_text(context_text) if context_text else ""
        original = original_text if original_text is not None else raw

        mentions = self._product_mentions(text)
        global_region = self._global_region(text, mentions)

        products = [
            self._build_product(text, mention, global_region, reference)
            for mention in mentions
        ]
        products = self._merge_duplicates(products)
        products = self._gate_vague_products(products, text)
        if not products:
            fallback = self._unspecified_product(text, reference)
            if fallback is not None:
                products = [fallback]
        self._apply_revision_quantities(products, text)

        intent, sub_intents = self._classify(text, products)
        preferences = self._preferences(text, context, original)
        occasion = self._occasion(text)

        result = EmailIntent(
            email_id=email_id,
            customer_intent=intent,
            sub_intents=sub_intents,
            summary=self.summarize(intent, products, occasion),
            products=products,
            customer_preferences=preferences,
            occasion=occasion,
            order_references=self._order_references(text + " " + context),
            questions=self._questions(raw),
            confidence=0.0,
        )

        if preferences.previous_order_reference:
            for product in result.products:
                if PREVIOUS_ORDER_REQUIREMENT not in product.special_requirements:
                    product.special_requirements.append(PREVIOUS_ORDER_REQUIREMENT)

        result.normalize()
        result.missing_information = result.compute_missing_information(
            self.config.required_product_fields
        )
        return result

    # -- product discovery --------------------------------------------------

    def _product_mentions(self, text: str) -> list[ProductMention]:
        matches = find_matches(
            text, PRODUCT_TYPES, fuzzy=True, fuzzy_threshold=self.config.fuzzy_threshold
        )
        matches = self._drop_spurious(text, matches)
        if not matches:
            return []

        mentions: list[ProductMention] = []
        for index, match in enumerate(matches):
            previous_end = matches[index - 1].end if index else 0
            next_start = matches[index + 1].start if index + 1 < len(matches) else len(text)
            seg_start = previous_end if index else 0
            seg_end = self._segment_end(text, match.end, next_start)
            mentions.append(ProductMention(match.canonical, match, seg_start, seg_end))
        return mentions

    @staticmethod
    def _drop_spurious(_text: str, matches: list[Match]) -> list[Match]:
        """Remove product words that are not actually product references."""
        # A vague catch-all ("promotional products", "swag") only stands in
        # when the customer named nothing concrete.
        specific = [m for m in matches if m.canonical not in VAGUE_PRODUCT_TYPES]
        return specific or matches

    @staticmethod
    def _segment_end(text: str, product_end: int, next_start: int) -> int:
        """End this product's region before the next product's own quantity.

        In "pricing for 200 polos and also 100 hats", the 100 belongs to the
        hats, so the polo segment must stop before it.
        """
        if next_start >= len(text):
            return next_start
        gap = text[product_end:next_start]
        numbers = [h for h in classify_numbers(gap) if h.role in ("quantity", "unknown")]
        if numbers:
            last = numbers[-1]
            distance_to_next = len(gap) - last.end
            if distance_to_next <= 30:
                return product_end + last.start
        return next_start

    @staticmethod
    def _global_region(text: str, mentions: list[ProductMention]) -> str:
        """Sentences that mention no product -- constraints shared by all lines.

        "...all delivered by Oct 20" applies to every line item, not just the
        one that happens to sit closest to it.
        """
        if len(mentions) <= 1:
            return ""
        spans = [(m.match.start, m.match.end) for m in mentions]
        parts: list[str] = []
        for chunk, start, end in sentences(text):
            if not any(start <= s < end for s, _ in spans):
                parts.append(chunk)
        tail_start = max(end for _, end in spans)
        tail = text[tail_start:]
        if tail.strip() and tail.strip() not in parts:
            parts.append(tail)
        return " ".join(parts)

    def _build_product(
        self,
        text: str,
        mention: ProductMention,
        global_region: str,
        reference: date,
    ) -> ProductRequirement:
        segment = text[mention.seg_start:mention.seg_end]
        offset = mention.seg_start
        local_start = mention.match.start - offset
        local_end = mention.match.end - offset

        product = ProductRequirement(
            product_type=mention.canonical,
            product_type_is_specific=mention.canonical not in VAGUE_PRODUCT_TYPES,
            evidence=snippet(text, mention.match.start, mention.match.end),
        )
        if mention.match.fuzzy:
            product.warnings.append(
                f"product word {mention.match.matched_text!r} matched by spelling similarity "
                f"({mention.match.score:.2f})"
            )

        self._apply_quantity(product, segment, local_start, local_end)
        self._apply_attributes(product, segment, mention.canonical)
        self._apply_budget(product, segment)
        self._apply_deadline(product, segment, reference)
        self._apply_special_requirements(product, segment)
        product.request_type = self._segment_request_type(segment)
        product.refers_to_existing_order = bool(
            contains_any(segment, INTENT_CUES["complaint"])
            or contains_any(segment, INTENT_CUES["order_status"])
        )

        if global_region:
            self._apply_globals(product, global_region, mention.canonical, reference)
        return product

    # -- post-processing ----------------------------------------------------

    @staticmethod
    def _gate_vague_products(products: list[ProductRequirement], text: str) -> list[ProductRequirement]:
        """Drop a catch-all like "promotional products" unless it is an ask.

        Trade publications and newsletters say "promotional products" too. A
        vague term only becomes a line item when the email is asking us for
        something.
        """
        if not products or any(p.product_type_is_specific for p in products):
            return products
        asking = (
            contains_any(text, INTENT_CUES["product_request"])
            or contains_any(text, INTENT_CUES["quote_request"])
            or contains_any(text, ("can you", "could you", "would you", "are you able"))
        )
        return products if asking else []

    def _unspecified_product(self, text: str, reference: date) -> ProductRequirement | None:
        """Build a line item when a quantity is requested but never named.

        "We want to go with Yeti this time - 75 units, 20 oz" is a real
        request. product_type stays null (it was never stated) and surfaces in
        missing_information rather than being guessed from the brand.
        """
        asking = (
            contains_any(text, INTENT_CUES["product_request"])
            or contains_any(text, INTENT_CUES["quote_request"])
        )
        if not asking:
            return None
        quantity = parse_quantity(text)
        if quantity.value is None:
            return None

        product = ProductRequirement(
            product_type=None,
            product_type_is_specific=False,
            quantity=quantity.value,
            quantity_min=quantity.minimum,
            quantity_max=quantity.maximum,
            quantity_is_approximate=quantity.approximate,
            quantity_raw=quantity.raw,
            evidence=snippet(text, 0, min(len(text), 80)),
        )
        product.warnings.append("product type was not stated in the email")
        self._apply_attributes(product, text, "")
        self._apply_budget(product, text)
        self._apply_deadline(product, text, reference)
        self._apply_special_requirements(product, text)
        product.request_type = self._segment_request_type(text)
        return product

    @staticmethod
    def _apply_revision_quantities(products: list[ProductRequirement], text: str) -> None:
        """Honour "instead of 200 can we make it 300" -- the new number wins."""
        match = REVISION_QUANTITY_RE.search(text)
        if not match or not products:
            return
        new_quantity = int(match.group("n").replace(",", ""))
        target = products[0]
        if target.quantity == new_quantity:
            return
        if target.quantity is not None:
            target.special_requirements.append(
                f"Revised quantity from {target.quantity} to {new_quantity}"
            )
        target.quantity = new_quantity
        target.quantity_raw = match.group(0).strip()
        target.quantity_min = target.quantity_max = None

    # -- attribute extraction ----------------------------------------------

    @staticmethod
    def _apply_quantity(
        product: ProductRequirement, segment: str, local_start: int, local_end: int
    ) -> None:
        # Prefer a number immediately before the product word ("150 tumblers").
        lead = segment[max(0, local_start - 34):local_start]
        quantity = parse_quantity(lead)
        if quantity.value is None:
            trail = segment[local_end:local_end + 26]
            if re.match(r"^\s*[:x=-]?\s*\d", trail):
                quantity = parse_quantity(trail)
        if quantity.value is None:
            quantity = parse_quantity(segment)

        product.quantity = quantity.value
        product.quantity_min = quantity.minimum
        product.quantity_max = quantity.maximum
        product.quantity_is_approximate = quantity.approximate
        product.quantity_raw = quantity.raw
        product.warnings.extend(quantity.warnings)

    def _apply_attributes(self, product: ProductRequirement, segment: str, canonical: str) -> None:
        product.colors = [m.canonical for m in find_matches(segment, COLORS)]
        product.materials = [m.canonical for m in find_matches(segment, MATERIALS)]
        product.sizes = self._sizes(segment, canonical)

        decorations = [m.canonical for m in find_matches(segment, DECORATIONS)]
        specific = [d for d in decorations if d != GENERIC_DECORATION]
        if specific:
            product.decoration = specific[0]
        elif decorations:
            product.decoration = GENERIC_DECORATION

    @staticmethod
    def _sizes(segment: str, canonical: str) -> list[str]:
        sizes: list[str] = []
        sizing_context = bool(
            re.search(r"\b(size|sizes|sizing|mostly|mix of|run of|breakdown|each size)\b", segment)
        )
        for match in find_matches(segment, APPAREL_SIZES):
            alias = match.matched_text.lower()
            if alias in AMBIGUOUS_SIZE_TOKENS and not sizing_context:
                continue
            if canonical not in APPAREL_PRODUCTS and alias in AMBIGUOUS_SIZE_TOKENS:
                continue
            sizes.append(match.canonical)

        # Capacity sizes: "20 oz", "16-oz"
        for hit in classify_numbers(segment):
            if hit.role != "capacity":
                continue
            unit = re.match(r"^-?\s*([a-z]+)", segment[hit.end:hit.end + 10])
            if unit:
                value = int(hit.value) if hit.value == int(hit.value) else hit.value
                sizes.append(f"{value} {unit.group(1)}")
        seen: set[str] = set()
        return [s for s in sizes if not (s in seen or seen.add(s))]

    @staticmethod
    def _apply_budget(product: ProductRequirement, segment: str) -> None:
        budget = parse_budget(segment, quantity=product.quantity)
        product.budget_per_unit = budget.per_unit
        product.budget_total = budget.total
        product.budget_is_maximum = budget.is_maximum
        product.budget_is_approximate = budget.approximate
        product.budget_basis = budget.basis
        if budget.per_unit is not None or budget.total is not None:
            product.currency = budget.currency
        product.warnings.extend(budget.warnings)

    @staticmethod
    def _apply_deadline(product: ProductRequirement, segment: str, reference: date) -> None:
        deadline = dateutil.find_delivery_deadline(segment, reference)
        if not deadline:
            return
        product.delivery_deadline = deadline.iso
        product.delivery_deadline_raw = deadline.raw
        product.delivery_precision = deadline.precision
        if deadline.note:
            product.warnings.append(deadline.note)
        if (deadline.precision == dateutil.PRECISION_RUSH
                and "Delivery ASAP" not in product.special_requirements):
            product.special_requirements.append("Delivery ASAP")

    @staticmethod
    def _apply_special_requirements(product: ProductRequirement, segment: str) -> None:
        for requirement, cues in SPECIAL_REQUIREMENT_CUES.items():
            if contains_any(segment, cues) and requirement not in product.special_requirements:
                product.special_requirements.append(requirement)

    @staticmethod
    def _segment_request_type(segment: str) -> str:
        if contains_any(segment, INTENT_CUES["quote_request"]):
            return "quote_request"
        if contains_any(segment, INTENT_CUES["product_information"]):
            return "product_information"
        return "product_request"

    def _apply_globals(
        self, product: ProductRequirement, region: str, canonical: str, reference: date
    ) -> None:
        """Fill gaps from constraints that were stated once for the whole email."""
        if product.budget_per_unit is None and product.budget_total is None:
            self._apply_budget(product, region)
        if product.delivery_deadline is None and product.delivery_precision is None:
            self._apply_deadline(product, region, reference)
        if not product.colors:
            product.colors = [m.canonical for m in find_matches(region, COLORS)]
        if not product.materials:
            product.materials = [m.canonical for m in find_matches(region, MATERIALS)]
        if not product.sizes:
            product.sizes = self._sizes(region, canonical)
        if product.decoration is None:
            decorations = [m.canonical for m in find_matches(region, DECORATIONS)]
            specific = [d for d in decorations if d != GENERIC_DECORATION]
            product.decoration = specific[0] if specific else (decorations[0] if decorations else None)
        self._apply_special_requirements(product, region)

    @staticmethod
    def _merge_duplicates(products: list[ProductRequirement]) -> list[ProductRequirement]:
        """Fold repeated mentions of the same product into one line item."""
        merged: dict[str, ProductRequirement] = {}
        order: list[str] = []
        for product in products:
            key = product.product_type or ""
            existing = merged.get(key)
            if existing is None:
                merged[key] = product
                order.append(key)
                continue
            if existing.quantity is None:
                existing.quantity = product.quantity
                existing.quantity_min = product.quantity_min
                existing.quantity_max = product.quantity_max
                existing.quantity_is_approximate = product.quantity_is_approximate
                existing.quantity_raw = product.quantity_raw
            elif product.quantity is not None and product.quantity != existing.quantity:
                # Genuinely different quantities -- keep both lines.
                suffix = f"{key}#{len(order)}"
                merged[suffix] = product
                order.append(suffix)
                continue
            for attribute in ("colors", "materials", "sizes", "special_requirements"):
                combined = getattr(existing, attribute) + getattr(product, attribute)
                seen: set[str] = set()
                setattr(existing, attribute, [
                    v for v in combined if not (v.lower() in seen or seen.add(v.lower()))
                ])
            for attribute in ("budget_per_unit", "budget_total", "delivery_deadline", "decoration"):
                if getattr(existing, attribute) is None:
                    setattr(existing, attribute, getattr(product, attribute))
            if existing.currency is None:
                existing.currency = product.currency
            if existing.delivery_precision is None:
                existing.delivery_precision = product.delivery_precision
                existing.delivery_deadline_raw = product.delivery_deadline_raw
        return [merged[key] for key in order]

    # -- intent -------------------------------------------------------------

    def _classify(self, text: str, products: list[ProductRequirement]) -> tuple[str, list[str]]:
        document_intents: list[str] = []
        if contains_any(text, INTENT_CUES["complaint"]):
            document_intents.append("complaint")
        if contains_any(text, INTENT_CUES["order_status"]):
            document_intents.append("order_status")
        if contains_any(text, INTENT_CUES["revision_request"]) and (
            contains_any(text, ("order", "po ", "quote")) or ORDER_REF_RE.search(text)
        ):
            document_intents.append("revision_request")

        product_intents: list[str] = []
        for product in products:
            if product.request_type not in product_intents:
                product_intents.append(product.request_type)

        # A revision supersedes the new-request reading of the same sentence.
        if "revision_request" in document_intents and products:
            return "revision_request", ["revision_request"]

        sub_intents = document_intents + [p for p in product_intents if p not in document_intents]

        if not products:
            if document_intents:
                intent = document_intents[0]
                return intent, sub_intents or [intent]
            if contains_any(text, INTENT_CUES["general_question"]) or "?" in text:
                return "general_question", ["general_question"]
            return "unknown", []

        # A product named inside a complaint or a status chase is not a new
        # request: "40 of the mugs arrived damaged" is one complaint, not a
        # complaint plus an order for 40 mugs.
        new_asks = [
            p for p in products
            if not p.refers_to_existing_order or p.request_type != "product_request"
        ]
        if document_intents and not new_asks:
            intent = document_intents[0]
            return intent, sub_intents

        mixed_products = len(products) >= 2 and len(product_intents) >= 2
        mixed_kinds = bool(document_intents) and bool(new_asks)
        forced = self.config.multi_product_is_multiple_requests and len(products) >= 2

        if mixed_products or mixed_kinds or forced:
            return "multiple_requests", sub_intents

        precedence = ("quote_request", "product_information", "product_request")
        for candidate in precedence:
            if candidate in product_intents:
                return candidate, sub_intents
        return "product_request", sub_intents

    # -- preferences and context -------------------------------------------

    @staticmethod
    def _preferences(text: str, context: str, original: str) -> CustomerPreferences:
        preferences = CustomerPreferences()
        combined = f"{text} {context}"
        preferences.previous_order_reference = bool(
            references_previous_order(combined)
        )

        brand = contains_any(text, KNOWN_BRANDS)
        if brand:
            preferences.preferred_brand = brand.title() if brand.islower() else brand

        for match in SUPPLIER_RE.finditer(original):
            candidate = match.group(1).strip()
            if candidate and candidate.lower() not in {s.lower() for s in preferences.preferred_suppliers}:
                preferences.preferred_suppliers.append(candidate)
        return preferences

    @staticmethod
    def _occasion(text: str) -> str | None:
        hits = find_matches(text, OCCASIONS)
        if not hits:
            return None
        specific = [h for h in hits if h.canonical != "event"]
        return (specific[0] if specific else hits[0]).canonical

    @staticmethod
    def _order_references(text: str) -> list[str]:
        found: list[str] = []
        for match in ORDER_REF_RE.finditer(text):
            reference = match.group(1).strip().upper()
            if reference not in found:
                found.append(reference)
        return found

    @staticmethod
    def _questions(raw: str) -> list[str]:
        return [q.strip() for q in QUESTION_RE.findall(raw) if len(q.strip()) > 8]

    # -- summary ------------------------------------------------------------

    @staticmethod
    def summarize(
        intent: str, products: list[ProductRequirement], occasion: str | None
    ) -> str:
        def describe(product: ProductRequirement) -> str:
            parts: list[str] = []
            if product.quantity is not None:
                prefix = "approximately " if product.quantity_is_approximate else ""
                if product.quantity_min and product.quantity_max:
                    parts.append(f"{product.quantity_min}-{product.quantity_max}")
                else:
                    parts.append(f"{prefix}{product.quantity}")
            parts.extend(product.materials[:1])
            name = product.product_type or "items"
            parts.append(pluralize(name) if product.quantity != 1 else name)
            return " ".join(parts)

        tail = f" for a {occasion}" if occasion and occasion != "event" else (
            " for an event" if occasion == "event" else ""
        )

        if not products:
            phrases = {
                "order_status": "Customer is asking for a status update on an existing order.",
                "complaint": "Customer is reporting a problem with an order.",
                "general_question": "Customer is asking a general question.",
                "revision_request": "Customer is asking to change an existing order.",
                "product_information": "Customer is asking for product information.",
                "quote_request": "Customer is requesting pricing.",
            }
            return phrases.get(intent, "Customer email could not be matched to a known request type.")

        listing = "; ".join(describe(p) for p in products)
        verbs = {
            "product_request": "Customer is looking for",
            "quote_request": "Customer is requesting pricing for",
            "product_information": "Customer is asking about",
            "multiple_requests": "Customer is making multiple requests:",
            "revision_request": "Customer is asking to change an existing order for",
            "complaint": "Customer is reporting a problem with",
            "order_status": "Customer is asking for a status update on",
        }
        verb = verbs.get(intent, "Customer is asking about")
        return f"{verb} {listing}{tail}."


def extract_with_rules(body: str, **kwargs) -> EmailIntent:
    """Convenience wrapper around :class:`RuleEngine`."""
    return RuleEngine(kwargs.pop("config", None)).extract(body, **kwargs)
