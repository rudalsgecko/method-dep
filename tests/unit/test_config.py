"""Config loader and scaffold tests."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from methoddep.config import SCAFFOLD, Config, load_config, scaffold_config


def test_scaffold_is_valid_toml() -> None:
    tomllib.loads(SCAFFOLD)  # should not raise


def test_scaffold_writes_file(tmp_path: Path) -> None:
    path = scaffold_config(tmp_path)
    assert path.exists()
    assert path.name == "methoddep.toml"


def test_scaffold_refuses_to_overwrite(tmp_path: Path) -> None:
    scaffold_config(tmp_path)
    with pytest.raises(FileExistsError):
        scaffold_config(tmp_path)


def test_scaffold_force_overwrite(tmp_path: Path) -> None:
    scaffold_config(tmp_path)
    path = scaffold_config(tmp_path, force=True)
    assert path.exists()


def test_load_scaffold_produces_valid_config(tmp_path: Path) -> None:
    path = scaffold_config(tmp_path)
    cfg = load_config(path)
    assert isinstance(cfg, Config)
    assert cfg.workspace.strategy == "auto"
    assert "acme" in cfg.customers
    assert cfg.test.framework == "gtest"
    assert cfg.analysis.analyzer_chain[0] == "msbuild"


def test_minimal_config_defaults(tmp_path: Path) -> None:
    path = tmp_path / "min.toml"
    path.write_text(
        """
[target]
repo_root = "."

[workspace]
worktree_base = "."
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.workspace.strategy == "auto"
    assert cfg.analysis.pch_autodetect is True
    assert cfg.output.index_file == "index.json"
