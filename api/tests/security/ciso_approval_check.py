#!/usr/bin/env python3
"""CISO per-commit approval gate.

The pre-commit gate blocks a credential. That is correct for a real leak, but a
finding is sometimes content that must be committed: a quotation inside a task
card, a fixture, a documented example. The answer must NOT be a path allowlist —
an allowlist grants a file permission forever, so a card that later gains a real
token would be committed silently. The answer is an approval bound to the STAGED
CONTENT and spent by one commit.

This gate proves that behaviour against a real throwaway repository and the real
hook, so a regression cannot hide behind a mock:

    current staged content  -> blocked
    --approve               -> the same content commits exactly once
    commit again            -> blocked, the approval was spent
    file edited after approval -> blocked, the fingerprint no longer matches
    --status / --revoke     -> inspect and cancel
    the approval store      -> lives in the git dir and holds no credential

Run it from the repository root:

    python3 api/tests/security/ciso_approval_check.py

Exit code 0 means every check passed. Stdlib only, no network, no docker.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

#: Directory of this script (``<api>/tests/security``).
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
#: The API root, i.e. the folder that contains the ``codx`` package.
API_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
#: The repository root (used to copy the real hook).
REPO_ROOT = os.path.dirname(API_ROOT)

MODULE = "codx.junior.security.ciso.precommit"

#: A syntactically valid fake key: the scanner models a provider shape, so it
#: cannot know that this one is not live. That is exactly the point of the gate.
FAKE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
CARD = '{"id": "card", "note": "triage table quotes %s here"}'

results = []


def check(name, condition, detail=""):
    """Record one check.

    Args:
        name: What was verified.
        condition: ``True`` when the check passed.
        detail: Extra context printed on failure.
    """
    results.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {name}" + (f"  → {detail}" if detail and not condition else ""))


def run(args, cwd, env_extra=None):
    """Run a command and return ``(returncode, stdout+stderr)``."""
    env = dict(os.environ)
    env["PYTHONPATH"] = API_ROOT + (
        ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env.pop("CISO_PRECOMMIT_STRICT", None)
    env["CISO_PYTHON"] = sys.executable
    if env_extra:
        env.update(env_extra)
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def git(args, cwd):
    """Run git in *cwd* with a throwaway identity and return its exit code."""
    return run(
        [
            "git",
            "-c",
            "user.name=CISO Check",
            "-c",
            "user.email=ciso@example.invalid",
            *args,
        ],
        cwd,
    )


def stage(path, content, cwd):
    """Write *content* to *path* and stage it."""
    target = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(target) or cwd, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(content)
    git(["add", "--", path], cwd)


def commit(cwd, message="check"):
    """Attempt a commit through the real hook."""
    return git(["commit", "--no-gpg-sign", "-m", message], cwd)


def main():
    """Run every check and print a summary."""
    print("CISO per-commit approval gate")
    print("=" * 40)

    hook_source = os.path.join(REPO_ROOT, ".githooks", "pre-commit")
    if not os.path.isfile(hook_source):
        print(f"  SKIP  no hook at {hook_source}")
        return 0

    workdir = tempfile.mkdtemp(prefix="ciso-approval-")
    try:
        git(["init", "--quiet"], workdir)
        # The hook resolves `api/` from the repository root: link the real one so
        # the throwaway repo runs the real scanner, not a stub.
        os.symlink(API_ROOT, os.path.join(workdir, "api"))
        hook_dir = os.path.join(workdir, ".githooks")
        os.makedirs(hook_dir, exist_ok=True)
        shutil.copy2(hook_source, os.path.join(hook_dir, "pre-commit"))
        os.chmod(os.path.join(hook_dir, "pre-commit"), 0o755)
        git(["config", "core.hooksPath", ".githooks"], workdir)

        # 1. Clean content commits without interference.
        stage("notes.md", "# notes\nnothing sensitive here\n", workdir)
        code, output = commit(workdir, "clean")
        check("clean staged content commits", code == 0, output[-300:])

        # 2. A credential blocks.
        stage("card.json", CARD % FAKE_KEY, workdir)
        code, output = commit(workdir, "leak")
        check("a credential blocks the commit", code != 0, output[-300:])
        check("the block says what to do", "--approve" in output, output[-300:])
        check(
            "the blocked report does not print the credential",
            FAKE_KEY not in output,
            "the credential leaked into the report",
        )

        # 3. Explicitly approve THIS staged content.
        code, output = run([sys.executable, "-m", MODULE, "--approve"], workdir)
        check("--approve accepts the staged content", code == 0, output[-300:])
        approval_file = os.path.join(workdir, ".git", "ciso-commit-approval.json")
        check("the approval is stored in the git dir", os.path.isfile(approval_file))
        check(
            "the approval is not a git object",
            not (git(["ls-files", ".git/ciso-commit-approval.json"], workdir)[1] or "").strip(),
        )
        record = {}
        if os.path.isfile(approval_file):
            with open(approval_file, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        check(
            "the approval record holds no credential",
            FAKE_KEY not in json.dumps(record) and bool(record.get("fingerprint")),
            "the approval store would itself be a leak",
        )

        code, output = run([sys.executable, "-m", MODULE, "--status"], workdir)
        check("--status reports the pending approval", code == 0 and "card.json" in output, output[-300:])

        # 4. The approved content commits — once.
        code, output = commit(workdir, "approved quotation")
        check("the approved content commits", code == 0, output[-300:])
        check(
            "the approval is spent by that commit",
            not os.path.isfile(approval_file),
            "the approval survived the commit it authorised",
        )

        # 5. Re-staging identical content is a NEW commit and is blocked again.
        stage("card.json", CARD % FAKE_KEY, workdir)
        code, output = commit(workdir, "same content again")
        check("identical content is reviewed again", code != 0, output[-300:])

        # 6. Approve one content, then edit the file: the approval must not carry
        #    over, and it must not be spent by a commit it did not authorise.
        #    The token itself stays intact: appending a character would break the
        #    provider pattern, so the edit is text AROUND the credential.
        stage("card.json", CARD % FAKE_KEY + ' "extra": 1', workdir)
        code, output = run([sys.executable, "-m", MODULE, "--approve"], workdir)
        check("--approve covers the newly staged content", code == 0, output[-300:])
        stage("card.json", CARD % FAKE_KEY + ' "extra": 2', workdir)
        code, output = commit(workdir, "changed after approval")
        check("an edit after approval is reviewed again", code != 0, output[-300:])
        check(
            "the stale approval is not consumed",
            os.path.isfile(approval_file),
            "a mismatching approval should stay pending, not be spent",
        )

        # 7. Expiry and revoke, exercised on the module directly.
        code, output = run([sys.executable, "-m", MODULE, "--revoke"], workdir)
        check("--revoke removes the approval", code == 0 and not os.path.isfile(approval_file), output[-300:])

        probe = os.path.join(workdir, "probe.py")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write(
                "import time\n"
                "from codx.junior.security.ciso import precommit\n"
                "from codx.junior.security.ciso.models import SecurityFinding\n"
                "finding = SecurityFinding(rule_id='secret.test', path='card.json')\n"
                "fingerprint = precommit.index_fingerprint([finding])\n"
                "precommit.record_approval([finding], fingerprint, ttl=60)\n"
                "assert precommit.consume_approval(fingerprint, now=time.time() + 120) is False\n"
                "assert precommit.read_approval() is None, 'an expired approval must be dropped'\n"
                "precommit.record_approval([finding], fingerprint, ttl=60)\n"
                "assert precommit.consume_approval('deadbeef') is False\n"
                "assert precommit.read_approval() is not None\n"
                "assert precommit.consume_approval(fingerprint) is True\n"
                "print('expiry-ok')\n"
            )
        code, output = run([sys.executable, probe], workdir)
        check("an expired approval does not apply", code == 0 and "expiry-ok" in output, output[-400:])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    failed = [name for name, ok, _ in results if not ok]
    print("=" * 40)
    print(f"{len(results) - len(failed)}/{len(results)} check(s) passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
