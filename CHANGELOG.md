# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The `spec` output contract (`to_spec_dict()`) is the public API for versioning
purposes — a change to its fields is a major version.

## [Unreleased]

## [1.0.0] - 2026-09-23

First release.

### Added

- **Extraction pipeline** turning an unstructured customer email into structured
  purchasing requirements: intake → thread assembly → rule engine → LLM semantic
  extraction → deterministic normalisation → grounding → confidence.
- **Nine intent types**: `product_request`, `quote_request`, `product_information`,
  `order_status`, `revision_request`, `general_question`, `complaint`,
  `multiple_requests`, `unknown`.
- **Per-product requirements**: type, quantity (with range bounds and an
  approximation flag), materials, colors, sizes, per-unit budget, currency,
  decoration, delivery deadline and special requirements — each attached to the
  product the customer attached it to, so "200 polos and 100 hats" stays two
  line items.
- **Deterministic normalisation.** The model returns the customer's verbatim
  wording; `numbers.py` and `dates.py` convert it. "around 100-150 pcs",
  "under 20 bucks each" and "before the 20th" become numbers and ISO dates by
  arithmetic, not inference, resolved against the email's own received date.
- **Anti-hallucination grounding.** Every value is verified against the email
  text before it is emitted — vocabulary values through their aliases, numbers
  through their digits, free text through word overlap. Ungrounded values are
  dropped and recorded in `extraction.warnings`.
- **Computed `missing_information`**, derived from what is absent rather than
  asked of the model.
- **Explainable confidence**, a weighted blend of model signal, grounding,
  agreement between the LLM and rule readings, and completeness, with the full
  breakdown in `extraction.confidence_breakdown`.
- **Offline rule engine** that handles every email with no API key and no
  network, and doubles as the fallback and cross-check for the LLM path.
- **Free LLM providers**: Groq, Gemini, OpenRouter and Ollama, auto-detected
  from whichever key is present, over the standard library only.
- **Thread context**: quoted reply history is separated from new content, so
  last month's numbers inform the reading without becoming this month's
  requirements.
- **Two intake modes**: an HTTP webhook and a drop-folder watcher.
- **CLI** (`email-intent`) with `spec`, `full`, `jsonl` and `table` output, and
  `--schema` to print the output JSON Schema.
- 18 bundled sample emails and 35 offline tests, including a stubbed LLM.

[Unreleased]: https://github.com/rishab-sharma/email-intent/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/rishab-sharma/email-intent/releases/tag/v1.0.0
