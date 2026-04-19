"""tree-sitter-cpp based symbol index.

Parses `.h`/`.hpp`/`.cpp`/`.cc` files and yields `IndexedSymbol` and
`IndexedMethod` records. Tracks namespace and class context via an
explicit stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from tree_sitter import Language, Node, Parser
import tree_sitter_cpp as _ts_cpp

from methoddep.index.models import IndexedMethod, IndexedSymbol, Location

_LANGUAGE = Language(_ts_cpp.language())
_PARSER = Parser(_LANGUAGE)

_HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".H"}
_SOURCE_SUFFIXES = {".cpp", ".cc", ".cxx", ".c++"}
_ALL_SUFFIXES = _HEADER_SUFFIXES | _SOURCE_SUFFIXES


def is_indexable(path: Path) -> bool:
    return path.suffix in _ALL_SUFFIXES


def _is_header(path: Path) -> bool:
    return path.suffix in _HEADER_SUFFIXES


@dataclass
class _Ctx:
    namespaces: list[str]
    classes: list[str]
    access: str = "public"

    def push_namespace(self, name: str) -> None:
        self.namespaces.append(name)

    def pop_namespace(self) -> None:
        self.namespaces.pop()

    def push_class(self, name: str) -> None:
        self.classes.append(name)

    def pop_class(self) -> None:
        self.classes.pop()

    @property
    def current_namespace(self) -> str | None:
        return "::".join(self.namespaces) if self.namespaces else None

    @property
    def current_class(self) -> str | None:
        return "::".join(self.classes) if self.classes else None

    def qualify(self, name: str) -> str:
        parts = [p for p in [self.current_namespace, self.current_class, name] if p]
        return "::".join(parts)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _location(node: Node, path: Path) -> Location:
    # tree-sitter uses 0-based (row, col); methoddep uses 1-based lines.
    return Location(path=path, line=node.start_point[0] + 1, column=node.start_point[1] + 1)


def _child_of_type(node: Node, type_name: str) -> Node | None:
    for c in node.named_children:
        if c.type == type_name:
            return c
    return None


def _children_of_type(node: Node, type_name: str) -> list[Node]:
    return [c for c in node.named_children if c.type == type_name]


def _parameter_records(param_list: Node, source: bytes) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for pd in _children_of_type(param_list, "parameter_declaration"):
        type_tokens: list[str] = []
        name = ""
        for c in pd.named_children:
            text = _node_text(c, source)
            if c.type in {"identifier"}:
                # bare identifier inside declarator — treat as name
                name = text
            elif c.type in {"pointer_declarator", "reference_declarator"}:
                ident = _child_of_type(c, "identifier")
                if ident:
                    name = _node_text(ident, source)
                # Keep the leading `*`/`&` in the type.
                prefix = source[c.start_byte : (ident.start_byte if ident else c.end_byte)].decode("utf-8", errors="replace").strip()
                type_tokens.append(prefix)
            elif c.type in {"abstract_pointer_declarator", "abstract_reference_declarator"}:
                type_tokens.append(_node_text(c, source))
            else:
                type_tokens.append(text)
        type_str = " ".join(t for t in type_tokens if t).strip()
        records.append({"name": name, "type": type_str})
    return records


def _extract_name_and_parent(declarator: Node, source: bytes) -> tuple[str, str | None]:
    """Return (bare_name, parent_class_if_qualified) from a function_declarator."""
    for c in declarator.named_children:
        if c.type == "qualified_identifier":
            ns_id = _child_of_type(c, "namespace_identifier")
            # Drill down to the rightmost identifier/destructor/operator.
            last = c
            while True:
                nested = _child_of_type(last, "qualified_identifier")
                if nested is None:
                    break
                last = nested
            # The name is the last non-qualifier child.
            name_node: Node | None = None
            for sub in last.named_children:
                if sub.type in {
                    "identifier",
                    "destructor_name",
                    "operator_name",
                    "field_identifier",
                }:
                    name_node = sub
            name = _node_text(name_node, source) if name_node else _node_text(last, source)
            parent = _node_text(ns_id, source) if ns_id else None
            return name, parent
        if c.type in {"identifier", "field_identifier"}:
            return _node_text(c, source), None
        if c.type == "destructor_name":
            return _node_text(c, source), None
        if c.type == "operator_name":
            return _node_text(c, source), None
    return _node_text(declarator, source), None


def _declarator_signature(
    return_type: str | None, qualified_name: str, param_list_text: str,
    trailing_specifiers: str = "",
) -> str:
    head = f"{return_type} " if return_type else ""
    spec = f" {trailing_specifiers}".rstrip() if trailing_specifiers else ""
    return f"{head}{qualified_name}{param_list_text}{spec}".strip()


class _Walker:
    def __init__(self, path: Path, source: bytes) -> None:
        self.path = path
        self.source = source
        self.ctx = _Ctx(namespaces=[], classes=[])
        self.symbols: list[IndexedSymbol] = []
        self.methods: list[IndexedMethod] = []
        self._is_header = _is_header(path)

    def walk(self, root: Node) -> None:
        self._visit(root)

    # --- dispatch --------------------------------------------------

    def _visit(self, node: Node) -> None:
        handler = getattr(self, f"_on_{node.type}", None)
        if handler is not None:
            handler(node)
            return
        for child in node.named_children:
            self._visit(child)

    # --- containers ------------------------------------------------

    def _on_namespace_definition(self, node: Node) -> None:
        name_node = _child_of_type(node, "namespace_identifier")
        name = _node_text(name_node, self.source) if name_node else "_anon_"
        self.ctx.push_namespace(name)
        self.symbols.append(
            IndexedSymbol(
                name=name,
                qualified_name=self.ctx.current_namespace or name,
                kind="namespace",
                location=_location(node, self.path),
                namespace=self.ctx.current_namespace,
            )
        )
        try:
            decls = _child_of_type(node, "declaration_list")
            if decls:
                for child in decls.named_children:
                    self._visit(child)
        finally:
            self.ctx.pop_namespace()

    def _handle_class_like(self, node: Node, kind: str) -> None:
        name_node = _child_of_type(node, "type_identifier")
        if name_node is None:
            return  # forward decls / anonymous
        name = _node_text(name_node, self.source)
        qualified = self.ctx.qualify(name)
        self.symbols.append(
            IndexedSymbol(
                name=name,
                qualified_name=qualified,
                kind="class" if kind == "class" else "struct",
                location=_location(node, self.path),
                namespace=self.ctx.current_namespace,
                parent_class=self.ctx.current_class,
            )
        )
        body = _child_of_type(node, "field_declaration_list")
        if body is None:
            return
        self.ctx.push_class(name)
        prev_access = self.ctx.access
        self.ctx.access = "public" if kind == "struct" else "private"
        try:
            for child in body.named_children:
                self._visit(child)
        finally:
            self.ctx.access = prev_access
            self.ctx.pop_class()

    def _on_class_specifier(self, node: Node) -> None:
        self._handle_class_like(node, "class")

    def _on_struct_specifier(self, node: Node) -> None:
        self._handle_class_like(node, "struct")

    def _on_access_specifier(self, node: Node) -> None:
        text = _node_text(node, self.source).strip().rstrip(":").strip()
        if text in {"public", "protected", "private"}:
            self.ctx.access = text

    # --- methods / functions --------------------------------------

    def _on_field_declaration(self, node: Node) -> None:
        # Inside a class body: method declaration.
        declarator = self._find_function_declarator(node)
        if declarator is None:
            return
        self._record_method(node, declarator, is_definition=False)

    def _on_declaration(self, node: Node) -> None:
        # Inside a class body we sometimes get `declaration` for ctors/dtors.
        declarator = self._find_function_declarator(node)
        if declarator is None:
            return
        if self.ctx.current_class is not None:
            self._record_method(node, declarator, is_definition=False)
        else:
            self._record_method(node, declarator, is_definition=False)

    def _on_function_definition(self, node: Node) -> None:
        declarator = self._find_function_declarator(node)
        if declarator is None:
            return
        self._record_method(node, declarator, is_definition=True)

    def _on_template_declaration(self, node: Node) -> None:
        # Dive into the templated entity.
        for child in node.named_children:
            if child.type != "template_parameter_list":
                self._visit(child)

    # --- helpers ---------------------------------------------------

    def _find_function_declarator(self, node: Node) -> Node | None:
        """Find the innermost function_declarator within a
        field_declaration/declaration/function_definition."""
        for child in node.named_children:
            if child.type == "function_declarator":
                return child
            # Pointer/reference-return wrappers.
            if child.type in {"pointer_declarator", "reference_declarator"}:
                inner = self._find_function_declarator(child)
                if inner is not None:
                    return inner
        return None

    def _record_method(self, outer: Node, declarator: Node, *, is_definition: bool) -> None:
        name, qualifier = _extract_name_and_parent(declarator, self.source)
        if not name:
            return
        param_list = _child_of_type(declarator, "parameter_list")
        params = _parameter_records(param_list, self.source) if param_list else []
        param_text = _node_text(param_list, self.source) if param_list else "()"

        # Return type: whatever TYPE-CLASS children appear before the
        # declarator at the outer level. Exclude specifiers, initializers,
        # bodies, and the pure-virtual `= 0` clause so we don't collect
        # noise like `= 0` / `= default` into the return type string.
        return_type = None
        specifiers: list[str] = []
        _TYPE_NODE_TYPES = {
            "primitive_type",
            "type_identifier",
            "qualified_identifier",
            "sized_type_specifier",
            "template_type",
            "auto",
            "placeholder_type_specifier",
            "type_descriptor",
            "dependent_type",
        }
        for c in outer.named_children:
            if c is declarator:
                continue
            if c.type == "storage_class_specifier":
                specifiers.append(_node_text(c, self.source))
                continue
            if c.type in _TYPE_NODE_TYPES:
                token = _node_text(c, self.source).strip()
                if token:
                    return_type = token if return_type is None else f"{return_type} {token}"

        # Trailing const / virtual / override detection from outer text.
        outer_text = _node_text(outer, self.source)
        is_virtual = "virtual" in outer_text.split(name)[0]
        is_pure = "= 0" in outer_text
        is_static = any(s == "static" for s in specifiers)
        # const member function: the `const` token appears after the
        # parameter list and before `;` or `{`.
        after_params = outer_text.split(param_text, 1)[-1]
        is_const_method = after_params.strip().startswith("const")

        # Determine class context.
        class_name = qualifier or self.ctx.current_class
        namespace = self.ctx.current_namespace
        if qualifier and self.ctx.current_class is None:
            # Out-of-class definition like `Bar::doWork` inside namespace.
            class_qualified = f"{namespace}::{qualifier}" if namespace else qualifier
        else:
            class_qualified = (
                f"{namespace}::{class_name}" if namespace and class_name else class_name
            )

        qualified_parts = [namespace, class_qualified.split("::")[-1] if class_qualified else None, name]
        qualified_name = "::".join(p for p in qualified_parts if p)

        # signature (tree-sitter level — libclang will provide a richer one later)
        trailing = "const" if is_const_method else ""
        signature = _declarator_signature(return_type, name, param_text, trailing).strip()

        loc = _location(outer, self.path)

        method = IndexedMethod(
            qualified_name=qualified_name,
            signature=signature,
            parameters=params,
            return_type=return_type,
            class_name=class_qualified,
            namespace=namespace,
            declaration=loc if not is_definition else None,
            definition=loc if is_definition else None,
            is_virtual=is_virtual,
            is_pure=is_pure,
            is_static=is_static,
            is_const=is_const_method,
            access=self.ctx.access,  # type: ignore[arg-type]
            defined_in_header=is_definition and self._is_header,
            sources=["tree-sitter"],
        )
        self.methods.append(method)
        self.symbols.append(
            IndexedSymbol(
                name=name,
                qualified_name=qualified_name,
                kind=(
                    "method_def" if is_definition and class_qualified
                    else ("method_decl" if class_qualified else "free_function")
                ),
                location=loc,
                namespace=namespace,
                parent_class=class_qualified,
            )
        )


def parse_file(path: Path) -> tuple[list[IndexedSymbol], list[IndexedMethod]]:
    source = path.read_bytes()
    if source.startswith(b"\xef\xbb\xbf"):
        source = source[3:]
    tree = _PARSER.parse(source)
    walker = _Walker(path, source)
    walker.walk(tree.root_node)
    return walker.symbols, walker.methods


def iter_source_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and is_indexable(path):
            # Skip .git and anything under .git inside the workspace.
            if ".git" in path.parts:
                continue
            yield path


def index_tree(root: Path) -> tuple[list[IndexedSymbol], list[IndexedMethod]]:
    all_symbols: list[IndexedSymbol] = []
    all_methods: list[IndexedMethod] = []
    for path in iter_source_files(root):
        symbols, methods = parse_file(path)
        all_symbols.extend(symbols)
        all_methods.extend(methods)
    return all_symbols, all_methods
