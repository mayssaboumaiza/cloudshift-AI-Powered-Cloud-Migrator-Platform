"""
executor/failure_classifier.py — Deterministic Terraform error classifier.

Pure function — no I/O, no LLM, fully unit-testable.

classify_terraform_error(stderr_text, stage, exit_code) -> dict

Returns:
    {
      "class":       str,        # STATIC|SEMANTIC|DEPENDENCY|RUNTIME|QUOTA|STATE|ENVIRONMENTAL
      "resource":    str | None, # first matched terraform resource address (normalised)
      "message":     str,        # first Error: line, ≤ 300 chars
      "stage":       str,        # originating stage: "init"|"plan"|"apply"
      "raw_excerpt": str,        # first 1000 chars of stderr for post-mortem
    }
"""
from __future__ import annotations

import hashlib
import re

# ── Rule table — ordered, FIRST match wins ─────────────────────────────────────
_RULES: list[tuple[re.Pattern, str]] = [
    # STATE  (precedes ENVIRONMENTAL so "state lock" is not misclassed as auth)
    (re.compile(
        r"(state\s*lock|error acquiring the state lock|failed to load state"
        r"|state file is corrupted|backend not initializ|error loading state)",
        re.IGNORECASE,
    ), "STATE"),

    # QUOTA / CAPACITY
    (re.compile(
        r"(QuotaExceeded|LimitExceeded|out of capacity|quota.*exceeded"
        r"|subscription.*limit|core.*limit|vcpu.*limit|resource.*quota"
        r"|OperationNotAllowed.*quota|limit reached|insufficient.*quota)",
        re.IGNORECASE,
    ), "QUOTA"),

    # ENVIRONMENTAL — auth / missing credentials / network-unreachable / Azure soft-delete conflicts
    (re.compile(
        r"(AuthorizationFailed|InvalidClientTokenId|NoCredentialProviders"
        r"|Forbidden|UnauthorizedAccess|credentials.*not.*found"
        r"|no valid credential|authentication.*failed"
        r"|InvalidAuthenticationToken|MSI.*not.*available"
        r"|could not find.*account|account.*not.*found"
        r"|\\b403\\b|\\b401\\b"
        r"|VaultAlreadyExists|vault.*already.*exist|already.*exist.*vault"
        r"|key vault.*soft.delet|soft.delet.*key vault"
        r"|is in a deleted but recoverable state|vault.*deleted.*recoverable"
        r"|purge.*vault|vault.*purge|needs to be purged"
        r"|ConflictError.*vault|vault.*conflict"
        r"|resource.*already.*exists|already.*been.*taken)",
        re.IGNORECASE,
    ), "ENVIRONMENTAL"),

    # DEPENDENCY — cycle / ordering
    (re.compile(
        r"(cycle:|cyclic dependency|depends_on.*cycle|circular.*reference"
        r"|there is a cycle in the graph)",
        re.IGNORECASE,
    ), "DEPENDENCY"),

    # SEMANTIC — invalid references / values
    (re.compile(
        r"(reference to undeclared|undefined variable|invalid value for variable"
        r"|unknown variable|value of count cannot|error in function call"
        r"|is not permitted|unsupported value|is required|because it is not known"
        r"|cannot determine|no suitable|expected type|object has no attribute"
        r"|the argument.*is required|is not assignable"
        r"|cannot parse.*empty string|cannot parse an empty"
        r"|invalid resource id|expected a valid azure resource id"
        r"|parsing.*cannot parse|failed to parse.*id"
        r"|must be a valid|is not a valid|expected to be a"
        r"|var\.[a-z_]+.*empty|empty string.*resource id)",
        re.IGNORECASE,
    ), "SEMANTIC"),

    # STATIC — HCL syntax / provider schema
    (re.compile(
        r"(unsupported argument|unsupported block type|invalid block definition"
        r"|missing required argument|an argument named|unexpected symbols"
        r"|invalid expression|expected.*block|syntax error|invalid HCL"
        r"|attribute name required|this object does not have an attribute)",
        re.IGNORECASE,
    ), "STATIC"),

    # RUNTIME — transient / rate-limit / network
    (re.compile(
        r"(Throttling|RateLimitExceeded|TooManyRequests|rate.*limit"
        r"|503|504|connection reset|connection.*timed out"
        r"|context deadline exceeded|i/o timeout|dial tcp|net/http"
        r"|server.*unavailable|service.*unavailable|retry-after)",
        re.IGNORECASE,
    ), "RUNTIME"),
]

