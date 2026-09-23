"""Email and thread handling.

Step 2 of the brief -- "read the complete email content and relevant
thread/context" -- lives here. Input shapes vary between mail providers, so the
parser is permissive about field names, and a reply's quoted history is
recovered as context rather than thrown away.

Context is kept strictly separate from the new message: requirements are
extracted from the new email, while the thread answers questions like "is this
customer referring back to an earlier order?".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .textutil import strip_quoted_reply

_ID_KEYS = ("email_id", "id", "message_id", "messageId", "msg_id")
_FROM_KEYS = ("from", "sender", "from_address", "fromAddress", "from_email")
_SUBJECT_KEYS = ("subject", "title")
_BODY_KEYS = ("body", "text", "content", "body_text", "plain_text", "snippet")
_DATE_KEYS = ("received_at", "receivedAt", "date", "timestamp", "sent_at", "internalDate")
_THREAD_KEYS = ("thread", "thread_messages", "previous_messages", "history", "context", "messages")

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
}


def _first(data: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def _strip_html(text: str) -> str:
    if "<" not in text:
        return text
    text = re.sub(r"<(br|/p|/div|/tr)[^>]*>", "\n", text, flags=re.I)
    text = _HTML_TAG_RE.sub("", text)
    for entity, replacement in _HTML_ENTITIES.items():
        text = text.replace(entity, replacement)
    return text


def parse_date(value: Any) -> date | None:
    """Accept ISO strings, RFC-2822 headers, epoch millis, date/datetime objects."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # Gmail internalDate is milliseconds
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds).date()
    text = str(value).strip()
    for parse in (
        lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")).date(),
        lambda t: parsedate_to_datetime(t).date(),
        lambda t: datetime.strptime(t, "%Y-%m-%d").date(),
        lambda t: datetime.strptime(t, "%m/%d/%Y").date(),
    ):
        try:
            return parse(text)
        except (ValueError, TypeError, IndexError):
            continue
    if text.isdigit():
        return parse_date(int(text))
    return None


@dataclass
class EmailMessage:
    """One message, normalised out of whatever the mail source provided."""

    email_id: str | None = None
    sender: str = ""
    subject: str = ""
    body: str = ""
    received_at: date | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | str) -> EmailMessage:
        if isinstance(data, str):
            return cls(body=data.strip(), raw={"body": data})
        body = _strip_html(str(_first(data, _BODY_KEYS, "") or ""))
        return cls(
            email_id=_first(data, _ID_KEYS),
            sender=str(_first(data, _FROM_KEYS, "") or ""),
            subject=str(_first(data, _SUBJECT_KEYS, "") or ""),
            body=body.replace("\r\n", "\n").strip(),
            received_at=parse_date(_first(data, _DATE_KEYS)),
            raw=data,
        )

    @property
    def new_content(self) -> str:
        """The body with quoted history and signature removed."""
        return strip_quoted_reply(self.body)

    @property
    def quoted_history(self) -> str:
        """Whatever `new_content` removed -- still useful as context."""
        stripped = self.new_content
        if not stripped or stripped == self.body.strip():
            return ""
        return self.body[len(stripped):].strip()

    def as_context_line(self) -> str:
        stamp = self.received_at.isoformat() if self.received_at else "unknown date"
        who = self.sender or "unknown sender"
        subject = f" | Subject: {self.subject}" if self.subject else ""
        body = " ".join(self.body.split())
        return f"[{stamp}] From: {who}{subject}\n{body}"


@dataclass
class EmailThread:
    """The new message plus the earlier messages that give it context."""

    message: EmailMessage
    history: list[EmailMessage] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | str) -> EmailThread:
        message = EmailMessage.from_dict(payload)
        history: list[EmailMessage] = []
        if isinstance(payload, dict):
            raw_history = _first(payload, _THREAD_KEYS, []) or []
            if isinstance(raw_history, dict):
                raw_history = raw_history.get("messages", [])
            for entry in raw_history:
                previous = EmailMessage.from_dict(entry)
                # A thread array often includes the new message itself.
                if previous.email_id and previous.email_id == message.email_id:
                    continue
                if previous.body.strip() and previous.body.strip() == message.body.strip():
                    continue
                history.append(previous)

        quoted = message.quoted_history
        if quoted and not history:
            history.append(EmailMessage(body=quoted, subject=message.subject, raw={"source": "quoted"}))

        history.sort(key=lambda m: m.received_at or date.min)
        return cls(message=message, history=history)

    @property
    def reference_date(self) -> date:
        """Anchor for every relative date. Falls back to today for bare inputs."""
        return self.message.received_at or date.today()

    def context_text(self, *, max_messages: int = 6, max_chars: int = 4000) -> str:
        """Earlier messages, newest last, trimmed to a sane prompt budget."""
        if not self.history:
            return ""
        selected = self.history[-max_messages:]
        rendered = "\n\n".join(m.as_context_line() for m in selected)
        if len(rendered) > max_chars:
            rendered = "...(earlier context truncated)...\n" + rendered[-max_chars:]
        return rendered

    def full_text(self) -> str:
        """Everything the customer wrote -- the grounding corpus.

        Values are allowed to be grounded in the thread as well as the new
        message, because "same as the ones we ordered in March" legitimately
        resolves against context.
        """
        parts = [self.message.subject, self.message.new_content or self.message.body]
        parts.extend(m.body for m in self.history)
        return "\n".join(p for p in parts if p)


def detect_new_email(payload: dict[str, Any] | str) -> EmailThread:
    """Step 1 of the brief: accept an inbound email event and normalise it."""
    return EmailThread.from_payload(payload)
