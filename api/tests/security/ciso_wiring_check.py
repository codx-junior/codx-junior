"""Wiring self-check for the CISO secret gates.

Two questions are answered here, and they are different:

1. **Does the sanitiser work?** (behaviour) — a nested task-card payload and a
   rendered tool snapshot must come out without the credential.
2. **Is it actually called?** (wiring) — a correct detector that no process
   calls protects nothing, so the call sites are asserted on the source.

Plus the pre-commit gate is exercised against a real throwaway git repository.

Run it in the API container::

    PYTHONPATH=/home/codx-junior/codx-junior/api \\
        api/.venv/bin/python api/tests/security/ciso_wiring_check.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

FAILURES = []


def check(name, condition, detail=""):
    """Record and print one assertion result."""
    if condition:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}" + (f" → {detail}" if detail else ""))
        FAILURES.append(name)


def read(relative_path):
    """Read a repository file relative to the project root."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    with open(os.path.join(root, relative_path), "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1. Behaviour — payload sanitiser
# ---------------------------------------------------------------------------
def check_payload():
    """A nested task-card payload must lose its credential."""
    from codx.junior.security.ciso.payload import redact_payload

    github_pat = "github_pat_" + ("A" * 60)
    payload = {
        "id": "chat-1",
        "name": "package codx-junior",
        "messages": [
            {
                "role": "assistant",
                "content": f"remote: https://codx:{github_pat}@github.com/o/r",
                "tool_events": [
                    {
                        "tool": "docker_inspect",
                        "output": 'Env: ["OPENAI_API_KEY=sk-proj-' + "b" * 40 + '"]',
                    }
                ],
                "attachments": [{"file_name": "shot.png", "base64_data": "aGVsbG8="}],
            }
        ],
        "cost": 1.25,
        "done": True,
    }

    sanitised, count = redact_payload(payload)

    check("payload: the credential is gone", github_pat not in str(sanitised))
    check("payload: the API key is gone", "sk-proj-" not in str(sanitised))
    check("payload: redactions are counted", count >= 2, f"count={count}")
    check(
        "payload: non-text fields survive",
        sanitised["cost"] == 1.25 and sanitised["done"] is True,
    )
    check(
        "payload: binary bodies are left alone",
        sanitised["messages"][0]["attachments"][0]["base64_data"] == "aGVsbG8=",
    )
    check(
        "payload: the input is not mutated",
        github_pat in payload["messages"][0]["content"],
    )


def check_output_gate():
    """The tool-output gate must redact a rendered snapshot."""
    from codx.junior.security.ciso import gate

    github_pat = "github_pat_" + ("C" * 60)
    snapshot = f"- **Remote**: `https://codx:{github_pat}@github.com/o/r`"
    safe = gate.redact_output(snapshot)

    check("output gate: the remote credential is redacted", github_pat not in safe)
    check("output gate: clean text is returned unchanged", gate.redact_output("- **Remote**: none configured") == "- **Remote**: none configured")


# ---------------------------------------------------------------------------
# 2. Wiring — the call sites exist
# ---------------------------------------------------------------------------
def check_wiring():
    """Every P0 call site must call the central gate."""
    store = read("api/codx/junior/chat_manager.py")
    check(
        "wiring: task cards are sanitised on write",
        "redact_payload(chat.model_dump())" in store,
    )

    prompt = read("api/codx/junior/chat/chat_prompt_builder.py")
    check(
        "wiring: the prompt is redacted before it leaves",
        "self._redact_for_model(messages, chat=chat)" in prompt,
    )
    check(
        "wiring: the redaction rewrites the text part only",
        "self._set_text_content(message, redacted)" in prompt,
    )

    tools = read("api/codx/junior/tools/project_tools.py")
    check(
        "wiring: a file write is gated before it lands",
        "ciso_gate.guard_file_write(" in tools,
    )
    check(
        "wiring: git stages AFTER the write",
        tools.index("_add_changes_to_git(settings=settings, file_path=rel_path)")
        > tools.index('file.write(content)'),
    )

    repository = read("api/codx/junior/tools/repository_tools.py")
    check(
        "wiring: the repository snapshot goes through the gate",
        "ciso_gate.redact_output(llm_response, settings=settings)" in repository,
    )
    check(
        "wiring: the local remote-URL regex is gone",
        're.sub(r"//[^/@]+@", "//***@", remote_url)' not in repository,
    )


# ---------------------------------------------------------------------------
# 3. Pre-commit gate — real git repository
# ---------------------------------------------------------------------------
def check_precommit():
    """The hook must block a staged secret and allow a clean commit."""
    from codx.junior.security.ciso import precommit

    github_pat = "github_pat_" + ("D" * 60)
    workdir = tempfile.mkdtemp(prefix="ciso-precommit-")
    previous = os.getcwd()
    try:
        os.chdir(workdir)
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "ciso@example.com"],
            ["git", "config", "user.name", "CISO"],
        ):
            subprocess.run(command, check=True, capture_output=True)

        with open("config.py", "w", encoding="utf-8") as f:
            f.write(f'GITHUB_TOKEN = "{github_pat}"\n')
        subprocess.run(["git", "add", "config.py"], check=True, capture_output=True)

        blocked = precommit.main([])
        check(
            "precommit: a staged token blocks the commit",
            blocked == precommit.EXIT_BLOCKED,
            f"exit={blocked}",
        )

        with open("config.py", "w", encoding="utf-8") as f:
            f.write('import os\nGITHUB_TOKEN = os.environ["GITHUB_TOKEN"]\n')
        subprocess.run(["git", "add", "config.py"], check=True, capture_output=True)

        allowed = precommit.main([])
        check(
            "precommit: an env-based credential passes",
            allowed == precommit.EXIT_OK,
            f"exit={allowed}",
        )
    except (OSError, subprocess.CalledProcessError) as ex:
        check("precommit: the gate ran", False, str(ex))
    finally:
        os.chdir(previous)
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    """Run every check and return the process exit code."""
    print("CISO wiring self-check\n" + "=" * 40)
    check_payload()
    check_output_gate()
    check_wiring()
    check_precommit()

    print("=" * 40)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
