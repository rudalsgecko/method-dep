"""Emit per-method JSON and index.json for a given customer run.

The emit module is the only place that writes output; all other modules
produce in-memory facts. This keeps determinism invariants local.
"""

from __future__ import annotations

import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from methoddep.analyze.models import AnalyzedMethod
from methoddep.complexity.lizard_runner import Complexity as LizardComplexity
from methoddep.determinism import (
    compute_input_fingerprint,
    dump_json,
    path_normalization_tag,
    write_json_deterministically,
)
from methoddep.mocks.resolver import MockMatch
from methoddep.schema.hash import method_id
from methoddep.schema.models import (
    CallSiteBlock,
    Complexity,
    DependenciesBlock,
    DependencyClassBlock,
    DependencyEnumBlock,
    DependencyFunctionBlock,
    DependencyStructBlock,
    ExceptionSpec,
    GlobalRefBlock,
    IndexEntry,
    Location,
    LocationBlock,
    MethodBlock,
    MethodRecord,
    MockBlock,
    ParameterRecord,
    Provenance,
    Specifiers,
    StaticLocalBlock,
    TestHints,
)
from methoddep.schema.paths import encode_component, encode_namespace


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _prune(obj):
    """Recursively drop None, empty lists, empty dicts, and empty strings.

    Leaves booleans and numbers alone. Used to strip structural noise
    from per-method JSON before emit so the LLM sees only populated fields.
    """
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            pv = _prune(v)
            if pv is None:
                continue
            if isinstance(pv, (list, dict, str)) and not pv:
                continue
            cleaned[k] = pv
        return cleaned or None
    if isinstance(obj, list):
        out = []
        for item in obj:
            pv = _prune(item)
            if pv is None:
                continue
            if isinstance(pv, (list, dict, str)) and not pv:
                continue
            out.append(pv)
        return out
    return obj


_TRUE_SPECIFIERS = (
    "virtual", "override", "final", "const", "static", "noexcept", "pure",
    "inline", "constexpr", "deleted", "defaulted",
)


def _compact_record(record: "MethodRecord") -> dict:
    """Render a MethodRecord as a lean dict optimized for LLM consumption.

    - Drops `id`/`schema_version`/`provenance`/`raw_signature`/`std_types`
      (redundant or tool-metadata; stored once in index.json instead).
    - Collapses `specifiers` to a list of the True flags only.
    - Strips empty arrays/objects, None, and False defaults.
    """
    m = record.method

    def _param(p):
        return {
            "name": p.name,
            "type": p.type,
            "qualifiers": p.qualifiers,
            "direction": p.direction if p.direction != "in" else None,
            "default_value": p.default_value,
        }

    spec_flags = [k for k in _TRUE_SPECIFIERS if getattr(m.specifiers, k)]

    method_block = {
        "qualified_name": m.qualified_name,
        "class": m.class_name,
        "namespace": m.namespace,
        "signature": m.signature,
        "return_type": m.return_type,
        "parameters": [_param(p) for p in m.parameters],
        "specifiers": spec_flags,
        "template_params": m.template_params,
        "access": m.access if m.access != "public" else None,
        "exception_spec": {
            "declared": m.exception_spec.declared,
            "observed_throws": m.exception_spec.observed_throws,
        },
        "defined_in_header": m.defined_in_header or None,
        "is_header_only": m.is_header_only or None,
        "friends_of_class": m.friends_of_class,
    }

    location_block = {
        "header": (
            {"path": record.location.header.path, "line": record.location.header.line}
            if record.location.header else None
        ),
        "definition": (
            {"path": record.location.definition.path, "line": record.location.definition.line}
            if record.location.definition else None
        ),
        "customer": record.location.customer,
    }

    complexity_block = None
    if record.complexity is not None:
        complexity_block = {
            "cyclomatic": record.complexity.cyclomatic,
            "nloc": record.complexity.nloc,
            "parameter_count": record.complexity.parameter_count,
        }

    deps = record.dependencies

    def _class_entry(d):
        return {
            "qualified_name": d.qualified_name,
            "kind": d.kind if d.kind != "class" else None,
            "header": d.header,
            "used_as": d.used_as,
            "used_methods": d.used_methods,
            "is_interface": d.is_interface or None,
            "construction": d.construction,
        }

    def _struct_entry(d):
        return {
            "qualified_name": d.qualified_name,
            "kind": d.kind if d.kind != "struct" else None,
            "header": d.header,
            "construction": d.construction,
        }

    dependencies_block = {
        "classes": [_class_entry(d) for d in deps.classes],
        "data_structures": [_struct_entry(d) for d in deps.data_structures],
        "free_functions": [
            {"qualified_name": f.qualified_name, "header": f.header, "signature": f.signature}
            for f in deps.free_functions
        ],
        "globals_read": [
            {"qualified_name": g.qualified_name, "header": g.header} for g in deps.globals_read
        ],
        "globals_written": [
            {"qualified_name": g.qualified_name, "header": g.header} for g in deps.globals_written
        ],
        "static_locals": [
            {"name": s.name, "type": s.type} for s in deps.static_locals
        ],
        "enums_referenced": [
            {"qualified_name": e.qualified_name, "header": e.header, "members_used": e.members_used}
            for e in deps.enums_referenced
        ],
    }

    call_graph = [
        {"target": c.target, "call_site_line": c.call_site_line,
         "in_branch": c.in_branch or None}
        for c in record.call_graph
    ]

    mocks = [
        {
            "target_class": mk.target_class,
            "status": mk.status,
            "mock_class": mk.mock_class,
            "header": mk.header,
            "verified_inheritance": mk.verified_inheritance,
            "resolved_by": mk.resolved_by,
            "suggested_pattern": mk.suggested_pattern,
            "suggested_path": mk.suggested_path,
            "gmock_stub_skeleton": mk.gmock_stub_skeleton,
        }
        for mk in record.mocks
    ]

    raw = {
        "method": method_block,
        "location": location_block,
        "complexity": complexity_block,
        "dependencies": dependencies_block,
        "call_graph": call_graph,
        "mocks": mocks,
    }
    return _prune(raw) or {}


