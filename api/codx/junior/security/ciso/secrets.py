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

import hashlib
import math
import os
import re
import threading
from collections import Counter, OrderedDict
from typing import Iterable, List, Optional, Set, Tuple

from codx.junior.security.ciso import context as ciso_context
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
    re.compile(r"(?i)(?:^|[-_])replace[-_]?me(?:[-_].*)?$"),
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
    # A mask is a run of one repeated character: ``••••••••``, ``********``,
    # ``xxxxxxxx``. The project settings and the log helpers are full of them.
    re.compile(r"^(.)\1{3,}$", re.DOTALL),
]

#: Deliberately synthetic values used in documentation, fixtures and tests.
#: Applied to the generic assignment and entropy rules only -- never to a
#: known provider shape, because a real ``sk-...`` token is not a placeholder
#: just because a docs example happens to contain a familiar run.
_SYNTHETIC_VALUE_PATTERNS: List[re.Pattern] = [
    re.compile(
        r"(?i)(?:hunter2|1234567890|abcdefghijklmnop|qwertyuiop|deadbeef|"
        r"s3cr3t[-_](?:value|password|token|secret))"
    ),
]


def is_synthetic_example(value: str) -> bool:
    """Return True for a value that is an obvious documentation/fixture sample.

    Args:
        value: Candidate value.

    Returns:
        bool: True for ``hunter2-hunter2-hunter2``, ``ghp_1234567890abcdef``.

    Example:
        >>> is_synthetic_example("hunter2-hunter2-hunter2")
        True
        >>> is_synthetic_example("aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0fI")
        False
    """
    candidate = (value or "").strip()
    if not candidate:
        return False
    return any(pattern.search(candidate) for pattern in _SYNTHETIC_VALUE_PATTERNS)

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

# -----------------------------------------------------------------------------
# Inline binary payloads (data URLs)
# -----------------------------------------------------------------------------
# Chat tool outputs embed screenshots as ``![screenshot](data:image/png;base64,…)``.
# A chat with a handful of screenshots is several megabytes of base64, and
# running the detector over it is pure cost: base64 is a binary body, not text,
# so it cannot carry a *usable* credential (the same reason the file gates skip
# binaries). Worse, the regexes backtrack over the long random runs, which made
# a single chat save take ~27 s (measured) and blocked the whole chat turn.
#
# Only the surrounding text is scanned; the binary segment is copied verbatim.
_DATA_URL_PATTERN = re.compile(
    r"data:(?:image|audio|video|application|font|model)"
    r"/[A-Za-z0-9.+-]+"
    r"(?:;[A-Za-z0-9.+-]+=[A-Za-z0-9.+-]+)*"
    r";base64,[A-Za-z0-9+/=\r\n]+",
    re.IGNORECASE,
)

# -----------------------------------------------------------------------------
# Redaction cache
# -----------------------------------------------------------------------------
# ``store_chat`` sanitises the WHOLE chat on every streamed chunk / tool event.
# Without a cache, the same immutable history (tool outputs, file reads, logs)
# is re-scanned on every save. The cache keeps the redacted result of a string
# keyed by its digest, so a save only pays for the new/changed strings.
#
# Only the default policy is cached: a custom policy can change the result, and
# mixing policies in one cache would leak a relaxed result into a strict save.
# Small strings are not cached (the lookup is more expensive than the scan).
_REDACT_CACHE_MIN_CHARS = 256
_REDACT_CACHE_MAX_CHARS = int(
    os.environ.get("CODX_JUNIOR_CISO_REDACT_CACHE_CHARS", str(64 * 1024 * 1024))
)
_REDACT_CACHE_LOCK = threading.Lock()
# digest -> (redacted_text, replacements, cost_in_chars)
_REDACT_CACHE: "OrderedDict[bytes, Tuple[str, int, int]]" = OrderedDict()
_REDACT_CACHE_CHARS = 0


