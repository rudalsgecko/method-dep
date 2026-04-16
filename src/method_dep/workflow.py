"""Main workflow orchestration: scan -> analyze -> generate -> test -> coverage loop."""
from __future__ import annotations
import time
from pathlib import Path
from typing import Optional

from .config import Config
from .extractor import extract_all_methods
from .dependency import DependencyAnalyzer
from .context import generate_context_markdown, save_context
from .tracker import TestTracker
from .llm import LLMCaller
from .runner import TestRunner
from .models import MethodInfo, MethodContext


class Workflow:
    """Orchestrate the full test generation workflow."""

    def __init__(self, config: Config):
        self.config = config
        self.tracker = TestTracker(config.methods_json)
        self.analyzer: Optional[DependencyAnalyzer] = None
        self.llm = LLMCaller(config)
        self.runner = TestRunner(config)

    # ──────────────────────────────────────────────
    # Phase 1: Scan
    # ──────────────────────────────────────────────
    def scan(self) -> int:
        """Scan project, extract methods, register in tracker."""
        print(f"\n[SCAN] Scanning {self.config.project_root} ...")
        self.config.ensure_dirs()

        all_ext = self.config.source_extensions + self.config.header_extensions
        methods = extract_all_methods(
            self.config.project_root,
            all_ext,
            self.config.exclude_patterns,
        )
        print(f"  Found {len(methods)} methods/functions")

        # Filter: skip trivial methods (getters/setters, empty bodies)
        methods = [m for m in methods if _is_testable(m)]
        print(f"  {len(methods)} testable methods after filtering")

        new_count = self.tracker.register_methods(methods)
        self.tracker.save()
        print(f"  Registered {new_count} new methods ({len(self.tracker.get_all())} total)")

        return len(methods)

    # ──────────────────────────────────────────────
    # Phase 2: Analyze dependencies
    # ──────────────────────────────────────────────
    def analyze(self) -> int:
        """Analyze dependencies and generate context MDs for all methods."""
        print(f"\n[ANALYZE] Building dependency graph ...")
        self.config.ensure_dirs()

        self.analyzer = DependencyAnalyzer(
            self.config.project_root,
            self.config.compile_commands_path if self.config.compile_commands_path.exists() else None,
        )
        self.analyzer.build_symbol_table()

        # Re-extract methods to get full MethodInfo objects
        all_ext = self.config.source_extensions + self.config.header_extensions
        methods = extract_all_methods(
            self.config.project_root,
            all_ext,
            self.config.exclude_patterns,
        )
        methods = [m for m in methods if _is_testable(m)]

        # Build method lookup
        method_map = {m.method_id: m for m in methods}

        count = 0
        for status in self.tracker.get_all():
            method = method_map.get(status.method_id)
            if method is None:
                continue

            print(f"  Analyzing: {method.qualified_name}")
            deps = self.analyzer.analyze_method(method)

            # Filter external deps if configured
            if self.config.skip_external_deps:
                deps = [d for d in deps if not d.is_external]

            mock_candidates = self.analyzer.find_mock_candidates(method, deps)

            # Build include paths
            include_paths = _collect_includes(method, self.config.project_root)

            ctx = MethodContext(
                method=method,
                dependencies=deps,
                include_paths=include_paths,
                mock_candidates=mock_candidates,
            )

            save_context(ctx, self.config.context_root)
            count += 1

        print(f"  Generated {count} context documents")
        return count

    # ──────────────────────────────────────────────
    # Phase 3: Generate tests (loop)
    # ──────────────────────────────────────────────
    def generate_loop(self) -> dict:
        """Main loop: generate tests until all pass or max attempts reached."""
        print(f"\n[GENERATE] Starting test generation loop ...")
        print(f"  LLM tool: {self.config.llm_tool}")
        print(f"  Coverage threshold: {self.config.coverage_threshold}%")
        print(f"  Max attempts per method: {self.config.max_attempts}")

        self.config.ensure_dirs()
        iteration = 0

        while True:
            pending = self.tracker.get_pending()
            if not pending:
                print("\n  All methods processed!")
                break

            iteration += 1
            print(f"\n--- Iteration {iteration} | {len(pending)} methods remaining ---")

            for status in pending:
                print(f"\n  [{status.attempts + 1}/{self.config.max_attempts}] {status.name}")

                # Find context MD
                context_md = self._find_context_md(status.slug)
                if context_md is None:
                    print(f"    [SKIP] No context MD found for {status.name}")
                    self.tracker.increment_attempts(status.method_id)
                    continue

                # Test file path
                test_file = self.config.tests_root / f"test_{status.slug}.cpp"

                # Generate or regenerate
                if not status.created or not test_file.exists():
                    print(f"    Generating test with {self.config.llm_tool} ...")
                    success = self.llm.generate_test(context_md, status.name, test_file)
                    if not success:
                        print(f"    [FAIL] LLM generation failed")
                        self.tracker.increment_attempts(status.method_id)
                        continue
                    self.tracker.mark_created(status.method_id, str(test_file))
                    print(f"    Test file created: {test_file.name}")
                else:
                    # Regenerate based on previous error
                    print(f"    Regenerating test (previous error: {status.error_message[:80]})")
                    success = self.llm.regenerate_test(
                        context_md, status.name, test_file, status.error_message,
                    )
                    if not success:
                        self.tracker.increment_attempts(status.method_id)
                        continue

                # Compile and run
                print(f"    Compiling and running ...")
                test_result = self.runner.compile_and_run(test_file, status.file_path)

                if not test_result.compiled:
                    print(f"    [FAIL] Compilation failed")
                    self.tracker.mark_compiled(status.method_id, False, test_result.error_message)
                    self.tracker.increment_attempts(status.method_id)
                    continue

                self.tracker.mark_compiled(status.method_id, True)

                if not test_result.passed:
                    print(f"    [FAIL] Test failed")
                    self.tracker.update(
                        status.method_id,
                        error_message=test_result.error_message,
                    )
                    self.tracker.increment_attempts(status.method_id)
                    continue

                # Check coverage
                coverage = test_result.coverage
                self.tracker.mark_passed(status.method_id, True, coverage)

                if coverage >= self.config.coverage_threshold:
                    print(f"    [OK] Passed with {coverage:.1f}% coverage")
                else:
                    print(f"    [WARN] Passed but coverage {coverage:.1f}% < {self.config.coverage_threshold}%")
                    # Still mark as passed but note low coverage
                    self.tracker.update(
                        status.method_id,
                        error_message=f"Low coverage: {coverage:.1f}%",
                    )
                    self.tracker.increment_attempts(status.method_id)

        return self.tracker.summary()

    # ──────────────────────────────────────────────
    # Full pipeline
    # ──────────────────────────────────────────────
    def run_all(self) -> dict:
        """Run the complete workflow: scan -> analyze -> generate loop."""
        self.scan()
        self.analyze()
        summary = self.generate_loop()
        self._print_summary(summary)
        return summary

    def _find_context_md(self, slug: str) -> Optional[Path]:
        """Find the context MD file for a method slug."""
        for md in self.config.context_root.rglob(f"{slug}.md"):
            return md
        # Fuzzy match
        for md in self.config.context_root.rglob("*.md"):
            if slug in md.stem:
                return md
        return None

    def _print_summary(self, summary: dict) -> None:
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  Total methods:     {summary['total']}")
        print(f"  Tests created:     {summary['created']}")
        print(f"  Tests compiled:    {summary['compiled']}")
        print(f"  Tests passed:      {summary['passed']}")
        print(f"  Coverage >= {self.config.coverage_threshold}%: {summary['coverage_ok']}")
        print(f"  Remaining:         {summary['remaining']}")
        print("=" * 60)

        if summary['remaining'] > 0:
            print(f"\n  {summary['remaining']} methods still need tests.")
            print(f"  Run 'method-dep generate' to retry.")
        else:
            print("\n  All methods have passing tests!")


def _is_testable(method: MethodInfo) -> bool:
    """Filter out methods that are not worth testing."""
    name = method.method_name

    # Skip constructors/destructors
    if name.startswith("~") or name == method.class_name:
        return False

    # Skip operators (optional: could include them)
    if name.startswith("operator"):
        return False

    # Skip trivial getters/setters (body < 3 lines of actual code)
    body = method.body.strip()
    body_lines = [l.strip() for l in body.split("\n") if l.strip() and not l.strip().startswith("//")]
    # Subtract braces
    code_lines = [l for l in body_lines if l not in ("{", "}")]
    if len(code_lines) <= 1:
        return False

    # Skip main
    if name == "main":
        return False

    return True


def _collect_includes(method: MethodInfo, project_root: Path) -> list[str]:
    """Collect #include directives from the method's source file."""
    import re
    source_path = project_root / method.file_path
    if not source_path.exists():
        return []

    try:
        content = source_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    includes = []
    for m in re.finditer(r'#include\s*[<"]([^>"]+)[>"]', content):
        includes.append(m.group(1))
    return includes
