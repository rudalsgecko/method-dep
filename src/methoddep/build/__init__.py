"""L0 build-intel layer — MSBuild binlog + /showIncludes parsing."""

from methoddep.build.showincludes_parser import parse_showincludes_log, ShowIncludesRecord
from methoddep.build.binlog_parser import parse_binlog_xml, read_binlog_xml, BuildIntel
from methoddep.build.textlog_parser import parse_msbuild_text_log, read_msbuild_text_log
from methoddep.build.msbuild_driver import (
    export_binlog_xml,
    find_msbuild,
    run_msbuild_with_binlog,
    structured_logger_cli_available,
)

__all__ = [
    "parse_showincludes_log",
    "ShowIncludesRecord",
    "parse_binlog_xml",
    "read_binlog_xml",
    "parse_msbuild_text_log",
    "read_msbuild_text_log",
    "BuildIntel",
    "export_binlog_xml",
    "find_msbuild",
    "run_msbuild_with_binlog",
    "structured_logger_cli_available",
]