def clear_redaction_cache() -> None:
    """Drop every cached redaction (tests / policy reloads)."""
    global _REDACT_CACHE_CHARS
    with _REDACT_CACHE_LOCK:
        _REDACT_CACHE.clear()
        _REDACT_CACHE_CHARS = 0


def redaction_cache_stats() -> Tuple[int, int]:
    """Return ``(entries, cached_chars)`` for diagnostics/tests."""
    with _REDACT_CACHE_LOCK:
        return len(_REDACT_CACHE), _REDACT_CACHE_CHARS


def _cache_lookup(key: bytes) -> Optional[Tuple[str, int]]:
    """Return a cached ``(redacted, replacements)`` and mark it recently used."""
    with _REDACT_CACHE_LOCK:
        entry = _REDACT_CACHE.get(key)
        if entry is None:
            return None
        _REDACT_CACHE.move_to_end(key)
        return entry[0], entry[1]


def _cache_store(key: bytes, cost: int, value: Tuple[str, int]) -> None:
    """Store one redaction under a bounded total-size budget."""
    global _REDACT_CACHE_CHARS
    if cost > _REDACT_CACHE_MAX_CHARS:
        return
    with _REDACT_CACHE_LOCK:
        previous = _REDACT_CACHE.pop(key, None)
        if previous is not None:
            _REDACT_CACHE_CHARS -= previous[2]
        _REDACT_CACHE[key] = (value[0], value[1], cost)
        _REDACT_CACHE_CHARS += cost
        while _REDACT_CACHE_CHARS > _REDACT_CACHE_MAX_CHARS and _REDACT_CACHE:
            _, evicted = _REDACT_CACHE.popitem(last=False)
            _REDACT_CACHE_CHARS -= evicted[2]
        if _REDACT_CACHE_CHARS < 0:  # defensive: never report a negative size
            _REDACT_CACHE_CHARS = 0


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
    segments = [segment for segment in normalised.split("/") if segment]
    if len(segments) < 2:
        return False
    # A ``.gitignore`` entry such as
    # ``codx-junior-installer/codx-junior/workspaces/default/config`` has no
    # dotted final segment, so requiring one let every long ignore path through
    # to the entropy detector. A base64 token that contains ``/`` usually also
    # contains ``+`` or ``=``; reject those as paths so the token is still
    # scored.
    if any("+" in segment or "=" in segment for segment in segments):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._~%:@-]+(?:/[A-Za-z0-9._~%:@-]+)+", normalised))


# -----------------------------------------------------------------------------
# Value shape and documentation context
# -----------------------------------------------------------------------------
# The assignment rule fires on the KEY: any `*password*` / `*token*` / `*secret*`
# name qualifies, so the value must also look like a credential. Without this, a
# constant (``ERROR_INVALID_PASSWORD = "Invalid password"``), a module path
# (``codx.junior.security.ciso.gate``) or a code-fence opener is reported as a
# hardcoded credential -- in every transcript that quotes it.
#
# KEPT NEGATIVE ON PURPOSE: these patterns reject shapes that cannot hold a
# secret; they do not demand a "random looking" value. A short human password
# (``password = "hunter2"``) is therefore still reported.
_NON_CREDENTIAL_VALUE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\s"),                       # a sentence, not a value
    re.compile(r"[()\[\]{}<>;,\"'`|\\]"),    # code punctuation / escapes
    re.compile(r"\\u[0-9a-fA-F]{2,4}"),      # JSON escape (``\u2...``)
    # Dotted lowercase names: modules, packages, domains. Case-sensitive on
    # purpose: a JWT also has dot-separated segments but carries uppercase.
    re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+){2,}$"),
    # Lower-case kebab-case names: ``x-access-token`` is the Git username, not
    # the token. Values with digits or symbols (``s3cr3t-pw``) are not excused.
    re.compile(r"^[a-z]+(?:-[a-z]+)+$"),
    # A config key or header name: ``http.extraheader=``.
    re.compile(r"^[a-z][a-z0-9_.-]*=$"),
    # A mask made of one repeated character.
    re.compile(r"^(.)\1{3,}$", re.DOTALL),
    # A camelCase variable/property (``graphRefreshToken``), not a literal.
    re.compile(r"^[a-z][a-z0-9]*(?:[A-Z][a-z]+){2,}$"),
    # A fixture label with a numeric suffix (``session-1``, ``user_2``).
    re.compile(r"^[a-z][a-z0-9]*[-_]\d{1,6}$"),
    # A plain endpoint carries no credential; ``user:pass@`` is the URL rule's job.
    re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://[^@\s]*$"),
    re.compile(
        r"^(?:true|false|null|none|nil|undefined|nan|\d+(?:\.\d+)*)$",
        re.IGNORECASE,
    ),
]


