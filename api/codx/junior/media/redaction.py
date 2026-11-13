"""Redaction gate for the Project's Channel.

Task cards, replays and wiki exports routinely carry .env values, API keys,
bearer tokens and client contact details. Nothing may be published on the
channel before it goes through this module:

* :func:`scan` reports one :class:`Finding` per secret / PII match.
* :func:`redact` returns a sanitised copy of the text plus the findings, using
  stable ``[redacted:<kind>]`` placeholders so the script stays readable and the
  line structure is preserved.
* :func:`assert_publishable` raises :class:`RedactionError` while any critical
  finding is present.
* :func:`policy_for` resolves the project publish policy, ``never`` by default
  (client work is never published unless somebody opts in explicitly).
* :func:`scan_episode` runs the same gate over an ``EpisodeIR``.

Pure python: standard library plus pydantic, no network access, no optional
dependencies, so it runs in the worker, in the API and in offline tests alike.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Pattern, Tuple

from pydantic import BaseModel, ConfigDict, Field

from codx.junior.media.model import EpisodeIR

__all__ = [
    "DEFAULT_PUBLISH_POLICY",
    "Finding",
    "PUBLISH_POLICIES",
    "RedactionError",
    "assert_publishable",
    "policy_for",
    "redact",
    "scan",
    "scan_episode",
]


Severity = Literal["low", "medium", "high", "critical"]

DEFAULT_PUBLISH_POLICY = "never"
PUBLISH_POLICIES = ("never", "review", "allow")

# Accepted spellings per policy, so a slightly different config still resolves.
_POLICY_ALIASES: Dict[str, str] = {
    "never": "never",
    "none": "never",
    "no": "never",
    "deny": "never",
    "private": "never",
    "review": "review",
    "ask": "review",
    "manual": "review",
    "draft": "review",
    "allow": "allow",
    "yes": "allow",
    "always": "allow",
    "auto": "allow",
    "public": "allow",
}

# Placeholder used when a match is redacted: "[redacted:api_key]"
PLACEHOLDER = "[redacted:{kind}]"


class Finding(BaseModel):
    """One secret or PII occurrence discovered by :func:`scan`."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="Pattern kind, e.g. 'api_key', 'email'")
    severity: Severity = Field(
        default="high", description="low | medium | high | critical"
    )
    line: int = Field(default=1, description="1-based line number of the match")
    match: str = Field(
        default="",
        description="Masked preview of the match; the secret itself is never copied",
    )
    suggestion: str = Field(
        default="", description="How to remove or replace the occurrence"
    )


class RedactionError(Exception):
    """Raised when critical findings block publication."""

    def __init__(self, findings: List[Finding]):
        self.findings: List[Finding] = list(findings)
        kinds = ", ".join(sorted({finding.kind for finding in self.findings}))
        super().__init__(
            f"{len(self.findings)} critical finding(s) block publication: {kinds}"
        )


# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #


def _not_a_placeholder(value: str) -> bool:
    """Keep redact() idempotent: 'KEY=[redacted:env_secret]' is already safe."""
    return not value.startswith(PLACEHOLDER.split("{")[0])


def _looks_like_hex_blob(value: str) -> bool:
    return any(char.isdigit() for char in value) and any(char.isalpha() for char in value)


def _looks_like_token_blob(value: str) -> bool:
    """Long hex/base64 strings only count when they plausibly are a token."""
    if len(value) < 32:
        return False
    if not any(char.isdigit() for char in value):
        return False
    mixed_case = any(char.isupper() for char in value) and any(
        char.islower() for char in value
    )
    return mixed_case or value.endswith("=") or "+" in value or "/" in value


_SECRET_KEY_NAME = re.compile(
    r"(?:API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|_DSN|ACCESS_KEY"
    r"|CREDENTIAL|SESSION)",
    re.IGNORECASE,
)
_ENV_ASSIGN = re.compile(
    r"(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]{2,})\s*=\s*(?P<value>[\"']?[^\s\"'#]+[\"']?)"
)


def _env_secret_check(found: re.Match) -> bool:
    """Only assignments of secret-looking keys count: ``WIDTH=1920`` is fine."""
    key = found.group("key") or ""
    value = found.group("value") or ""
    return bool(_SECRET_KEY_NAME.search(key)) and _not_a_placeholder(value)


@dataclass(frozen=True)
class _Rule:
    kind: str
    severity: Severity
    pattern: Pattern[str]
    suggestion: str
    group: Optional[str] = None
    accept: Optional[Callable[[str], bool]] = None
    check: Optional[Callable[[re.Match], bool]] = None


