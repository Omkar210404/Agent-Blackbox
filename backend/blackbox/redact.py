"""Optional rule-based redaction of personal data and secrets before payloads are stored."""
import re

UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _key(m):  # long mixed strings look like secrets, but UUIDs are ids, not secrets
    return m.group(0) if UUID.match(m.group(0)) else "[REDACTED:api_key]"


def _phone(m):  # 10-15 digits, and not a date such as 2026-10-12 10:30
    s = m.group(0)
    return s if not 10 <= sum(ch.isdigit() for ch in s) <= 15 or DATE.match(s) else "[REDACTED:phone]"


# Order matters: secrets first, then emails, then long digit runs, then phone numbers.
RULES = [
    (re.compile(r"\b(?:sk|pk|rk|bbk|bba|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{16,}"), "[REDACTED:api_key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:api_key]"),
    (re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}"), _key),
    # Keep the email domain: it matters for auditing where data was sent.
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"), r"[email]@\1"),
    (re.compile(r"(?<!\w)\+?\d(?:[ \-]?\d){11,}\b"), "[REDACTED:number]"),
    (re.compile(r"(?<!\w)\+?\d[\d \-()]{7,}\d\b"), _phone),
]


def redact_text(text: str) -> str:
    for pattern, repl in RULES:
        text = pattern.sub(repl, text)
    return text


def redact(value):
    """Redact every string inside a JSON-like value (keys are left as they are)."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