def looks_like_credential_value(value: str) -> bool:
    """Return True when *value* can actually be a credential.

    Args:
        value: Right-hand side of an assignment whose key names a credential.

    Returns:
        bool: False for sentences, code fragments, module paths and bare URLs.
    """
    candidate = (value or "").strip()
    if not candidate:
        return False
    if looks_like_path(candidate):
        return False
    return not any(
        pattern.search(candidate) for pattern in _NON_CREDENTIAL_VALUE_PATTERNS
    )


# Vendor documentation gets pasted into task bodies, notes and transcripts: the
# "Authorizing OAuth apps" page ships a sample access token, and every note that
# quoted it was reported as a leaked credential.
_PROSE_PATH_PATTERN = re.compile(r"(?i)\.(?:md|markdown|mdx|rst|adoc|txt|json)$")
# Wording that marks the surrounding text as a documentation sample.
_DOC_CONTEXT_PATTERN = re.compile(
    r"(?i)(?:\bexample\b|\be\.g\.|\bfor instance\b|\bsample\b|\bplaceholder\b|"
    r"\bdummy\b|\bredacted\b|\bdocumentation\b|\bauthoriz\w+|\byour [\w ]{0,24}app\b)"
)
# ``<access_token>gho_...`` ... ``</access_token>``: a value inside a markup sample.
_XML_SAMPLE_OPEN_PATTERN = re.compile(r"<[A-Za-z_][\w\-]{0,40}>[^<>\n]{0,200}$")
_XML_SAMPLE_CLOSE_PATTERN = re.compile(r"^[^<>\n]{0,200}</[A-Za-z_][\w\-]{0,40}>")
_DOC_CONTEXT_WINDOW = 200


#: Non-executable paths: a connection string written there is documentation or
#: a transcript, never an endpoint the code dials.
_NON_EXECUTABLE_EXTENSIONS = frozenset({
    ".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc", ".log",
})
#: Public search-only keys (Algolia / DocSearch) ship in the browser bundle by
#: design. They are shape-identical to a provider key, so only the context can
#: tell them apart: such a value is still reported, as ``low``.
_PUBLIC_SEARCH_KEY_CONTEXT = re.compile(
    r"(?i)(?:\balgolia\b|\bdocsearch\b|\bappid\b|\bindexname\b|\bsearch-only\b)"
)


def is_non_executable_path(path: Optional[str]) -> bool:
    """Return True when *path* is runtime data or documentation.

    Args:
        path: File path.

    Returns:
        bool: True for a ``.codx`` control-plane path or a prose extension.
    """
    if not path:
        return False
    posix = path.replace("\\", "/").lower()
    if ".codx" in posix.split("/"):
        return True
    return os.path.splitext(posix)[1] in _NON_EXECUTABLE_EXTENSIONS


def is_compose_example_url(value: str, path: Optional[str]) -> bool:
    """Return True for a connection string pointing at a compose service.

    A host without a dot (``db``, ``postgres``, ``milvus``) is a service name
    inside a stack definition, not an external endpoint. Such a value is
    reported as ``low``, and only on a non-executable path: the same URL in a
    ``.env`` or a Python module still blocks.

    Args:
        value: Matched connection string.
        path: File the value was found in.

    Returns:
        bool: True for an internal compose-style connection string.
    """
    if not is_non_executable_path(path) or "@" not in (value or ""):
        return False
    host = (value or "").split("@", 1)[1].split("/")[0].split("?")[0]
    hostname = host.split(":")[0].strip()
    return bool(hostname) and "." not in hostname


