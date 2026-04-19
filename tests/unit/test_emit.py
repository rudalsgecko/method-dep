"""Emit per-method JSON + determinism byte-diff."""

from __future__ import annotations

import filecmp
import json
from pathlib import Path

from methoddep.analyze import analyze_file
from methoddep.complexity.lizard_runner import analyze_file as lizard_file, find_match
from methoddep.determinism import compute_input_fingerprint, normalize_path_for_fingerprint
from methoddep.mocks.resolver import resolve_mocks
from methoddep.schema.emit import emit_method, write_index

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def _emit_interface_impl(out_dir: Path) -> list[tuple[Path, object]]:
    root = FIXTURES / "interface_impl"
    cpp = root / "src" / "acme" / "foo" / "Bar.cpp"
    methods = analyze_file(cpp, include_dirs=[root / "include"], workspace_root=root)
    lizards = lizard_file(cpp)
    fingerprint = compute_input_fingerprint(sorted(root.rglob("*.cpp")) + sorted(root.rglob("*.h")), root)
    tool_versions = {"libclang": "18.1.1", "lizard": "1.21.6"}
    records = []
    for m in methods:
        mocks = resolve_mocks(
            [d.qualified_name for d in m.dep_classes if d.is_interface],
            workspace_root=root,
            mock_dirs=["tests/mocks"],
            name_patterns=["Mock{Class}", "Fake{Class}"],
        )
        lz = find_match(
            lizards,
            name=m.qualified_name.rsplit("::", 1)[-1],
            class_name=m.class_name,
            definition_line=m.definition.line if m.definition else None,
        )
        path, record = emit_method(
            m,
            customer="acme",
            output_dir=out_dir,
            workspace_root=root,
            tool_versions=tool_versions,
            input_fingerprint=fingerprint,
            lizard_match=lz,
            mocks=mocks,
        )
        records.append((path, record))
    write_index(records, customer="acme", output_dir=out_dir, tool_versions=tool_versions)
    return records


def test_emit_creates_valid_json(tmp_path: Path) -> None:
    records = _emit_interface_impl(tmp_path)
    assert records
    # Index file exists and parses.
    idx_path = tmp_path / "acme" / "index.json"
    assert idx_path.exists()
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    assert idx["customer"] == "acme"
    assert "by_class" in idx
    assert any("foo::Bar" in k for k in idx["by_class"])

    # Every per-method file is parseable and has the lean shape.
    for path, _ in records:
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        # Lean form: no id/schema_version/provenance in the per-method file.
        assert "id" not in data
        assert "schema_version" not in data
        assert "provenance" not in data
        assert data["location"]["customer"] == "acme"
        # Index.json carries the tool metadata instead.
    idx_doc = json.loads(idx_path.read_text(encoding="utf-8"))
    assert idx_doc["schema_version"] == "1.0"
    assert idx_doc["path_normalization"] in {"win-lower-relative", "posix-relative"}


def test_emit_is_deterministic_byte_for_byte(tmp_path: Path) -> None:
    # Run twice into separate directories and compare per-method files.
    first = tmp_path / "a"
    second = tmp_path / "b"
    recs_a = _emit_interface_impl(first)
    recs_b = _emit_interface_impl(second)

    # Map files by MethodRecord.id (ids no longer live in per-method files).
    def _id_map(recs):
        return {record.id: path for path, record in recs}

    ids_a = _id_map(recs_a)
    ids_b = _id_map(recs_b)
    assert ids_a.keys() == ids_b.keys()

    # Per-method JSONs are byte-deterministic (no timestamps inside them).
    for mid in ids_a:
        assert ids_a[mid].read_bytes() == ids_b[mid].read_bytes()


def test_input_fingerprint_ignores_system_headers(tmp_path: Path) -> None:
    # Paths outside workspace_root should be silently filtered.
    root = FIXTURES / "interface_impl"
    outside = tmp_path / "somewhere.h"
    outside.write_text("// external\n", encoding="utf-8")
    inside = root / "include" / "foo" / "Bar.h"
    fp = compute_input_fingerprint([outside, inside], root)
    fp_alone = compute_input_fingerprint([inside], root)
    assert fp == fp_alone


def test_path_normalization_form() -> None:
    rel = "include\\foo\\Bar.h" if __import__("os").name == "nt" else "include/foo/Bar.h"
    normalized = normalize_path_for_fingerprint(rel)
    assert "/" in normalized
    assert "\\" not in normalized