import re as _re

_LINE_SUFFIX_RE = _re.compile(r":(\d+)$")


def _strip_line_suffix(header: str) -> str:
    """Strip a trailing ':<line>' from a `"path:line"` header string.

    Splitting on the FIRST colon breaks Windows absolute paths like
    `D:/proj/.../header.h:5` (where `D:` contains a colon). Use a regex
    anchored to the end so only the line-number suffix is removed.
    """
    return _LINE_SUFFIX_RE.sub("", header)


def _relative_header(abs_or_rel: str | None, workspace_root: Path) -> str | None:
    if abs_or_rel is None:
        return None
    # When analyzer fed us a "path:line" form, pass through.
    if ":" in abs_or_rel and not Path(abs_or_rel.split(":")[0]).is_absolute():
        return abs_or_rel
    try:
        return Path(abs_or_rel).relative_to(workspace_root).as_posix()
    except ValueError:
        return abs_or_rel


def _to_location(
    loc, workspace_root: Path
) -> Location | None:
    if loc is None:
        return None
    p = loc.path if hasattr(loc, "path") else Path(str(loc))
    try:
        rel = Path(p).resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        rel = Path(p).as_posix()
    return Location(path=rel, line=int(loc.line), column=int(getattr(loc, "column", 1)))


def _method_block(am: AnalyzedMethod) -> MethodBlock:
    params = [
        ParameterRecord(
            name=p.name,
            type=p.type,
            qualifiers=_classify_qualifiers(p.type),
            direction=p.direction,
            default_value=p.default_value,
        )
        for p in am.parameters
    ]
    return MethodBlock(
        qualified_name=am.qualified_name,
        **{"class": am.class_name},
        namespace=am.namespace,
        signature=am.signature,
        raw_signature=am.raw_signature,
        return_type=am.return_type,
        parameters=params,
        specifiers=Specifiers(**am.specifiers.__dict__),
        template_params=am.template_params,
        access=am.access,
        exception_spec=ExceptionSpec(**am.exception_spec.__dict__),
        defined_in_header=am.defined_in_header,
        is_header_only=am.defined_in_header and am.declaration is None,
        friends_of_class=am.friends_of_class,
    )


def _classify_qualifiers(type_str: str) -> list[str]:
    q = []
    if "const" in type_str:
        q.append("const")
    if type_str.rstrip().endswith("&"):
        q.append("ref")
    if type_str.rstrip().endswith("*"):
        q.append("ptr")
    return q


