"""Prompt construction.

The model is asked for *semantic* extraction only: which products, which
attributes, and the customer's own wording for anything numeric or temporal.
It is explicitly told not to convert quantities, prices or dates -- Python does
that in `numbers.py` / `dates.py`. That split is deliberate:

* models are good at "this sentence is about hats, and 100 belongs to it";
* models are unreliable at "the 20th, given today is Sept 23, is 2026-10-20";
* and a number a model rewrote is a number nobody can audit.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from .schema import INTENTS

INTENT_DEFINITIONS = {
    "product_request": "Wants to buy/source specific products. The core sourcing request.",
    "quote_request": "Explicitly asks for pricing, a quote, an estimate or a bid.",
    "product_information": "Asks about availability, stock, specs, colors, sizes, samples or options.",
    "order_status": "Asks where an existing order is: tracking, ship date, delivery ETA.",
    "revision_request": "Wants to change an order or quote that already exists.",
    "general_question": "A business question not tied to a specific product request (lead times, terms, MOQ, artwork).",
    "complaint": "Reports a problem: damaged, wrong, late or poor-quality goods.",
    "multiple_requests": "The email contains two or more DIFFERENT kinds of ask (e.g. pricing on one item AND availability on another, or an order-status question AND a new request).",
    "unknown": "Intent cannot be determined from the text.",
}

SYSTEM_PROMPT = f"""You extract structured purchasing requirements from customer emails for a \
promotional-products distributor. Your output feeds an automated sourcing and quoting system, \
so it must be precise, conservative and machine-readable.

INTENT TYPES (choose exactly one for customer_intent):
{chr(10).join(f"- {name}: {definition}" for name, definition in INTENT_DEFINITIONS.items())}

CORE RULES

1. EXTRACT, NEVER INVENT. Only record what the customer actually wrote. If a detail is not in \
the email, use null (or an empty list). Never fill a gap with a plausible default, an industry \
norm, or a value carried over from an example. Do not guess colors, materials, sizes, budgets or \
dates. An empty field is a correct answer.

2. DO NOT NORMALISE NUMBERS OR DATES. For quantity, budget and delivery timing, copy the \
customer's wording VERBATIM into the *_raw fields:
   - "we need around 150 tumblers"       -> quantity_raw: "around 150"
   - "100-150 pieces"                    -> quantity_raw: "100-150 pieces"
   - "trying to stay under 15 bucks each"-> budget_raw: "under 15 bucks each"
   - "need them delivered by Oct 20"     -> delivery_deadline_raw: "by Oct 20"
   - "delivery ASAP"                     -> delivery_deadline_raw: "ASAP"
   Never compute a number, never resolve a date to a calendar date. Downstream code does that.

3. ONE ENTRY PER DISTINCT PRODUCT. "200 polos and 100 hats" is two products. Attach each \
attribute to the product the customer attached it to. A constraint stated once for the whole \
email (e.g. "all delivered by the 15th") may be repeated on every product.

4. SPELLING AND SLANG. Customers write informally and misspell: "tumbelers" -> "tumbler", \
"tees" -> "t-shirt", "polos" -> "polo shirt", "hoodys" -> "hoodie", "250 pcs" -> quantity. \
Map to the plain singular product name in lowercase. Keep colors and materials as the customer \
described them, lowercased ("dark blue", "stainless steel").

5. VAGUE REQUESTS ARE STILL REQUESTS. "Can you find us some good promotional products?" is a \
product_request with product_type "promotional products" and everything else null. Do not \
invent a concrete product.

6. request_type per product: "quote_request" if they want a price for it, "product_information" \
if they are asking whether it exists / what it comes in, otherwise "product_request".

7. special_requirements: short phrases for constraints that do not fit another field -- \
"Similar to previously ordered product", "Individually packaged", "Made in USA", \
"Pre-production sample required". Use the exact phrase "Similar to previously ordered product" \
whenever the customer references an earlier order.

8. confidence: your own 0.0-1.0 estimate that this reading is correct. Lower it when the email \
is vague, garbled, or could reasonably be read another way.

