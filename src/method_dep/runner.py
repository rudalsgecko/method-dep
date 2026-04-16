"""Test execution and coverage measurement."""
from __future__ import annotations
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config


@dataclass
class TestResult:
    compiled: bool = False
    passed: bool = False
    coverage: float = 0.0
    error_message: str = ""
    output: str = ""


class TestRunner:
    """Run Google Test executables and measure coverage with OpenCppCoverage."""

    def __init__(self, config: Config):
        self.config = config
        self.build_dir = config.project_root / config.cmake_build_dir

    def compile_test(self, test_file: Path, output_exe: Optional[Path] = None) -> TestResult:
        """Compile a single test file using the project's build system."""
        result = TestResult()

        if output_exe is None:
            output_exe = test_file.with_suffix(".exe" if self._is_windows() else "")

        # Strategy 1: Use custom build command if specified
        if self.config.test_build_command:
            return self._compile_custom(test_file, output_exe, result)

        # Strategy 2: Try CMake integration
        if (self.build_dir / "CMakeCache.txt").exists():
            return self._compile_cmake(test_file, output_exe, result)

        # Strategy 3: Direct compilation with compiler flags from compile_commands.json
        return self._compile_direct(test_file, output_exe, result)

    def run_test(self, test_exe: Path) -> TestResult:
        """Run a compiled test executable."""
        result = TestResult(compiled=True)

        if not test_exe.exists():
            result.error_message = f"Test executable not found: {test_exe}"
            return result

        try:
            proc = subprocess.run(
                [str(test_exe), "--gtest_output=json"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(self.config.project_root),
            )

            result.output = proc.stdout + proc.stderr
            result.passed = proc.returncode == 0

            if not result.passed:
                # Extract failure info
                result.error_message = self._extract_gtest_failures(result.output)

        except subprocess.TimeoutExpired:
            result.error_message = "Test execution timed out (120s)"
        except Exception as e:
            result.error_message = str(e)

        return result

    def measure_coverage(self, test_exe: Path, source_file: str) -> float:
        """Measure code coverage using OpenCppCoverage."""
        if not test_exe.exists():
            return 0.0

        coverage_dir = self.config.output_root / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)
        coverage_xml = coverage_dir / f"{test_exe.stem}_coverage.xml"

        try:
            cmd = [
                self.config.opencppcoverage_path,
                "--sources", str(self.config.project_root / source_file),
                "--export_type", f"cobertura:{coverage_xml}",
                "--", str(test_exe),
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(self.config.project_root),
            )

            if coverage_xml.exists():
                return self._parse_cobertura_coverage(coverage_xml, source_file)

            # Try parsing from stdout as fallback
            return self._parse_coverage_stdout(proc.stdout)

        except FileNotFoundError:
            print(f"  [WARN] OpenCppCoverage not found: {self.config.opencppcoverage_path}")
            return 0.0
        except subprocess.TimeoutExpired:
            print("  [WARN] Coverage measurement timed out")
            return 0.0
        except Exception as e:
            print(f"  [WARN] Coverage measurement failed: {e}")
            return 0.0

    def compile_and_run(self, test_file: Path, source_file: str) -> TestResult:
        """Full pipeline: compile, run, measure coverage."""
        test_exe = test_file.with_suffix(".exe" if self._is_windows() else "")

        # Compile
        compile_result = self.compile_test(test_file, test_exe)
        if not compile_result.compiled:
            return compile_result

        # Run
        run_result = self.run_test(test_exe)
        if not run_result.passed:
            return run_result

        # Coverage
        coverage = self.measure_coverage(test_exe, source_file)
        run_result.coverage = coverage

        return run_result

    def _compile_custom(self, test_file: Path, output_exe: Path, result: TestResult) -> TestResult:
        """Compile using user-specified command."""
        cmd = self.config.test_build_command.format(
            test_file=test_file,
            output=output_exe,
            project_root=self.config.project_root,
            build_dir=self.build_dir,
        )
        try:
            proc = subprocess.run(
                cmd, shell=True,
                capture_output=True, text=True,
                timeout=120, cwd=str(self.config.project_root),
            )
            result.compiled = proc.returncode == 0
            if not result.compiled:
                result.error_message = proc.stderr[:2000]
            result.output = proc.stdout
        except Exception as e:
            result.error_message = str(e)
        return result

    def _compile_cmake(self, test_file: Path, output_exe: Path, result: TestResult) -> TestResult:
        """Compile by adding test to CMake build."""
        # Generate a minimal CMakeLists.txt for this test
        test_cmake = test_file.parent / "CMakeLists_test.txt"
        rel_test = test_file.relative_to(self.config.project_root) if test_file.is_relative_to(self.config.project_root) else test_file

        cmake_content = f"""
cmake_minimum_required(VERSION 3.14)
add_executable({test_file.stem} {rel_test})
target_link_libraries({test_file.stem} PRIVATE gtest gtest_main gmock)
"""
        # Fallback to direct compilation if CMake integration is complex
        return self._compile_direct(test_file, output_exe, result)

    def _compile_direct(self, test_file: Path, output_exe: Path, result: TestResult) -> TestResult:
        """Compile directly using g++/clang++ with flags from compile_commands.json."""
        compiler = "g++"
        flags = ["-std=c++17", "-g", "-O0"]
        include_dirs = set()

        # Extract flags from compile_commands.json
        cc_path = self.config.compile_commands_path
        if cc_path.exists():
            try:
                cc = json.loads(cc_path.read_text(encoding="utf-8"))
                if cc:
                    # Get include dirs and flags from first entry
                    for entry in cc:
                        cmd_str = entry.get("command", "") or " ".join(entry.get("arguments", []))
                        # Extract -I flags
                        for m in re.finditer(r'-I\s*(\S+)', cmd_str):
                            include_dirs.add(m.group(1))
                        # Detect compiler
                        if "clang" in cmd_str:
                            compiler = "clang++"
                        # Extract -std flag
                        std_match = re.search(r'-std=(\S+)', cmd_str)
                        if std_match:
                            flags = [f"-std={std_match.group(1)}"] + [f for f in flags if not f.startswith("-std=")]
                        break
            except Exception:
                pass

        cmd = [compiler] + flags
        for inc in include_dirs:
            cmd.extend(["-I", inc])
        cmd.extend([
            "-I", str(self.config.project_root),
            str(test_file),
            "-lgtest", "-lgtest_main", "-lgmock", "-lpthread",
            "-o", str(output_exe),
        ])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=120, cwd=str(self.config.project_root),
            )
            result.compiled = proc.returncode == 0
            if not result.compiled:
                result.error_message = (proc.stdout + proc.stderr)[:2000]
            result.output = proc.stdout
        except FileNotFoundError:
            result.error_message = f"Compiler not found: {compiler}"
        except Exception as e:
            result.error_message = str(e)

        return result

    def _parse_cobertura_coverage(self, xml_path: Path, source_file: str) -> float:
        """Parse Cobertura XML to get line coverage for a specific file."""
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # Find the package/class matching our source file
            for pkg in root.findall(".//package"):
                for cls in pkg.findall("classes/class"):
                    filename = cls.get("filename", "")
                    if source_file in filename or filename in source_file:
                        line_rate = cls.get("line-rate", "0")
                        return float(line_rate) * 100.0

            # Fallback: overall coverage
            overall = root.get("line-rate", "0")
            return float(overall) * 100.0

        except Exception:
            return 0.0

    def _parse_coverage_stdout(self, stdout: str) -> float:
        """Parse coverage percentage from OpenCppCoverage stdout."""
        # Look for pattern like "Overall coverage: 75%"
        match = re.search(r'(?:Overall|Total).*?(\d+(?:\.\d+)?)\s*%', stdout)
        if match:
            return float(match.group(1))
        return 0.0

    def _extract_gtest_failures(self, output: str) -> str:
        """Extract failure messages from gtest output."""
        failures = []
        for line in output.split("\n"):
            if "[  FAILED  ]" in line or "FAILED" in line or "error:" in line.lower():
                failures.append(line.strip())
        return "\n".join(failures[:20]) if failures else output[-1000:]

    def _is_windows(self) -> bool:
        import sys
        return sys.platform == "win32"
