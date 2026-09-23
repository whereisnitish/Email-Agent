"""Access to data bundled with the package.

The demo emails ship inside the wheel, so ``email-intent --demo`` works after a
plain ``pip install`` with no checkout present.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

DATA_PACKAGE = "email_intent.data"

SAMPLE_EMAILS = "emails.json"
SINGLE_EMAIL = "single_email.json"


def data_path(name: str = SAMPLE_EMAILS):
    """Filesystem path to a bundled data file (for docs and curl examples)."""
    return resources.files(DATA_PACKAGE).joinpath(name)


def load_json(name: str = SAMPLE_EMAILS) -> Any:
    """Read a bundled JSON data file."""
    with resources.files(DATA_PACKAGE).joinpath(name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sample_emails() -> list[dict[str, Any]]:
    """The bundled demo inbox: one entry per customer email."""
    return load_json(SAMPLE_EMAILS)
