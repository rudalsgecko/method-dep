"""methoddep CLI entry point.

Subcommands are wired up as they come online per the plan's phase
ordering. Each subcommand lives in its own module; this file only
composes them.
"""

from __future__ import annotations

from pathlib import Path

import click

from methoddep import __version__


@click.group(help="methoddep — per-method C++/MSVC intel gatherer")
@click.version_option(__version__, prog_name="methoddep")
def main() -> None:
    pass


@main.command("init", help="Scaffold a methoddep.toml in the current directory.")
@click.option("--force", is_flag=True, help="Overwrite existing config.")
def _init(force: bool) -> None:
    from methoddep.config import scaffold_config

    path = scaffold_config(force=force)
    click.echo(f"wrote {path}")


@main.command("doctor", help="Diagnose external tool availability.")
def _doctor() -> None:
    from methoddep.doctor import run_doctor

    ok = run_doctor()
    raise SystemExit(0 if ok else 1)


@main.command("run", help="Compose workspace, analyze, and emit JSON for one customer.")
@click.option("--config", "config_path", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--customer", required=True)
@click.option("--refresh", is_flag=True, help="Force workspace re-compose.")
def _run(config_path: Path, customer: str, refresh: bool) -> None:
    from methoddep.config import load_config
    from methoddep.pipeline import run_customer

    cfg = load_config(config_path)
    result = run_customer(cfg, customer, refresh=refresh)
    click.echo(
        f"customer={result.customer} strategy={result.workspace.strategy} "
        f"methods={result.method_count} index={result.index_path}"
    )
    for w in result.warnings[:10]:
        click.echo(f"  warn: {w}")


@main.command("verify-fixtures", help="Run analyzer against annotated fixtures and measure coverage.")
@click.option("--fixture-root", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--customer", default="acme", show_default=True)
@click.option("--strict/--relaxed", default=True, show_default=True)
@click.option("--min-rate", default=0.95, show_default=True, type=float)
@click.option("--min-count", default=1, show_default=True, type=int)
def _verify_fixtures(fixture_root: Path, customer: str, strict: bool, min_rate: float, min_count: int) -> None:
    from methoddep.verify_fixtures import verify

    report = verify(fixture_root, customer=customer, strict=strict)
    click.echo(
        f"annotated={report.annotated_methods} covered={report.covered_methods} "
        f"rate={report.coverage_rate:.3f}"
    )
    for failure in report.failures:
        click.echo(f"  FAIL {failure.path}:{failure.line} {failure.qualified_name}")
        for key, items in failure.missing.items():
            click.echo(f"    missing[{key}]: {', '.join(items)}")
    raise SystemExit(0 if report.passes(min_rate=min_rate, min_count=min_count) else 1)


if __name__ == "__main__":
    main()
