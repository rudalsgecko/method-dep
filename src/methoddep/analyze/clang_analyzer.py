"""libclang-based L1 analyzer.

Parses a single translation unit and extracts, for every method
definition (and free-function definition) in the main file:
    - signature / parameters / return type
    - specifiers (virtual/static/const/noexcept/pure/...)
    - exception_spec (declared + body-observed throws)
    - dependencies: classes, structs, free functions, enums, globals,
      static locals, std types
    - ordered call graph with branch context

Access modifiers are tracked by walking the parent class cursor.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator

import clang.cindex as cc

from methoddep.analyze.models import (
    AnalyzedMethod,
    CallSite,
    DependencyClass,
    DependencyDataStruct,
    DependencyEnum,
    DependencyFunction,
    ExceptionSpec,
    GlobalRef,
    MethodSpecifiers,
    Parameter,
    SourceLoc,
    StaticLocal,
)

def _is_std_type(qname: str) -> bool:
    return qname.startswith("std::") or qname.startswith("__gnu_cxx::") or qname.startswith("__cxx")


def _is_public_std_type(head: str) -> bool:
    """Return False for internal std:: helpers like `std::_Vector_const_iterator`."""
    tail = head.split("::", 1)[1] if "::" in head else head
    return not tail.startswith("_") and not tail.startswith("__")


_ACCESS_MAP = {
    cc.AccessSpecifier.PUBLIC: "public",
    cc.AccessSpecifier.PROTECTED: "protected",
    cc.AccessSpecifier.PRIVATE: "private",
    cc.AccessSpecifier.NONE: "public",
    cc.AccessSpecifier.INVALID: "public",
}

_FUNCTION_KINDS = {
    cc.CursorKind.FUNCTION_DECL,
    cc.CursorKind.CXX_METHOD,
    cc.CursorKind.CONSTRUCTOR,
    cc.CursorKind.DESTRUCTOR,
    cc.CursorKind.CONVERSION_FUNCTION,
    cc.CursorKind.FUNCTION_TEMPLATE,
}

_CLASS_KINDS = {
    cc.CursorKind.CLASS_DECL,
    cc.CursorKind.STRUCT_DECL,
    cc.CursorKind.CLASS_TEMPLATE,
    cc.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
}

# Parse options.
_PARSE_OPTS = (
    cc.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
    | cc.TranslationUnit.PARSE_INCOMPLETE
)


def _is_main_file(cursor: cc.Cursor, main_path: Path) -> bool:
    loc_file = cursor.location.file
    if loc_file is None:
        return False
    try:
        return os.path.samefile(str(loc_file), str(main_path))
    except (OSError, FileNotFoundError):
        return str(loc_file).replace("\\", "/") == str(main_path).replace("\\", "/")


def _rel_header(file_path: str | None, workspace_root: Path | None, line: int) -> str | None:
    if file_path is None:
        return None
    p = Path(file_path)
    if workspace_root is not None:
        try:
            return f"{p.relative_to(workspace_root).as_posix()}:{line}"
        except ValueError:
            pass
    return f"{p.as_posix()}:{line}"


def _extract_specifiers(cursor: cc.Cursor) -> MethodSpecifiers:
    spec = MethodSpecifiers()
    if cursor.kind in _FUNCTION_KINDS:
        try:
            spec.static = bool(cursor.is_static_method())
        except Exception:
            spec.static = cursor.storage_class == cc.StorageClass.STATIC if hasattr(cursor, "storage_class") else False
        try:
            spec.virtual = bool(cursor.is_virtual_method()) or bool(cursor.is_pure_virtual_method())
        except Exception:
            spec.virtual = False
        try:
            spec.pure = bool(cursor.is_pure_virtual_method())
        except Exception:
            spec.pure = False
        try:
            spec.const = bool(cursor.is_const_method())
        except Exception:
            spec.const = False
        try:
            spec.defaulted = bool(cursor.is_default_method())
        except Exception:
            spec.defaulted = False
    # override/final/noexcept/inline/constexpr/deleted aren't all exposed
    # via python bindings; parse the display name / extent as a fallback.
    token_text = " ".join(t.spelling for t in cursor.get_tokens())
    spec.override = " override" in token_text
    spec.final = " final" in token_text
    spec.noexcept = "noexcept" in token_text
    spec.inline = token_text.startswith("inline ") or " inline " in token_text
    spec.constexpr = "constexpr" in token_text
    spec.deleted = "= delete" in token_text
    if "= default" in token_text:
        spec.defaulted = True
    return spec


def _namespace_chain(cursor: cc.Cursor) -> list[str]:
    chain: list[str] = []
    parent = cursor.semantic_parent
    while parent is not None and parent.kind != cc.CursorKind.TRANSLATION_UNIT:
        if parent.kind == cc.CursorKind.NAMESPACE:
            chain.append(parent.spelling or "_anon_")
        parent = parent.semantic_parent
    chain.reverse()
    return chain


def _owning_class(cursor: cc.Cursor) -> cc.Cursor | None:
    parent = cursor.semantic_parent
    while parent is not None and parent.kind != cc.CursorKind.TRANSLATION_UNIT:
        if parent.kind in _CLASS_KINDS:
            return parent
        parent = parent.semantic_parent
    return None


def _qualified_spelling(cursor: cc.Cursor) -> str:
    # Preferred: use Cursor.type.spelling for canonical full name; fall back
    # to walking the semantic parents.
    parts = [cursor.spelling]
    parent = cursor.semantic_parent
    while parent is not None and parent.kind != cc.CursorKind.TRANSLATION_UNIT:
        if parent.kind in _CLASS_KINDS or parent.kind == cc.CursorKind.NAMESPACE:
            name = parent.spelling or "_anon_"
            parts.append(name)
        parent = parent.semantic_parent
    parts.reverse()
    return "::".join(p for p in parts if p)


def _parameter_records(cursor: cc.Cursor) -> list[Parameter]:
    params: list[Parameter] = []
    for arg in cursor.get_arguments():
        ty = arg.type.spelling if arg.type else ""
        direction = "in"
        if ty.endswith("&") and "const" not in ty:
            direction = "in_out"
        elif ty.endswith("*") and "const" not in ty:
            direction = "in_out"
        default = None
        # Heuristic: tokens between `=` and the next `,`/`)`.
        tokens = [t.spelling for t in arg.get_tokens()]
        if "=" in tokens:
            idx = tokens.index("=")
            default = " ".join(tokens[idx + 1 :])
        params.append(Parameter(name=arg.spelling or "", type=ty, direction=direction, default_value=default))
    return params


def _friends_of_class(class_cursor: cc.Cursor | None) -> list[str]:
    if class_cursor is None:
        return []
    out: list[str] = []
    for c in class_cursor.get_children():
        if c.kind == cc.CursorKind.FRIEND_DECL:
            # Iterate children of the friend decl to find the named entity.
            for sub in c.get_children():
                if sub.spelling:
                    out.append(sub.spelling)
    return out


def _extract_declared_exceptions(cursor: cc.Cursor) -> list[str]:
    """Very conservative: scan the declaration tokens for `throw(...)` or
    `noexcept(...)` argument lists. Dynamic specs are rare in MSVC code
    but the code path must not crash."""
    tokens = [t.spelling for t in cursor.get_tokens()]
    if "throw" in tokens:
        # Take tokens between the matching parens right after `throw`.
        try:
            start = tokens.index("throw") + 1
            if tokens[start] == "(":
                depth = 1
                i = start + 1
                acc: list[str] = []
                while i < len(tokens) and depth > 0:
                    if tokens[i] == "(":
                        depth += 1
                    elif tokens[i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    else:
                        acc.append(tokens[i])
                    i += 1
                joined = " ".join(acc).replace(" ,", ",").replace(", ", ",")
                return [t for t in joined.split(",") if t]
        except (ValueError, IndexError):
            return []
    return []


def _is_inside(candidate: str | None, root: Path | None) -> bool:
    """True iff `candidate` is a path under `root` (string comparison,
    case-insensitive on Windows)."""
    if candidate is None or root is None:
        return False
    try:
        cand = Path(candidate).resolve()
    except (OSError, ValueError):
        cand = Path(candidate)
    try:
        root_abs = root.resolve()
    except (OSError, ValueError):
        root_abs = root
    cand_str = str(cand).replace("\\", "/")
    root_str = str(root_abs).replace("\\", "/").rstrip("/")
    if os.name == "nt":
        cand_str = cand_str.lower()
        root_str = root_str.lower()
    return cand_str == root_str or cand_str.startswith(root_str + "/")


class _BodyAnalyzer:
    """Walk a function body to collect dependencies and the call graph."""

    def __init__(
        self,
        main_path: Path,
        workspace_root: Path | None,
        scope_root: Path | None = None,
    ) -> None:
        self.main_path = main_path
        self.workspace_root = workspace_root
        self.scope_root = scope_root or workspace_root
        self.dep_classes: dict[str, DependencyClass] = {}
        self.dep_structs: dict[str, DependencyDataStruct] = {}
        self.dep_free_functions: dict[str, DependencyFunction] = {}
        self.dep_enums: dict[str, DependencyEnum] = {}
        self.globals_read: dict[str, GlobalRef] = {}
        self.globals_written: dict[str, GlobalRef] = {}
        self.static_locals: list[StaticLocal] = []
        self.std_types: set[str] = set()
        self.calls: list[CallSite] = []
        self.observed_throws: list[str] = []
        self._branch_stack: int = 0

    # --- type handling -------------------------------------------

    def _record_type(self, ty: cc.Type, used_as: str) -> None:
        if ty is None:
            return
        canonical = ty.get_canonical() if ty.kind != cc.TypeKind.INVALID else ty
        spelling = canonical.spelling
        if _is_std_type(spelling):
            head = spelling.split("<", 1)[0]
            if _is_public_std_type(head):
                self.std_types.add(head)
            # Do not descend into std types — skip class-dep registration.
            return
        decl = canonical.get_declaration()
        # Scope check — external (system/SDK/vendor) types are dropped.
        decl_file = decl.location.file.name if decl.location.file else None
        if self.scope_root is not None and not _is_inside(decl_file, self.scope_root):
            return
        if decl.kind in _CLASS_KINDS or decl.kind in {cc.CursorKind.CLASS_TEMPLATE, cc.CursorKind.UNION_DECL}:
            qname = _qualified_spelling(decl) or decl.spelling
            if not qname:
                return
            loc_file = decl.location.file.name if decl.location.file else None
            header = _rel_header(loc_file, self.workspace_root, decl.location.line)
            kind: str
            if decl.kind == cc.CursorKind.STRUCT_DECL:
                kind = "struct"
                bucket = self.dep_structs
                existing = bucket.get(qname)
                if existing is None:
                    fields = []
                    for child in decl.get_children():
                        if child.kind == cc.CursorKind.FIELD_DECL:
                            fields.append({"name": child.spelling, "type": child.type.spelling})
                    bucket[qname] = DependencyDataStruct(qualified_name=qname, kind="struct", header=header, fields=fields)
                return
            # class / union / template
            kind_str = "union" if decl.kind == cc.CursorKind.UNION_DECL else "class"
            existing = self.dep_classes.get(qname)
            if existing is None:
                is_interface = all(
                    child.kind != cc.CursorKind.CXX_METHOD or child.is_pure_virtual_method()
                    for child in decl.get_children()
                    if child.kind == cc.CursorKind.CXX_METHOD
                ) and any(
                    child.kind == cc.CursorKind.CXX_METHOD and child.is_pure_virtual_method()
                    for child in decl.get_children()
                )
                self.dep_classes[qname] = DependencyClass(
                    qualified_name=qname,
                    kind=("interface" if is_interface else kind_str),
                    header=header,
                    used_as={used_as},
                    is_interface=is_interface,
                )
            else:
                existing.used_as.add(used_as)
        elif decl.kind == cc.CursorKind.ENUM_DECL:
            qname = _qualified_spelling(decl) or decl.spelling
            loc_file = decl.location.file.name if decl.location.file else None
            header = _rel_header(loc_file, self.workspace_root, decl.location.line)
            self.dep_enums.setdefault(qname, DependencyEnum(qualified_name=qname, header=header))

    # --- cursor walk ---------------------------------------------

    def walk(self, cursor: cc.Cursor) -> None:
        for node in cursor.walk_preorder():
            self._handle(node)

    def _handle(self, node: cc.Cursor) -> None:
        kind = node.kind
        if kind in (cc.CursorKind.IF_STMT, cc.CursorKind.FOR_STMT, cc.CursorKind.WHILE_STMT,
                    cc.CursorKind.DO_STMT, cc.CursorKind.SWITCH_STMT, cc.CursorKind.CXX_CATCH_STMT,
                    cc.CursorKind.CASE_STMT):
            self._branch_stack += 1
        elif kind == cc.CursorKind.CALL_EXPR:
            referenced = node.referenced
            if referenced is not None and referenced.kind in _FUNCTION_KINDS:
                target = _qualified_spelling(referenced) or referenced.spelling
                ref_file = referenced.location.file.name if referenced.location.file else None
                in_scope = self.scope_root is None or _is_inside(ref_file, self.scope_root)
                # std:: usage is still useful as a hint even when the
                # declaration lives outside scope — route to std_types.
                if target and _is_std_type(target):
                    head = target.split("<", 1)[0]
                    if _is_public_std_type(head):
                        self.std_types.add(head)
                elif target and in_scope:
                    self.calls.append(
                        CallSite(target=target, call_site_line=node.location.line, in_branch=self._branch_stack > 0)
                    )
                if in_scope:
                    # Owning class of the call target → class dependency.
                    owner = _owning_class(referenced)
                    if owner is not None:
                        self._ensure_class_dep(owner, "call_target").used_methods.add(referenced.spelling or "")
                    elif referenced.kind == cc.CursorKind.FUNCTION_DECL:
                        loc_file = referenced.location.file.name if referenced.location.file else None
                        header = _rel_header(loc_file, self.workspace_root, referenced.location.line)
                        qname = _qualified_spelling(referenced) or referenced.spelling
                        self.dep_free_functions.setdefault(
                            qname,
                            DependencyFunction(
                                qualified_name=qname,
                                header=header,
                                signature=referenced.displayname,
                            ),
                        )
        elif kind == cc.CursorKind.TYPE_REF:
            self._record_type(node.type, "local")
        elif kind == cc.CursorKind.DECL_REF_EXPR:
            ref = node.referenced
            if ref is not None and ref.kind == cc.CursorKind.VAR_DECL and ref.semantic_parent and ref.semantic_parent.kind in {cc.CursorKind.TRANSLATION_UNIT, cc.CursorKind.NAMESPACE}:
                qname = _qualified_spelling(ref) or ref.spelling
                loc_file = ref.location.file.name if ref.location.file else None
                if self.scope_root is not None and not _is_inside(loc_file, self.scope_root):
                    return  # external global — drop
                if qname:
                    header = _rel_header(loc_file, self.workspace_root, ref.location.line)
                    # Very simple read/write heuristic: mark as read unless
                    # the parent is an assignment/compound-assignment.
                    parent = node.semantic_parent
                    # We cannot reliably inspect parent-expression here —
                    # classify as read by default. Write detection is best
                    # left to a secondary pass (future work).
                    self.globals_read.setdefault(qname, GlobalRef(qualified_name=qname, header=header))
        elif kind == cc.CursorKind.VAR_DECL:
            if node.storage_class == cc.StorageClass.STATIC and node.semantic_parent and node.semantic_parent.kind != cc.CursorKind.TRANSLATION_UNIT:
                self.static_locals.append(StaticLocal(name=node.spelling, type=node.type.spelling))
        elif kind == cc.CursorKind.CXX_THROW_EXPR:
            # The child TYPE_REF (if any) tells us the thrown type.
            for child in node.get_children():
                ty = child.type.spelling if child.type else ""
                if ty and ty not in self.observed_throws:
                    self.observed_throws.append(ty)
                    break

    def _ensure_class_dep(self, cursor: cc.Cursor, used_as: str) -> DependencyClass:
        qname = _qualified_spelling(cursor) or cursor.spelling
        if _is_std_type(qname):
            head = qname.split("<", 1)[0]
            if _is_public_std_type(head):
                self.std_types.add(head)
            return DependencyClass(qualified_name=qname, kind="class")
        decl_file = cursor.location.file.name if cursor.location.file else None
        if self.scope_root is not None and not _is_inside(decl_file, self.scope_root):
            # External class — return throwaway that never gets emitted.
            return DependencyClass(qualified_name=qname, kind="class")
        dep = self.dep_classes.get(qname)
        if dep is None:
            loc_file = cursor.location.file.name if cursor.location.file else None
            header = _rel_header(loc_file, self.workspace_root, cursor.location.line)
            is_interface = any(
                child.kind == cc.CursorKind.CXX_METHOD and child.is_pure_virtual_method()
                for child in cursor.get_children()
            )
            dep = DependencyClass(
                qualified_name=qname,
                kind=("interface" if is_interface else ("struct" if cursor.kind == cc.CursorKind.STRUCT_DECL else "class")),
                header=header,
                used_as={used_as},
                is_interface=is_interface,
            )
            self.dep_classes[qname] = dep
        else:
            dep.used_as.add(used_as)
        return dep


# --- top-level API ------------------------------------------------


def _iter_function_cursors(tu: cc.TranslationUnit, main_path: Path) -> Iterator[cc.Cursor]:
    for cursor in tu.cursor.walk_preorder():
        if cursor.kind not in _FUNCTION_KINDS:
            continue
        if not _is_main_file(cursor, main_path):
            continue
        if not cursor.is_definition():
            continue
        yield cursor


def analyze_file(
    path: Path,
    *,
    include_dirs: Iterable[Path] | None = None,
    defines: Iterable[str] | None = None,
    extra_args: Iterable[str] | None = None,
    workspace_root: Path | None = None,
    scope_root: Path | None = None,
) -> list[AnalyzedMethod]:
    """Analyze one translation unit and return the methods it defines.

    `scope_root` is the broader project root. Dependencies whose source
    lives outside it are treated as external (system/SDK/vendor) and
    filtered from the returned records. Defaults to `workspace_root`.

    Unparseable TUs return an empty list; callers fall back to L2.
    """
    args: list[str] = ["-x", "c++", "-std=c++20"]
    if extra_args:
        args.extend(extra_args)
    else:
        args += ["-fms-extensions", "-fms-compatibility", "-fdelayed-template-parsing"]
    for inc in include_dirs or []:
        args.append(f"-I{Path(inc)}")
    for d in defines or []:
        args.append(f"-D{d}")

    index = cc.Index.create()
    try:
        tu = index.parse(str(path), args=args, options=_PARSE_OPTS)
    except cc.TranslationUnitLoadError:
        return []

    methods: list[AnalyzedMethod] = []
    for cursor in _iter_function_cursors(tu, path):
        methods.append(_build_method(cursor, path, workspace_root, scope_root))
    return methods


def _build_method(
    cursor: cc.Cursor,
    main_path: Path,
    workspace_root: Path | None,
    scope_root: Path | None = None,
) -> AnalyzedMethod:
    class_cursor = _owning_class(cursor)
    namespace = "::".join(_namespace_chain(cursor)) or None
    class_q = _qualified_spelling(class_cursor) if class_cursor else None
    qualified_name = _qualified_spelling(cursor)

    return_type = cursor.result_type.spelling if cursor.result_type.kind != cc.TypeKind.INVALID else None
    params = _parameter_records(cursor)
    signature = cursor.displayname or cursor.spelling
    raw_signature = cursor.type.spelling if cursor.type else signature

    specifiers = _extract_specifiers(cursor)
    access = _ACCESS_MAP.get(cursor.access_specifier, "public")

    body_analyzer = _BodyAnalyzer(main_path, workspace_root, scope_root=scope_root)

    # Parameter and return types count as dependencies even when they
    # don't appear in the body. Walk them explicitly before the body.
    for child in cursor.get_children():
        if child.kind == cc.CursorKind.PARM_DECL:
            # Record the parameter's declared type directly...
            if child.type.kind != cc.TypeKind.INVALID:
                body_analyzer._record_type(child.type, "parameter")
            # ...and any TYPE_REF descendants (template args, nested types).
            for node in child.walk_preorder():
                if node.kind == cc.CursorKind.TYPE_REF:
                    body_analyzer._record_type(node.type, "parameter")
    if cursor.result_type is not None and cursor.result_type.kind != cc.TypeKind.INVALID:
        body_analyzer._record_type(cursor.result_type, "return")

    for child in cursor.get_children():
        if child.kind in {cc.CursorKind.COMPOUND_STMT, cc.CursorKind.CXX_TRY_STMT, cc.CursorKind.NULL_STMT}:
            body_analyzer.walk(child)

    exception_spec = ExceptionSpec(
        declared=_extract_declared_exceptions(cursor),
        observed_throws=sorted(set(body_analyzer.observed_throws)),
    )

    is_header = main_path.suffix in {".h", ".hpp", ".hh", ".hxx", ".H"}

    return AnalyzedMethod(
        qualified_name=qualified_name,
        class_name=class_q,
        namespace=namespace,
        signature=f"{return_type + ' ' if return_type else ''}{qualified_name}({', '.join(p.type for p in params)})".strip(),
        raw_signature=raw_signature,
        return_type=return_type,
        parameters=params,
        specifiers=specifiers,
        access=access,  # type: ignore[arg-type]
        declaration=None,
        definition=SourceLoc(path=Path(str(cursor.location.file)), line=cursor.location.line, column=cursor.location.column),
        defined_in_header=is_header,
        exception_spec=exception_spec,
        friends_of_class=_friends_of_class(class_cursor),
        dep_classes=sorted(body_analyzer.dep_classes.values(), key=lambda d: d.qualified_name),
        dep_data_structures=sorted(body_analyzer.dep_structs.values(), key=lambda d: d.qualified_name),
        dep_free_functions=sorted(body_analyzer.dep_free_functions.values(), key=lambda d: d.qualified_name),
        dep_enums=sorted(body_analyzer.dep_enums.values(), key=lambda d: d.qualified_name),
        dep_globals_read=sorted(body_analyzer.globals_read.values(), key=lambda g: g.qualified_name),
        dep_globals_written=sorted(body_analyzer.globals_written.values(), key=lambda g: g.qualified_name),
        dep_static_locals=list(body_analyzer.static_locals),
        dep_std_types=sorted(body_analyzer.std_types),
        call_graph=sorted(body_analyzer.calls, key=lambda c: (c.call_site_line, c.target)),
        sources=["libclang"],
    )