OUTPUT: a single JSON object, no markdown fence, no commentary."""


OUTPUT_TEMPLATE: dict[str, Any] = {
    "customer_intent": "one of: " + ", ".join(INTENTS),
    "summary": "one sentence, factual, describing what the customer wants",
    "occasion": "event/trade show/onboarding/etc, or null",
    "order_references": ["existing PO or order numbers mentioned, else empty"],
    "products": [
        {
            "product_type": "lowercase singular product name, or null",
            "quantity_raw": "customer's own wording, or null",
            "materials": ["lowercase"],
            "colors": ["lowercase"],
            "sizes": ["XS/S/M/L/XL/2XL or capacity like '20 oz'"],
            "budget_raw": "customer's own wording, or null",
            "decoration": "imprint method if stated, else null",
            "delivery_deadline_raw": "customer's own wording, or null",
            "special_requirements": ["short phrases"],
            "request_type": "product_request | quote_request | product_information",
            "evidence": "the clause from the email this product came from",
        }
    ],
    "customer_preferences": {
        "preferred_suppliers": [],
        "preferred_brand": "brand name if named, else null",
        "previous_order_reference": "true only if they reference an earlier order",
    },
    "confidence": 0.0,
}


FEW_SHOT_EXAMPLES: list[tuple[str, dict[str, Any]]] = [
    (
        "Subject: Need some tumblers for our event\n"
        "From: customer@example.com\n\n"
        "Hey John, we need around 150 tumblers for our event next month. Looking for something "
        "black or dark blue, preferably stainless steel. Budget is around $15 each. If you can "
        "find something similar to the one we ordered last time that would be great. Need them "
        "delivered by Oct 20. Thanks!",
        {
            "customer_intent": "product_request",
            "summary": "Customer is looking for approximately 150 stainless steel tumblers for an event.",
            "occasion": "event",
            "order_references": [],
            "products": [
                {
                    "product_type": "tumbler",
                    "quantity_raw": "around 150",
                    "materials": ["stainless steel"],
                    "colors": ["black", "dark blue"],
                    "sizes": [],
                    "budget_raw": "around $15 each",
                    "decoration": None,
                    "delivery_deadline_raw": "delivered by Oct 20",
                    "special_requirements": ["Similar to previously ordered product"],
                    "request_type": "product_request",
                    "evidence": "we need around 150 tumblers for our event next month",
                }
            ],
            "customer_preferences": {
                "preferred_suppliers": [],
                "preferred_brand": None,
                "previous_order_reference": True,
            },
            "confidence": 0.94,
        },
    ),
    (
        "Subject: quick question\n"
        "From: buyer@acme.com\n\n"
        "Can you send me pricing for 200 polos and also check if you have 100 hats available?",
        {
            "customer_intent": "multiple_requests",
            "summary": "Customer wants pricing on 200 polo shirts and an availability check on 100 hats.",
            "occasion": None,
            "order_references": [],
            "products": [
                {
                    "product_type": "polo shirt",
                    "quantity_raw": "200",
                    "materials": [],
                    "colors": [],
                    "sizes": [],
                    "budget_raw": None,
                    "decoration": None,
                    "delivery_deadline_raw": None,
                    "special_requirements": [],
                    "request_type": "quote_request",
                    "evidence": "send me pricing for 200 polos",
                },
                {
                    "product_type": "hat",
                    "quantity_raw": "100",
                    "materials": [],
                    "colors": [],
                    "sizes": [],
                    "budget_raw": None,
                    "decoration": None,
                    "delivery_deadline_raw": None,
                    "special_requirements": [],
                    "request_type": "product_information",
                    "evidence": "check if you have 100 hats available",
                },
            ],
            "customer_preferences": {
                "preferred_suppliers": [],
                "preferred_brand": None,
                "previous_order_reference": False,
            },
            "confidence": 0.9,
        },
    ),
    (
        "Subject: event swag\n"
        "From: sam@nonprofit.org\n\n"
        "Can you find us some good promotional products for our event?",
        {
            "customer_intent": "product_request",
            "summary": "Customer is asking for promotional product suggestions for an event, with no specifics given.",
            "occasion": "event",
            "order_references": [],
            "products": [
                {
                    "product_type": "promotional products",
                    "quantity_raw": None,
                    "materials": [],
                    "colors": [],
                    "sizes": [],
                    "budget_raw": None,
                    "decoration": None,
                    "delivery_deadline_raw": None,
                    "special_requirements": [],
                    "request_type": "product_request",
                    "evidence": "find us some good promotional products for our event",
                }
            ],
            "customer_preferences": {
                "preferred_suppliers": [],
                "preferred_brand": None,
                "previous_order_reference": False,
            },
            "confidence": 0.55,
        },
    ),
]


def build_user_prompt(
    *,
    subject: str,
    body: str,
    sender: str = "",
    reference_date: date | None = None,
    thread_context: str = "",
) -> str:
    """Assemble the user-turn prompt for one email."""
    reference = (reference_date or date.today()).isoformat()
    sections = [
        "Extract the structured purchasing intent from the NEW EMAIL below.",
        f"Today's date (for your reference only -- do NOT resolve dates yourself): {reference}",
    ]
    if thread_context:
        sections.append(
            "EARLIER MESSAGES IN THIS THREAD (context only -- do not extract requirements from "
            "these unless the new email refers back to them):\n"
            f"{thread_context}"
        )
    sections.append(
        "NEW EMAIL:\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Body:\n{body}"
    )
    sections.append(
        "Return JSON in exactly this shape (values here describe what to put, not what to copy):\n"
        + json.dumps(OUTPUT_TEMPLATE, indent=2)
    )
    return "\n\n".join(sections)


def build_messages(
    *,
    subject: str,
    body: str,
    sender: str = "",
    reference_date: date | None = None,
    thread_context: str = "",
    few_shot: bool = True,
) -> list[dict[str, str]]:
    """Chat-format messages for OpenAI-compatible providers (Groq/OpenRouter)."""
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if few_shot:
        for example_email, example_output in FEW_SHOT_EXAMPLES:
            messages.append({"role": "user", "content": example_email})
            messages.append({"role": "assistant", "content": json.dumps(example_output)})
    messages.append({
        "role": "user",
        "content": build_user_prompt(
            subject=subject, body=body, sender=sender,
            reference_date=reference_date, thread_context=thread_context,
        ),
    })
    return messages


REPAIR_PROMPT = (
    "Your previous reply was not valid JSON matching the required shape.\n"
    "Problem: {error}\n"
    "Reply again with ONLY the corrected JSON object. No markdown, no explanation."
)
