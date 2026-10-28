from __future__ import annotations

import re
from collections.abc import Iterable
from re import Pattern


REDACTED = "[REDACTED]"

DEFAULT_PATTERNS: tuple[tuple[Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTED,
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (
        re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*)(['\"]?)[^\s'\"]{8,}\3"),
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
    ),
    (
        re.compile(r"(?i)\b(authorization|proxy-authorization)(\s*:\s*)(bearer|basic)\s+\S+"),
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)} {REDACTED}",
    ),
    (
        re.compile(r"(?i)(https?://[^/\s:@]+:)[^/\s@]+(@)"),
        lambda match: f"{match.group(1)}{REDACTED}{match.group(2)}",
    ),
)


def redact(text: str, patterns: Iterable[tuple[Pattern[str], str]] | None = None) -> str:
    result = text
    for pattern, replacement in patterns or DEFAULT_PATTERNS:
        result = pattern.sub(replacement, result)
    return result
