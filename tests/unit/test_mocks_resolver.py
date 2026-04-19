"""Mock resolver — real inheritance vs name-only decoy."""

from __future__ import annotations

from pathlib import Path

from methoddep.mocks import resolve_mocks
from methoddep.mocks.gmock_skeleton import VirtualMethodSpec, render_mock_class

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def test_resolve_real_mock_by_inheritance() -> None:
    root = FIXTURES / "gmock_pattern"
    results = resolve_mocks(
        ["net::IClient"],
        workspace_root=root,
        mock_dirs=["tests/mocks"],
        name_patterns=["Mock{Class}", "{Class}Mock", "Fake{Class}"],
    )
    assert len(results) == 1
    match = results[0]
    assert match.status == "found"
    assert match.verified_inheritance is True
    assert match.mock_class == "test::MockIClient"
    assert match.header and match.header.endswith("MockIClient.h")


def test_decoy_without_inheritance_rejected() -> None:
    # IClientV2 in the fixture has a class `MockIClientV2` that does NOT
    # inherit from IClient. Targeting a class named `IClientV2` should
    # return "missing", not a false match on name alone.
    root = FIXTURES / "gmock_pattern"
    results = resolve_mocks(
        ["net::IClientV2"],
        workspace_root=root,
        mock_dirs=["tests/mocks"],
        name_patterns=["Mock{Class}"],
    )
    assert results[0].status == "missing"


def test_missing_mock_gets_skeleton_when_virtuals_supplied() -> None:
    root = FIXTURES / "gmock_pattern"
    virtuals = {
        "net::INoMock": [
            {"name": "ping", "return_type": "bool", "args": "", "extras": "(override)"},
        ]
    }
    results = resolve_mocks(
        ["net::INoMock"],
        workspace_root=root,
        mock_dirs=["tests/mocks"],
        name_patterns=["Mock{Class}"],
        gmock_virtual_methods=virtuals,
    )
    match = results[0]
    assert match.status == "missing"
    assert match.gmock_stub_skeleton is not None
    assert "MOCK_METHOD(bool, ping" in match.gmock_stub_skeleton


def test_render_mock_class_shape() -> None:
    out = render_mock_class(
        "MockIService",
        "foo::IService",
        [
            VirtualMethodSpec(name="fetch", return_type="bool", args="std::string const& k"),
            VirtualMethodSpec(name="commit", return_type="void", args="int n", is_noexcept=True),
        ],
    )
    assert "class MockIService : public foo::IService" in out
    assert "MOCK_METHOD(bool, fetch," in out
    assert "noexcept, override" in out
