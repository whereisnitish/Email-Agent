<div align="center">

# email-intent

**Turn unstructured customer emails into structured purchasing requirements.**

[![CI](https://github.com/rishab-sharma/email-intent/actions/workflows/ci.yml/badge.svg)](https://github.com/rishab-sharma/email-intent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

</div>

A customer writes an email. Another automation needs a purchase order.
This bridges the two — and never makes anything up.

```
"Hey John, we need around 150 tumblers for our event next month. Looking for
 something black or dark blue, preferably stainless steel. Budget is around
 $15 each. If you can find something similar to the one we ordered last time
 that would be great. Need them delivered by Oct 20. Thanks!"
```

```json
{
  "customer_intent": "product_request",
  "summary": "Customer is looking for approximately 150 stainless steel tumblers for an event.",
  "products": [{
    "product_type": "tumbler", "quantity": 150,
    "materials": ["stainless steel"], "colors": ["black", "dark blue"], "sizes": [],
    "budget_per_unit": 15, "currency": "USD", "decoration": null,
    "delivery_deadline": "2026-10-20",
    "special_requirements": ["Similar to previously ordered product"]
  }],
  "customer_preferences": {
    "preferred_suppliers": [], "preferred_brand": null, "previous_order_reference": true
  },
  "missing_information": [],
  "confidence": 0.95
}
```

Rewrite that email as *"Hey, can you find me about 150 black stainless tumblers,
something close to what we got last time? Trying to stay under 15 bucks each. We
need these before the 20th."* and you get the same structured result. No keyword
matching, no template.

---

## Why it is built this way

**Zero runtime dependencies, and it works with no API key.** A deterministic rule
engine extracts every email offline. Configure a free LLM (Groq, Gemini,
OpenRouter or a local Ollama) and the same pipeline gets better at unusual
phrasing — nothing else changes.

**The model extracts; the code computes.** The LLM is never asked for a number or
a date. It returns the customer's verbatim wording — `"around 150"`,
`"before the 20th"` — and Python converts it. Date maths is arithmetic, not
inference, resolved against the *email's own* received date, so a re-run next
year gives the same answer.

**It does not invent information.** Every value must trace back to the email text
before it is emitted; anything that cannot is dropped and logged. Absent
information lands in `missing_information` — computed from what is missing, not
guessed by a model. An empty field is a correct answer.

**It degrades, it does not stop.** The rule engine runs on every email, so a
rate-limited or unreachable model means a slightly lower confidence score, not a
failed extraction.

---

## Install

```bash
pip install git+https://github.com/rishab-sharma/email-intent
```

Or from a checkout:

```bash
git clone https://github.com/rishab-sharma/email-intent
cd email-intent
pip install -e ".[dev]"
make check          # ruff + 35 tests, all offline
```

Python 3.10+. Nothing else required.

## Use it

```bash
# Every bundled sample email, one line each
email-intent --demo --format table

# A single email, in the exact output contract
email-intent --text "need 250 pcs tees, XL mostly, delivery ASAP"

# From a file, a pipe, or with provenance
email-intent --file inbox.json --format full
echo '{"body":"can you quote 200 polos?"}' | email-intent --stdin
```

```python
from email_intent import extract_intent

result = extract_intent({
    "email_id": "msg_1001",
    "received_at": "2026-09-23",
    "body": "Need 150 tumbelers in black. Can u check pricing?",
})

result.customer_intent          # "quote_request"
result.products[0].product_type # "tumbler"  (misspelling resolved)
result.missing_information      # ["budget_per_unit", "delivery_deadline"]
result.to_spec_dict()           # the JSON contract
```

## Run it as a service

```bash
docker compose up extractor            # webhook on :8080
curl -X POST localhost:8080/extract -H 'Content-Type: application/json' \
     -d '{"body":"quote 200 polos and 100 hats please"}'
```

Or watch a drop folder — the shape a scheduled IMAP/Graph poller uses:

```bash
email-intent-server --watch ./inbox --output ./extracted
```

## Add a free LLM

```bash
cp .env.example .env         # paste one key in
email-intent --providers     # shows what was detected
```

| Provider | Free tier | Default model | Get a key |
|---|---|---|---|
| `groq` | yes, generous | `llama-3.3-70b-versatile` | [console.groq.com](https://console.groq.com) |
| `gemini` | yes | `gemini-2.0-flash` | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `openrouter` | yes, `:free` models | `meta-llama/llama-3.3-70b-instruct:free` | [openrouter.ai](https://openrouter.ai/keys) |
| `ollama` | fully local | `llama3.1` | `ollama serve` |
| `heuristic` | — | rule engine | always available, the default |

Auto-detected from whichever key is present, or forced with
`--provider groq --model llama-3.1-8b-instant`.

---

## What it handles

Nine intents — `product_request`, `quote_request`, `product_information`,
`order_status`, `revision_request`, `general_question`, `complaint`,
`multiple_requests`, `unknown` — and the way people actually write:

| Variation | Example | Result |
|---|---|---|
| Informal | "hook me up with about 100 black hoodies" | `hoodie`, 100, approximate, black |
| Abbreviations | "250 pcs tees, XL mostly, delivery ASAP" | `t-shirt`, 250, `["XL"]`, ASAP requirement |
| Spelling mistakes | "150 tumbelers in black" | `tumbler` — matched by similarity, logged, confidence penalised |
| Conversational | "maybe 200 bottles, nothing too expensive" | `water bottle`, 200, budget **null** and reported missing |
| Approximate quantity | "around 100-150 pieces" | min 100, max 150, flagged approximate |
| Approximate budget | "under 20 bucks each" | 20, `budget_is_maximum: true`, USD |
| Multiple products | "100 polos, 200 tumblers and maybe 50 backpacks" | three line items, correct quantity on each |
| Mixed asks | "pricing for 200 polos **and** do you have 100 hats?" | `multiple_requests`, two products, two request types |
| Missing information | "some good promotional products" | one vague line, four fields reported missing, confidence 0.59 |
| Threads | "let's do the same tumblers again, 200 this time" | reads the quoted history for context, takes requirements from the new message only |

Attributes attach to the product they belong to. In *"pricing for 200 polos and
also check if you have 100 hats available"*, the 100 belongs to the hats, the
polos are a `quote_request` and the hats are `product_information`.

```
$ email-intent --demo --format table

email_id   intent                conf   products
msg_1001   product_request       0.95   150x tumbler
msg_1003   multiple_requests     0.88   200x polo shirt, 100x hat
msg_1005   product_request       0.92   250x t-shirt
msg_1010   product_request       0.59   ?x promotional products
                                        missing: product_specification, quantity, budget_per_unit, delivery_deadline
msg_1011   order_status          0.95   -
msg_1013   complaint             0.95   40x mug
msg_1017   unknown               0.50   -
msg_1018   product_request       0.63   75x unspecified
                                        missing: product_type, budget_per_unit
```

Note the last two: a marketing newsletter is `unknown` rather than invented into
a request, and an email that gives a quantity but never names the product keeps
`product_type: null` instead of guessing it from the brand.

## Documentation

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | the pipeline, the design decisions, how to add a provider |
| [docs/output-contract.md](docs/output-contract.md) | every field, the fixed conventions, intent definitions |
| [docs/integration.md](docs/integration.md) | intake modes, downstream/Sage mapping, configuration, deployment |
| [CONTRIBUTING.md](CONTRIBUTING.md) | setup, the rules that matter, where to change what |
| [SECURITY.md](SECURITY.md) | data handling, prompt injection, reporting |

## Project layout

```
src/email_intent/
  config.py  thread.py  textutil.py  vocab.py      intake, text, domain vocabulary
  numbers.py  dates.py                             deterministic normalisation
  heuristics.py                                    the offline rule engine
  prompts.py  providers.py                         the LLM path
  grounding.py  confidence.py  schema.py           verification, scoring, contract
  extractor.py  cli.py  server.py                  pipeline and interfaces
  data/emails.json                                 18 bundled sample emails
tests/                                             35 offline tests, incl. a stubbed LLM
```

## Development

```bash
make help       # all targets
make check      # lint + tests, what CI runs
make demo       # extract every sample email
make coverage   # tests with coverage reporting
```

CI runs ruff and the full suite on Linux, macOS and Windows across Python
3.10–3.13, plus a smoke test that installs the wheel and verifies the output
contract from outside the checkout.

## Known limits

- The vocabulary is hand-built for promotional products, so the offline path is
  domain-bound. Adding a category is a data edit in `vocab.py`; the LLM path
  generalises past it, and grounding falls back to word overlap for unseen terms.
- Compound deadlines — *"the gala is Nov 14 so we need everything a week before"* —
  resolve to no deadline rather than a wrong one. Deliberate, but a real gap.
- Size *breakdowns* ("40 M, 80 L, 30 XL") are captured as sizes, not as per-size
  quantities.
- No built-in deduplication of processed message IDs beyond the watch folder; a
  production deployment wants a real queue and an idempotency key.

## License

[MIT](LICENSE)
