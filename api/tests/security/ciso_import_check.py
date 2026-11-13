"""Prove the CISO package runs on a bare interpreter, without the API venv.

Run it with any python::

    python3 api/tests/security/ciso_import_check.py

Why this check exists
    The gate is executed by a git hook, and a hook runs in environments the API
    knows nothing about: a fresh clone, a PR worktree (``api/.venv`` is never
    committed, so it does not exist there), a container image or CI. When the
    package imported pydantic, all of those died with::

        ModuleNotFoundError: No module named 'pydantic'

    at commit time — the hook passed, nothing was scanned, and the failure
    surfaced as a traceback instead of a verdict. This check keeps the property
    that fixes it: **the whole ``security.ciso`` package imports and works on a
    stdlib-only interpreter**, so the gate protects every clone.

Checks
    1. the test interpreter really is bare (pydantic is not importable),
    2. ``import codx.junior.security.ciso`` works there,
    3. detection, policy and JSON serialisation still work there,
    4. a commit from a repository WITHOUT ``api/.venv`` is gated correctly
       (blocked with a credential, allowed when clean).

Made with ❤️ by codx-junior
"""

import os
import subprocess
import sys
import tempfile
import textwrap

#: ``api/`` root: this file lives in ``api/tests/security/``.
API_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
#: ``.githooks/`` of the repository that contains this API package.
REPO_ROOT = os.path.dirname(API_ROOT)
HOOK_PATH = os.path.join(REPO_ROOT, ".githooks", "pre-commit")

#: A token-shaped value, assembled at runtime so this file is not itself a hit.
FAKE_TOKEN = "ghp_" + ("A1b2C3d4E5f6G7h8I9j0" * 2)

RESULTS = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    """Record one check and print its result."""
    RESULTS.append((name, passed))
    status = "PASS" if passed else "FAIL"
    suffix = f"  ({detail})" if detail and not passed else ""
    print(f"  {status}  {name}{suffix}")
    return passed