def is_public_search_key(payload: str, start: int, end: int) -> bool:
    """Return True when a 32-char hex value is a public search-only key.

    Args:
        payload: Whole text.
        start: Match start offset.
        end: Match end offset.

    Returns:
        bool: True when the value sits next to a search-client configuration.
    """
    if not payload or end - start != 32:
        return False
    if not re.fullmatch(r"[0-9a-fA-F]{32}", payload[start:end]):
        return False
    window = payload[max(0, start - 300): min(len(payload), end + 300)]
    return bool(_PUBLIC_SEARCH_KEY_CONTEXT.search(window))


def in_documentation_context(
    payload: str,
    start: int,
    end: int,
    path: Optional[str] = None,
) -> bool:
    """Return True when a match sits inside pasted documentation.

    Applies to the provider token rules only. The path must be prose (a task
    body, a note, a transcript) -- a token in the source tree is never excused --
    and the value must sit inside a markup sample or next to documentation
    wording, so a real credential merely sitting near the word "example" is
    still reported at full severity by the value-shape rules.

    Args:
        payload: Text being scanned.
        start: Match start offset.
        end: Match end offset.
        path: File being scanned.

    Returns:
        bool: True when the value is a documentation example.
    """
    if ciso_context.is_test_path(path):
        # Test fixtures and examples are documentation-shaped by definition.
        # A real credential pasted into a test is still reported, as low.
        return True
    if not _PROSE_PATH_PATTERN.search((path or "").replace("\\", "/")):
        # Source code can document a credential too: a docstring, a comment, a
        # doctest or a detector table. Those are not executed, so the value is
        # reported as ``low`` (see the caller) instead of blocking a commit.
        return ciso_context.is_documentation_position(payload, start, path)
    before = payload[max(0, start - _DOC_CONTEXT_WINDOW):start]
    after = payload[end:end + _DOC_CONTEXT_WINDOW]
    if _XML_SAMPLE_OPEN_PATTERN.search(before) and _XML_SAMPLE_CLOSE_PATTERN.match(after):
        return True
    # The marker is not always on the match line: a docs page prints the sample
    # token first and explains "For example, Accept: application/json" right
    # after it, so both sides of the match are inspected.
    window = payload[max(0, start - _DOC_CONTEXT_WINDOW):end + _DOC_CONTEXT_WINDOW]
    return bool(_DOC_CONTEXT_PATTERN.search(window))


# A secret HANDLE (``{{secret:NAME}}`` / ``{{env:NAME}}``) names a secret, it is
# not one. Without this the gate blocks every file that documents the resolver
# syntax -- including the design doc and the tool schemas. See
# ``codx/junior/security/ciso/vault.py``.
_HANDLE_ONLY_PATTERN = re.compile(
    r"^\{\{\s*(?:secret|env)\s*:\s*[A-Za-z_][A-Za-z0-9_.:@\-]{0,127}\s*\}\}$"
)

# A key that NAMES an environment variable carries a variable NAME as its value,
# not a credential. Without this, a constant such as the secrets-folder override
# is reported CRITICAL only because both halves contain the word "secret".
_ENV_NAME_KEY_PATTERN = re.compile(r"(?i)_(?:env|env_var|envvar|var|variable|name)$")
# ...and the value must itself look like an identifier, so a real token assigned
# to a ``*_NAME`` key is still reported.
_ENV_NAME_VALUE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{2,127}$")