def _dependencies_block(am: AnalyzedMethod) -> DependenciesBlock:
    return DependenciesBlock(
        classes=sorted(
            (
                DependencyClassBlock(
                    qualified_name=d.qualified_name,
                    kind=d.kind,
                    header=d.header,
                    used_as=sorted(d.used_as),
                    used_methods=sorted(d.used_methods),
                    is_interface=d.is_interface,
                )
                for d in am.dep_classes
            ),
            key=lambda d: d.qualified_name,
        ),
        data_structures=sorted(
            (
                DependencyStructBlock(
                    qualified_name=d.qualified_name,
                    kind=d.kind,
                    header=d.header,
                    construction={
                        "aggregate": True,
                        "fields": d.fields,
                        "default_constructible": True,
                    } if d.fields else {},
                )
                for d in am.dep_data_structures
            ),
            key=lambda d: d.qualified_name,
        ),
        free_functions=sorted(
            (
                DependencyFunctionBlock(
                    qualified_name=f.qualified_name,
                    header=f.header,
                    signature=f.signature,
                )
                for f in am.dep_free_functions
            ),
            key=lambda f: f.qualified_name,
        ),
        globals_read=sorted(
            (GlobalRefBlock(qualified_name=g.qualified_name, header=g.header) for g in am.dep_globals_read),
            key=lambda g: g.qualified_name,
        ),
        globals_written=sorted(
            (GlobalRefBlock(qualified_name=g.qualified_name, header=g.header) for g in am.dep_globals_written),
            key=lambda g: g.qualified_name,
        ),
        static_locals=[StaticLocalBlock(name=s.name, type=s.type) for s in am.dep_static_locals],
        enums_referenced=sorted(
            (
                DependencyEnumBlock(
                    qualified_name=e.qualified_name,
                    header=e.header,
                    members_used=sorted(e.members_used),
                )
                for e in am.dep_enums
            ),
            key=lambda e: e.qualified_name,
        ),
        std_types=sorted(am.dep_std_types),
    )


def _test_hints(am: AnalyzedMethod, mocks: list[MockBlock]) -> TestHints:
    class_bare = am.class_name.rsplit("::", 1)[-1] if am.class_name else None
    fixture = f"{class_bare}Test" if class_bare else None
    includes: list[str] = []
    if am.definition:
        pass
    for dep in am.dep_classes:
        if dep.header:
            includes.append(_strip_line_suffix(dep.header))
    for d in am.dep_data_structures:
        if d.header:
            includes.append(_strip_line_suffix(d.header))
    for m in mocks:
        if m.header:
            includes.append(m.header)
    return TestHints(
        framework="gtest",
        suggested_fixture=fixture,
        required_includes=sorted(set(includes)),
        side_effects_observed=sorted({c.target for c in am.call_graph}),
        pure_function=(
            not am.dep_classes
            and not am.dep_globals_read
            and not am.dep_globals_written
            and not am.call_graph
        ),
        boundary_inputs=[],
    )


def _mock_blocks(mocks: Iterable[MockMatch]) -> list[MockBlock]:
    out = [
        MockBlock(
            target_class=m.target_class,
            status=m.status,
            mock_class=m.mock_class,
            header=m.header,
            framework=m.framework,
            verified_inheritance=m.verified_inheritance if m.status == "found" else None,
            resolved_by=m.resolved_by if m.status == "found" else None,
            suggested_pattern=m.suggested_pattern,
            suggested_path=m.suggested_path,
            gmock_stub_skeleton=m.gmock_stub_skeleton,
        )
        for m in mocks
    ]
    return sorted(out, key=lambda m: m.target_class)


def _complexity_block(lizard_match: LizardComplexity | None) -> Complexity | None:
    if lizard_match is None:
        return None
    return Complexity(
        cyclomatic=lizard_match.cyclomatic,
        nloc=lizard_match.nloc,
        token_count=lizard_match.token_count,
        parameter_count=lizard_match.parameter_count,
        source="lizard",
        match="signature",
    )


def _provenance(
    am: AnalyzedMethod,
    *,
    tool_versions: dict[str, str],
    input_fingerprint: str,
    warnings: list[str],
) -> Provenance:
    return Provenance(
        layers={
            "l0_msbuild": "msbuild" in am.sources,
            "l1_libclang": "libclang" in am.sources,
            "l2_tree_sitter": "tree-sitter" in am.sources,
            "l3_ctags": "ctags" in am.sources,
        },
        generated_at=_iso_now(),
        tool_versions=dict(sorted(tool_versions.items())),
        python_version=platform.python_version(),
        input_fingerprint=f"sha256:{input_fingerprint}",
        path_normalization=path_normalization_tag(),
        warnings=sorted(set(warnings)),
    )


