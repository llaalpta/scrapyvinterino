import re

SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"\b(access_token_web|authorization|cookie|csrf(?:_token)?|password|refresh_token|secret|set-cookie|token)(\s*[:=]\s*)([^\s;,&]+)",
    re.IGNORECASE,
)
BEARER_TOKEN_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def redact_sensitive_text(value: str) -> str:
    redacted = BEARER_TOKEN_PATTERN.sub("Bearer <redacted>", value)
    return SENSITIVE_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", redacted)
