# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | yes |

## Reporting a vulnerability

Please report privately through
[GitHub Security Advisories](https://github.com/rishab-sharma/email-intent/security/advisories/new)
rather than opening a public issue. Expect an acknowledgement within 3 working
days and an assessment within 10.

## What this software handles

Customer emails. Treat every input as untrusted and potentially confidential.

**Email content reaches a third party when an LLM provider is configured.** With
`--provider heuristic` (the default when no key is set) or with `ollama` running
locally, no email text leaves the machine. With Groq, Gemini or OpenRouter, the
email body and any thread context are sent to that provider — check their data
retention terms before pointing this at a real inbox, and prefer the local or
offline path for regulated data.

**Prompt injection is possible by design.** An email is attacker-controlled text
that reaches a language model. A malicious email can attempt to steer the model's
output. Mitigations in place: the model only ever returns data (never commands or
tool calls), output is schema-validated and enum-constrained, every value is
verified against the source text before it is emitted, and all numbers and dates
are computed by Python rather than accepted from the model. The worst realistic
outcome is a wrong extraction, which the confidence score and
`extraction.warnings` are there to surface. Do not wire the output directly into
an irreversible action without a confidence threshold and a review path.

**Secrets.** API keys are read from the environment or `.env`, never logged, and
never included in output. `.env` is gitignored — keep it that way.

**The server has no authentication.** `email-intent-server --serve` is intended
to run behind a reverse proxy, API gateway or private network boundary. Do not
expose it directly to the internet. It binds `0.0.0.0` for container use, caps
request bodies at 1 MB, and parses JSON only.

## Scope

In scope: code execution, key or email disclosure, denial of service through
crafted email content, container escape from the published image.

Out of scope: a wrong extraction on an unusual email (that is a bug — please
open a normal issue), and vulnerabilities in third-party LLM providers.
