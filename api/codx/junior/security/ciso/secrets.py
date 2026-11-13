"""Secret detection and redaction.

This is the detector that answers the incident that motivated the CISO module:
*repositories and generated files ended up containing passwords and tokens*.

Two complementary techniques are used, because either one alone has a blind
spot:

* **Known shapes** — provider token formats (AWS, GitHub, GitLab, Slack,
  Stripe, OpenAI, Anthropic, Google, Azure, Hugging Face, npm, PyPI, JWT,
  private key blocks, ...) and credential assignments
  (``password = "..."``), which are precise but only cover what is known.
* **Entropy** — long random-looking base64/hex runs, which catch unknown
  formats but need a placeholder filter to stay quiet.

Every finding is redacted before it is returned: findings travel to logs, chat
messages and audit files, so a detector must never re-leak what it detected.

Made with ❤️ by codx-junior
"""

import math
import re
from collections import Counter
from typing import Iterable, List, Optional, Tuple

from codx.junior.security.ciso.models import (
    CisoPolicy,
    SecurityCategory,
    SecurityFinding,
    SecuritySeverity,
)

# -----------------------------------------------------------------------------
# Known token shapes
# -----------------------------------------------------------------------------
# Each entry is (rule_id, title, severity, compiled regex). The regexes match
# the credential ONLY (no surrounding quotes) so the excerpt is easy to redact.
_TOKEN_PATTERNS: List[Tuple[str, str, SecuritySeverity, "re.Pattern"]] = [
    # Cloud providers
    ("secret.aws-access-key", "AWS access key id", SecuritySeverity.CRITICAL,
     re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AIPA|ANPA|ANVA|APKA|AROA)[0-9A-Z]{16}\b")),
    ("secret.aws-secret-key", "AWS secret access key", SecuritySeverity.CRITICAL,
     re.compile(r"\b(?:aws)?_?(?:secret)?_?(?:access)?_?key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}\b", re.IGNORECASE)),
    ("secret.gcp-api-key", "Google API key", SecuritySeverity.CRITICAL,
     re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("secret.gcp-service-account", "Google service account private key", SecuritySeverity.CRITICAL,
     re.compile(r'"type"\s*:\s*"service_account"[^}]{0,400}"private_key"', re.DOTALL)),
    ("secret.azure-storage-key", "Azure storage account key", SecuritySeverity.CRITICAL,
     re.compile(r"\bAccountKey\s*=\s*[A-Za-z0-9+/=]{40,}", re.IGNORECASE)),
    ("secret.digitalocean-token", "DigitalOcean token", SecuritySeverity.HIGH,
     re.compile(r"\bdop_v1_[a-f0-9]{64}\b")),
    ("secret.vault-token", "HashiCorp Vault token", SecuritySeverity.HIGH,
     re.compile(r"\bhv[sbr]\.[A-Za-z0-9_\-]{20,}\b")),

    # Source control / CI
    ("secret.github-token", "GitHub token", SecuritySeverity.CRITICAL,
     re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("secret.gitlab-token", "GitLab personal access token", SecuritySeverity.CRITICAL,
     re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("secret.bitbucket-token", "Bitbucket app password", SecuritySeverity.HIGH,
     re.compile(r"\bATBB[A-Za-z0-9]{28,}\b")),

    # LLM providers
    ("secret.openai-key", "OpenAI API key", SecuritySeverity.CRITICAL,
     re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}\b")),
    ("secret.anthropic-key", "Anthropic API key", SecuritySeverity.CRITICAL,
     re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("secret.huggingface-token", "Hugging Face token", SecuritySeverity.HIGH,
     re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("secret.replicate-token", "Replicate API token", SecuritySeverity.HIGH,
     re.compile(r"\br8_[A-Za-z0-9]{30,}\b")),
    ("secret.cohere-key", "Cohere API key", SecuritySeverity.HIGH,
     re.compile(r"\bco(?:here)?_[A-Za-z0-9]{34,}\b")),

    # Payments / messaging / SaaS
    ("secret.stripe-key", "Stripe secret key", SecuritySeverity.CRITICAL,
     re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b")),
    ("secret.slack-token", "Slack token", SecuritySeverity.CRITICAL,
     re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}\b")),
    ("secret.slack-webhook", "Slack webhook URL", SecuritySeverity.HIGH,
     re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]+")),
    ("secret.discord-webhook", "Discord webhook URL", SecuritySeverity.HIGH,
     re.compile(r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+")),
    ("secret.telegram-bot", "Telegram bot token", SecuritySeverity.HIGH,
     re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("secret.sendgrid-key", "SendGrid API key", SecuritySeverity.HIGH,
     re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b")),
    ("secret.twilio-key", "Twilio API key", SecuritySeverity.HIGH,
     re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
    ("secret.mailgun-key", "Mailgun API key", SecuritySeverity.HIGH,
     re.compile(r"\bkey-[0-9a-zA-Z]{32}\b")),
    ("secret.shopify-token", "Shopify access token", SecuritySeverity.HIGH,
     re.compile(r"\bshpat_[a-fA-F0-9]{32}\b")),
    ("secret.npm-token", "npm access token", SecuritySeverity.HIGH,
     re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("secret.pypi-token", "PyPI upload token", SecuritySeverity.HIGH,
     re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{40,}\b")),

    # Crypto material
    ("secret.private-key-block", "Private key block", SecuritySeverity.CRITICAL,
     re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----")),
    ("secret.openssh-private-key", "OpenSSH private key", SecuritySeverity.CRITICAL,
     re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----")),
    ("secret.jwt", "JSON Web Token", SecuritySeverity.HIGH,
     re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("secret.age-key", "age secret key", SecuritySeverity.HIGH,
     re.compile(r"\bAGE-SECRET-KEY-1[0-9A-Z]{50,}\b")),

    # Databases / brokers
    ("secret.postgres-url", "PostgreSQL connection string with credentials", SecuritySeverity.CRITICAL,
     re.compile(r"\bpostgres(?:ql)?(?:\+\w+)?://[^\s:@/]+:[^\s@/]{3,}@[^\s/]+")),
    ("secret.mysql-url", "MySQL connection string with credentials", SecuritySeverity.CRITICAL,
     re.compile(r"\bmysql(?:\+\w+)?://[^\s:@/]+:[^\s@/]{3,}@[^\s/]+")),
    ("secret.mongo-url", "MongoDB connection string with credentials", SecuritySeverity.CRITICAL,
     re.compile(r"\bmongodb(?:\+srv)?://[^\s:@/]+:[^\s@/]{3,}@[^\s/]+")),
    ("secret.redis-url", "Redis URL with credentials", SecuritySeverity.HIGH,
     re.compile(r"\bredis(?:s)?://[^\s:@/]*:[^\s@/]{3,}@[^\s/]+")),
    ("secret.amqp-url", "AMQP URL with credentials", SecuritySeverity.HIGH,
     re.compile(r"\bamqps?://[^\s:@/]+:[^\s@/]{3,}@[^\s/]+")),
]

# -----------------------------------------------------------------------------
# Credential assignments
# -----------------------------------------------------------------------------
# `password = "hunter2"` style. Kept separate so the placeholder filter and the
# minimum length can be applied to the captured value.
_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<key>[A-Za-z0-9_.\-]*(?:passw(?:or)?d|passwd|pwd|secret|token|api[_\-]?key|"
    r"apikey|access[_\-]?key|private[_\-]?key|client[_\-]?secret|auth[_\-]?key|"
    r"credentials?|bearer|session[_\-]?id)[A-Za-z0-9_.\-]*)"
    r"\s*(?P<sep>[:=]|=>)\s*"
    r"(?P<quote>['\"`])(?P<value>[^'\"`\n]{3,})['\"`]",
    re.IGNORECASE,
)

# URL embedded basic auth: scheme://user:password@host
_URL_CREDENTIALS_PATTERN = re.compile(
    r"\b(?P<scheme>https?|ftp|smtp|ssh|ldap)://(?P<user>[^\s:@/{]{1,64}):(?P<password>[^\s@/{]{3,64})@[^\s/]+",
    re.IGNORECASE,
)

# High entropy candidates: long runs without separators.
_ENTROPY_CANDIDATE_PATTERN = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")

# Values that look like secrets but are obviously placeholders / references.
_PLACEHOLDER_PATTERNS: List[re.Pattern] = [
    re.compile(r"^(?:x{3,}|\*{3,}|\.{3,}|-{3,})$", re.IGNORECASE),
    re.compile(r"^(?:your|my|the)[_\- ]?(?:api|secret|access|private)?[_\- ]?(?:key|token|password|secret|here)", re.IGNORECASE),
    re.compile(r"^(?:changeme|change_me|replace_me|placeholder|dummy|example|sample|test|todo|fixme|none|null|nil|undefined|true|false|password|passwd|secret|token|apikey|api_key)$", re.IGNORECASE),
    re.compile(r"<[^>]+>"),                       # <YOUR_TOKEN>
    re.compile(r"\{\{?[^}]+\}?\}"),               # {{token}} / {token}
    re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"),  # ${VAR} / $VAR
    # NAME=value: prose, documentation examples and command lines
    # (``PYTHONPATH=/home/x/y``) are long and random enough to look like a
    # token. An actual assignment is caught by the assignment rules instead.
    re.compile(r"^[A-Za-z_][A-Za-z0-9_]*="),
    re.compile(r"%(?:s|d|\()"),                   # printf placeholder
    re.compile(r"^(?:os\.environ|process\.env|env\(|getenv)", re.IGNORECASE),
    re.compile(r"^(?:redacted|masked|removed|hidden|encrypted|not[_\-]?set|no[_\-]?value)$", re.IGNORECASE),
    re.compile(r"^(?:sk-llmfactory|sk-local|sk-dummy|sk-test)$", re.IGNORECASE),
    re.compile(r"^[a-z]+(?:_[a-z]+){2,}$"),       # snake_case identifier, not a value
    re.compile(r"^[A-Z][a-z]+(?:[A-Z][a-z]+){1,}$"),  # CamelCase identifier
]

# File names that are expected to hold secrets: flagging them is noise unless
# the policy asks for it. `is_secret_file` lets a caller decide.
_SECRET_FILE_PATTERNS: List[re.Pattern] = [
    re.compile(r"(^|/)\.env(\..+)?$"),
    re.compile(r"(^|/)\.envrc$"),
    re.compile(r"\.pem$"), re.compile(r"\.p12$"), re.compile(r"\.pfx$"),
    re.compile(r"\.jks$"), re.compile(r"\.keystore$"),
    re.compile(r"(^|/)id_(?:rsa|dsa|ecdsa|ed25519)$"),
    re.compile(r"(^|/)\.npmrc$"), re.compile(r"(^|/)\.pypirc$"),
    re.compile(r"(^|/)\.netrc$"), re.compile(r"(^|/)\.git-credentials$"),
    re.compile(r"(^|/)credentials(?:\.json)?$"),
    re.compile(r"(^|/)service[-_]?account.*\.json$"),
    re.compile(r"(^|/)\.htpasswd$"),
    re.compile(r"(^|/)secrets?\.(?:json|ya?ml|toml)$"),
]

# Redaction marker kept short so it survives excerpt truncation.
REDACTED_MARKER = "[REDACTED]"


def shannon_entropy(value: str) -> float:
    """Return the Shannon entropy (bits per character) of *value*.

    Args:
        value: Text to measure.

    Returns:
        float: Entropy in bits/char; ``0.0`` for an empty string.

    Example:
        >>> round(shannon_entropy("aaaaaaaa"), 2)
        0.0
        >>> shannon_entropy("aB3$xY9!qW2&zR5%") > 3
        True
    """
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def is_secret_file(path: Optional[str]) -> bool:
    """Return True when *path* is a file conventionally holding credentials.

    Args:
        path: File path (relative or absolute).

    Returns:
        bool: True for ``.env``, ``*.pem``, ``id_rsa``, ``.npmrc``, ...

    Example:
        >>> is_secret_file("config/.env.production")
        True
        >>> is_secret_file("src/app.py")
        False
    """
    if not path:
        return False
    normalised = path.replace("\\", "/")
    return any(pattern.search(normalised) for pattern in _SECRET_FILE_PATTERNS)


def looks_like_path(value: str) -> bool:
    """Return True when *value* is a file path rather than a credential.

    Filesystem paths are the largest single source of false positives for the
    entropy detector: ``codx/junior/security/ciso/gate.py`` or
    ``/home/user/projects/my-api`` are long, low-redundancy and full of
    separators. A credential is not usually shaped like a path, so a candidate
    that is one is rejected.

    Args:
        value: Candidate secret value.

    Returns:
        bool: True when the value looks like a path.
    """
    candidate = (value or "").strip()
    if not candidate:
        return False
    if candidate.startswith(("/", "~/", "./", "../")):
        return True
    if "://" in candidate:
        # A URL, not a filesystem path: the host has dots, but the value is a
        # credential the moment it embeds a user:password pair.
        return False
    if "/" not in candidate and "\\" not in candidate:
        return False
    normalised = candidate.replace("\\", "/")
    last_segment = normalised.rsplit("/", 1)[-1]
    # ``docs/readme.md`` or ``codx/junior/app.py``: a dotted final segment.
    return "." in last_segment


def is_secret_placeholder(value: str) -> bool:
    """Return True when *value* looks like a placeholder instead of a secret.

    Filters documentation examples, environment variable references and
    identifiers, which are the main source of false positives.

    Paths are also rejected: a long absolute project path
    (``/home/user/projects/my-project-api``) has enough entropy to look like a
    token, and every settings or config file written by the platform contains
    dozens of them.

    Args:
        value: Candidate secret value.

    Returns:
        bool: True when the value must not be reported.
    """
    candidate = (value or "").strip()
    if not candidate:
        return True
    # Already redacted by this module: ``mask()`` renders the value as
    # ``eyJh[REDACTED](24 chars)``. Reporting it again would leave a file that
    # was just cleaned still blocked, so the gate could never converge and the
    # only way out would be a permanent path allowlist.
    if REDACTED_MARKER in candidate:
        return True
    if len(candidate) < 8:
        return True
    if looks_like_path(candidate):
        # Filesystem path, not a credential.
        return True
    return any(pattern.search(candidate) for pattern in _PLACEHOLDER_PATTERNS)


def _redact(value: str) -> str:
    """Render a detected secret in a shape that cannot be reused.

    A short prefix is kept so a human can tell *which* credential leaked in a
    report, while the remainder is unrecoverable.

    Args:
        value: Raw secret.

    Returns:
        str: Redacted representation.
    """
    text = (value or "").strip()
    if not text:
        return REDACTED_MARKER
    if len(text) <= 8:
        return REDACTED_MARKER
    return f"{text[:4]}{REDACTED_MARKER}({len(text)} chars)"


def _line_of(text: str, index: int) -> int:
    """Return the 1-based line number of *index* inside *text*."""
    return text.count("\n", 0, index) + 1


def _excerpt(text: str, start: int, end: int, max_chars: int) -> str:
    """Build a redacted, bounded excerpt around a match."""
    radius = max(20, max_chars // 2)
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    excerpt = text[left:right].replace("\n", " ⏎ ")
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "…"
    return excerpt


def redact_text(text: str, policy: Optional[CisoPolicy] = None) -> Tuple[str, int]:
    """Neutralise every detected secret inside *text*.

    Args:
        text: Payload to sanitise.
        policy: Optional policy (size and excerpt limits).

    Returns:
        tuple: ``(redacted_text, replacements)``. When nothing matched the
        original string is returned unchanged and replacements is ``0``.

    Example:
        >>> redact_text('password = "s3cr3t-value-1234"')[0]
        'password = "s3cr…'
    """
    policy = policy or CisoPolicy()
    result = text or ""
    replacements = 0

    def _replace(match: re.Match) -> str:
        nonlocal replacements
        raw = match.group(0)
        if is_secret_placeholder(raw):
            return raw
        replacements += 1
        return _redact(raw)

    for _, _, _, pattern in _TOKEN_PATTERNS:
        result = pattern.sub(_replace, result)

    def _replace_assignment(match: re.Match) -> str:
        nonlocal replacements
        value = match.group("value")
        if is_secret_placeholder(value):
            return match.group(0)
        replacements += 1
        return match.group(0).replace(value, _redact(value))

    result = _ASSIGNMENT_PATTERN.sub(_replace_assignment, result)

    def _replace_url(match: re.Match) -> str:
        nonlocal replacements
        password = match.group("password")
        if is_secret_placeholder(password):
            return match.group(0)
        replacements += 1
        return match.group(0).replace(f":{password}@", f":{_redact(password)}@", 1)

    result = _URL_CREDENTIALS_PATTERN.sub(_replace_url, result)
    return result, replacements


def scan_text(
    text: str,
    path: Optional[str] = None,
    policy: Optional[CisoPolicy] = None,
) -> Tuple[List[SecurityFinding], str, bool]:
    """Detect secrets in *text*.

    Args:
        text: Payload to scan.
        path: File the payload belongs to (used for reporting and for the
            entropy relaxation inside documentation files).
        policy: Optional policy.

    Returns:
        tuple: ``(findings, redacted_text, truncated)``. The redacted text is
        safe to log and is what callers must store when they only need to keep
        a record of the payload.
    """
    policy = policy or CisoPolicy()
    payload = text or ""
    truncated = False
    if len(payload.encode("utf-8", errors="replace")) > policy.max_scan_bytes:
        payload = payload.encode("utf-8", errors="replace")[: policy.max_scan_bytes].decode(
            "utf-8", errors="replace"
        )
        truncated = True

    findings: List[SecurityFinding] = []

    def _add(
        rule_id: str,
        title: str,
        severity: SecuritySeverity,
        category: SecurityCategory,
        start: int,
        end: int,
        evidence: str,
        remediation: str,
    ) -> None:
        if policy.ignores_rule(rule_id):
            return
        findings.append(SecurityFinding(
            rule_id=rule_id,
            title=title,
            severity=severity,
            category=category,
            reason=f"Detected {title.lower()} in the payload",
            path=path,
            line=_line_of(payload, start),
            # The excerpt is copied into reports, logs and chat messages, so it
            # is redacted with the same rules as the evidence: a finding must
            # never reproduce the credential it reports.
            excerpt=_redact(_excerpt(payload, start, end, policy.max_excerpt_chars)),
            evidence=_redact(evidence),
            remediation=remediation,
            confidence=0.95,
        ))

    for rule_id, title, severity, pattern in _TOKEN_PATTERNS:
        for match in pattern.finditer(payload):
            # Same guard as `redact_text` and the rules below: a value the
            # redactor refuses to touch must not be reported either, otherwise
            # the gate reports a credential it cannot neutralise and the
            # cleanup never converges.
            if is_secret_placeholder(match.group(0)):
                continue
            _add(
                rule_id=rule_id,
                title=title,
                severity=severity,
                category=SecurityCategory.SECRET,
                start=match.start(),
                end=match.end(),
                evidence=match.group(0),
                remediation=(
                    "Remove the credential from the file, rotate it at the provider "
                    "and load it from the environment or the project secret store."
                ),
            )

    for match in _ASSIGNMENT_PATTERN.finditer(payload):
        value = match.group("value")
        if is_secret_placeholder(value):
            continue
        key = match.group("key")
        severity = SecuritySeverity.HIGH
        if (match.group("quote") == '"' or match.group("quote") == "'") and len(value) >= 16:
            severity = SecuritySeverity.CRITICAL
        _add(
            rule_id="secret.hardcoded-credential",
            title=f"Hardcoded credential in '{key}'",
            severity=severity,
            category=SecurityCategory.SECRET,
            start=match.start("value"),
            end=match.end("value"),
            evidence=value,
            remediation=(
                "Read the value from an environment variable or a secret manager "
                "and keep only a placeholder in the source file."
            ),
        )

    for match in _URL_CREDENTIALS_PATTERN.finditer(payload):
        password = match.group("password")
        if is_secret_placeholder(password):
            continue
        _add(
            rule_id="secret.url-embedded-credentials",
            title="Credentials embedded in a URL",
            severity=SecuritySeverity.CRITICAL,
            category=SecurityCategory.SECRET,
            start=match.start("password"),
            end=match.end("password"),
            evidence=password,
            remediation=(
                "Remove the user:password pair from the URL and pass the "
                "credential through a header or a secret reference."
            ),
        )

    if policy.entropy_threshold > 0:
        for match in _ENTROPY_CANDIDATE_PATTERN.finditer(payload):
            token = match.group(0)
            if len(token) < policy.min_secret_length:
                continue
            if is_secret_placeholder(token):
                continue
            # A long run of the same character class is usually an identifier,
            # a hash of a non-secret or an already redacted value.
            if REDACTED_MARKER in token:
                continue
            if shannon_entropy(token) < policy.entropy_threshold:
                continue
            _add(
                rule_id="secret.high-entropy-token",
                title="High entropy string (possible unknown token format)",
                severity=SecuritySeverity.MEDIUM,
                category=SecurityCategory.SECRET,
                start=match.start(),
                end=match.end(),
                evidence=token,
                remediation=(
                    "Confirm what this value is. If it is a credential, rotate it "
                    "and load it from the environment."
                ),
            )

    redacted, _ = redact_text(payload, policy)
    return findings, redacted, truncated