# A key that ends in `_id` stores an identifier by contract: `chat_id`,
# `request_id`, `tool_call_id` (`call_…`), `cancellation_token_id`. The key
# contains the word "token" or "session", so the assignment rule reported every
# one of them as a hardcoded credential.
_IDENTIFIER_KEY_PATTERN = re.compile(r"(?i)(?:^|[_\-.])(?:ids?|uuid|hash|digest)$")
#: camelCase identifier keys: ``sessionId``, ``toolCallId``. The separator
#: pattern above does not see them, so they were reported as credentials.
_CAMEL_IDENTIFIER_KEY_PATTERN = re.compile(
    r"(?:[a-z0-9])(?:Id|ID|Uuid|UUID|Hash|HASH|Digest|DIGEST)$"
)

# Key/value pair of an identifier field, used to locate the exact span of the
# value so the entropy rule can skip it without re-parsing the payload.
_JSON_IDENTIFIER_VALUE_PATTERN = re.compile(
    r"(?P<key>[A-Za-z0-9_.\-]+)\s*[:=]\s*[\"'](?P<value>[^\"'\n]{8,})[\"']"
)


def names_an_identifier(key: str) -> bool:
    """Return True when *key* is a field that stores an identifier.

    Args:
        key: Left-hand side identifier of an assignment, or a JSON field name.

    Returns:
        bool: True for `chat_id`, `tool_call_id`, `request_id`, `doc_uuid`.
    """
    candidate = (key or "").strip()
    return bool(
        _IDENTIFIER_KEY_PATTERN.search(candidate)
        or _CAMEL_IDENTIFIER_KEY_PATTERN.search(candidate)
    )


def identifier_value_spans(payload: str) -> Set[Tuple[int, int]]:
    """Return the spans of the values stored in identifier fields.

    Args:
        payload: Text being scanned.

    Returns:
        set: `(start, end)` offsets of each identifier value in *payload*.
    """
    return {
        match.span("value")
        for match in _JSON_IDENTIFIER_VALUE_PATTERN.finditer(payload)
        if names_an_identifier(match.group("key"))
    }


def names_an_env_variable(key: str, value: str) -> bool:
    """Return True when the assignment stores an env variable NAME, not a secret.

    Args:
        key: Left-hand side identifier of the assignment.
        value: Assigned value.

    Returns:
        bool: True when the pair is a variable reference, not a credential.
    """
    return bool(_ENV_NAME_KEY_PATTERN.search(key or "") and
                _ENV_NAME_VALUE_PATTERN.match((value or "").strip()))


# Committed placeholder files: `.env.example`, `config.sample.yml`, `*.template`.
# The secret-file rule itself recommends committing them, so reporting them is a
# finding about following the rule.
_EXAMPLE_FILE_PATTERN = re.compile(
    r"(?i)(?:^|/)[^/]*(?:\.|_|-)(?:example|examples|sample|template|tpl|defaults?)"
    r"(?:\.[^/]*)?$"
)

# Long, random-looking values that *name* something instead of authenticating:
# UUIDs, digests, timestamps, versions and CONSTANT_CASE variable names. Inside
# the tracked task transcripts of this project they produced ~1 500 MEDIUM
# findings -- enough to hide a real token at position 1 501.
_IDENTIFIER_SHAPE_PATTERNS: List[re.Pattern] = [
    # UUID / run id.
    re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    # Prefixed or bare digest: `sha256:…`, `md5=…`, 40/64 hex characters.
    re.compile(r"^(?:sha\d{0,3}|md5|blake2[bs]?|xxh\d*)[:=\-_]?[0-9a-fA-F]{16,}$", re.IGNORECASE),
    re.compile(r"^[0-9a-fA-F]{20,}$"),
    # Timestamp / date / version.
    re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T_ ]?\d{2}[-:. ]\d{2}(?:[-:. ]\d{2})?)?(?:Z|[+-]\d{2}:?\d{2})?$"),
    re.compile(r"^v?\d+(?:\.\d+){1,}[0-9A-Za-z.\-+]*$"),
    # CONSTANT_CASE variable name (`CODX_JUNIOR_CONFIG_FOLDER`).
    re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}$"),
    # Slug built from words and numeric groups (`shift-2026-11-18-14-35`).
    re.compile(r"^[A-Za-z0-9_.\-]*[_\-]\d{4}(?:[_\-]\d{2}){2}[A-Za-z0-9_.\-]*$"),
    # Platform object id: `<prefix>_<random>`, such as `call_00_DXOo093mJKE4i8…`
    # or `msg_0A1B2C3D`. They are the bulk of `harness/audit.jsonl` (827 lines,
    # ~2 per line) and each one was a MEDIUM finding. The prefix list is closed
    # on purpose: an unknown `xxx_<random>` value may still be a credential, and
    # a provider token keeps its own known-shape rule.
    re.compile(
        r"^(?:call|msg|req|res|resp|run|chat|sess|thread|tool|evt|event|obj|node|"
        r"edge|turn|trace|span|job|task|batch|item|file|art|build|deploy|img|"
        r"doc|blk|usr|org|proj|cmpl|fn)_[A-Za-z0-9_\-]{6,}$",
        re.IGNORECASE,
    ),
]


