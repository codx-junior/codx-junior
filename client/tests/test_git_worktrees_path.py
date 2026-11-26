"""Tests for the worktree base path resolution (card 4f014b9b).

`git worktree add` refuses a target inside the repository, so
`GitEngine.get_worktrees_path()` must never return a folder nested in the
working tree: a configured `git_worktrees_path` that points inside the
repository is replaced by a sibling `<repo>-worktrees` default.

Run with: ``python -m pytest tests/test_git_worktrees_path.py -q``
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_module(name: str, **attrs) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


def _import_engine():
    """Import ``GitEngine``, stubbing the modules absent from this checkout."""
    try:
        from codx.junior.engine.git_engine import GitEngine

        return GitEngine
    except ImportError:
        pass

    _stub_module("codx.junior.project")
    _stub_module("codx.junior.project.project_discover", find_project_parents=lambda *a, **k: [])
    _stub_module("codx.junior.utils")
    _stub_module("codx.junior.utils.utils", exec_command=lambda *a, **k: None)
    for key in [k for k in sys.modules if k.startswith("codx.junior.engine")]:
        del sys.modules[key]
    from codx.junior.engine.git_engine import GitEngine

    return GitEngine


GitEngine = _import_engine()


class _Settings:
    """Minimal stand-in for the project settings object."""

    def __init__(self, project_path: Path, worktrees_path: str = "") -> None:
        self.abs_project_path = str(project_path)
        self.git_worktrees_path = worktrees_path

    def get_worktrees_path(self) -> str:
        return self.git_worktrees_path


def _engine(settings: _Settings, repo_path: Path):
    engine = GitEngine.__new__(GitEngine)
    engine.session = SimpleNamespace(settings=settings)
    engine.find_git_root_path = lambda: str(repo_path)
    engine._get_repo_root = lambda: str(repo_path)
    engine._run_git = lambda *args, **kwargs: {"ok": True, "output": "", "error": ""}
    return engine


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "codx-junior-platform"
    repo.mkdir()
    return repo


def test_default_worktrees_path_is_a_sibling_of_the_repository(tmp_path):
    repo = _repo(tmp_path)
    engine = _engine(_Settings(repo), repo)

    resolved = Path(engine.get_worktrees_path())

    assert resolved == repo.parent / f"{repo.name}-worktrees"
    assert not GitEngine._is_path_inside(str(resolved), str(repo))


def test_configured_path_inside_repository_falls_back_to_sibling(tmp_path):
    """The reported defect: `<repo>/client-worktrees` can never host a worktree."""
    repo = _repo(tmp_path)
    engine = _engine(_Settings(repo, str(repo / "client-worktrees")), repo)

    resolved = Path(engine.get_worktrees_path())

    assert resolved == repo.parent / f"{repo.name}-worktrees"
    assert not GitEngine._is_path_inside(str(resolved), str(repo))


def test_configured_path_outside_repository_is_used(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path / "shared-worktrees"
    engine = _engine(_Settings(repo, str(outside / ".." / "shared-worktrees")), repo)

    assert Path(engine.get_worktrees_path()) == outside


def test_relative_worktree_path_resolves_against_the_base_not_the_repository(tmp_path):
    repo = _repo(tmp_path)
    engine = _engine(_Settings(repo, str(repo / "client-worktrees")), repo)

    result = engine._create_branch_worktree(
        str(repo), "codx-junior/relative", "main", False, worktree_path="relative"
    )

    assert result["ok"] is True, result
    assert not GitEngine._is_path_inside(result["path"], str(repo))
    assert Path(result["path"]).parent == repo.parent / f"{repo.name}-worktrees"


def test_worktree_path_inside_the_repository_is_relocated(tmp_path):
    """An explicit in-repo target must not stop the PR/commit operation."""
    repo = _repo(tmp_path)
    engine = _engine(_Settings(repo), repo)

    result = engine._create_branch_worktree(
        str(repo), "codx-junior/inside", "main", False, worktree_path=str(repo / "wt")
    )

    assert result["ok"] is True, result
    assert not GitEngine._is_path_inside(result["path"], str(repo))
    assert Path(result["path"]) == repo.parent / f"{repo.name}-worktrees" / "wt"
