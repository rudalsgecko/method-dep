"""Command-line interface for method-dep."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .workflow import Workflow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="method-dep",
        description="C++ method-level unit test generation workflow with LLM",
    )
    parser.add_argument(
        "-c", "--config",
        default="method-dep.yaml",
        help="Path to config file (default: method-dep.yaml)",
    )
    parser.add_argument(
        "-p", "--project",
        help="Path to C++ project root (overrides config)",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # --- init ---
    init_cmd = sub.add_parser("init", help="Initialize config file for a project")
    init_cmd.add_argument("project_path", help="Path to C++ project")

    # --- scan ---
    scan_cmd = sub.add_parser("scan", help="Scan project and extract methods")

    # --- analyze ---
    analyze_cmd = sub.add_parser("analyze", help="Analyze dependencies and generate context MDs")

    # --- generate ---
    gen_cmd = sub.add_parser("generate", help="Generate tests using LLM (main loop)")
    gen_cmd.add_argument(
        "--llm", choices=["claude", "opencode"],
        help="LLM tool to use (overrides config)",
    )
    gen_cmd.add_argument(
        "--max-attempts", type=int,
        help="Max generation attempts per method",
    )

    # --- run ---
    run_cmd = sub.add_parser("run", help="Run full pipeline: scan + analyze + generate")
    run_cmd.add_argument(
        "--llm", choices=["claude", "opencode"],
        help="LLM tool to use",
    )

    # --- status ---
    status_cmd = sub.add_parser("status", help="Show current test generation status")

    # --- reset ---
    reset_cmd = sub.add_parser("reset", help="Reset test status (re-generate all)")
    reset_cmd.add_argument(
        "--method", help="Reset specific method by name (fuzzy match)",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    # Load config
    config = Config.load(args.config)
    if args.project:
        config.project_path = args.project

    # Handle init separately
    if args.command == "init":
        return cmd_init(args, config)

    # Validate project path
    if not config.project_path:
        print("Error: No project path specified.")
        print("  Use: method-dep init <project_path>")
        print("  Or:  method-dep -p <project_path> <command>")
        return 1

    if not Path(config.project_path).exists():
        print(f"Error: Project path does not exist: {config.project_path}")
        return 1

    # Apply CLI overrides
    if hasattr(args, "llm") and args.llm:
        config.llm_tool = args.llm
    if hasattr(args, "max_attempts") and args.max_attempts:
        config.max_attempts = args.max_attempts

    wf = Workflow(config)

    if args.command == "scan":
        wf.scan()
    elif args.command == "analyze":
        wf.analyze()
    elif args.command == "generate":
        summary = wf.generate_loop()
        wf._print_summary(summary)
    elif args.command == "run":
        wf.run_all()
    elif args.command == "status":
        return cmd_status(config)
    elif args.command == "reset":
        return cmd_reset(config, getattr(args, "method", None))

    return 0


def cmd_init(args, config: Config) -> int:
    """Initialize a config file for a project."""
    project_path = Path(args.project_path).resolve()
    if not project_path.exists():
        print(f"Error: Path does not exist: {project_path}")
        return 1

    config.project_path = str(project_path)

    # Check for compile_commands.json
    cc = project_path / "compile_commands.json"
    if cc.exists():
        print(f"  Found compile_commands.json")
    else:
        # Check in build/
        cc_build = project_path / "build" / "compile_commands.json"
        if cc_build.exists():
            config.compile_commands = "build/compile_commands.json"
            print(f"  Found compile_commands.json in build/")
        else:
            print(f"  Warning: compile_commands.json not found")
            print(f"  Generate it with: cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON ..")

    config_path = Path("method-dep.yaml")
    config.save(config_path)
    print(f"\n  Config saved to {config_path}")
    print(f"  Project: {config.project_path}")
    print(f"\n  Next steps:")
    print(f"    method-dep scan        # Extract methods")
    print(f"    method-dep analyze     # Analyze dependencies")
    print(f"    method-dep generate    # Generate tests with LLM")
    print(f"    method-dep run         # Full pipeline")
    return 0


def cmd_status(config: Config) -> int:
    """Show test generation status."""
    from .tracker import TestTracker
    tracker = TestTracker(config.methods_json)

    methods = tracker.get_all()
    if not methods:
        print("No methods tracked yet. Run 'method-dep scan' first.")
        return 0

    summary = tracker.summary()

    print(f"\n{'='*70}")
    print(f"  Method Test Generation Status")
    print(f"{'='*70}")
    print(f"  Total:     {summary['total']}")
    print(f"  Created:   {summary['created']}")
    print(f"  Compiled:  {summary['compiled']}")
    print(f"  Passed:    {summary['passed']}")
    print(f"  Coverage:  {summary['coverage_ok']} (>= {config.coverage_threshold}%)")
    print(f"  Remaining: {summary['remaining']}")
    print(f"{'='*70}")

    # Show per-method details
    pending = [m for m in methods if not m.passed]
    if pending:
        print(f"\n  Pending methods:")
        for m in pending:
            status_icon = "X" if m.attempts >= config.max_attempts else "."
            err = f" | {m.error_message[:60]}" if m.error_message else ""
            print(f"    [{status_icon}] {m.name} (attempts: {m.attempts}){err}")

    passed = [m for m in methods if m.passed]
    if passed:
        print(f"\n  Completed methods:")
        for m in passed:
            cov = f"{m.coverage:.1f}%" if m.coverage > 0 else "N/A"
            print(f"    [OK] {m.name} (coverage: {cov})")

    return 0


def cmd_reset(config: Config, method_name: str | None) -> int:
    """Reset test status."""
    from .tracker import TestTracker
    tracker = TestTracker(config.methods_json)

    if method_name:
        # Fuzzy match
        for m in tracker.get_all():
            if method_name.lower() in m.name.lower():
                tracker.update(
                    m.method_id,
                    created=False, compiled=False, passed=False,
                    coverage=0.0, attempts=0, error_message="",
                )
                print(f"  Reset: {m.name}")
    else:
        for m in tracker.get_all():
            tracker.update(
                m.method_id,
                created=False, compiled=False, passed=False,
                coverage=0.0, attempts=0, error_message="",
            )
        print(f"  Reset all {len(tracker.get_all())} methods")

    return 0


if __name__ == "__main__":
    sys.exit(main())