def _output_path(
    output_dir: Path, customer: str, am: AnalyzedMethod, id_hex: str
) -> Path:
    parts = list(encode_namespace(am.namespace or ""))
    if am.class_name:
        parts.append(encode_component(am.class_name.rsplit("::", 1)[-1]))
    parts.append(f"{id_hex}.json")
    return output_dir / customer / "methods" / Path(*parts)


def emit_method(
    am: AnalyzedMethod,
    *,
    customer: str,
    output_dir: Path,
    workspace_root: Path,
    tool_versions: dict[str, str],
    input_fingerprint: str,
    lizard_match: LizardComplexity | None,
    mocks: list[MockMatch],
    warnings: list[str] | None = None,
) -> tuple[Path, MethodRecord]:
    id_hex = method_id(customer, am.qualified_name, am.signature)
    header_loc = _to_location(am.declaration, workspace_root)
    definition_loc = _to_location(am.definition, workspace_root)
    method_block = _method_block(am)
    dependencies_block = _dependencies_block(am)
    mock_blocks = _mock_blocks(mocks)
    test_hints = _test_hints(am, mock_blocks)
    record = MethodRecord(
        id=f"sha1:{id_hex}",
        method=method_block,
        location=LocationBlock(header=header_loc, definition=definition_loc, customer=customer),
        complexity=_complexity_block(lizard_match),
        dependencies=dependencies_block,
        call_graph=[
            CallSiteBlock(target=c.target, call_site_line=c.call_site_line, in_branch=c.in_branch)
            for c in sorted(am.call_graph, key=lambda c: (c.call_site_line, c.target))
        ],
        mocks=mock_blocks,
        test_hints=test_hints,
        provenance=_provenance(
            am,
            tool_versions=tool_versions,
            input_fingerprint=input_fingerprint,
            warnings=warnings or [],
        ),
    )

    out_path = _output_path(output_dir, customer, am, id_hex)
    # Per-method JSONs are emitted in lean form — tool metadata, id,
    # schema_version, and default/empty fields are omitted. The full
    # provenance block lives once per customer in index.json.
    write_json_deterministically(out_path, _compact_record(record))
    return out_path, record


def write_index(
    records: list[tuple[Path, MethodRecord]],
    *,
    customer: str,
    output_dir: Path,
    tool_versions: dict[str, str],
    input_fingerprint: str | None = None,
    warnings: list[str] | None = None,
) -> Path:
    customer_dir = output_dir / customer
    customer_dir.mkdir(parents=True, exist_ok=True)

    by_class: dict[str, list[str]] = {}
    by_method: dict[str, str] = {}
    by_mock: dict[str, list[str]] = {}

    # Track which layers contributed so consumers know what to expect.
    layer_tally: dict[str, int] = {
        "l0_msbuild": 0,
        "l1_libclang": 0,
        "l2_tree_sitter": 0,
        "l3_ctags": 0,
    }

    for path, record in records:
        cls = record.method.class_name or "_global_"
        by_class.setdefault(cls, []).append(record.id)
        try:
            rel = path.relative_to(customer_dir).as_posix()
        except ValueError:
            rel = path.as_posix()
        by_method[record.id] = rel
        for mock in record.mocks:
            if mock.status == "found" and mock.mock_class:
                by_mock.setdefault(mock.mock_class, []).append(mock.target_class)
        for layer, on in record.provenance.layers.items():
            if on:
                layer_tally[layer] = layer_tally.get(layer, 0) + 1

    for lst in by_class.values():
        lst.sort()
    for lst in by_mock.values():
        lst.sort()

    import platform as _pf

    payload = {
        "schema_version": "1.0",
        "customer": customer,
        "generated_at": _iso_now(),
        "tool_version": "0.1.0",
        "tool_versions": dict(sorted(tool_versions.items())),
        "python_version": _pf.python_version(),
        "input_fingerprint": f"sha256:{input_fingerprint}" if input_fingerprint else None,
        "path_normalization": (
            "win-lower-relative" if __import__("os").name == "nt" else "posix-relative"
        ),
        "layer_method_counts": layer_tally,
        "warnings": sorted(set(warnings or [])),
        "by_class": dict(sorted(by_class.items())),
        "by_method": dict(sorted(by_method.items())),
        "by_mock": dict(sorted(by_mock.items())),
    }
    # Drop null/empty — keep index lean too.
    payload = _prune(payload) or {}
    idx_path = customer_dir / "index.json"
    write_json_deterministically(idx_path, payload)
    return idx_path
