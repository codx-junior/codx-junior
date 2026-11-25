"""
Regression tests for the two shell-string CISO findings in the engines.

* ``GitEngine.diff_file`` must build git with an argument list and send the
  payload on **stdin**. The previous implementation interpolated the content
  into a ``cat << EOF`` heredoc, so ``$(...)``, backticks or a line containing
  ``EOF`` were interpreted by the shell.
* ``CODXJuniorSession.coder_open_file`` must pass the file name as a separate
  argument instead of concatenating it into an ``os.system`` string.

Both tests assert the exact contract of the fix (argv list + stdin, never a
shell string), so they neither need a real git binary nor a slow fixture.
"""

import os
import subprocess
from types import SimpleNamespace

from codx.junior.engine.git_engine import GitEngine


class FakeSettings:
    """The subset of CODXJuniorSettings used by the git engine."""

    def __init__(self, path: str):
        self.abs_project_path = path
        self.codx_path = os.path.join(path, ".codx")
        self.project_name = "git-engine-test"
        self.git_access_token = ""


class FakeSession:
    """Minimal session exposing the settings the engine reads."""

    def __init__(self, settings):
        self.settings = settings


def make_engine(path, monkeypatch):
    """An engine whose git-root lookup does not touch the filesystem."""
    engine = GitEngine(session=FakeSession(FakeSettings(path)))
    monkeypatch.setattr(engine, "_get_git_root_for_file", lambda **kwargs: path)
    return engine


def test_diff_file_uses_an_argument_list_and_stdin(monkeypatch, tmp_path):
    """Both git commands are argv lists and the payload travels on stdin."""
    from codx.junior.engine import git_engine as git_module

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        stdout = "1 file changed, 1 insertion(+)" if "--shortstat" in cmd else "diff-body"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)

    engine = make_engine(str(tmp_path), monkeypatch)
    result = engine.diff_file("app.py", "print('two')\n")

    assert len(calls) == 2
    for cmd, kwargs in calls:
        assert isinstance(cmd, list), "git must be called with an argument list"
        assert all(isinstance(part, str) for part in cmd)
        assert kwargs.get("shell") is not True
    assert calls[0][1]["input"] == "print('two')\n"
    assert calls[1][1]["input"] == "print('two')\n"
    assert result == {
        "diff": "diff-body",
        "stats": "1 file changed, 1 insertion(+)",
    }


def test_diff_file_never_puts_the_payload_in_the_command(monkeypatch, tmp_path):
    """Shell metacharacters inside the content cannot reach a command line."""
    from codx.junior.engine import git_engine as git_module

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "1 file changed")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)

    payload = "print('one')\n$(touch pwned)\n`touch pwned`\nEOF\n"
    engine = make_engine(str(tmp_path), monkeypatch)

    engine.diff_file("app.py", payload)

    for cmd, kwargs in calls:
        joined = " ".join(cmd)
        assert payload not in joined
        assert "$(" not in joined
        assert kwargs.get("input") == payload


def test_coder_open_file_passes_the_path_as_an_argument(monkeypatch, tmp_path):
    """The editor command is an argv list: no shell, no quoting escape."""
    from codx.junior.engine import session as session_module

    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(session_module.subprocess, "run", fake_run)

    target = tmp_path / "app.py"
    target.write_text("print('one')\n", encoding="utf-8")
    errors = []
    fake_self = SimpleNamespace(
        settings=SimpleNamespace(abs_project_path=str(tmp_path)),
        log_error=lambda msg, *args: errors.append((msg, args)),
    )

    result = session_module.CODXJuniorSession.coder_open_file(fake_self, str(target))

    assert calls["cmd"] == ["code-server", "-r", str(target)]
    assert calls["kwargs"].get("shell") is not True
    assert result["file_name"] == str(target)
    assert errors == []


def test_coder_open_file_does_not_break_on_a_quote_in_the_name(monkeypatch, tmp_path):
    """A tricky file name stays a single argument instead of running a command."""
    from codx.junior.engine import session as session_module

    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(session_module.subprocess, "run", fake_run)

    name = str(tmp_path / 'a"; touch pwned.py; echo ".py')
    fake_self = SimpleNamespace(
        settings=SimpleNamespace(abs_project_path=str(tmp_path)),
        log_error=lambda msg, *args: None,
    )

    session_module.CODXJuniorSession.coder_open_file(fake_self, name)

    assert calls["cmd"] == ["code-server", "-r", name]
    assert not os.path.exists(os.path.join(str(tmp_path), "pwned.py"))