#: Executed by the child interpreter. ``-S`` gives it no site-packages.
CHILD_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    sys.path.insert(0, {api_root!r})

    # 1. the interpreter must be bare, otherwise this check proves nothing
    try:
        import pydantic  # noqa: F401
    except ModuleNotFoundError:
        pass
    else:
        print("BARE_INTERPRETER=0")
        raise SystemExit(0)
    print("BARE_INTERPRETER=1")

    # 2. the package and every module the gate needs must import
    import codx.junior.security.ciso as ciso
    from codx.junior.security.ciso import (  # noqa: F401
        audit, gate, injection, malicious, models, payload, policy, precommit,
        scanner, secrets,
    )

    # 3. the behaviour must be the real behaviour, not just a successful import
    assert ciso.SecuritySeverity.CRITICAL.value == "critical"
    assert models.parse_severity("HIGH") is ciso.SecuritySeverity.HIGH
    assert models.parse_severity("critical") is ciso.SecuritySeverity.CRITICAL

    finding = models.SecurityFinding(rule_id="secret.test", severity="high")
    assert finding.severity is ciso.SecuritySeverity.HIGH, finding.severity
    verdict = models.SecurityVerdict(findings=[finding], blocked=True)
    assert isinstance(verdict.findings[0], models.SecurityFinding)
    assert json.loads(json.dumps(verdict.to_dict()))["findings"][0]["severity"] == "high"

    kept = models.SecurityVerdict(
        findings=[
            models.SecurityFinding("a", severity="low"),
            models.SecurityFinding("b", severity="critical"),
        ]
    ).filtered(ciso.SecuritySeverity.HIGH)
    assert [f.rule_id for f in kept.findings] == ["b"], kept.findings

    # a write that carries a credential must be blocked, on a bare interpreter
    write = gate.guard_file_write("api/config.py", "TOKEN = {token!r}\\n")
    assert write.blocked, write.summary
    assert "{token}" not in (write.redacted_text or ""), write.redacted_text

    # a clean write must pass
    assert gate.guard_file_write("api/config.py", "NAME = 'codx'\\n").allowed

    print("CHILD=1")
    """
).strip()


def run_child(python: str) -> subprocess.CompletedProcess:
    """Import and exercise the package with *python* (no site-packages)."""
    script = CHILD_SCRIPT.format(api_root=API_ROOT, token=FAKE_TOKEN)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    return subprocess.run(
        [python, "-S", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=tempfile.gettempdir(),
        timeout=180,
    )


def check_bare_interpreter() -> None:
    """The package must import and work without site-packages."""
    python = sys.executable or "python3"
    completed = run_child(python)
    output = (completed.stdout or "") + (completed.stderr or "")

    if "BARE_INTERPRETER=0" in output:
        check(
            "the interpreter is bare (no site-packages)",
            False,
            f"{python} still has pydantic; use a system python3 to verify",
        )
        return
    check("the interpreter is bare (no site-packages)", True)
    check(
        "security.ciso imports without third-party packages",
        completed.returncode == 0 and "CHILD=1" in output,
        output.strip().splitlines()[-1] if output.strip() else "no output",
    )
    if completed.returncode != 0:
        print(textwrap.indent(output, "        "))

    # The known failure mode: the module is imported by `python -m`, so a
    # missing dependency surfaces before main() can handle it.
    runpy = subprocess.run(
        [python, "-S", "-c", f"import sys; sys.path.insert(0, {API_ROOT!r});"
         " import runpy, sys as s; s.argv=['precommit', '--check'];"
         " import codx.junior.security.ciso.precommit as m;"
         " print('PRE_IMPORT=1')"],
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        timeout=120,
    )
    check(
        "the hook entry point imports before main() runs",
        runpy.returncode == 0 and "PRE_IMPORT=1" in (runpy.stdout or ""),
        (runpy.stderr or "").strip().splitlines()[-1] if runpy.returncode else "",
    )


def check_commit_without_venv(python: str) -> None:
    """A commit from a tree with no api/.venv must still be gated."""
    if not os.path.isfile(HOOK_PATH):
        check("the pre-commit hook exists", False, HOOK_PATH)
        return

    with tempfile.TemporaryDirectory(prefix="ciso-novenv-") as workspace:
        repo = os.path.join(workspace, "repo")
        os.makedirs(repo)
        # `api/` is linked WITHOUT a .venv: a PR worktree looks exactly like this.
        os.symlink(API_ROOT, os.path.join(repo, "api"))
        os.makedirs(os.path.join(repo, ".githooks"))
        hook = os.path.join(repo, ".githooks", "pre-commit")
        with open(HOOK_PATH, "r", encoding="utf-8") as source:
            content = source.read()
        with open(hook, "w", encoding="utf-8") as target:
            target.write(content)
        os.chmod(hook, 0o755)

        def git(*args: str, env: dict = None) -> subprocess.CompletedProcess:
            full_env = {
                key: value
                for key, value in os.environ.items()
                if key not in ("PYTHONPATH", "GIT_DIR", "GIT_WORK_TREE", "CISO_PYTHON")
            }
            full_env.update(
                {
                    "GIT_AUTHOR_NAME": "ciso",
                    "GIT_AUTHOR_EMAIL": "ciso@example.com",
                    "GIT_COMMITTER_NAME": "ciso",
                    "GIT_COMMITTER_EMAIL": "ciso@example.com",
                    "CISO_PYTHON": python,
                }
            )
            full_env.update(env or {})
            return subprocess.run(
                ["git", *args],
                cwd=repo,
                capture_output=True,
                text=True,
                env=full_env,
                timeout=180,
            )

        git("init", "--quiet")
        git("config", "core.hooksPath", ".githooks")
        git("config", "user.useConfigOnly", "true")

        with open(os.path.join(repo, "clean.py"), "w", encoding="utf-8") as handle:
            handle.write("NAME = 'codx'\\n".replace("\\n", "\n"))
        git("add", "clean.py")
        clean = git("commit", "--quiet", "-m", "clean")
        check(
            "clean changes commit without a virtualenv",
            clean.returncode == 0,
            (clean.stderr or clean.stdout or "").strip().splitlines()[-1]
            if clean.returncode
            else "",
        )

        with open(os.path.join(repo, "leak.py"), "w", encoding="utf-8") as handle:
            handle.write(f"TOKEN = {FAKE_TOKEN!r}\n")
        git("add", "leak.py")
        blocked = git("commit", "--quiet", "-m", "leak")
        report = (blocked.stdout or "") + (blocked.stderr or "")
        check(
            "a credential blocks the commit without a virtualenv",
            blocked.returncode != 0 and "commit blocked" in report.lower(),
            "exit 0: the gate did not run" if blocked.returncode == 0 else "",
        )
        check(
            "the blocked report does not print the credential",
            FAKE_TOKEN not in report,
            "the token leaked into the report" if FAKE_TOKEN in report else "",
        )


def check_hook_contract() -> None:
    """The hook itself must not hard-code a venv-only interpreter."""
    with open(HOOK_PATH, "r", encoding="utf-8") as handle:
        content = handle.read()
    check(
        "the hook probes interpreters instead of assuming one",
        "import $module" in content and "CISO_PYTHON" in content,
        "the hook does not test that the scanner can load",
    )
    check(
        "the hook resolves the main worktree",
        "git-common-dir" in content,
        "a PR worktree would not find the API package",
    )


def main() -> int:
    """Run every check and report the summary."""
    print("CISO bare-interpreter check")
    print("=" * 40)
    python = sys.executable or "python3"
    system_python = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else python

    check_bare_interpreter()
    check_hook_contract()
    check_commit_without_venv(system_python)

    failed = [name for name, passed in RESULTS if not passed]
    print("=" * 40)
    if failed:
        print(f"{len(failed)} check(s) failed")
        return 1
    print(f"{len(RESULTS)} check(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