def is_example_file(path: Optional[str]) -> bool:
    """Return True when *path* is a committed placeholder file.

    Args:
        path: File path.

    Returns:
        bool: True for `.env.example`, `config.sample.yml`, `values.template`.
    """
    return bool(_EXAMPLE_FILE_PATTERN.search((path or "").replace("\\", "/")))


def looks_like_identifier(value: str) -> bool:
    """Return True when *value* is a machine identifier, not a credential.

    UUIDs, digests, timestamps, versions and CONSTANT_CASE names are long and
    random enough to pass the entropy rule while authenticating nothing.
    Assignment rules are unaffected: a real token assigned to a password key is
    still reported, because the shape check is applied to the entropy rule only.

    Args:
        value: Candidate value.

    Returns:
        bool: True when the value must not be reported as an unknown token.
    """
    candidate = (value or "").strip()
    if not candidate:
        return True
    return any(pattern.match(candidate) for pattern in _IDENTIFIER_SHAPE_PATTERNS)


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
    if _HANDLE_ONLY_PATTERN.match(candidate):
        # A handle names a secret; a later tool call resolves it.
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

    Inline ``data:<mime>;base64,…`` bodies are copied verbatim and only the
    surrounding text is scanned (see :data:`_DATA_URL_PATTERN`). Results for
    the default policy are cached by digest, because ``store_chat`` re-sanitises
    the whole chat on every streamed chunk / tool event.

    Args:
        text: Payload to sanitise.
        policy: Optional policy (size and excerpt limits).

    Returns:
        tuple: ``(redacted_text, replacements)``. When nothing matched the
        original string is returned unchanged and replacements is ``0``.

    Example:
        >>> redact_text('password = "S3cr3tP@ssw0rd!"')[0]
        'password = "S3cr[REDACTED](15 chars)"'
    """
    payload = text or ""
    if not payload:
        return "", 0

    # Base64 data URLs are binary bodies: scan only the text around them.
    # Cache only the default policy (a custom policy can change the result),
    # and skip the cache for tiny strings where the digest is not worth it.
    has_data_url = bool(_DATA_URL_PATTERN.search(payload))
    cacheable = (
        not has_data_url
        and policy is None
        and len(payload) >= _REDACT_CACHE_MIN_CHARS
    )
    key: Optional[bytes] = None
    if cacheable:
        # SHA-256: the cache returns a redaction result, so a collision must
        # stay cryptographically out of reach (BLAKE2 128-bit would also do,
        # but the extra bytes are cheaper than debating the margin).
        key = hashlib.sha256(
            payload.encode("utf-8", errors="replace")
        ).digest()
        cached = _cache_lookup(key)
        if cached is not None:
            return cached

    redacted, replacements = _redact_text_segments(payload, policy)
    if cacheable and key is not None:
        _cache_store(key, len(payload) + len(redacted), (redacted, replacements))
    return redacted, replacements


def _redact_text_segments(text: str, policy: Optional[CisoPolicy]) -> Tuple[str, int]:
    """Redact text while copying inline binary (base64 data URL) bodies verbatim."""
    if not _DATA_URL_PATTERN.search(text):
        return _redact_text_scan(text, policy)

    pieces: List[str] = []
    replacements = 0
    last = 0
    for match in _DATA_URL_PATTERN.finditer(text):
        chunk = text[last:match.start()]
        if chunk:
            redacted, found = _redact_text_scan(chunk, policy)
            pieces.append(redacted)
            replacements += found
        pieces.append(match.group(0))
        last = match.end()
    tail = text[last:]
    if tail:
        redacted, found = _redact_text_scan(tail, policy)
        pieces.append(redacted)
        replacements += found
    return "".join(pieces), replacements


def _redact_text_scan(text: str, policy: Optional[CisoPolicy]) -> Tuple[str, int]:
    """Run every detector over *text* (the original, non-cached implementation).

    Redaction rules are policy-independent (the policy decides what to BLOCK,
    not what to hide), so no ``CisoPolicy`` is materialised here: constructing
    one per string cost ~1 s on a real chat payload.
    """
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

    # The entropy detector reports unknown token shapes, so the redactor must
    # be able to neutralise them too. Otherwise the gate says ``redact`` while
    # the payload still carries the value -- a report that promises a fix it
    # did not make, and a knowledge/chat sink that stores the raw token.
    entropy_threshold = policy.entropy_threshold if policy is not None else 4.2
    min_length = policy.min_secret_length if policy is not None else 20

    def _replace_entropy(match: re.Match) -> str:
        nonlocal replacements
        token = match.group(0)
        if len(token) < min_length:
            return token
        if is_secret_placeholder(token) or looks_like_identifier(token):
            return token
        if is_synthetic_example(token):
            return token
        if ciso_context.is_url_value_without_userinfo(result, match.start()):
            return token
        if shannon_entropy(token) < entropy_threshold:
            return token
        replacements += 1
        return _redact(token)

    result = _ENTROPY_CANDIDATE_PATTERN.sub(_replace_entropy, result)
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

    token_spans: List[Tuple[int, int]] = []
    # Every span already reported by a *precise* rule. The entropy heuristic is
    # the last resort; reporting the same value twice was pure noise.
    reported_spans: List[Tuple[int, int]] = []
    for rule_id, title, severity, pattern in _TOKEN_PATTERNS:
        for match in pattern.finditer(payload):
            # Same guard as `redact_text` and the rules below: a value the
            # redactor refuses to touch must not be reported either, otherwise
            # the gate reports a credential it cannot neutralise and the
            # cleanup never converges.
            if is_secret_placeholder(match.group(0)):
                continue
            documented = in_documentation_context(
                payload, match.start(), match.end(), path
            )
            token_spans.append(match.span())
            reported_spans.append(match.span())
            _add(
                rule_id=rule_id,
                title=title,
                # A token quoted from a provider's documentation page
                # (``<access_token>gho_...``) is a sample. It is still reported,
                # but as low: quoting the docs must not turn the gate red, and
                # silence would hide a real credential pasted the same way.
                severity=(
                SecuritySeverity.LOW
                if documented or is_compose_example_url(match.group(0), path)
                else severity
            ),
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
        if is_secret_placeholder(value) or is_synthetic_example(value):
            continue
        key = match.group("key")
        if ciso_context.is_token_metadata_key(key):
            # ``tokenize = 'unicode61'``: the key contains "token" by accident.
            continue
        if names_an_identifier(key):
            # `session_id`, `cancellation_token_id`: the key carries a secret
            # word but the value identifies a request.
            continue
        if names_an_env_variable(key, value):
            # The right-hand side names WHERE a value is looked up; the
            # credential itself lives in the environment.
            continue
        if ciso_context.is_metadata_key(key) and ciso_context.is_label_value(value):
            # ``TOKEN_ARG_PREFIX = "http.extraheader="`` /
            # ``DEFAULT_TOKEN_USERNAME = "x-access-token"`` /
            # ``secret_source = "settings"``: metadata, not a credential.
            continue
        if not looks_like_credential_value(value):
            # The key names a credential but the value is a sentence, a code
            # fragment or a module path: ``ERROR_INVALID_PASSWORD =
            # "Invalid password"`` is not a leak.
            continue
        key_words = [word for word in re.split(r"[_\-.\s]+", str(key).lower()) if word]
        if value.strip().lower() in key_words:
            # ``PERSONAL_TOKEN = "personal"``: a label taken from its own key.
            continue
        if any(
            start <= match.start("value") and match.end("value") <= end
            for start, end in token_spans
        ):
            # Already reported by a provider rule: one value, one finding.
            continue
        documented = in_documentation_context(
            payload, match.start(), match.end(), path
        )
        severity = SecuritySeverity.HIGH
        if (match.group("quote") == '"' or match.group("quote") == "'") and len(value) >= 16:
            severity = SecuritySeverity.CRITICAL
        if documented:
            # An example in a docstring, a comment or a task card: still
            # reported, but never at blocking severity.
            severity = SecuritySeverity.LOW
        if is_public_search_key(payload, match.start("value"), match.end("value")):
            # A DocSearch/Algolia search-only key is published in the browser
            # bundle by design: same shape as a provider key, so the value is
            # still reported, but never blocks. A real key has no such context.
            severity = SecuritySeverity.LOW
        reported_spans.append(match.span("value"))
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
        documented = in_documentation_context(
            payload, match.start(), match.end(), path
        )
        reported_spans.append(match.span("password"))
        _add(
            rule_id="secret.url-embedded-credentials",
            title="Credentials embedded in a URL",
            severity=(
                SecuritySeverity.LOW if documented else SecuritySeverity.CRITICAL
            ),
            category=SecurityCategory.SECRET,
            start=match.start("password"),
            end=match.end("password"),
            evidence=password,
            remediation=(
                "Remove the user:password pair from the URL and pass the "
                "credential through a header or a secret reference."
            ),
        )

    entropy_suppressed = (
        ciso_context.is_lock_file(path)
        or ciso_context.is_test_path(path)
        or ciso_context.is_asset_path(path)
        or ciso_context.is_generated_path(path)
    )
    if policy.entropy_threshold > 0 and not entropy_suppressed:
        identifier_spans = identifier_value_spans(payload)
        for match in _ENTROPY_CANDIDATE_PATTERN.finditer(payload):
            token = match.group(0)
            if len(token) < policy.min_secret_length:
                continue
            if match.span() in identifier_spans:
                # The value of a `*_id` field addresses an object; it does not
                # authenticate anything.
                continue
            if any(
                start <= match.start() and match.end() <= end
                for start, end in reported_spans
            ):
                # Already reported by a known provider, assignment or URL rule:
                # one span, one finding. Without this the same GitHub token was
                # reported twice (provider + entropy) in every docstring that
                # quoted it, and every unassigned random token twice more.
                continue
            if ciso_context.is_url_value_without_userinfo(payload, match.start()):
                # A thumbnail id or a package hash inside an HTTP URL is not a
                # credential; a URL with ``user:pass@`` is not excused.
                continue
            if is_synthetic_example(token):
                continue
            if is_secret_placeholder(token) or looks_like_identifier(token):
                continue
            # A long run of the same character class is usually an identifier,
            # a hash of a non-secret or an already redacted value.
            if REDACTED_MARKER in token:
                continue
            if shannon_entropy(token) < policy.entropy_threshold:
                continue
            documented = in_documentation_context(
                payload, match.start(), match.end(), path
            )
            _add(
                rule_id="secret.high-entropy-token",
                title="High entropy string (possible unknown token format)",
                severity=(
                    SecuritySeverity.LOW if documented else SecuritySeverity.MEDIUM
                ),
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
