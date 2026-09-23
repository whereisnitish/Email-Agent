# Integration guide

Getting emails in, and structured requirements out into the systems that act
on them.

## Intake: detecting a new email

Two dependency-free modes, both feeding the same pipeline and producing the same
contract.

### Webhook

What a Gmail Pub/Sub push, a Microsoft Graph subscription or a SendGrid
inbound-parse hook would call.

```bash
email-intent-server --serve 8080

curl -X POST http://localhost:8080/extract \
     -H 'Content-Type: application/json' \
     -d '{"email_id":"msg_1","received_at":"2026-09-23",
          "body":"Can you quote 200 polos and check if you have 100 hats?"}'
```

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET | readiness, plus the active provider and model |
| `/extract` | POST | one email object, or an array of them |

Responses are `full` by default; `--format spec` returns the minimal contract.
Bodies are capped at 1 MB. A malformed email returns 400 and a failing
extraction returns 500 — neither takes the server down.

### Drop folder

The shape a scheduled IMAP/Graph poller or an iPaaS step uses.

```bash
email-intent-server --watch ./inbox --output ./extracted
```

New `.json` files in `./inbox` are treated as newly arrived emails; results land
in `./extracted` under the same stem. Already-processed stems are skipped, so a
restart does not reprocess the folder. `--once` runs a single pass, which is what
you want from cron.

### Input format

The parser is permissive about field names, so most providers' payloads work
unchanged:

| Field | Accepted keys |
|---|---|
| id | `email_id`, `id`, `message_id`, `messageId`, `msg_id` |
| sender | `from`, `sender`, `from_address`, `fromAddress`, `from_email` |
| subject | `subject`, `title` |
| body | `body`, `text`, `content`, `body_text`, `plain_text`, `snippet` |
| date | `received_at`, `date`, `timestamp`, `sent_at`, `internalDate` |
| thread | `thread`, `thread_messages`, `previous_messages`, `history`, `context`, `messages` |

Dates accept ISO strings, RFC-2822 headers, epoch seconds or milliseconds
(Gmail's `internalDate`), and `date`/`datetime` objects. HTML bodies are
stripped to text.

**Supply `received_at`.** Every relative date resolves against it, so it is what
makes "before the 20th" mean the right month — and what makes a re-run produce
the same answer. Without it the extractor falls back to today.

### Threads

Pass earlier messages in `thread` and they become context: they inform whether
the customer is referring back to a previous order, but requirements are only
extracted from the new message. If no thread array is supplied, quoted reply
history in the body (`On Mon, Sep 21 ... wrote:`, `>` lines) is detected and used
the same way — so last month's numbers in the quote trail never become this
month's requirements.

## Downstream: quoting, sourcing, Sage

The `spec` output is intentionally close to a purchase-requisition line, so a
downstream step maps it without further language work:

| Extracted | Downstream use |
|---|---|
| `product_type` + `materials` / `colors` / `sizes` | catalogue or item search |
| `quantity` (+ `quantity_min` / `quantity_max`) | order line quantity; quote the range when it is soft |
| `budget_per_unit`, `currency` | target price, margin check, supplier filter |
| `budget_basis` | whether that figure is per unit or for the whole job |
| `delivery_deadline` + `delivery_precision` | required-by date; coarse precision means confirm with the customer |
| `decoration` | imprint method, setup line, artwork requirements |
| `special_requirements` | packaging, compliance and handling flags |
| `customer_preferences.previous_order_reference` | pull the customer's order history before quoting |
| `customer_preferences.preferred_brand` / `preferred_suppliers` | supplier shortlist |
| `order_references` (full output) | look up the existing order for status, revision and complaint intents |
| `missing_information` | the exact questions a reply-draft should ask |
| `confidence` | routing: auto-quote above a threshold, human review below it |

### A routing policy that works

```python
from email_intent import extract_intent

result = extract_intent(payload)

if result.customer_intent in {"complaint", "order_status", "revision_request"}:
    route_to_service_desk(result)                    # existing order, not new business
elif result.confidence >= 0.85 and not result.missing_information:
    create_quote(result)                             # enough to act on
elif result.confidence >= 0.60:
    draft_clarification(result.missing_information)  # ask for what is missing
else:
    route_to_human(result)                           # too uncertain to automate
```

`result.to_spec_dict()` gives the JSON contract; `result.to_dict()` adds the
provenance a reviewer needs.

### Using it as a library

```python
from email_intent import IntentExtractor
from email_intent.config import Config

extractor = IntentExtractor(Config(provider="groq"))   # or "auto", "heuristic", ...
for result in extractor.extract_many(emails):
    print(result.customer_intent, result.confidence, result.to_spec_dict())
```

One extractor instance can process many emails; provider resolution happens once
at construction.

## Configuration

Every setting is on `Config` and has a sensible default.

| Setting | Default | Purpose |
|---|---|---|
| `provider` | `"auto"` | `groq`, `gemini`, `openrouter`, `ollama`, `heuristic`, or auto-detect from env keys |
| `model` | provider default | override the model |
| `temperature` | `0.0` | determinism first |
| `max_retries` | `2` | repair attempts on malformed model output |
| `timeout_seconds` | `45` | per model call |
| `required_product_fields` | type, quantity, budget, deadline | what counts as missing for your workflow |
| `drop_ungrounded_values` | `True` | drop values not traceable to the email (`False` flags them instead) |
| `grounding_similarity` | `0.82` | fuzzy threshold for grounding |
| `fuzzy_threshold` | `0.80` | fuzzy threshold for misspelled product words |
| `merge_rule_findings` | `True` | fill gaps in the LLM result from the rule engine |
| `multi_product_is_multiple_requests` | `False` | whether 2+ products alone means `multiple_requests` |

Environment: `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`,
`OLLAMA_HOST`, `LLM_PROVIDER`, `LLM_MODEL`. A `.env` file in the working
directory is loaded automatically and never overrides an existing variable.

## Deployment

```bash
docker compose up extractor            # webhook on :8080
docker compose --profile watch up      # plus the folder watcher
```

The image runs as a non-root user, has no third-party runtime dependencies, and
carries a health check against `/health`. It works with no API key — the offline
rule engine handles every email — and adding a key to `.env` upgrades the same
container.

Operational notes:

- **The server has no authentication.** Put it behind a reverse proxy, API
  gateway or private network boundary.
- **Email content reaches a third party when an LLM provider is configured.**
  Use `heuristic` or `ollama` if that is not acceptable. See
  [SECURITY.md](../SECURITY.md).
- **Idempotency.** The watcher skips stems it has already written; a real
  deployment should key on `email_id` in a queue or database.
- **Throughput.** The offline path is pure CPU and takes single-digit
  milliseconds per email; the LLM path is dominated by provider latency
  (`extraction.duration_ms` records it). Scale horizontally — the extractor
  holds no state between emails.
