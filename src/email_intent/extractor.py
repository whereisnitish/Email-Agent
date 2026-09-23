"""The extraction pipeline.

    detect email -> assemble thread context -> rule engine (always)
                 -> LLM semantic extraction (when configured)
                 -> deterministic normalisation of every number and date
                 -> merge + grounding check -> missing info -> confidence

The rule engine always runs. When an LLM is configured, the rule result
becomes the cross-check that feeds the agreement term of the confidence score,
and the safety net if the model is unreachable, rate-limited or returns junk.
That is what makes the workflow safe to put in front of a real inbox: it
degrades, it does not stop.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import date
from typing import Any

from . import confidence as confidence_mod
from . import dates as dateutil
from . import grounding
from .config import Config
from .heuristics import RuleEngine
from .numbers import detect_currency, parse_budget, parse_quantity
from .prompts import REPAIR_PROMPT, build_messages
from .providers import LLMError, extract_json_object, get_provider
from .schema import (
    INTENTS,
    CustomerPreferences,
    EmailIntent,
    ExtractionMeta,
    ProductRequirement,
)
from .textutil import find_matches, normalize_text
from .thread import EmailThread, detect_new_email
from .vocab import COLORS, DECORATIONS, MATERIALS, PRODUCT_TYPES, VAGUE_PRODUCT_TYPES

VALID_REQUEST_TYPES = ("product_request", "quote_request", "product_information")


class IntentExtractor:
    """Turns a raw inbound email into structured purchasing requirements."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.provider_name, self.model_name = self.config.resolve_provider()
        self.provider = get_provider(self.provider_name, self.model_name, self.config)
        self.rules = RuleEngine(self.config)

    # -- public API ---------------------------------------------------------

    def extract(self, payload: dict[str, Any] | str) -> EmailIntent:
        started = time.monotonic()
        thread = detect_new_email(payload)
        reference = thread.reference_date

        rule_result = self._run_rules(thread, reference)

        intent = rule_result
        model_confidence: float | None = None
        fallback_reason: str | None = None
        llm_used = False
        rules_for_comparison: EmailIntent | None = None

        if self.provider is not None:
            try:
                llm_result, model_confidence = self._run_llm(thread, reference)
                if self.config.merge_rule_findings:
                    self._merge(llm_result, rule_result)
                intent = llm_result
                rules_for_comparison = rule_result
                llm_used = True
            except LLMError as error:
                fallback_reason = f"{self.provider_name} unavailable: {error}"
            except Exception as error:  # noqa: BLE001 - never fail an inbox on a model
                fallback_reason = f"{self.provider_name} error: {type(error).__name__}: {error}"

        report = grounding.verify(intent, thread.full_text(), self.config)
        intent.normalize()
        intent.missing_information = intent.compute_missing_information(
            self.config.required_product_fields
        )

        errors = intent.validate()
        score, breakdown = confidence_mod.score(
            intent,
            rules=rules_for_comparison,
            model_confidence=model_confidence,
            grounding_score=report.score,
            validation_errors=len(errors),
            llm_used=llm_used,
            fallback=fallback_reason is not None,
            config=self.config,
        )
        intent.confidence = score

        intent.email_id = intent.email_id or thread.message.email_id
        intent.extraction = ExtractionMeta(
            provider=self.provider_name if llm_used else "heuristic",
            model=self.model_name if llm_used else "rule-engine",
            llm_used=llm_used,
            fallback_reason=fallback_reason,
            used_thread_context=bool(thread.history),
            thread_message_count=len(thread.history),
            grounding_score=report.score,
            reference_date=reference.isoformat(),
            duration_ms=int((time.monotonic() - started) * 1000),
            confidence_breakdown=breakdown,
            warnings=intent.extraction.warnings + report.summary()
            + [f"schema validation: {e}" for e in errors],
        )
        return intent

    def extract_many(self, payloads: Iterable[dict[str, Any] | str]) -> list[EmailIntent]:
        return [self.extract(payload) for payload in payloads]

    # -- rule path ----------------------------------------------------------

    def _run_rules(self, thread: EmailThread, reference: date) -> EmailIntent:
        message = thread.message
        return self.rules.extract(
            message.new_content or message.body,
            subject=message.subject,
            reference_date=reference,
            context_text=thread.context_text(),
            original_text=message.body,
            email_id=message.email_id,
        )

    # -- LLM path -----------------------------------------------------------

    def _run_llm(self, thread: EmailThread, reference: date) -> tuple[EmailIntent, float | None]:
        message = thread.message
        messages = build_messages(
            subject=message.subject,
            body=message.new_content or message.body,
            sender=message.sender,
            reference_date=reference,
            thread_context=thread.context_text(),
        )

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.provider.complete(messages)  # type: ignore[union-attr]
                payload = extract_json_object(response.text)
                intent = self._from_llm_payload(payload, thread, reference)
                errors = intent.validate()
                if errors:
                    raise LLMError("; ".join(errors))
                return intent, _as_float(payload.get("confidence"))
            except (LLMError, ValueError, TypeError, KeyError) as error:
                last_error = error
                if attempt >= self.config.max_retries:
                    break
                messages = [
                    *messages,
                    {"role": "assistant", "content": "(invalid output)"},
                    {"role": "user", "content": REPAIR_PROMPT.format(error=error)},
                ]
        raise LLMError(str(last_error) if last_error else "model call failed")

    def _from_llm_payload(
        self, payload: dict[str, Any], thread: EmailThread, reference: date
    ) -> EmailIntent:
        """Convert the model's raw extraction into the normalised contract.

        Every number and date the model returned as customer wording is parsed
        here by the same code the rule engine uses.
        """
        products: list[ProductRequirement] = []
        for entry in payload.get("products") or []:
            if not isinstance(entry, dict):
                continue
            products.append(self._product_from_llm(entry, reference))

        preferences_raw = payload.get("customer_preferences") or {}
        preferences = CustomerPreferences(
            preferred_suppliers=preferences_raw.get("preferred_suppliers") or [],
            preferred_brand=preferences_raw.get("preferred_brand"),
            previous_order_reference=bool(preferences_raw.get("previous_order_reference")),
        )

        raw_intent = str(payload.get("customer_intent") or "unknown").strip().lower()
        if raw_intent not in INTENTS:
            raw_intent = "unknown"

        intent = EmailIntent(
            email_id=thread.message.email_id,
            customer_intent=raw_intent,
            summary=str(payload.get("summary") or "").strip(),
            products=products,
            customer_preferences=preferences,
            occasion=(payload.get("occasion") or None),
            order_references=payload.get("order_references") or [],
            sub_intents=[p.request_type for p in products],
            questions=payload.get("questions") or [],
        )
        intent.normalize()
        return intent

    def _product_from_llm(self, entry: dict[str, Any], reference: date) -> ProductRequirement:
        product = ProductRequirement()
        product.product_type = _canonical(entry.get("product_type"), PRODUCT_TYPES, self.config)
        product.product_type_is_specific = (
            product.product_type not in VAGUE_PRODUCT_TYPES if product.product_type else False
        )
        product.colors = _canonical_list(entry.get("colors"), COLORS, self.config)
        product.materials = _canonical_list(entry.get("materials"), MATERIALS, self.config)
        product.sizes = [str(s).strip().upper() if len(str(s)) <= 3 else str(s).strip()
                         for s in (entry.get("sizes") or []) if str(s).strip()]
        product.decoration = _canonical(entry.get("decoration"), DECORATIONS, self.config)
        product.special_requirements = [
            str(s).strip() for s in (entry.get("special_requirements") or []) if str(s).strip()
        ]
        product.evidence = entry.get("evidence")
        request_type = str(entry.get("request_type") or "product_request").strip().lower()
        product.request_type = request_type if request_type in VALID_REQUEST_TYPES else "product_request"

        # --- deterministic normalisation of the model's verbatim spans -----
        quantity_raw = _as_text(entry.get("quantity_raw")) or _as_text(entry.get("quantity"))
        if quantity_raw:
            quantity = parse_quantity(normalize_text(quantity_raw))
            product.quantity = quantity.value
            product.quantity_min = quantity.minimum
            product.quantity_max = quantity.maximum
            product.quantity_is_approximate = quantity.approximate
            product.quantity_raw = quantity_raw
            if quantity.value is None:
                product.warnings.append(f"could not parse a quantity from {quantity_raw!r}")

        budget_raw = _as_text(entry.get("budget_raw")) or _as_text(entry.get("budget_per_unit"))
        if budget_raw:
            budget = parse_budget(normalize_text(budget_raw), quantity=product.quantity)
            product.budget_per_unit = budget.per_unit
            product.budget_total = budget.total
            product.budget_is_maximum = budget.is_maximum
            product.budget_is_approximate = budget.approximate
            product.budget_basis = budget.basis
            product.currency = (
                budget.currency
                or _as_text(entry.get("currency"))
                or detect_currency(budget_raw)
            )
            product.warnings.extend(budget.warnings)
            if budget.per_unit is None and budget.total is None:
                product.warnings.append(f"could not parse a budget from {budget_raw!r}")

        deadline_raw = _as_text(entry.get("delivery_deadline_raw")) or _as_text(
            entry.get("delivery_deadline")
        )
        if deadline_raw:
            resolved = dateutil.resolve_phrase(normalize_text(deadline_raw), reference)
            product.delivery_deadline_raw = deadline_raw
            if resolved:
                product.delivery_deadline = resolved.iso
                product.delivery_precision = resolved.precision
                if resolved.note:
                    product.warnings.append(resolved.note)
                if (resolved.precision == dateutil.PRECISION_RUSH
                        and "Delivery ASAP" not in product.special_requirements):
                    product.special_requirements.append("Delivery ASAP")
            else:
                product.warnings.append(f"could not resolve a date from {deadline_raw!r}")

        product.normalize()
        return product

    # -- merge --------------------------------------------------------------

    @staticmethod
    def _merge(primary: EmailIntent, secondary: EmailIntent) -> None:
        """Fill gaps in the LLM result from the rule result. Never overwrite.

        Both readings come from the same email, so anything the rule engine
        found is by construction grounded -- this raises recall without
        introducing anything the customer did not write.
        """
        if not primary.products and secondary.products:
            primary.products = secondary.products
            primary.extraction.warnings.append(
                "products recovered from the rule engine: the model returned none"
            )
        else:
            by_type = {p.product_type: p for p in secondary.products if p.product_type}
            for product in primary.products:
                other = by_type.get(product.product_type)
                if other is None:
                    continue
                for attribute in (
                    "quantity", "quantity_min", "quantity_max", "quantity_raw",
                    "budget_per_unit", "budget_total", "budget_basis", "currency",
                    "decoration", "delivery_deadline", "delivery_deadline_raw",
                    "delivery_precision",
                ):
                    if getattr(product, attribute) is None:
                        setattr(product, attribute, getattr(other, attribute))
                for attribute in ("colors", "materials", "sizes", "special_requirements"):
                    if not getattr(product, attribute):
                        setattr(product, attribute, list(getattr(other, attribute)))

        if not primary.summary:
            primary.summary = secondary.summary
        if not primary.occasion:
            primary.occasion = secondary.occasion
        if not primary.order_references:
            primary.order_references = secondary.order_references
        if not primary.questions:
            primary.questions = secondary.questions
        if secondary.customer_preferences.previous_order_reference:
            primary.customer_preferences.previous_order_reference = True
        if not primary.customer_preferences.preferred_brand:
            primary.customer_preferences.preferred_brand = (
                secondary.customer_preferences.preferred_brand
            )
        if not primary.customer_preferences.preferred_suppliers:
            primary.customer_preferences.preferred_suppliers = list(
                secondary.customer_preferences.preferred_suppliers
            )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _canonical(value: Any, vocab: dict[str, tuple[str, ...]], config: Config) -> str | None:
    """Map a model-supplied word onto the canonical vocabulary term."""
    text = _as_text(value)
    if not text:
        return None
    lowered = normalize_text(text)
    if lowered in vocab:
        return lowered
    matches = find_matches(lowered, vocab, fuzzy=True, fuzzy_threshold=config.fuzzy_threshold)
    return matches[0].canonical if matches else lowered


def _canonical_list(values: Any, vocab: dict[str, tuple[str, ...]], config: Config) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values:
        canonical = _canonical(value, vocab, config)
        if canonical and canonical not in out:
            out.append(canonical)
    return out


def extract_intent(
    payload: dict[str, Any] | str, *, config: Config | None = None
) -> EmailIntent:
    """One-shot helper: extract structured intent from a single email payload."""
    return IntentExtractor(config).extract(payload)
