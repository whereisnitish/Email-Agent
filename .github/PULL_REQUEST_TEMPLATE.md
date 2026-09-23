# What this changes

<!-- One or two sentences. Link the issue it closes: Closes #123 -->

## Type

- [ ] Bug fix (an email that extracted wrongly)
- [ ] Vocabulary addition (new products, colors, materials, decorations)
- [ ] New extraction capability
- [ ] Provider / integration
- [ ] Docs, tooling or CI

## Extraction changes

<!-- Delete this section if the PR does not change what comes out of an email. -->

**Email:**

```text

```

**Before / after:**

```json

```

## Checklist

- [ ] `make check` passes (ruff + the full test suite)
- [ ] A test covers the new behaviour — for extraction changes, the customer's
      wording is in the test, so a regression is obvious
- [ ] No invented data: every value still traces back to the email text, and
      absent information lands in `missing_information` rather than being guessed
- [ ] The `spec` output contract is unchanged, or the change is called out here
      and recorded in `CHANGELOG.md`
