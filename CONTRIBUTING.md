# Contributing

Thanks for your interest. The most valuable contribution to this project is
**a customer email that extracts wrongly** — with the email text attached, it
becomes a test case and stays fixed.

## Setup

```bash
git clone https://github.com/rishab-sharma/email-intent
cd email-intent
python -m pip install -e ".[dev]"
make check        # ruff + the full test suite
```

Python 3.10+. The runtime has no third-party dependencies and should stay that
way; anything new belongs in the `dev` extra.

## The rules that matter

These are not style preferences — the project's usefulness depends on them.

**1. Never invent information.** If the customer did not write it, it is `null`
or `[]`. An empty field is a correct answer; a plausible guess is a bug. Values
that cannot be traced back to the email text are dropped by `grounding.py`, so
a new field also needs a grounding rule.

**2. The model does not do arithmetic.** The LLM returns the customer's verbatim
wording (`quantity_raw`, `budget_raw`, `delivery_deadline_raw`); `numbers.py` and
`dates.py` convert it. If you are tempted to ask the model for a resolved date,
add a pattern to `dates.py` instead. This is what keeps extraction reproducible
and auditable, and what keeps the two extraction paths from disagreeing.

**3. Dates resolve against the email, never against "now".** Every date function
takes a reference date. `date.today()` appears in exactly one place — as the
fallback when the email carries no timestamp.

**4. The `spec` output is a contract.** Downstream automation binds to
`to_spec_dict()`. Adding fields there is a breaking change; put new information
in the full output (`to_dict()`) instead, and note anything unavoidable in
`CHANGELOG.md`.

## Where things live

| I want to... | Edit |
|---|---|
| Add a product category, color, material or imprint method | `vocab.py` |
| Handle a new way of writing a quantity or price | `numbers.py` |
| Handle a new way of writing a date | `dates.py` |
| Change how an intent is decided | `heuristics.py` (`_classify`) |
| Change what the model is asked for | `prompts.py` |
| Add an LLM provider | `providers.py` (~20 lines: one class + registry entry) |
| Add a field to the output | `schema.py`, then `grounding.py` |

`vocab.py` is the intentional tuning surface — adding a product category is a
data change, not a code change, and needs no understanding of the pipeline.

## Tests

```bash
make test
python -m unittest tests.test_extraction.NaturalLanguageTests -v
```

Every behaviour change needs a test, and for extraction changes the customer's
actual wording goes in the test:

```python
def test_informal(self):
    result = extract("Hey John, can you hook me up with about 100 black hoodies?")
    product = result.products[0]
    self.assertEqual(product.product_type, "hoodie")
    self.assertEqual(product.quantity, 100)
    self.assertTrue(product.quantity_is_approximate)
```

The suite runs fully offline — the LLM path is exercised against a stub
provider, so no key is needed and CI never depends on a third party's uptime.
If you add a provider, stub its transport rather than calling it.

New sample emails go in `src/email_intent/data/emails.json`; they are covered
automatically by `test_every_sample_email_produces_valid_json`.

## Pull requests

- Branch from `main`, keep the change focused.
- `make check` must pass. CI runs the same thing on Linux, macOS and Windows
  across Python 3.10–3.13, plus a smoke test against an installed wheel.
- Fill in the PR template, including the before/after JSON for extraction
  changes.
- Commit messages: imperative mood, one line of subject
  (`Handle "a week before <date>" deadlines`), detail in the body.

## Reporting a bug without a fix

Open an issue with the email text (redact names, keep the phrasing and numbers —
phrasing is what the extractor reads), the JSON you got from
`email-intent --text "..." --format full`, and the JSON you expected.
