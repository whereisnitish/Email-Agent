# Architecture

How an email becomes structured data, and why the pipeline is shaped this way.

## The pipeline

```
  new email  ─►  intake        thread.py      normalise fields, split the new message
                                              from quoted history, pick the reference date
             ─►  rule engine   heuristics.py  span-aware extraction, always runs
             ─►  LLM           prompts.py     semantic extraction only, returns the
                               providers.py   customer's VERBATIM wording for numbers/dates
             ─►  normalise     numbers.py     "around 100-150 pcs" → 100/150, approximate
                               dates.py       "before the 20th"    → 2026-10-20, precision=day
             ─►  merge         extractor.py   LLM result, gaps filled from the rule engine
             ─►  ground        grounding.py   drop anything not traceable to the email
             ─►  report        schema.py      missing_information + confidence + validation
```

Each stage is a separate module with a narrow job, so a change to date handling
cannot break intent classification.

## 1. The model extracts, the code computes

The LLM is never asked for a number or a date. It is asked which product a
clause is about and what the customer literally wrote:

| Email says | Model returns | Code produces |
|---|---|---|
| "around 150 tumblers" | `quantity_raw: "around 150"` | `quantity: 150, quantity_is_approximate: true` |
| "100-150 pieces" | `quantity_raw: "100-150 pieces"` | `quantity_min: 100, quantity_max: 150, quantity: 150` |
| "under 15 bucks each" | `budget_raw: "under 15 bucks each"` | `budget_per_unit: 15, budget_is_maximum: true, currency: "USD"` |
| "before the 20th" | `delivery_deadline_raw: "before the 20th"` | `delivery_deadline: "2026-10-20"` |

Three things follow:

- **Reproducible.** Date maths is arithmetic, not inference. "The 20th" resolves
  against the email's own `received_at`, so re-running the extraction next year
  gives the same answer. `date.today()` appears in exactly one place — the
  fallback for an email with no timestamp.
- **Auditable.** Every number keeps the customer's phrasing beside it in the
  full output, so a disputed quote can be traced to the sentence it came from.
- **Consistent.** The rule engine and the LLM path call the *same* parsers, so
  they cannot disagree about how "a couple hundred" reads.

## 2. Two extraction paths, always

The rule engine in `heuristics.py` runs on every email regardless of provider.
It is three things at once:

- **the offline product** — a complete extractor with no key, no network, no cost;
- **the fallback** — if the model is rate-limited, unreachable or returns prose
  instead of JSON (retried once with a repair prompt), its result is returned,
  `extraction.fallback_reason` says why, and confidence takes a small penalty;
- **the cross-check** — when the model does answer, the rule engine's
  independent reading becomes the `agreement` term of the confidence score.

An inbox automation that stops working when a free tier runs out is not an
automation.

### How the rule engine attaches attributes

Vocabulary matching is span-aware: every hit knows where in the email it came
from. Product mentions partition the text into regions, and each product takes
the quantity immediately before it and the attributes around it.

```
"Can you send me pricing for 200 polos and also check if you have 100 hats available?"
                            └──── polo shirt region ────┘└───── hat region ─────┘
                                  quote_request              product_information
```

A region stops before the next product's own quantity, so the 100 belongs to the
hats. Constraints stated once for the whole email ("all delivered by Dec 1") are
collected from the sentences that mention no product and applied to every line.

Misspellings resolve by similarity against the alias table — "tumbelers" scores
0.93 against "tumblers" — and the match is recorded as a warning and a small
confidence penalty rather than passed off as certain.

## 3. Not inventing information

Handled in three layers, because prompting alone does not guarantee it:

**Prompt** — an explicit instruction that an empty field is a correct answer,
with a worked example of a vague request that stays vague.

**Grounding** (`grounding.py`) — every value must trace back to the email text
before it is allowed out:

- vocabulary values are grounded through their aliases, so `"tees"` grounds
  `"t-shirt"` even though the strings share nothing;
- numbers are grounded on their digits actually occurring in the email;
- free text is grounded on content-word overlap.

Ungrounded values are dropped and recorded in `extraction.warnings`, and the
grounding ratio feeds confidence. A model reply claiming a colour of `purple`,
a quantity of `900` and a requirement of "Rush shipping to Alaska" — none of
which are in the email — loses all three.

**Computed `missing_information`** — a model asked to list what is missing
hallucinates in both directions. The code compares the extracted product against
the fields a sourcing bot needs and reports the gaps.

The same principle decides harder cases. An event date mentioned in passing is
not a delivery commitment, so `"for our event next month"` produces no deadline
while `"delivered by Oct 20"` does. A fabricated commitment is worse than an
acknowledged gap.

## 4. Confidence

A transparent weighted blend, not a number the model chose:

```
0.30 × model_signal   (the model's own confidence, or 0.85 for the rule engine)
0.30 × grounding      (share of values traceable to the email)
0.25 × agreement      (LLM reading vs the independent rule reading)
0.15 × completeness
  −  penalties        (vague product −0.25, unknown intent −0.30, fuzzy spelling
                       match −0.05, validation errors −0.20, LLM fallback −0.05, ...)
```

Confidence measures *how likely this reading is correct*, not how complete the
customer's information was — a crisp two-line email with no budget still scores
high, and the gap shows up in `missing_information` instead. The full breakdown
ships in `extraction.confidence_breakdown`.

## Module map

| Module | Responsibility |
|---|---|
| `config.py` | settings, provider registry and auto-detection |
| `thread.py` | email/thread parsing, quoted-history split, reference date |
| `textutil.py` | cleanup, tokenising, span-aware vocabulary matching (incl. fuzzy) |
| `vocab.py` | the domain: products, colors, materials, sizes, decorations, intent cues, brands |
| `numbers.py` | quantity + budget parsing, number role classification |
| `dates.py` | relative-date resolution, delivery-cue detection, US holidays |
| `heuristics.py` | the rule engine |
| `prompts.py` | system prompt, few-shot examples, repair prompt |
| `providers.py` | Groq / Gemini / OpenRouter / Ollama over `urllib`, JSON recovery |
| `grounding.py` | anti-hallucination verification |
| `confidence.py` | scoring |
| `schema.py` | the output contract, validation, JSON Schema |
| `extractor.py` | the pipeline |
| `cli.py`, `server.py` | command line and intake |

`vocab.py` is the intentional tuning surface: adding a product category is a data
change, not a code change.

## Adding an LLM provider

One class and one registry entry, about 20 lines. Providers only move bytes —
parsing, validation, normalisation and scoring all happen in the pipeline:

```python
class MyProvider(OpenAICompatibleProvider):
    name = "myprovider"
    endpoint = "https://api.example.com/v1/chat/completions"

_REGISTRY["myprovider"] = MyProvider
```

Add a `ProviderSpec` to `PROVIDERS` in `config.py` with its env key and default
model, and it becomes available to auto-detection, the CLI and the server.
