"""Log redaction.

API keys must never appear in logs. Nothing in the normal paths logs a request
body or an auth header, but the guard has to hold even when an unexpected
provider/transport error carries a header dump in its message, so a filter scrubs
credential-shaped substrings from every record before it is emitted.

The patterns are intentionally specific (a known header name, a ``key=`` pair, a
JSON field) rather than a broad "long random string" match, so ordinary log text
is not mangled.
"""

from __future__ import annotations

import logging
import re

# Header names and field names that carry a credential. The value after them is
# replaced with ``[redacted]``.
_CREDENTIAL_PATTERNS = [
    re.compile(r"(?i)(x-subscription-token\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+"),
    re.compile(r"(?i)(brave_api_key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]+"),
]

REDACTED = "[redacted]"


def redact(text: str) -> str:
    """Return ``text`` with any credential-shaped substring removed."""
    for pattern in _CREDENTIAL_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrite each record's message without the credential substrings."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        if isinstance(record.msg, str) and record.msg:
            record.msg = redact(record.msg)
        # ``%``-args may hold the sensitive value when the message is a template.
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (redact(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
        return True


def install_log_redaction() -> None:
    """Attach the redaction filter to the root handlers (idempotent)."""
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
