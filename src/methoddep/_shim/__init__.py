"""Bundled native-tooling shims.

Currently just `binlog2xml` — a tiny C# program that wraps
`MSBuild.StructuredLogger` so we can convert a `.binlog` → XML from
Python without depending on any dotnet global tool (none of which
expose this conversion).
"""
