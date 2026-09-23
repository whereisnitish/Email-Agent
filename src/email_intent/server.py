"""Email intake -- step 1 of the brief, "detect a new customer email".

Two intake modes, both dependency-free, both feeding the same pipeline:

  webhook   POST an email payload, get structured intent back synchronously.
            This is what a mail provider's push notification (Gmail Pub/Sub,
            Graph subscriptions, SendGrid inbound parse) would call.

  watch     Poll a drop folder for new .json email files. This is the shape a
            scheduled IMAP/Graph poller or an iPaaS step would use.

    python -m email_intent.server --serve 8080
    python -m email_intent.server --watch ./inbox --output ./extracted

Both write the same JSON contract, so whichever intake a deployment uses, the
downstream automation sees one format.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .config import Config
from .extractor import IntentExtractor

MAX_BODY_BYTES = 1_000_000


class IntentHandler(BaseHTTPRequestHandler):
    """POST /extract (or /) with an email payload -> structured intent JSON."""

    extractor: IntentExtractor
    output_format: str = "full"
    server_version = "EmailIntent/1.0"

    def _send(self, status: int, payload: dict[str, Any] | list[Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("", "/health"):
            self._send(200, {
                "status": "ok",
                "provider": self.extractor.provider_name,
                "model": self.extractor.model_name,
                "endpoint": "POST /extract",
            })
        else:
            self._send(404, {"error": "not found", "try": "POST /extract"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") not in ("", "/extract"):
            self._send(404, {"error": "not found", "try": "POST /extract"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._send(400, {"error": "empty request body"})
            return
        if length > MAX_BODY_BYTES:
            self._send(413, {"error": "payload too large"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            self._send(400, {"error": f"invalid JSON: {error}"})
            return

        try:
            if isinstance(payload, list):
                results = self.extractor.extract_many(payload)
                self._send(200, [self._render(r) for r in results])
            else:
                self._send(200, self._render(self.extractor.extract(payload)))
        except Exception as error:  # noqa: BLE001 - a bad email must not kill the server
            self._send(500, {"error": f"{type(error).__name__}: {error}"})

    def _render(self, result: Any) -> dict[str, Any]:
        return result.to_dict() if self.output_format == "full" else result.to_spec_dict()

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[server] {self.address_string()} {fmt % args}\n")


def serve(port: int, config: Config, output_format: str = "full") -> None:
    extractor = IntentExtractor(config)
    handler = type("BoundIntentHandler", (IntentHandler,), {
        "extractor": extractor,
        "output_format": output_format,
    })
    httpd = HTTPServer(("0.0.0.0", port), handler)
    print(
        f"listening on http://localhost:{port}  "
        f"(provider={extractor.provider_name}, model={extractor.model_name})\n"
        f"  curl -X POST http://localhost:{port}/extract "
        f"-H 'Content-Type: application/json' -d @src/email_intent/data/single_email.json",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", file=sys.stderr)
        httpd.server_close()


def watch(inbox: Path, output: Path, config: Config, interval: float, once: bool = False) -> None:
    """Treat new .json files in `inbox` as newly-arrived customer emails."""
    inbox.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    extractor = IntentExtractor(config)
    seen: set[str] = {p.stem for p in output.glob("*.json")}

    print(
        f"watching {inbox} every {interval}s "
        f"(provider={extractor.provider_name}); results -> {output}",
        file=sys.stderr,
    )
    while True:
        for path in sorted(inbox.glob("*.json")):
            if path.stem in seen:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as error:
                print(f"[skip] {path.name}: {error}", file=sys.stderr)
                seen.add(path.stem)
                continue

            payloads = payload if isinstance(payload, list) else [payload]
            results = extractor.extract_many(payloads)
            destination = output / f"{path.stem}.json"
            destination.write_text(
                json.dumps(
                    [r.to_dict() for r in results] if len(results) > 1 else results[0].to_dict(),
                    indent=2,
                ),
                encoding="utf-8",
            )
            seen.add(path.stem)
            for result in results:
                print(
                    f"[new email] {path.name} -> {result.customer_intent} "
                    f"({len(result.products)} product(s), confidence {result.confidence})",
                    file=sys.stderr,
                )
        if once:
            return
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="email-intent-server", description="Email intake for intent extraction."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", type=int, metavar="PORT", help="run the webhook listener")
    mode.add_argument("--watch", metavar="DIR", help="poll a drop folder for new emails")

    parser.add_argument("--output", default="./extracted", help="where watch mode writes results")
    parser.add_argument("--interval", type=float, default=2.0, help="watch poll interval (seconds)")
    parser.add_argument("--once", action="store_true", help="watch mode: one pass, then exit")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--model")
    parser.add_argument("--format", default="full", choices=["spec", "full"])
    args = parser.parse_args(argv)

    config = Config(provider=args.provider, model=args.model)
    if args.serve:
        serve(args.serve, config, args.format)
    else:
        watch(Path(args.watch), Path(args.output), config, args.interval, args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