_RULES: Tuple[_Rule, ...] = (
    _Rule(
        kind="env_secret",
        severity="critical",
        pattern=_ENV_ASSIGN,
        group="value",
        check=_env_secret_check,
        suggestion="Never paste .env values; publish a placeholder instead.",
    ),
    _Rule(
        kind="private_key",
        severity="critical",
        pattern=re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
        suggestion="Remove the PEM block entirely.",
    ),
    _Rule(
        kind="api_key",
        severity="critical",
        pattern=re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
        suggestion="Remove the key; reference a secret, not a literal.",
    ),
    _Rule(
        kind="aws_access_key",
        severity="critical",
        pattern=re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        suggestion="Rotate the access key and publish only the region/bucket.",
    ),
    _Rule(
        kind="bearer_token",
        severity="critical",
        pattern=re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{8,}={0,2}"),
        suggestion="Strip the Authorization header from the published script.",
    ),
    _Rule(
        kind="url_credentials",
        severity="critical",
        pattern=re.compile(
            r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s/:@]+:[^\s/@]+@[^\s]+"
        ),
        suggestion="Drop user:pass from the URL and keep the host only.",
    ),
    _Rule(
        kind="secret_blob",
        severity="high",
        pattern=re.compile(r"(?<![A-Za-z0-9])[0-9A-Fa-f]{32,}(?![A-Za-z0-9])"),
        accept=_looks_like_hex_blob,
        suggestion="Replace the blob with a short, non-sensitive identifier.",
    ),
    _Rule(
        kind="secret_blob",
        severity="high",
        pattern=re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{32,}={0,2}(?![A-Za-z0-9+/=])"),
        accept=_looks_like_token_blob,
        suggestion="Replace the blob with a short, non-sensitive identifier.",
    ),
    _Rule(
        kind="email",
        severity="medium",
        pattern=re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        suggestion="Use a role address or a masked handle.",
    ),
    _Rule(
        kind="home_path",
        severity="medium",
        pattern=re.compile(
            r"(?:(?<![A-Za-z0-9])/(?:home|Users)/[A-Za-z0-9._\-]+"
            r"|[A-Za-z]:\\{1,2}Users\\{1,2}[A-Za-z0-9._\-]+)"
        ),
        suggestion="Use a project relative path instead of a home directory.",
    ),
    _Rule(
        kind="private_ip",
        severity="low",
        pattern=re.compile(
            r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            r"|192\.168\.\d{1,3}\.\d{1,3}"
            r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
        ),
        suggestion="Use the service name or a public host.",
    ),
)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    kind: str
    severity: Severity
    raw: str
    suggestion: str


def _mask(raw: str, keep: int = 4) -> str:
    """Never copy the secret itself into a finding."""
    value = raw.strip()
    if len(value) <= keep:
        return "***"
    return f"{value[:keep]}***"


def _line_of(text: str, start: int) -> int:
    return text.count("\n", 0, start) + 1


def _find(text: str) -> List[_Match]:
    """Collect non overlapping matches, most specific match wins."""
    if not text:
        return []
    candidates: List[Tuple[int, int, int, _Rule, str]] = []
    for order, rule in enumerate(_RULES):
        for found in rule.pattern.finditer(text):
            if rule.check is not None and not rule.check(found):
                continue
            if rule.group:
                raw = found.group(rule.group) or ""
                start, end = found.span(rule.group)
            else:
                raw = found.group(0) or ""
                start, end = found.span(0)
            if not raw or end <= start:
                continue
            if rule.accept is not None and not rule.accept(raw):
                continue
            candidates.append((start, end, order, rule, raw))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2]))
    matches: List[_Match] = []
    last_end = -1
    for start, end, _order, rule, raw in candidates:
        if start < last_end:
            continue
        matches.append(
            _Match(
                start=start,
                end=end,
                kind=rule.kind,
                severity=rule.severity,
                raw=raw,
                suggestion=rule.suggestion,
            )
        )
        last_end = end
    return matches


def _finding(text: str, match: _Match) -> Finding:
    return Finding(
        kind=match.kind,
        severity=match.severity,
        line=_line_of(text, match.start),
        match=_mask(match.raw),
        suggestion=match.suggestion,
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def scan(text: str) -> List[Finding]:
    """Report every secret/PII occurrence in ``text``."""
    return [_finding(text, match) for match in _find(text or "")]


def redact(text: str) -> Tuple[str, List[Finding]]:
    """Return a sanitised copy of ``text`` plus the findings that were masked."""
    matches = _find(text or "")
    if not matches:
        return text, []
    parts: List[str] = []
    cursor = 0
    for match in matches:
        parts.append(text[cursor:match.start])
        parts.append(PLACEHOLDER.format(kind=match.kind))
        cursor = match.end
    parts.append(text[cursor:])
    return "".join(parts), [_finding(text, match) for match in matches]


def assert_publishable(findings: List[Finding]) -> None:
    """Raise :class:`RedactionError` while a critical finding is present."""
    critical = [finding for finding in findings if finding.severity == "critical"]
    if critical:
        raise RedactionError(critical)


def policy_for(project_config: Optional[Dict[str, Any]]) -> str:
    """Resolve the publish policy: ``never`` (default), ``review`` or ``allow``."""
    value: Any = None
    if isinstance(project_config, dict):
        value = project_config.get("publish_policy")
        if value is None:
            media = project_config.get("media")
            if isinstance(media, dict):
                value = media.get("publish_policy")
    if not isinstance(value, str):
        return DEFAULT_PUBLISH_POLICY
    return _POLICY_ALIASES.get(value.strip().lower(), DEFAULT_PUBLISH_POLICY)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def scan_episode(ir: EpisodeIR) -> List[Finding]:
    """Scan every shot narration and every ``refs`` value of an episode ir."""
    findings: List[Finding] = []
    for shot in getattr(ir, "shots", None) or []:
        where = f" (shot {shot.shot_id})" if shot.shot_id else ""
        texts = [shot.narration or ""]
        for value in (shot.refs or {}).values():
            if value is None:
                continue
            texts.append(_stringify(value))
        for text in texts:
            for finding in scan(text):
                findings.append(
                    finding.model_copy(
                        update={"suggestion": f"{finding.suggestion}{where}"}
                    )
                )
    seen: set = set()
    unique: List[Finding] = []
    for finding in findings:
        key = (finding.kind, finding.match)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique
