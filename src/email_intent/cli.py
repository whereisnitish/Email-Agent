"""Command-line entry point.

    python -m email_intent.cli --demo
    python -m email_intent.cli --file samples/emails.json --format full
    echo '{"body": "need 200 polos"}' | python -m email_intent.cli --stdin
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import AUTO_ORDER, PROVIDERS, Config, load_dotenv
from .extractor import IntentExtractor
from .resources import sample_emails
from .schema import JSON_SCHEMA


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-intent",
        description="Extract structured purchasing requirements from customer emails.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Providers (all free):\n"
            + "\n".join(f"  {name:<11} {spec.notes}" for name, spec in PROVIDERS.items())
            + "\n\nWith no key configured the deterministic rule engine runs offline."
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("-f", "--file", help="JSON file: one email object or an array of them")
    source.add_argument("--stdin", action="store_true", help="read the email JSON from stdin")
    source.add_argument("--text", help="extract from a raw body string")
    source.add_argument("--demo", action="store_true", help="run the bundled sample emails")

    parser.add_argument("--id", help="with --file/--demo, process only this email_id")
    parser.add_argument("-p", "--provider", default="auto",
                        choices=["auto", *PROVIDERS.keys()],
                        help="LLM provider (default: auto-detect from env keys)")
    parser.add_argument("-m", "--model", help="override the provider's default model")
    parser.add_argument("--format", default="spec", choices=["spec", "full", "jsonl", "table"],
                        help="spec = exactly the required contract; full = plus provenance")
    parser.add_argument("--out", help="write output to this file instead of stdout")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--no-merge", action="store_true",
                        help="do not fill LLM gaps from the rule engine")
    parser.add_argument("--keep-ungrounded", action="store_true",
                        help="flag values that are not traceable to the email instead of dropping them")
    parser.add_argument("--schema", action="store_true", help="print the output JSON Schema and exit")
    parser.add_argument("--providers", action="store_true", help="show provider detection and exit")
    return parser


def load_payloads(args: argparse.Namespace) -> list[dict[str, Any] | str]:
    if args.text:
        return [{"body": args.text}]
    if args.stdin:
        raw = sys.stdin.read().strip()
        if not raw:
            raise SystemExit("no input on stdin")
        return _coerce(json.loads(raw) if raw.startswith(("{", "[")) else {"body": raw})
    if not args.file:
        return _coerce(sample_emails())
    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"no such file: {path}")
    return _coerce(json.loads(path.read_text(encoding="utf-8")))


def _coerce(data: Any) -> list[dict[str, Any] | str]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "emails" in data and isinstance(data["emails"], list):
            return data["emails"]
        return [data]
    raise SystemExit("input must be a JSON object or array")


def show_providers() -> None:
    load_dotenv()
    import os

    print("Provider status (auto-detection order):")
    for name in AUTO_ORDER:
        spec = PROVIDERS[name]
        if spec.env_key:
            ready = "ready" if os.environ.get(spec.env_key) else f"missing {spec.env_key}"
        elif name == "ollama":
            ready = "local server (start with: ollama serve)"
        else:
            ready = "always available"
        print(f"  {name:<11} {ready:<28} model={spec.default_model}")
    resolved, model = Config().resolve_provider()
    print(f"\nWould use: {resolved} ({model})")


def render(results: list, fmt: str) -> str:
    if fmt == "jsonl":
        return "\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in results)
    if fmt == "table":
        return render_table(results)
    payload = [r.to_spec_dict() if fmt == "spec" else r.to_dict() for r in results]
    return json.dumps(payload[0] if len(payload) == 1 else payload, indent=2, ensure_ascii=False)


def render_table(results: list) -> str:
    lines = [f"{'email_id':<10} {'intent':<21} {'conf':<6} products"]
    lines.append("-" * 96)
    for result in results:
        items = ", ".join(
            f"{p.quantity or '?'}x {p.product_type or 'unspecified'}" for p in result.products
        ) or "-"
        lines.append(
            f"{(result.email_id or '-'):<10} {result.customer_intent:<21} "
            f"{result.confidence:<6.2f} {items}"
        )
        if result.missing_information:
            lines.append(f"{'':<10} {'':<21} {'':<6} missing: {', '.join(result.missing_information)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.schema:
        print(json.dumps(JSON_SCHEMA, indent=2))
        return 0
    if args.providers:
        show_providers()
        return 0

    config = Config(
        provider=args.provider,
        model=args.model,
        temperature=args.temperature,
        merge_rule_findings=not args.no_merge,
        drop_ungrounded_values=not args.keep_ungrounded,
    )

    try:
        payloads = load_payloads(args)
    except json.JSONDecodeError as error:
        raise SystemExit(f"input is not valid JSON: {error}") from error

    if args.id:
        payloads = [
            p for p in payloads
            if isinstance(p, dict) and str(p.get("email_id") or p.get("id")) == args.id
        ]
        if not payloads:
            raise SystemExit(f"no email with id {args.id!r} in the input")

    extractor = IntentExtractor(config)
    if not extractor.provider:
        print(
            "[info] no LLM key found - running the offline rule engine. "
            "See --providers to configure a free model.",
            file=sys.stderr,
        )

    results = extractor.extract_many(payloads)
    output = render(results, args.format)

    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
        print(f"wrote {len(results)} result(s) to {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
