"""Stability of the method id hash across cosmetic signature variations."""

from __future__ import annotations

from methoddep.schema.hash import method_id, normalize_signature


def test_id_is_40_hex_chars() -> None:
    h = method_id("acme", "foo::Bar::doWork", "bool doWork(int, int)")
    assert len(h) == 40
    assert all(c in "0123456789abcdef" for c in h)


def test_id_stable_across_whitespace_variations() -> None:
    a = method_id("acme", "foo::Bar::f", "bool f(Config const& cfg)")
    b = method_id("acme", "foo::Bar::f", "bool f(Config  const  &  cfg)")
    c = method_id("acme", "foo::Bar::f", "bool f(Config const & cfg)")
    assert a == b == c


def test_id_stable_when_parameter_name_changes() -> None:
    a = method_id("acme", "foo::Bar::f", "void f(int x)")
    b = method_id("acme", "foo::Bar::f", "void f(int y)")
    assert a == b


def test_id_stable_when_default_value_removed() -> None:
    a = method_id("acme", "foo::Bar::f", "void f(int x = 0)")
    b = method_id("acme", "foo::Bar::f", "void f(int x)")
    assert a == b


def test_id_differs_across_customers() -> None:
    a = method_id("acme", "foo::Bar::f", "void f()")
    b = method_id("globex", "foo::Bar::f", "void f()")
    assert a != b


def test_id_differs_for_different_return_types() -> None:
    a = method_id("acme", "foo::Bar::f", "int f()")
    b = method_id("acme", "foo::Bar::f", "long f()")
    assert a != b


def test_id_differs_for_different_parameter_types() -> None:
    a = method_id("acme", "foo::Bar::f", "void f(int)")
    b = method_id("acme", "foo::Bar::f", "void f(long)")
    assert a != b


def test_id_differs_for_different_qualified_names() -> None:
    a = method_id("acme", "foo::Bar::f", "void f()")
    b = method_id("acme", "foo::Baz::f", "void f()")
    assert a != b


def test_normalize_collapses_const_ref_spacing() -> None:
    assert normalize_signature("bool f(Config  const  &)") == "bool f(Config const&)"


def test_normalize_strips_default_args() -> None:
    assert normalize_signature("void f(int x = 0, bool y = true)") == "void f(int, bool)"


def test_normalize_keeps_pointer_qualifier() -> None:
    assert normalize_signature("void f(char const *)") == "void f(char const*)"


def test_normalize_tightens_commas_and_parens() -> None:
    assert normalize_signature("void f( int a , int b )") == "void f(int, int)"


def test_id_stable_with_pointer_parameter_rename() -> None:
    # Pointer parameters: `ptr->` pre-class ensures regex matches.
    a = method_id("acme", "foo::Bar::f", "void f(int* ptr)")
    b = method_id("acme", "foo::Bar::f", "void f(int* other)")
    assert a == b
