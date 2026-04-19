"""Fixed-output comparison for the 10 Path Encoding cases in the plan.

No round-tripping — the SHA-1 id is the authoritative key.
"""

from __future__ import annotations

import pytest

from methoddep.schema.paths import encode_component, encode_namespace


# Ten canonical cases pinned in the plan's §Path Encoding Algorithm.
PATH_ENCODING_CASES: list[tuple[str, str]] = [
    ("Bar", "Bar"),
    ("~Bar", "_dtor_Bar"),
    ("operator==", "operator__eq"),
    ("operator<<=", "operator__shleq"),
    ("operator[]", "operator__subscript"),
    ("operator int", "operator__cvt_int"),
    ('operator ""_km', "operator__udl_km"),
    ("Vec<int>", "Vec_lt_int_gt_"),
    ("Vec<int,Alloc<int>>", "Vec_lt_int_comma_Alloc_lt_int_gt__gt_"),
    ("Map<std::string,int>", "Map_lt_std_colon__colon_string_comma_int_gt_"),
]


@pytest.mark.parametrize(("component", "expected"), PATH_ENCODING_CASES)
def test_encode_component_fixed_outputs(component: str, expected: str) -> None:
    assert encode_component(component) == expected


def test_encode_component_empty() -> None:
    assert encode_component("") == ""


def test_encode_namespace_splits_on_top_level_colons() -> None:
    assert encode_namespace("foo::bar::Baz") == ["foo", "bar", "Baz"]


def test_encode_namespace_keeps_template_args_intact() -> None:
    # "::" inside <> must not be split.
    segments = encode_namespace("foo::Map<std::string,int>")
    assert segments == ["foo", "Map_lt_std_colon__colon_string_comma_int_gt_"]


def test_encode_namespace_empty_is_global() -> None:
    assert encode_namespace("") == ["_global_"]


def test_anonymous_namespace_goes_through_url_encoding() -> None:
    # "(anonymous namespace)" contains spaces + parens; operator/~ rules
    # do NOT apply. Spaces are collapsed to `_` in the punctuation pass;
    # parens are URL-encoded in Stage 2.
    assert encode_component("(anonymous namespace)") == "%28anonymous_namespace%29"


def test_conversion_operator_with_qualified_type() -> None:
    # Conversion type goes through recursive encoding.
    assert encode_component("operator std::string") == "operator__cvt_std_colon__colon_string"


def test_destructor_with_template() -> None:
    # "~Vec<int>" — Stage 1 dtor prefix, then template punctuation.
    assert encode_component("~Vec<int>") == "_dtor_Vec_lt_int_gt_"


def test_operator_comma() -> None:
    assert encode_component("operator,") == "operator__comma"


def test_operator_new_array() -> None:
    assert encode_component("operator new[]") == "operator__newarr"
