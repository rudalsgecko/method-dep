"""Top-level workspace composer.

Given a customer name and config, produce a composed workspace path
containing only headers, that customer's sources, and tests. The
strategy resolution follows the plan's Workspace Composition diagram:

    auto: git-worktree-sparse -> symlink-tree -> copy-tree
    explicit: use the named strategy, fail loudly if it does not work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from methoddep.config import Config
from methoddep.workspace import copy_tree, git_worktree, symlink_tree, sparse_checkout

log = logging.getLogger(__name__)

Strategy = Literal["git-worktree-sparse", "symlink-tree", "copy-tree", "in-place"]


@dataclass(frozen=True)
class ComposedWorkspace:
    path: Path
    strategy: Strategy
    customer: str
    included_globs: tuple[str, ...]


def _select_globs(config: Config, customer: str) -> tuple[str, ...]:
    globs: list[str] = []
    globs.extend(config.workspace.headers_glob)
    globs.extend(config.workspace.tests_glob)
    globs.append(f"src/{customer}/**")
    customer_cfg = config.customers.get(customer)
    if customer_cfg is not None:
        globs.extend(customer_cfg.extra_paths)
    return tuple(dict.fromkeys(globs))  # dedupe, preserve order


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def compose_workspace(
    config: Config,
    customer: str,
    *,
    refresh: bool = False,
) -> ComposedWorkspace:
    repo_root = Path(config.target.repo_root).resolve()
    if not repo_root.exists():
        raise FileNotFoundError(f"target.repo_root does not exist: {repo_root}")

    strategy = config.workspace.strategy
    globs = _select_globs(config, customer)

    if strategy == "in-place":
        return ComposedWorkspace(
            path=repo_root,
            strategy="in-place",  # type: ignore[arg-type]
            customer=customer,
            included_globs=globs,
        )

    if config.workspace.worktree_base is None:
        raise ValueError("workspace.worktree_base is required unless strategy='in-place'")
    dest = Path(config.workspace.worktree_base).resolve() / customer

    if strategy == "auto":
        chosen = _auto_compose(repo_root, dest, globs, customer, refresh=refresh)
    elif strategy == "git-worktree-sparse":
        if not _is_git_repo(repo_root):
            raise RuntimeError(
                f"strategy=git-worktree-sparse but {repo_root} is not a git repo"
            )
        git_worktree.create(repo_root, dest, customer, refresh=refresh)
        sparse_checkout.configure(dest, globs)
        chosen = "git-worktree-sparse"
    elif strategy == "symlink-tree":
        symlink_tree.build(repo_root, dest, globs, refresh=refresh)
        chosen = "symlink-tree"
    elif strategy == "copy-tree":
        copy_tree.build(repo_root, dest, globs, refresh=refresh)
        chosen = "copy-tree"
    else:  # pragma: no cover — pydantic already validates this.
        raise ValueError(f"unknown workspace strategy: {strategy!r}")

    return ComposedWorkspace(
        path=dest,
        strategy=chosen,  # type: ignore[arg-type]
        customer=customer,
        included_globs=globs,
    )


def _auto_compose(
    repo_root: Path,
    dest: Path,
    globs: tuple[str, ...],
    customer: str,
    *,
    refresh: bool,
) -> Strategy:
    if _is_git_repo(repo_root):
        try:
            git_worktree.create(repo_root, dest, customer, refresh=refresh)
            sparse_checkout.configure(dest, globs)
            return "git-worktree-sparse"
        except Exception as exc:
            log.warning("git-worktree-sparse failed (%s); falling back to symlink", exc)
    try:
        symlink_tree.build(repo_root, dest, globs, refresh=refresh)
        return "symlink-tree"
    except (OSError, PermissionError) as exc:
        log.warning("symlink-tree failed (%s); falling back to copy", exc)
    copy_tree.build(repo_root, dest, globs, refresh=refresh)
    return "copy-tree"
