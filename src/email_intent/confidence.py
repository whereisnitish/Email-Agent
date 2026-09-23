"""Confidence scoring.

Confidence answers "how likely is this reading of the email correct?", not
"how complete was the customer's information". A crisp two-line email that
omits the budget should still score high -- the omission shows up in
`missing_information`, not as doubt about what was extracted.

The score is a transparent weighted blend plus explicit penalties, and the
breakdown travels with the result so a reviewer can see why.
"""

from __future__ import annotations

from typing import Any

from .config import Config
from .schema import SOURCING_INTENTS, EmailIntent

WEIGHTS = {
    "model_signal": 0.30,   # the model's own confidence (or the rule baseline)
    "grounding": 0.30,      # share of values traceable to the email text
    "agreement": 0.25,      # LLM reading vs independent rule-engine reading
    "completeness": 0.15,   # how much of the sourcing picture came through
}

RULE_ENGINE_BASELINE = 0.85

PENALTIES = {
    "unknown_intent": 0.30,
    "vague_product": 0.25,
    "no_products": 0.15,
    "validation_errors": 0.20,
    "llm_fallback": 0.05,
    "fuzzy_product_match": 0.05,   # per product, capped at 2
    "assumed_budget_basis": 0.03,
}

FLOOR, CEILING = 0.05, 0.99


def _agreement(intent: EmailIntent, rules: EmailIntent | None) -> float:
    """How much an independent rule-based reading backs up the LLM reading."""
    if rules is None:
        return 1.0
    score = 0.0
    if intent.customer_intent == rules.customer_intent:
        score += 0.5
    elif intent.customer_intent in rules.sub_intents or rules.customer_intent in intent.sub_intents:
        score += 0.25

    llm_types = {p.product_type for p in intent.products if p.product_type}
    rule_types = {p.product_type for p in rules.products if p.product_type}
    if llm_types or rule_types:
        overlap = len(llm_types & rule_types) / max(len(llm_types | rule_types), 1)
        score += 0.3 * overlap
    else:
        score += 0.3

    llm_quantities = sorted(p.quantity for p in intent.products if p.quantity)
    rule_quantities = sorted(p.quantity for p in rules.products if p.quantity)
    if llm_quantities or rule_quantities:
        score += 0.2 if llm_quantities == rule_quantities else 0.0
    else:
        score += 0.2
    return min(1.0, score)


def _completeness(intent: EmailIntent, required: tuple[str, ...]) -> float:
    if intent.customer_intent not in SOURCING_INTENTS:
        return 1.0 if intent.customer_intent != "unknown" else 0.0
    if not intent.products:
        return 0.0
    filled = 0
    total = 0
    for product in intent.products:
        for name in required:
            total += 1
            value = getattr(product, name, None)
            if value is not None and value != []:
                filled += 1
    return filled / total if total else 0.0


def score(
    intent: EmailIntent,
    *,
    rules: EmailIntent | None = None,
    model_confidence: float | None = None,
    grounding_score: float = 1.0,
    validation_errors: int = 0,
    llm_used: bool = False,
    fallback: bool = False,
    config: Config | None = None,
) -> tuple[float, dict[str, Any]]:
    config = config or Config()

    signal = model_confidence if (llm_used and model_confidence is not None) else RULE_ENGINE_BASELINE
    signal = max(0.0, min(1.0, float(signal)))

    components = {
        "model_signal": signal,
        "grounding": max(0.0, min(1.0, grounding_score)),
        "agreement": _agreement(intent, rules),
        "completeness": _completeness(intent, config.required_product_fields),
    }
    base = sum(WEIGHTS[name] * value for name, value in components.items())

    applied: dict[str, float] = {}
    if intent.customer_intent == "unknown":
        applied["unknown_intent"] = PENALTIES["unknown_intent"]
    if any(not p.product_type_is_specific for p in intent.products):
        applied["vague_product"] = PENALTIES["vague_product"]
    if intent.customer_intent in SOURCING_INTENTS and not intent.products:
        applied["no_products"] = PENALTIES["no_products"]
    if validation_errors:
        applied["validation_errors"] = PENALTIES["validation_errors"]
    if fallback:
        applied["llm_fallback"] = PENALTIES["llm_fallback"]
    fuzzy = sum(
        1 for p in intent.products
        if any("spelling similarity" in w for w in p.warnings)
    )
    if fuzzy:
        applied["fuzzy_product_match"] = PENALTIES["fuzzy_product_match"] * min(fuzzy, 2)
    if any(p.budget_basis == "assumed_per_unit" for p in intent.products):
        applied["assumed_budget_basis"] = PENALTIES["assumed_budget_basis"]

    final = max(FLOOR, min(CEILING, base - sum(applied.values())))
    breakdown = {
        "components": {k: round(v, 3) for k, v in components.items()},
        "weights": WEIGHTS,
        "base": round(base, 3),
        "penalties": {k: round(v, 3) for k, v in applied.items()},
        "final": round(final, 2),
    }
    return round(final, 2), breakdown
