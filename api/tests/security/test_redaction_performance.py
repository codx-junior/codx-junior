"""Regression tests for the CISO redaction hot path.

A task card is sanitised with ``redact_payload`` on every streamed chunk and
every tool event. Screenshot tool outputs embed multi-megabyte base64 data URLs;
scanning them as text made a single ``store_chat`` take ~27 s (measured on a
real chat), which blocked the whole chat turn.

The fix has two parts, both covered here:

* inline binary data URLs are copied verbatim (only the surrounding text is
  scanned),
* repeated strings (the immutable history) are cached by digest, so a save
  only pays for the new/changed strings.
"""

import time
from unittest.mock import patch

from codx.junior.security.ciso.payload import redact_payload
from codx.junior.security.ciso import secrets

GITHUB_TOKEN = "ghp_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8"


def test_data_url_binary_is_not_scanned_as_text():
    image = "A" * 2_000_000
    text = (
        f'token = "{GITHUB_TOKEN}"\n'
        f"![screenshot](data:image/png;base64,{image})"
    )

    started = time.monotonic()
    redacted, count = secrets.redact_text(text)
    elapsed = time.monotonic() - started

    assert count == 1
    assert GITHUB_TOKEN not in redacted
    # The binary body is preserved byte-for-byte.
    assert image in redacted
    assert "data:image/png;base64," in redacted
    # The old implementation spent ~10-27 s on this input; the data-URL path
    # scans only the short prefix/suffix and stays in the low milliseconds.
    assert elapsed < 2.0, elapsed


def test_repeated_string_is_served_from_the_cache():
    secrets.clear_redaction_cache()
    text = ("log line without credentials\n" * 4000) + GITHUB_TOKEN

    with patch.object(
        secrets, "_redact_text_scan", wraps=secrets._redact_text_scan
    ) as scan:
        first, first_count = secrets.redact_text(text)
        entries_after_first, _chars = secrets.redaction_cache_stats()
        second, second_count = secrets.redact_text(text)

    assert first == second
    assert first_count == second_count == 1
    assert entries_after_first >= 1
    # The expensive scan ran once; the second call was a digest lookup.
    assert scan.call_count == 1


def test_payload_skips_base64_named_keys():
    payload = {"image_base64": GITHUB_TOKEN, "note": "hello"}
    sanitised, count = redact_payload(payload)
    assert count == 0
    assert sanitised["image_base64"] == GITHUB_TOKEN
    assert sanitised["note"] == "hello"


def test_payload_still_redacts_regular_text():
    payload = {"note": f"token = {GITHUB_TOKEN}"}
    sanitised, count = redact_payload(payload)
    assert count == 1
    assert GITHUB_TOKEN not in sanitised["note"]


def test_data_url_payload_is_preserved_and_counted_correctly():
    image = "B" * 500_000
    payload = {
        "tool_events": [
            {
                "output": (
                    f"token={GITHUB_TOKEN}\n"
                    f"![screenshot](data:image/png;base64,{image})"
                )
            }
        ]
    }
    sanitised, count = redact_payload(payload)
    output = sanitised["tool_events"][0]["output"]
    assert count == 1
    assert GITHUB_TOKEN not in output
    assert image in output
