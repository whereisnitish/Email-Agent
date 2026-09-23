"""Test suite for email -> customer intent extraction.

Runs fully offline: the rule engine needs no network, and the LLM path is
exercised against a stub provider so the model contract is tested without a
key. Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from email_intent.config import Config
from email_intent.extractor import IntentExtractor
from email_intent.providers import LLMError, LLMResponse, extract_json_object
from email_intent.resources import sample_emails
from email_intent.schema import INTENTS
from email_intent.thread import EmailThread

REFERENCE = "2026-09-23"


def make_email(body: str, **overrides) -> dict:
    payload = {
        "email_id": "test_1",
        "from": "customer@example.com",
        "subject": "",
        "received_at": REFERENCE,
        "body": body,
    }
    payload.update(overrides)
    return payload


def extract(body: str, **overrides):
    return IntentExtractor(Config(provider="heuristic")).extract(make_email(body, **overrides))


class BriefExampleTests(unittest.TestCase):
    """The worked example from the brief, and its informal paraphrase."""

    EXAMPLE = (
        "Hey John, we need around 150 tumblers for our event next month. Looking for "
        "something black or dark blue, preferably stainless steel. Budget is around $15 "
        "each. If you can find something similar to the one we ordered last time that "
        "would be great. Need them delivered by Oct 20. Thanks!"
    )
    PARAPHRASE = (
        "Hey, can you find me about 150 black stainless tumblers, something close to what "
        "we got last time? Trying to stay under 15 bucks each. We need these before the 20th."
    )

    def test_reference_output(self):
        result = extract(self.EXAMPLE, subject="Need some tumblers for our event")
        payload = result.to_spec_dict()

        self.assertEqual(payload["customer_intent"], "product_request")
        self.assertEqual(len(payload["products"]), 1)

        product = payload["products"][0]
        self.assertEqual(product["product_type"], "tumbler")
        self.assertEqual(product["quantity"], 150)
        self.assertEqual(product["materials"], ["stainless steel"])
        self.assertEqual(product["colors"], ["black", "dark blue"])
        self.assertEqual(product["sizes"], [])
        self.assertEqual(product["budget_per_unit"], 15)
        self.assertEqual(product["currency"], "USD")
        self.assertIsNone(product["decoration"])
        self.assertEqual(product["delivery_deadline"], "2026-10-20")
        self.assertIn("Similar to previously ordered product", product["special_requirements"])

        self.assertTrue(payload["customer_preferences"]["previous_order_reference"])
        self.assertEqual(payload["missing_information"], [])
        self.assertGreater(payload["confidence"], 0.8)

    def test_paraphrase_produces_the_same_intent(self):
        """Different wording, same structured meaning -- no keyword dependence."""
        formal = extract(self.EXAMPLE, subject="Need some tumblers for our event").to_spec_dict()
        casual = extract(self.PARAPHRASE).to_spec_dict()

        self.assertEqual(formal["customer_intent"], casual["customer_intent"])
        for field in ("product_type", "quantity", "budget_per_unit", "delivery_deadline"):
            self.assertEqual(
                formal["products"][0][field], casual["products"][0][field],
                f"{field} differs between phrasings",
            )
        self.assertIn("black", casual["products"][0]["colors"])
        self.assertEqual(casual["products"][0]["materials"], ["stainless steel"])
        self.assertTrue(casual["customer_preferences"]["previous_order_reference"])


class MultipleRequestTests(unittest.TestCase):

    def test_pricing_plus_availability_is_two_products(self):
        result = extract(
            "Can you send me pricing for 200 polos and also check if you have 100 hats available?"
        )
        payload = result.to_spec_dict()

        self.assertEqual(payload["customer_intent"], "multiple_requests")
        self.assertEqual(len(payload["products"]), 2)
        self.assertEqual(payload["products"][0]["product_type"], "polo shirt")
        self.assertEqual(payload["products"][0]["quantity"], 200)
        self.assertEqual(payload["products"][1]["product_type"], "hat")
        self.assertEqual(payload["products"][1]["quantity"], 100)

    def test_quantities_attach_to_the_right_product(self):
        result = extract("Need 100 polos, 200 tumblers and maybe 50 backpacks.")
        pairs = [(p.product_type, p.quantity) for p in result.products]
        self.assertEqual(pairs, [("polo shirt", 100), ("tumbler", 200), ("backpack", 50)])

    def test_shared_constraints_apply_to_every_line(self):
        result = extract(
            "We need 100 polos and 200 tumblers, all embroidered with our logo, "
            "delivered by Dec 1."
        )
        self.assertEqual(len(result.products), 2)
        for product in result.products:
            self.assertEqual(product.delivery_deadline, "2026-12-01")
            self.assertEqual(product.decoration, "embroidery")


class NaturalLanguageTests(unittest.TestCase):
    """Section 4 of the brief: the wording variations that must all work."""

    def test_informal(self):
        result = extract("Hey John, can you hook me up with about 100 black hoodies?")
        product = result.products[0]
        self.assertEqual(product.product_type, "hoodie")
        self.assertEqual(product.quantity, 100)
        self.assertTrue(product.quantity_is_approximate)
        self.assertEqual(product.colors, ["black"])

    def test_abbreviations(self):
        result = extract("Need 250 pcs tees, XL mostly, delivery ASAP.")
        product = result.products[0]
        self.assertEqual(product.product_type, "t-shirt")
        self.assertEqual(product.quantity, 250)
        self.assertEqual(product.sizes, ["XL"])
        self.assertIn("Delivery ASAP", product.special_requirements)
        self.assertIsNone(product.delivery_deadline)

    def test_spelling_mistakes(self):
        result = extract("Need 150 tumbelers in black. Can u check pricing?")
        product = result.products[0]
        self.assertEqual(product.product_type, "tumbler")
        self.assertEqual(product.quantity, 150)
        self.assertEqual(result.customer_intent, "quote_request")
        self.assertTrue(any("spelling similarity" in w for w in product.warnings))

    def test_conversational(self):
        result = extract(
            "We're doing an event next month and were thinking maybe 200 bottles, "
            "nothing too expensive."
        )
        product = result.products[0]
        self.assertEqual(product.product_type, "water bottle")
        self.assertEqual(product.quantity, 200)
        self.assertIsNone(product.budget_per_unit, "a vague 'not expensive' is not a number")
        self.assertIn("budget_per_unit", result.missing_information)

    def test_approximate_quantity_range(self):
        result = extract("We'll need around 100-150 pieces of the drawstring bags.")
        product = result.products[0]
        self.assertEqual(product.quantity_min, 100)
        self.assertEqual(product.quantity_max, 150)
        self.assertEqual(product.quantity, 150, "the upper bound drives sourcing")
        self.assertTrue(product.quantity_is_approximate)

    def test_approximate_budget_ceiling(self):
        result = extract("Need 200 tumblers, trying to keep these under 20 bucks each.")
        product = result.products[0]
        self.assertEqual(product.budget_per_unit, 20)
        self.assertTrue(product.budget_is_maximum)
        self.assertEqual(product.currency, "USD")

    def test_missing_information_is_reported_not_invented(self):
        result = extract("Can you find us some good promotional products for our event?")
        self.assertEqual(result.customer_intent, "product_request")
        product = result.products[0]
        self.assertIsNone(product.quantity)
        self.assertEqual(product.colors, [])
        self.assertEqual(product.materials, [])
        self.assertIsNone(product.budget_per_unit)
        self.assertIsNone(product.delivery_deadline)
        self.assertIn("quantity", result.missing_information)
        self.assertIn("budget_per_unit", result.missing_information)
        self.assertLess(result.confidence, 0.75, "a vague request should not look confident")


class IntentCoverageTests(unittest.TestCase):

    CASES = [
        ("product_request", "We need 100 tote bags for our conference."),
        ("quote_request", "Can you send pricing on 500 pens?"),
        ("product_information", "Do you carry recycled notebooks, and what colors are available?"),
        ("order_status", "Any update on my order? PO 44821 was supposed to ship last week."),
        ("revision_request", "Quick change to our order 44900 - instead of 200 polos make it 300."),
        ("complaint", "We received the mugs today and 40 arrived damaged. We need a refund."),
        ("general_question", "What is your typical lead time and do you have a minimum order?"),
        ("multiple_requests", "Can you send me pricing for 200 polos and check if you have 100 hats available?"),
    ]

    def test_each_intent_is_detected(self):
        for expected, body in self.CASES:
            with self.subTest(intent=expected):
                self.assertEqual(extract(body).customer_intent, expected)

    def test_unrecognised_email_is_unknown_not_a_fake_request(self):
        result = extract(
            "This week in promotional products: supply chain trends, new decoration "
            "techniques, and an interview with an industry veteran. Click here to read more."
        )
        self.assertEqual(result.customer_intent, "unknown")
        self.assertEqual(result.products, [])

    def test_complaint_mentioning_a_product_stays_a_complaint(self):
        result = extract("We received the mugs today and about 40 arrived damaged - cracked handles.")
        self.assertEqual(result.customer_intent, "complaint")
        self.assertTrue(result.products[0].refers_to_existing_order)

    def test_revision_uses_the_new_quantity(self):
        result = extract("Change to order 44900 - instead of 200 polos can we make it 300?")
        self.assertEqual(result.customer_intent, "revision_request")
        self.assertEqual(result.products[0].quantity, 300)
        self.assertEqual(result.order_references, ["44900"])


class DateResolutionTests(unittest.TestCase):

    def resolve(self, body: str):
        return extract(body).products[0].delivery_deadline

    def test_named_month_rolls_into_the_future(self):
        self.assertEqual(self.resolve("Need 100 mugs delivered by Oct 20."), "2026-10-20")

    def test_bare_ordinal_rolls_to_the_next_month(self):
        self.assertEqual(self.resolve("Need 100 mugs before the 20th."), "2026-10-20")

    def test_relative_offsets(self):
        self.assertEqual(self.resolve("Need 100 mugs in 2 weeks."), "2026-10-07")

    def test_us_holiday(self):
        self.assertEqual(self.resolve("Need 100 mugs in hand before Christmas."), "2026-12-25")

    def test_event_date_is_not_a_delivery_deadline(self):
        """An event mentioned in passing must not become a commitment."""
        result = extract("We have an event next month and need about 200 mugs.")
        self.assertIsNone(result.products[0].delivery_deadline)
        self.assertIn("delivery_deadline", result.missing_information)

    def test_month_precision_is_flagged(self):
        result = extract("Need 100 mugs delivered next month.")
        product = result.products[0]
        self.assertEqual(product.delivery_deadline, "2026-10-31")
        self.assertEqual(product.delivery_precision, "month")

    def test_dates_resolve_against_the_email_not_today(self):
        result = extract("Need 100 mugs by the 20th.", received_at="2026-01-05")
        self.assertEqual(result.products[0].delivery_deadline, "2026-01-20")


class ThreadContextTests(unittest.TestCase):

    PAYLOAD = {
        "email_id": "msg_1015",
        "from": "customer@example.com",
        "subject": "Re: Spring order follow-up",
        "received_at": REFERENCE,
        "body": "Hi John, let's do the same tumblers again, 200 this time. Need them by Oct 30.",
        "thread": [
            {
                "email_id": "msg_0990",
                "from": "john@distributor.com",
                "received_at": "2026-03-14",
                "body": "Confirming your March order: 150 stainless steel tumblers in black on PO 41203.",
            }
        ],
    }

    def test_thread_is_read_and_recorded(self):
        result = IntentExtractor(Config(provider="heuristic")).extract(self.PAYLOAD)
        self.assertTrue(result.extraction.used_thread_context)
        self.assertEqual(result.extraction.thread_message_count, 1)
        self.assertTrue(result.customer_preferences.previous_order_reference)

    def test_new_message_quantity_wins_over_thread_quantity(self):
        """Context informs; it must not leak old numbers into the new request."""
        result = IntentExtractor(Config(provider="heuristic")).extract(self.PAYLOAD)
        self.assertEqual(result.products[0].quantity, 200)
        self.assertEqual(result.products[0].delivery_deadline, "2026-10-30")

    def test_quoted_reply_history_is_split_out(self):
        thread = EmailThread.from_payload({
            "body": (
                "Yes please go ahead with 300 hats.\n\n"
                "On Mon, Sep 21, 2026 at 9:02 AM John <john@x.com> wrote:\n"
                "> We can do 200 hats at $9 each.\n"
            ),
        })
        self.assertIn("300 hats", thread.message.new_content)
        self.assertNotIn("200 hats", thread.message.new_content)
        self.assertEqual(len(thread.history), 1)


class StubProvider:
    """Stands in for a real LLM so the model contract can be tested offline."""

    def __init__(self, payload: dict, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        if self.fail:
            raise LLMError("simulated outage")
        return LLMResponse(json.dumps(self.payload), "stub", "stub-model", 12)


class LLMPathTests(unittest.TestCase):
    """The model returns customer wording; Python does the arithmetic."""

    BODY = (
        "Hey John, we need around 150 tumblers for our event next month. Black or dark "
        "blue, stainless steel. Budget is around $15 each. Need them delivered by Oct 20."
    )

    MODEL_OUTPUT = {
        "customer_intent": "product_request",
        "summary": "Customer wants roughly 150 stainless steel tumblers for an event.",
        "occasion": "event",
        "order_references": [],
        "products": [{
            "product_type": "tumblers",
            "quantity_raw": "around 150",
            "materials": ["stainless steel"],
            "colors": ["black", "dark blue"],
            "sizes": [],
            "budget_raw": "around $15 each",
            "decoration": None,
            "delivery_deadline_raw": "delivered by Oct 20",
            "special_requirements": [],
            "request_type": "product_request",
            "evidence": "we need around 150 tumblers",
        }],
        "customer_preferences": {
            "preferred_suppliers": [], "preferred_brand": None,
            "previous_order_reference": False,
        },
        "confidence": 0.93,
    }

    def build(self, payload: dict, fail: bool = False) -> IntentExtractor:
        extractor = IntentExtractor(Config(provider="heuristic"))
        extractor.provider = StubProvider(payload, fail)  # type: ignore[assignment]
        extractor.provider_name, extractor.model_name = "stub", "stub-model"
        return extractor

    def test_raw_spans_are_normalised_by_code(self):
        result = self.build(self.MODEL_OUTPUT).extract(make_email(self.BODY))
        product = result.products[0]
        self.assertEqual(product.product_type, "tumbler", "alias canonicalised")
        self.assertEqual(product.quantity, 150)
        self.assertTrue(product.quantity_is_approximate)
        self.assertEqual(product.budget_per_unit, 15)
        self.assertEqual(product.delivery_deadline, "2026-10-20")
        self.assertTrue(result.extraction.llm_used)

    def test_hallucinated_values_are_dropped(self):
        payload = json.loads(json.dumps(self.MODEL_OUTPUT))
        payload["products"][0]["colors"] = ["black", "purple"]
        payload["products"][0]["quantity_raw"] = "900"
        payload["products"][0]["special_requirements"] = ["Rush shipping to Alaska"]

        result = self.build(payload).extract(make_email(self.BODY))
        product = result.products[0]
        self.assertNotIn("purple", product.colors, "colour never mentioned must be dropped")
        self.assertIn("black", product.colors)
        self.assertIsNone(product.quantity, "a fabricated number must not survive")
        self.assertNotIn("Rush shipping to Alaska", product.special_requirements)
        self.assertLess(result.extraction.grounding_score, 1.0)

    def test_model_failure_falls_back_to_the_rule_engine(self):
        result = self.build(self.MODEL_OUTPUT, fail=True).extract(make_email(self.BODY))
        self.assertFalse(result.extraction.llm_used)
        self.assertIn("simulated outage", result.extraction.fallback_reason or "")
        self.assertEqual(result.products[0].quantity, 150, "rule engine still delivers")
        self.assertEqual(result.products[0].delivery_deadline, "2026-10-20")

    def test_malformed_model_output_is_retried_then_falls_back(self):
        class BadProvider:
            calls = 0

            def complete(self, messages):
                BadProvider.calls += 1
                return LLMResponse("I think they want tumblers!", "stub", "stub-model", 5)

        extractor = IntentExtractor(Config(provider="heuristic", max_retries=1))
        extractor.provider = BadProvider()  # type: ignore[assignment]
        result = extractor.extract(make_email(self.BODY))
        self.assertEqual(BadProvider.calls, 2, "one retry with a repair prompt")
        self.assertFalse(result.extraction.llm_used)
        self.assertEqual(result.products[0].quantity, 150)

    def test_json_is_recovered_from_a_fenced_reply(self):
        parsed = extract_json_object('Sure!\n```json\n{"customer_intent": "product_request"}\n```')
        self.assertEqual(parsed["customer_intent"], "product_request")


class ContractTests(unittest.TestCase):

    def test_spec_output_has_exactly_the_required_shape(self):
        result = extract("Need 100 tote bags by Nov 3, budget $6 each.")
        payload = result.to_spec_dict()
        self.assertEqual(
            set(payload),
            {"customer_intent", "summary", "products", "customer_preferences",
             "missing_information", "confidence"},
        )
        self.assertEqual(
            set(payload["products"][0]),
            {"product_type", "quantity", "materials", "colors", "sizes", "budget_per_unit",
             "currency", "decoration", "delivery_deadline", "special_requirements"},
        )
        self.assertEqual(
            set(payload["customer_preferences"]),
            {"preferred_suppliers", "preferred_brand", "previous_order_reference"},
        )

    def test_every_sample_email_produces_valid_json(self):
        samples = sample_emails()
        extractor = IntentExtractor(Config(provider="heuristic"))
        for payload in samples:
            with self.subTest(email=payload["email_id"]):
                result = extractor.extract(payload)
                self.assertEqual(result.validate(), [])
                self.assertIn(result.customer_intent, INTENTS)
                self.assertTrue(0.0 <= result.confidence <= 1.0)
                json.dumps(result.to_dict())   # must be serialisable
                json.dumps(result.to_spec_dict())

    def test_empty_body_does_not_crash(self):
        result = extract("")
        self.assertEqual(result.customer_intent, "unknown")
        self.assertEqual(result.products, [])

    def test_confidence_is_explainable(self):
        result = extract("Need 100 tote bags by Nov 3, budget $6 each.")
        breakdown = result.extraction.confidence_breakdown
        self.assertIn("components", breakdown)
        self.assertIn("penalties", breakdown)
        self.assertAlmostEqual(breakdown["final"], result.confidence, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
