"""Offline tests for the media redaction gate."""

import pytest
from pydantic import ValidationError

from codx.junior.media.model import EpisodeIR, Shot
from codx.junior.media.redaction import (
    Finding,
    RedactionError,
    assert_publishable,
    policy_for,
    redact,
    scan,
    scan_episode,
)

# One occurrence of every pattern the gate has to catch.
FIXTURE = """\
# channel notes for the client
EXTERNAL_API_KEY=plain-value-for-env-test
PRIVATE_DB_PASSWORD=correcthorsebatterystaple
SERVICE_DSN=postgres://localhost/app

openai key sk-proj-abc123XYZ456def789ghi012 stays out
anthropic sk-ant-api03-QWERTYuiop12345asdf stays out
aws AKIAIOSFODNN7EXAMPLE stays out
curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"

-----BEGIN RSA PRIVATE KEY-----
-----END RSA PRIVATE KEY-----

db url mysql://root:hunter2@db.internal:3306/app
contact alice@example.com for access
paths /home/alice/work and C:\\Users\\alice\\project
hosts 10.1.2.3 192.168.1.20 172.20.0.5
blob 3f9a1c2d4e5b6f708192a3b4c5d6e7f8
"""

EXPECTED_KINDS = {
    "env_secret",
    "api_key",
    "aws_access_key",
    "bearer_token",
    "private_key",
    "url_credentials",
    "email",
    "home_path",
    "private_ip",
    "secret_blob",
}


def test_scan_covers_every_pattern():
    kinds = {finding.kind for finding in scan(FIXTURE)}
    assert EXPECTED_KINDS <= kinds, f"missing kinds: {EXPECTED_KINDS - kinds}"


def test_scan_reports_severity_and_line():
    findings = scan(FIXTURE)
    critical = {finding.kind for finding in findings if finding.severity == "critical"}
    assert {"env_secret", "api_key", "aws_access_key", "bearer_token", "private_key",
            "url_credentials"} <= critical
    low = {finding.kind for finding in findings if finding.severity == "low"}
    assert low == {"private_ip"}
    env = next(finding for finding in findings if finding.kind == "env_secret")
    assert env.line == 2
    assert all(finding.line >= 1 for finding in findings)
    assert all(finding.suggestion for finding in findings)


def test_scan_never_copies_the_secret():
    findings = scan(FIXTURE)
    assert all("plain-value-for-env-test" not in finding.match for finding in findings)
    assert all("hunter2" not in finding.match for finding in findings)


def test_scan_empty_text():
    assert scan("") == []


def test_scan_ignores_harmless_assignments():
    text = "width=1920\nfps=30\nname=channel-cover\nformat=16:9\n"
    assert scan(text) == []
    redacted, findings = redact(text)
    assert redacted == text
    assert findings == []


def test_redact_removes_secret_and_keeps_the_rest():
    text = "intro line\nAPI_KEY=abc123secret\noutro line stays\n"
    redacted, findings = redact(text)

    assert "abc123secret" not in redacted
    assert redacted == "intro line\nAPI_KEY=[redacted:env_secret]\noutro line stays\n"
    assert [finding.kind for finding in findings] == ["env_secret"]
    assert findings[0].line == 2


def test_redact_fixture_keeps_line_structure():
    redacted, findings = redact(FIXTURE)

    assert findings
    for secret in (
        "plain-value-for-env-test",
        "correcthorsebatterystaple",
        "sk-proj-abc123XYZ456def789ghi012",
        "sk-ant-api03-QWERTYuiop12345asdf",
        "AKIAIOSFODNN7EXAMPLE",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "hunter2",
        "alice@example.com",
        "/home/alice",
        "C:\\Users\\alice",
        "10.1.2.3",
        "3f9a1c2d4e5b6f708192a3b4c5d6e7f8",
        "-----BEGIN RSA PRIVATE KEY-----",
    ):
        assert secret not in redacted, f"{secret} survived redaction"

    # every placeholder is stable and the readable text is untouched
    assert "[redacted:env_secret]" in redacted
    assert "[redacted:email]" in redacted
    assert "[redacted:api_key]" in redacted
    assert "# channel notes for the client" in redacted
    assert "for access" in redacted
    assert "and" in redacted
    assert redacted.count("\n") == FIXTURE.count("\n")


def test_redact_without_secrets_is_identity():
    text = "nothing sensitive here\njust a script\n"
    redacted, findings = redact(text)
    assert redacted == text
    assert findings == []


def test_assert_publishable_raises_for_critical():
    findings = [
        Finding(
            kind="api_key",
            severity="critical",
            line=3,
            match="sk-a***",
            suggestion="remove it",
        )
    ]
    with pytest.raises(RedactionError) as excinfo:
        assert_publishable(findings)
    error = excinfo.value
    assert isinstance(error, Exception)
    assert len(error.findings) == 1
    assert error.findings[0].kind == "api_key"


def test_assert_publishable_passes_low_findings():
    assert_publishable([])
    assert_publishable([
        Finding(kind="private_ip", severity="low", line=1, match="10.0.***",
                suggestion="use a hostname"),
        Finding(kind="email", severity="medium", line=4, match="a***",
                suggestion="use a role address"),
    ])


def test_redacted_text_is_idempotent_and_publishable():
    redacted, findings = redact(FIXTURE)
    # the raw findings block publication ...
    with pytest.raises(RedactionError):
        assert_publishable(findings)
    # ... the sanitised script does not, and a second pass changes nothing.
    assert_publishable(scan(redacted))
    assert redact(redacted) == (redacted, [])


def test_policy_defaults_to_never():
    assert policy_for({}) == "never"
    assert policy_for(None) == "never"
    assert policy_for({"publish_policy": "garbage"}) == "never"
    assert policy_for({"publish_policy": None}) == "never"


def test_policy_accepts_known_values():
    assert policy_for({"publish_policy": "review"}) == "review"
    assert policy_for({"publish_policy": "ALLOW"}) == "allow"
    assert policy_for({"publish_policy": " never "}) == "never"
    assert policy_for({"media": {"publish_policy": "review"}}) == "review"


def test_finding_rejects_unknown_severity():
    with pytest.raises(ValidationError):
        Finding(kind="api_key", severity="catastrophic")


def test_scan_episode_finds_secret_in_shot_narration():
    ir = EpisodeIR(
        episode_id="e1",
        title="Pilot",
        shots=[
            Shot(shot_id="s1", kind="title", narration="Welcome to the channel"),
            Shot(
                shot_id="s2",
                kind="slide",
                narration="Deploy with sk-proj-abc123XYZ456def789ghi012 now",
                refs={
                    "wiki_path": "docs/notes.md",
                    "contact": "reach alice@example.com",
                },
            ),
        ],
    )
    findings = scan_episode(ir)
    kinds = {finding.kind for finding in findings}
    assert "api_key" in kinds
    assert "email" in kinds
    assert all(finding.line >= 1 for finding in findings)
    assert all("s2" in finding.suggestion for finding in findings)


def test_scan_episode_clean_episode():
    ir = EpisodeIR(shots=[Shot(shot_id="s0", kind="slide", narration="hello")])
    assert scan_episode(ir) == []
    assert scan_episode(EpisodeIR()) == []