# ── Resource address extraction ────────────────────────────────────────────────
_RESOURCE_RE = re.compile(
    r'\b([a-z][a-z0-9]*_[a-z][a-z0-9_]*)\.([a-zA-Z][a-zA-Z0-9_-]*)(?:\[|\b)',
)
# Strip trailing digit/version suffixes for dedup normalisation
_VERSION_SUFFIX_RE = re.compile(r'[_-]?v?\d+(_\d+)*$', re.IGNORECASE)


def classify_terraform_error(
    stderr_text: str,
    stage: str,
    exit_code: int = 1,
) -> dict:
    """Classify a Terraform failure from its stderr output.

    Args:
        stderr_text: Raw stderr captured from the failed terraform subprocess.
        stage:       "init" | "plan" | "apply".
        exit_code:   Process exit code (137 = OOM/SIGKILL, 143 = SIGTERM).
    """
    excerpt = (stderr_text or "")[:2000]
    text = stderr_text or ""

    # OOM / signal kill with no useful text → ENVIRONMENTAL
    if exit_code in (137, 143) and not text.strip():
        return _make(
            cls="ENVIRONMENTAL",
            resource=None,
            message=f"Process killed with exit_code={exit_code} (OOM or SIGKILL)",
            stage=stage,
            excerpt=excerpt,
        )

    first_error = _first_error_line(text)
    resource = _extract_resource(text)

    for pattern, cls in _RULES:
        if pattern.search(text):
            return _make(cls, resource, first_error, stage, excerpt)

    # No match → RUNTIME (conservative: retryable by default)
    return _make("RUNTIME", resource, first_error or "unknown terraform error", stage, excerpt)


def failure_signature(error_class: str, error_detail: dict) -> str:
    """Stable 16-char hex hash for oscillation detection.

    Normalises resource addresses so azurerm_storage_account.main and
    azurerm_storage_account.main2 produce the same signature.
    """
    resource = normalize_resource(error_detail.get("resource") or "")
    message = (error_detail.get("message") or "")[:200]
    raw = f"{error_class}:{resource}:{message}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def normalize_resource(resource: str) -> str:
    """Strip trailing version/digit suffixes from a resource address."""
    if not resource:
        return ""
    parts = resource.split(".", 1)
    if len(parts) == 2:
        parts[1] = _VERSION_SUFFIX_RE.sub("", parts[1])
    return ".".join(parts)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make(cls: str, resource: str | None, message: str, stage: str, excerpt: str) -> dict:
    return {
        "class":       cls,
        "resource":    resource,
        "message":     (message or "")[:300],
        "stage":       stage,
        "raw_excerpt": excerpt,
        "file_location": _extract_file_location(excerpt),
    }


def _extract_file_location(text: str) -> str | None:
    """Extract 'file.tf line N' context from terraform error output."""
    match = re.search(r'on\s+([\w./\\-]+\.tf)\s+line\s+(\d+)', text, re.IGNORECASE)
    if match:
        return f"{match.group(1)} line {match.group(2)}"
    return None


def _first_error_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("│").strip()
        if stripped.lower().startswith("error"):
            return stripped[:300]
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:300]
    return ""


def _extract_resource(text: str) -> str | None:
    match = _RESOURCE_RE.search(text)
    if not match:
        return None
    rtype = match.group(1)
    rname = _VERSION_SUFFIX_RE.sub("", match.group(2))
    return f"{rtype}.{rname}"
