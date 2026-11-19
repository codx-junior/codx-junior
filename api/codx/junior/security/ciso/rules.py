"""Catalogue of the CISO detector rules.

The detectors own the patterns; this module owns the *presentation* of those
patterns. The CISO view needs to list every rule, explain what it looks for and
label which of them are silenced by the current policy, and it must do that
without duplicating the rule tables (a copy is the classic way a security screen
starts lying: it lists a rule the scanner no longer runs, or misses a new one).

The catalogue is built by reading the detector tables directly. Every entry is a
:class:`RuleDescriptor` with the same identity used by findings
(``rule_id``), so the UI can join a finding to its description and the policy
can silence a rule through ``CisoPolicy.ignore_rules``.

Two scopes are reported:

``file``     the rule runs over file content, diffs, prompts and payloads;
``command``  the rule only runs over a shell command line (``scan_command``).

Made with ❤️ by codx-junior
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from codx.junior.security.ciso import malicious, secrets
from codx.junior.security.ciso.models import (
    CisoPolicy,
    SecurityCategory,
    SecuritySeverity,
)
from codx.junior.security.ciso.policy import severity_rank

#: Rule scopes understood by the catalogue.
SCOPE_FILE = "file"
SCOPE_COMMAND = "command"

#: Human labels for the detection families (used by the API and the UI).
SOURCE_LABELS: Dict[str, str] = {
    "secrets": "Secrets",
    "malicious": "Malicious code",
    "injection": "Prompt injection",
    "configuration": "Configuration",
}


@dataclass(frozen=True)
class RuleDescriptor:
    """One detector rule as shown in the CISO view.

    Attributes:
        rule_id: Stable identifier also written to every finding.
        title: Short human title.
        severity: Severity the detector assigns.
        category: Risk family of the finding.
        source: Detector module that owns the rule (``secrets``, ``malicious``,
            ``injection`` or ``configuration``).
        scope: ``file`` (content scanners) or ``command`` (shell scanner).
        remediation: Advice shown next to a finding.
    """

    rule_id: str
    title: str
    severity: SecuritySeverity
    category: SecurityCategory
    source: str
    scope: str = SCOPE_FILE
    remediation: str = ""

    def to_dict(self, policy: Optional[CisoPolicy] = None) -> Dict[str, object]:
        """Return the JSON representation, including the policy state.

        Args:
            policy: Effective policy used to compute ``enabled`` and
                ``ignored`` (both True/False, never guessed from the UI).

        Returns:
            dict: JSON-safe rule description.
        """
        ignored = bool(policy and policy.ignores_rule(self.rule_id))
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity.value,
            "category": self.category.value,
            "source": self.source,
            "source_label": SOURCE_LABELS.get(self.source, self.source),
            "scope": self.scope,
            "remediation": self.remediation,
            "enabled": not ignored,
            "ignored": ignored,
        }


# Rules that live outside the detector tables because they are the policy's own
# checks (the scanner appends them explicitly). Keep this list in sync with
# ``scanner.scan_project``: it is the only hand-maintained part of the catalogue.
_EXTRA_RULES: List[RuleDescriptor] = [
    RuleDescriptor(
        rule_id="configuration.secret-file",
        title="Credential-bearing file in the project",
        severity=SecuritySeverity.MEDIUM,
        category=SecurityCategory.CONFIGURATION,
        source="configuration",
        remediation=(
            "Keep the file out of the repository and commit a `.env.example` "
            "with placeholders instead."
        ),
    ),
]

# Stored prompt-injection rules. ``injection.scan_text`` emits these ids; the
# catalogue names them so a CISO can silence a family (for example a security
# profile that documents attack strings on purpose).
_INJECTION_RULES: List[RuleDescriptor] = [
    RuleDescriptor(
        rule_id="injection.instruction-override",
        title="Instruction override",
        severity=SecuritySeverity.HIGH,
        category=SecurityCategory.INJECTION,
        source="injection",
        remediation=(
            "Remove the instruction from the artifact, wrap untrusted content "
            "as inert data and review how it reached the repository."
        ),
    ),
    RuleDescriptor(
        rule_id="injection.coercion",
        title="Coercion or secrecy instruction",
        severity=SecuritySeverity.HIGH,
        category=SecurityCategory.INJECTION,
        source="injection",
        remediation=(
            "Remove the instruction and review the origin of the artifact: it "
            "tries to make the agent act without telling the human."
        ),
    ),
    RuleDescriptor(
        rule_id="injection.hidden-html-comment",
        title="Instruction hidden in an HTML comment",
        severity=SecuritySeverity.HIGH,
        category=SecurityCategory.INJECTION,
        source="injection",
        remediation="Delete the hidden instruction; comments are not a channel for instructions.",
    ),
    RuleDescriptor(
        rule_id="injection.invisible-characters",
        title="Invisible text encoding",
        severity=SecuritySeverity.MEDIUM,
        category=SecurityCategory.INJECTION,
        source="injection",
        remediation="Strip zero-width/bidirectional control characters before the content is used.",
    ),
    RuleDescriptor(
        rule_id="injection.control-characters",
        title="Unicode control characters",
        severity=SecuritySeverity.LOW,
        category=SecurityCategory.INJECTION,
        source="injection",
        remediation="Review the file encoding; control characters are a common hiding mechanism.",
    ),
]

# The scanner builds these three findings directly (assignment, URL credentials
# and high entropy). They share the secrets detector but not its token table.
_SECRET_HEURISTIC_RULES: List[RuleDescriptor] = [
    RuleDescriptor(
        rule_id="secret.hardcoded-credential",
        title="Hardcoded credential assignment",
        severity=SecuritySeverity.HIGH,
        category=SecurityCategory.SECRET,
        source="secrets",
        remediation=(
            "Move the value to the secrets vault (or an environment variable) "
            "and commit a placeholder example instead."
        ),
    ),
    RuleDescriptor(
        rule_id="secret.url-embedded-credentials",
        title="Credentials embedded in a URL",
        severity=SecuritySeverity.HIGH,
        category=SecurityCategory.SECRET,
        source="secrets",
        remediation="Remove the userinfo from the URL and inject the credential at runtime.",
    ),
    RuleDescriptor(
        rule_id="secret.high-entropy-token",
        title="High-entropy token",
        severity=SecuritySeverity.MEDIUM,
        category=SecurityCategory.SECRET,
        source="secrets",
        remediation=(
            "Confirm whether this is a credential; if it is, move it to the "
            "vault, if it is test data mark the path as allow-listed."
        ),
    ),
]


def _catalog() -> List[RuleDescriptor]:
    """Build the catalogue from the detector tables (never hand-copied)."""
    rules: List[RuleDescriptor] = []

    for rule_id, title, severity, _pattern in secrets._TOKEN_PATTERNS:
        rules.append(
            RuleDescriptor(
                rule_id=rule_id,
                title=title,
                severity=severity,
                category=SecurityCategory.SECRET,
                source="secrets",
                remediation=(
                    "Revoke and rotate the credential, remove it from the "
                    "artifact and load it from the vault at runtime."
                ),
            )
        )
    rules.extend(_SECRET_HEURISTIC_RULES)

    for rule_id, title, severity, category, _pattern, remediation in malicious._RULES:
        rules.append(
            RuleDescriptor(
                rule_id=rule_id,
                title=title,
                severity=severity,
                category=category,
                source="malicious",
                remediation=remediation,
            )
        )
    for rule_id, title, severity, category, _pattern, remediation in malicious._COMMAND_EXTRA_RULES:
        rules.append(
            RuleDescriptor(
                rule_id=rule_id,
                title=title,
                severity=severity,
                category=category,
                source="malicious",
                scope=SCOPE_COMMAND,
                remediation=remediation,
            )
        )

    rules.extend(_INJECTION_RULES)
    rules.extend(_EXTRA_RULES)
    return rules


#: Deterministic catalogue order: highest severity first, then by id. A stable
#: order matters because the UI renders this list and pagination would otherwise
#: reshuffle it between requests.
#:
#: ``SecuritySeverity`` values are strings (``"critical"``…), so they cannot be
#: sorted by value; the policy engine's rank is the ranking authority.
_CATALOG: List[RuleDescriptor] = sorted(
    _catalog(),
    key=lambda rule: (-severity_rank(rule.severity), rule.rule_id),
)

#: Rule id -> descriptor, used to validate a toggle request.
RULES_BY_ID: Dict[str, RuleDescriptor] = {
    rule.rule_id: rule for rule in _CATALOG
}


def iter_rules() -> Iterable[RuleDescriptor]:
    """Iterate over every known rule (catalogue order)."""
    return iter(_CATALOG)


def get_rule(rule_id: str) -> Optional[RuleDescriptor]:
    """Return the descriptor of *rule_id*, or ``None`` when it is unknown."""
    return RULES_BY_ID.get(str(rule_id or ""))


def catalog(policy: Optional[CisoPolicy] = None) -> List[Dict[str, object]]:
    """Return every rule as a JSON-safe dictionary.

    Args:
        policy: Effective policy used to mark rules silenced by
            ``ignore_rules``.

    Returns:
        list: Rule dictionaries in catalogue order.
    """
    return [rule.to_dict(policy) for rule in _CATALOG]


def counts(policy: Optional[CisoPolicy] = None) -> Dict[str, int]:
    """Return aggregate rule counts for the overview.

    Args:
        policy: Effective policy.

    Returns:
        dict: ``total``, ``enabled``, ``ignored`` and the totals per scope.
    """
    entries = catalog(policy)
    enabled = [entry for entry in entries if entry["enabled"]]
    return {
        "total": len(entries),
        "enabled": len(enabled),
        "ignored": len(entries) - len(enabled),
        "file": sum(1 for entry in entries if entry["scope"] == SCOPE_FILE),
        "command": sum(1 for entry in entries if entry["scope"] == SCOPE_COMMAND),
    }


__all__ = [
    "RULES_BY_ID",
    "RuleDescriptor",
    "SCOPE_COMMAND",
    "SCOPE_FILE",
    "SOURCE_LABELS",
    "catalog",
    "counts",
    "get_rule",
    "iter_rules",
]
