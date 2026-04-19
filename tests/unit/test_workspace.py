"""Workspace strategies — exercise copy-tree (always available) and
auto-fallback behaviour. git-worktree and symlink paths are covered by
integration tests when git/permissions are present."""

from __future__ import annotations

from pathlib import Path

import pytest

from methoddep.config import (
    AnalysisConfig,
    BuildIntelConfig,
    ComplexityConfig,
    Config,
    CustomerConfig,
    OutputConfig,
    TargetConfig,
    TestConfig,
    WorkspaceConfig,
)
from methoddep.workspace import copy_tree, symlink_tree
from methoddep.workspace._filter import iter_matching_files
from methoddep.workspace.composer import compose_workspace


def _make_repo(root: Path) -> None:
    (root / "include" / "foo").mkdir(parents=True)
    (root / "include" / "foo" / "Bar.h").write_text("struct Bar {};\n", encoding="utf-8")
    (root / "src" / "acme" / "foo").mkdir(parents=True)
    (root / "src" / "acme" / "foo" / "Bar.cpp").write_text(
        '#include "foo/Bar.h"\n', encoding="utf-8"
    )
    (root / "src" / "globex" / "foo").mkdir(parents=True)
    (root / "src" / "globex" / "foo" / "Bar.cpp").write_text(
        "// other variant\n", encoding="utf-8"
    )
    (root / "tests" / "mocks").mkdir(parents=True)
    (root / "tests" / "mocks" / "MockBar.h").write_text("// mock\n", encoding="utf-8")
    (root / "README.txt").write_text("unrelated\n", encoding="utf-8")


def _config(repo: Path, wt_base: Path, strategy: str = "copy-tree") -> Config:
    return Config(
        target=TargetConfig(repo_root=repo),
        workspace=WorkspaceConfig(
            strategy=strategy,  # type: ignore[arg-type]
            worktree_base=wt_base,
        ),
        customers={"acme": CustomerConfig()},
        analysis=AnalysisConfig(),
        build_intel=BuildIntelConfig(),
        complexity=ComplexityConfig(),
        test=TestConfig(),
        output=OutputConfig(),
    )


def test_iter_matching_files_selects_only_requested(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    globs = ("include/**", "src/acme/**", "tests/**")
    matched = {p.relative_to(tmp_path).as_posix() for p in iter_matching_files(tmp_path, globs)}
    assert "include/foo/Bar.h" in matched
    assert "src/acme/foo/Bar.cpp" in matched
    assert "tests/mocks/MockBar.h" in matched
    assert "src/globex/foo/Bar.cpp" not in matched
    assert "README.txt" not in matched


def test_copy_tree_builds_expected_layout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    dest = tmp_path / "out"
    _make_repo(repo)
    copy_tree.build(repo, dest, ("include/**", "src/acme/**"))
    assert (dest / "include" / "foo" / "Bar.h").exists()
    assert (dest / "src" / "acme" / "foo" / "Bar.cpp").exists()
    assert not (dest / "src" / "globex").exists()


def test_compose_workspace_explicit_copy_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    _make_repo(repo)
    cfg = _config(repo, wt, strategy="copy-tree")
    result = compose_workspace(cfg, "acme")
    assert result.strategy == "copy-tree"
    assert result.customer == "acme"
    assert (result.path / "include" / "foo" / "Bar.h").exists()
    assert (result.path / "src" / "acme" / "foo" / "Bar.cpp").exists()
    assert not (result.path / "src" / "globex").exists()


def test_compose_workspace_auto_falls_back_on_non_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    _make_repo(repo)
    cfg = _config(repo, wt, strategy="auto")
    result = compose_workspace(cfg, "acme")
    # Not a git repo, so auto must choose symlink-tree or copy-tree.
    assert result.strategy in {"symlink-tree", "copy-tree"}
    assert (result.path / "include" / "foo" / "Bar.h").exists()


def test_compose_workspace_rejects_missing_repo(tmp_path: Path) -> None:
    cfg = _config(tmp_path / "nope", tmp_path / "wt", strategy="copy-tree")
    with pytest.raises(FileNotFoundError):
        compose_workspace(cfg, "acme")


def test_compose_workspace_rejects_explicit_git_on_nongit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo)
    cfg = _config(repo, tmp_path / "wt", strategy="git-worktree-sparse")
    with pytest.raises(RuntimeError, match="not a git repo"):
        compose_workspace(cfg, "acme")


def test_symlink_tree_or_skip_when_permissions_absent(tmp_path: Path) -> None:
    """Symlinks on Windows need developer mode. If unavailable, the
    function raises OSError — we accept either outcome."""
    repo = tmp_path / "repo"
    _make_repo(repo)
    dest = tmp_path / "link"
    try:
        symlink_tree.build(repo, dest, ("include/**",))
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    assert (dest / "include" / "foo" / "Bar.h").exists()
