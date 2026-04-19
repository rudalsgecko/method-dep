"""End-to-end pipeline: compose workspace → analyze → emit JSON + index."""

from __future__ import annotations

import json
from pathlib import Path

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
from methoddep.pipeline import run_customer


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def _config(repo: Path, wt: Path, out: Path) -> Config:
    return Config(
        target=TargetConfig(repo_root=repo),
        workspace=WorkspaceConfig(
            strategy="copy-tree",  # deterministic, no symlinks/git needed
            worktree_base=wt,
        ),
        customers={"acme": CustomerConfig()},
        analysis=AnalysisConfig(
            include_dirs=["include"],
            clang_flags=[
                "-fms-extensions",
                "-fms-compatibility",
                "-fdelayed-template-parsing",
                "-std=c++20",
                "-target",
                "x86_64-pc-windows-msvc",
            ],
        ),
        build_intel=BuildIntelConfig(enabled=False),
        complexity=ComplexityConfig(),
        test=TestConfig(),
        output=OutputConfig(dir=str(out)),
    )


def test_run_customer_interface_impl_end_to_end(tmp_path: Path) -> None:
    cfg = _config(FIXTURES / "interface_impl", tmp_path / "wt", tmp_path / "out")
    result = run_customer(cfg, "acme")

    assert result.customer == "acme"
    assert result.method_count >= 2  # ctor + doWork
    assert result.index_path.exists()

    idx = json.loads(result.index_path.read_text(encoding="utf-8"))
    assert idx["customer"] == "acme"
    assert any("foo::Bar" in k for k in idx["by_class"])

    # Per-method JSONs exist and contain the expected shape.
    methods_dir = result.output_dir / "acme" / "methods"
    per_method = sorted(methods_dir.rglob("*.json"))
    assert per_method
    sample = json.loads(per_method[0].read_text(encoding="utf-8"))
    # Lean per-method JSON: id/schema_version live in index.json only.
    assert "id" not in sample
    assert "schema_version" not in sample
    assert "method" in sample
    assert idx["schema_version"] == "1.0"


def test_run_customer_with_deps_resolves_mock_skeleton(tmp_path: Path) -> None:
    cfg = _config(FIXTURES / "with_deps", tmp_path / "wt", tmp_path / "out")
    # with_deps has no tests/mocks dir — missing interfaces should get skeletons.
    result = run_customer(cfg, "acme")
    assert result.method_count >= 2

    # Find the process() JSON — the one with interfaces in dependencies.
    idx = json.loads(result.index_path.read_text(encoding="utf-8"))
    process_doc = None
    for rel in idx["by_method"].values():
        doc = json.loads((result.output_dir / "acme" / rel).read_text(encoding="utf-8"))
        classes = doc.get("dependencies", {}).get("classes", [])
        if any(d.get("is_interface") for d in classes):
            process_doc = doc
            break
    assert process_doc is not None, "no method found with interface dependencies"
    mocks = process_doc["mocks"]
    statuses = {m["target_class"]: m["status"] for m in mocks}
    assert statuses.get("svc::Cache") == "missing"
    # Missing mock should include a gmock skeleton.
    cache_mock = next(m for m in mocks if m["target_class"] == "svc::Cache")
    assert cache_mock["gmock_stub_skeleton"] is not None
    assert "MOCK_METHOD" in cache_mock["gmock_stub_skeleton"]
