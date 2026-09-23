# Output contract

Two serialisations come from the same objects:

- **`spec`** — exactly the documented field set. Bind downstream automation to
  this. It is the public API for semantic-versioning purposes: a change to these
  fields is a major version.
- **`full`** — the same data plus provenance: the customer's raw wording, range
  bounds, precision flags, per-value warnings, grounding score, confidence
  breakdown and timings. Use it for review queues, audits and tuning.

`email-intent --schema` prints the JSON Schema for the `spec` shape.

## The spec shape

```json
{
  "customer_intent": "product_request",
  "summary": "Customer is looking for approximately 150 stainless steel tumblers for an event.",
  "products": [
    {
      "product_type": "tumbler",
      "quantity": 150,
      "materials": ["stainless steel"],
      "colors": ["black", "dark blue"],
      "sizes": [],
      "budget_per_unit": 15,
      "currency": "USD",
      "decoration": null,
      "delivery_deadline": "2026-10-20",
      "special_requirements": ["Similar to previously ordered product"]
    }
  ],
  "customer_preferences": {
    "preferred_suppliers": [],
    "preferred_brand": null,
    "previous_order_reference": true
  },
  "missing_information": [],
  "confidence": 0.95
}
```

| Field | Type | Notes |
|---|---|---|
| `customer_intent` | enum | one of the nine intents below |
| `summary` | string | one factual sentence |
| `products[]` | array | one entry per distinct product; empty for non-sourcing intents |
| `products[].product_type` | string \| null | canonical singular, lowercase (`"polo shirt"`); `null` when never stated |
| `products[].quantity` | integer \| null | see the range rule below |
| `products[].materials/colors/sizes` | string[] | canonical values, `[]` when not stated |
| `products[].budget_per_unit` | number \| null | per-unit target or ceiling |
| `products[].currency` | string \| null | ISO code, defaults to `USD` when an amount is present |
| `products[].decoration` | string \| null | imprint method (`"embroidery"`, `"screen print"`, ...) |
| `products[].delivery_deadline` | string \| null | `YYYY-MM-DD` |
| `products[].special_requirements` | string[] | short constraint phrases |
| `customer_preferences.preferred_suppliers` | string[] | named suppliers |
| `customer_preferences.preferred_brand` | string \| null | named brand |
| `customer_preferences.previous_order_reference` | boolean | the email points back at an earlier purchase |
| `missing_information` | string[] | field names, `products[i].field` when there are several |
| `confidence` | number | 0.0–1.0 |

Absent information is `null` or `[]` — never a guess. `missing_information` is
computed from what is absent, so it is the list of questions a reply-draft bot
should ask.

## Additional fields in `full`

```jsonc
"quantity_min": 100, "quantity_max": 150,   // "around 100-150"
"quantity_is_approximate": true,
"quantity_raw": "around 100-150 pieces",     // the customer's own words
"budget_total": null,                        // set instead of per_unit for "under $2000 total"
"budget_is_maximum": true,                   // "under $20" vs "about $20"
"budget_is_approximate": false,
"budget_basis": "per_unit",                  // per_unit | total | assumed_per_unit
"delivery_deadline_raw": "before the 20th",
"delivery_precision": "day",                 // day | week | month | quarter | asap
"product_type_is_specific": true,            // false for "promotional products"
"refers_to_existing_order": false,           // true inside a complaint or status chase
"request_type": "quote_request",             // this line's own ask
"evidence": "...the clause this line came from...",
"warnings": ["..."]
```

Plus, at the top level: `email_id`, `sub_intents`, `occasion`,
`order_references`, `questions`, and an `extraction` block with `provider`,
`model`, `llm_used`, `fallback_reason`, `used_thread_context`,
`thread_message_count`, `grounding_score`, `reference_date`, `duration_ms`,
`confidence_breakdown` and `warnings`.

## Fixed conventions

Documented so downstream code never has to guess:

| Situation | Rule |
|---|---|
| Range ("100-150") | `quantity` = the **upper** bound, so sourcing never under-quotes; both bounds kept in `quantity_min`/`quantity_max`, `quantity_is_approximate: true` |
| "ASAP" | `delivery_deadline: null`, `delivery_precision: "asap"`, `"Delivery ASAP"` in `special_requirements` — a rush is not a date |
| "next month" | last day of that month, `delivery_precision: "month"` — flagged as soft, not a commitment |
| Event date vs deadline | an event mentioned in passing is **not** a deadline (`"for our event next month"` → none; `"delivered by Oct 20"` → deadline) |
| Budget with no basis stated | read as per-unit below $1000, as a total above, and always reported in `budget_basis` |
| Product never named | `product_type: null` + `"product_type"` in `missing_information` — a named brand is not used to guess it |
| Vague product ("promotional products") | kept as the product type with `product_type_is_specific: false`, and `"product_specification"` added to `missing_information` |
| Repeated mention of one product | merged into a single line item unless the quantities genuinely differ |

## Intent types

| Intent | Meaning |
|---|---|
| `product_request` | wants to buy or source specific products |
| `quote_request` | explicitly asks for pricing, a quote, an estimate or a bid |
| `product_information` | asks about availability, stock, specs, colors, sizes, samples |
| `order_status` | asks where an existing order is |
| `revision_request` | wants to change an order or quote that already exists |
| `general_question` | a business question not tied to a product request (lead times, terms, MOQ) |
| `complaint` | reports damaged, wrong, late or poor-quality goods |
| `multiple_requests` | the email mixes two or more **different kinds** of ask |
| `unknown` | intent cannot be determined |

Two rules worth knowing:

**`multiple_requests` means different kinds of ask**, matching the brief it was
built against — pricing on polos *and* an availability check on hats. Three
products all being sourced the same way stays `product_request` with three line
items. Set `Config.multi_product_is_multiple_requests = True` for the other
convention.

**A product named inside a complaint is not a new request.** *"40 of the mugs
arrived damaged"* is `complaint` with the mugs flagged
`refers_to_existing_order`, not a complaint plus an order for 40 mugs. The same
applies to order-status chases.

Per-product `request_type` (`product_request` / `quote_request` /
`product_information`) and top-level `sub_intents` are available in the full
output when you need the finer breakdown.

## Validation

`EmailIntent.validate()` returns a list of contract violations — an unknown
intent, a non-positive quantity or budget, a malformed date, an inverted
quantity range, an out-of-range confidence. An empty list means the payload is
safe to hand downstream. The pipeline runs it on every email, records any
failures in `extraction.warnings`, and penalises confidence; the LLM path also
runs it before accepting a model reply, retrying with a repair prompt on failure.
