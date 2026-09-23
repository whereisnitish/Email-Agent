"""The output contract.

Two serialisations are produced from the same objects:

* ``to_spec_dict()`` -- exactly the field set in the brief. Stable, minimal,
  what a downstream automation should bind to.
* ``to_dict()``      -- the same data plus provenance: raw customer wording,
  range bounds, precision flags, warnings and engine metadata. Useful for
  review queues, audits and tuning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------

INTENTS: tuple[str, ...] = (
    "product_request",
    "quote_request",
    "product_information",
    "order_status",
    "revision_request",
    "general_question",
    "complaint",
    "multiple_requests",
    "unknown",
)

#: Intents whose emails are expected to carry sourcing requirements.
SOURCING_INTENTS = frozenset({
    "product_request", "quote_request", "multiple_requests",
    "product_information", "revision_request",
})

#: Intents that should reference an existing order.
ORDER_INTENTS = frozenset({"order_status", "revision_request", "complaint"})

DELIVERY_PRECISIONS = ("day", "week", "month", "quarter", "asap", None)


def _clean_list(values: Any) -> list[str]:
    """De-duplicate, strip and order-preserve a list of strings."""
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {v.lower() for v in out}:
            out.append(text)
    return out


# ---------------------------------------------------------------------------

@dataclass
class ProductRequirement:
    """One purchasable line item the customer is asking about."""

    product_type: str | None = None
    quantity: int | None = None
    materials: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    sizes: list[str] = field(default_factory=list)
    budget_per_unit: float | None = None
    currency: str | None = None
    decoration: str | None = None
    delivery_deadline: str | None = None
    special_requirements: list[str] = field(default_factory=list)

    # --- provenance / precision (full output only) -------------------------
    quantity_min: int | None = None
    quantity_max: int | None = None
    quantity_is_approximate: bool = False
    quantity_raw: str | None = None
    budget_total: float | None = None
    budget_is_maximum: bool = False
    budget_is_approximate: bool = False
    budget_basis: str | None = None
    delivery_deadline_raw: str | None = None
    delivery_precision: str | None = None
    product_type_is_specific: bool = True
    #: True when this line describes goods from an order that already exists
    #: (a complaint or a status chase), not a new thing to source.
    refers_to_existing_order: bool = False
    request_type: str = "product_request"
    evidence: str | None = None
    warnings: list[str] = field(default_factory=list)

    def normalize(self) -> None:
        self.materials = _clean_list(self.materials)
        self.colors = _clean_list(self.colors)
        self.sizes = _clean_list(self.sizes)
        self.special_requirements = _clean_list(self.special_requirements)
        self.warnings = _clean_list(self.warnings)
        if self.product_type:
            self.product_type = str(self.product_type).strip().lower() or None
        if self.quantity is not None:
            self.quantity = int(self.quantity)
        for attr in ("budget_per_unit", "budget_total"):
            value = getattr(self, attr)
            if value is not None:
                value = round(float(value), 2)
                setattr(self, attr, int(value) if value == int(value) else value)
        if (self.budget_per_unit is not None or self.budget_total is not None) and not self.currency:
            self.currency = "USD"
        if self.decoration is not None and not str(self.decoration).strip():
            self.decoration = None

    def missing_fields(self, required: tuple[str, ...]) -> list[str]:
        missing: list[str] = []
        for name in required:
            value = getattr(self, name, None)
            if value is None or (isinstance(value, list) and not value):
                missing.append(name)
            elif name == "product_type" and not self.product_type_is_specific:
                missing.append("product_specification")
        return missing

    def to_spec_dict(self) -> dict[str, Any]:
        return {
            "product_type": self.product_type,
            "quantity": self.quantity,
            "materials": self.materials,
            "colors": self.colors,
            "sizes": self.sizes,
            "budget_per_unit": self.budget_per_unit,
            "currency": self.currency,
            "decoration": self.decoration,
            "delivery_deadline": self.delivery_deadline,
            "special_requirements": self.special_requirements,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.to_spec_dict()
        payload.update({
            "quantity_min": self.quantity_min,
            "quantity_max": self.quantity_max,
            "quantity_is_approximate": self.quantity_is_approximate,
            "quantity_raw": self.quantity_raw,
            "budget_total": self.budget_total,
            "budget_is_maximum": self.budget_is_maximum,
            "budget_is_approximate": self.budget_is_approximate,
            "budget_basis": self.budget_basis,
            "delivery_deadline_raw": self.delivery_deadline_raw,
            "delivery_precision": self.delivery_precision,
            "product_type_is_specific": self.product_type_is_specific,
            "refers_to_existing_order": self.refers_to_existing_order,
            "request_type": self.request_type,
            "evidence": self.evidence,
            "warnings": self.warnings,
        })
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductRequirement:
        known = set(cls.__dataclass_fields__)
        item = cls(**{k: v for k, v in data.items() if k in known})
        item.normalize()
        return item


@dataclass
class CustomerPreferences:
    preferred_suppliers: list[str] = field(default_factory=list)
    preferred_brand: str | None = None
    previous_order_reference: bool = False

    def normalize(self) -> None:
        self.preferred_suppliers = _clean_list(self.preferred_suppliers)
        if self.preferred_brand is not None:
            self.preferred_brand = str(self.preferred_brand).strip() or None
        self.previous_order_reference = bool(self.previous_order_reference)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preferred_suppliers": self.preferred_suppliers,
            "preferred_brand": self.preferred_brand,
            "previous_order_reference": self.previous_order_reference,
        }


@dataclass
class ExtractionMeta:
    """Provenance -- how this result was produced, and how much to trust it."""

    provider: str = "heuristic"
    model: str = "rule-engine"
    llm_used: bool = False
    fallback_reason: str | None = None
    used_thread_context: bool = False
    thread_message_count: int = 0
    grounding_score: float = 1.0
    reference_date: str | None = None
    duration_ms: int = 0
    confidence_breakdown: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    processed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "llm_used": self.llm_used,
            "fallback_reason": self.fallback_reason,
            "used_thread_context": self.used_thread_context,
            "thread_message_count": self.thread_message_count,
            "grounding_score": round(self.grounding_score, 3),
            "reference_date": self.reference_date,
            "duration_ms": self.duration_ms,
            "confidence_breakdown": self.confidence_breakdown,
            "warnings": _clean_list(self.warnings),
            "processed_at": self.processed_at,
        }


@dataclass
class EmailIntent:
    """The structured reading of one customer email."""

    email_id: str | None = None
    customer_intent: str = "unknown"
    summary: str = ""
    products: list[ProductRequirement] = field(default_factory=list)
    customer_preferences: CustomerPreferences = field(default_factory=CustomerPreferences)
    missing_information: list[str] = field(default_factory=list)
    confidence: float = 0.0

    # --- extras (full output only) -----------------------------------------
    sub_intents: list[str] = field(default_factory=list)
    occasion: str | None = None
    order_references: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    extraction: ExtractionMeta = field(default_factory=ExtractionMeta)

    # -- lifecycle ----------------------------------------------------------

    def normalize(self) -> None:
        if self.customer_intent not in INTENTS:
            self.customer_intent = "unknown"
        self.sub_intents = [s for s in _clean_list(self.sub_intents) if s in INTENTS]
        self.summary = " ".join(str(self.summary or "").split())
        for product in self.products:
            product.normalize()
        self.customer_preferences.normalize()
        self.missing_information = _clean_list(self.missing_information)
        self.order_references = _clean_list(self.order_references)
        self.questions = _clean_list(self.questions)
        self.confidence = round(max(0.0, min(1.0, float(self.confidence or 0.0))), 2)

    def compute_missing_information(self, required: tuple[str, ...]) -> list[str]:
        """Derive `missing_information` from what is actually absent.

        Computed, never asked of the model: a model asked to *list what is
        missing* tends to hallucinate both directions.
        """
        missing: list[str] = []

        if self.customer_intent in SOURCING_INTENTS:
            if not self.products:
                missing.extend(required)
            else:
                multi = len(self.products) > 1
                for index, product in enumerate(self.products):
                    prefix = f"products[{index}]." if multi else ""
                    missing.extend(prefix + name for name in product.missing_fields(required))

        if self.customer_intent in ORDER_INTENTS and not self.order_references:
            missing.append("order_reference")

        seen: set[str] = set()
        return [m for m in missing if not (m in seen or seen.add(m))]

    def validate(self) -> list[str]:
        """Structural self-check. Empty list means the payload is contract-safe."""
        errors: list[str] = []
        if self.customer_intent not in INTENTS:
            errors.append(f"customer_intent {self.customer_intent!r} is not a known intent")
        if not isinstance(self.confidence, (int, float)) or not 0.0 <= self.confidence <= 1.0:
            errors.append("confidence must be a float between 0 and 1")
        for index, product in enumerate(self.products):
            if product.quantity is not None and product.quantity <= 0:
                errors.append(f"products[{index}].quantity must be positive")
            if product.budget_per_unit is not None and product.budget_per_unit <= 0:
                errors.append(f"products[{index}].budget_per_unit must be positive")
            if product.delivery_deadline:
                try:
                    datetime.strptime(product.delivery_deadline, "%Y-%m-%d")
                except ValueError:
                    errors.append(f"products[{index}].delivery_deadline must be YYYY-MM-DD")
            if product.delivery_precision not in DELIVERY_PRECISIONS:
                errors.append(f"products[{index}].delivery_precision is invalid")
            if (product.quantity_min is not None and product.quantity_max is not None
                    and product.quantity_min > product.quantity_max):
                errors.append(f"products[{index}] quantity range is inverted")
        return errors

    # -- serialisation ------------------------------------------------------

    def to_spec_dict(self) -> dict[str, Any]:
        """Exactly the shape defined in the brief."""
        return {
            "customer_intent": self.customer_intent,
            "summary": self.summary,
            "products": [p.to_spec_dict() for p in self.products],
            "customer_preferences": self.customer_preferences.to_dict(),
            "missing_information": self.missing_information,
            "confidence": self.confidence,
        }

    def to_dict(self) -> dict[str, Any]:
        """Spec fields plus provenance."""
        return {
            "email_id": self.email_id,
            "customer_intent": self.customer_intent,
            "sub_intents": self.sub_intents,
            "summary": self.summary,
            "occasion": self.occasion,
            "products": [p.to_dict() for p in self.products],
            "customer_preferences": self.customer_preferences.to_dict(),
            "order_references": self.order_references,
            "questions": self.questions,
            "missing_information": self.missing_information,
            "confidence": self.confidence,
            "extraction": self.extraction.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmailIntent:
        preferences = CustomerPreferences(**{
            k: v for k, v in (data.get("customer_preferences") or {}).items()
            if k in CustomerPreferences.__dataclass_fields__
        })
        intent = cls(
            email_id=data.get("email_id"),
            customer_intent=data.get("customer_intent", "unknown"),
            summary=data.get("summary", ""),
            products=[ProductRequirement.from_dict(p) for p in data.get("products") or []],
            customer_preferences=preferences,
            missing_information=data.get("missing_information") or [],
            confidence=data.get("confidence") or 0.0,
            sub_intents=data.get("sub_intents") or [],
            occasion=data.get("occasion"),
            order_references=data.get("order_references") or [],
            questions=data.get("questions") or [],
        )
        intent.normalize()
        return intent


# ---------------------------------------------------------------------------
# JSON Schema -- published for downstream consumers and for providers that
# support native structured output (Gemini responseSchema).
# ---------------------------------------------------------------------------

JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "customer_intent", "summary", "products", "customer_preferences",
        "missing_information", "confidence",
    ],
    "properties": {
        "customer_intent": {"type": "string", "enum": list(INTENTS)},
        "summary": {"type": "string"},
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_type": {"type": ["string", "null"]},
                    "quantity": {"type": ["integer", "null"]},
                    "materials": {"type": "array", "items": {"type": "string"}},
                    "colors": {"type": "array", "items": {"type": "string"}},
                    "sizes": {"type": "array", "items": {"type": "string"}},
                    "budget_per_unit": {"type": ["number", "null"]},
                    "currency": {"type": ["string", "null"]},
                    "decoration": {"type": ["string", "null"]},
                    "delivery_deadline": {"type": ["string", "null"], "format": "date"},
                    "special_requirements": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "customer_preferences": {
            "type": "object",
            "properties": {
                "preferred_suppliers": {"type": "array", "items": {"type": "string"}},
                "preferred_brand": {"type": ["string", "null"]},
                "previous_order_reference": {"type": "boolean"},
            },
        },
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}
