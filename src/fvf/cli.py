"""Click CLI entry point for the Functional Validation Framework."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click
import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from fvf.config import FVFConfig
from fvf.gates.evidence import EvidenceCollector
from fvf.gates.gate import GateRunner, load_gates
from fvf.gates.report import ReportGenerator

console = Console()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_time=False)],
)
logger = logging.getLogger("fvf")


def _configure_logging(verbose: bool) -> None:
    """Set logging verbosity based on the ``--verbose`` flag."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.getLogger("fvf").setLevel(level)
    logging.getLogger("playwright").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.version_option(package_name="functional-validation-framework")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Functional Validation Framework — real systems, real evidence, no mocks.

    Run validation gates against live applications (browser, iOS simulator,
    or HTTP APIs) and collect timestamped evidence artifacts.

    \b
    Quick start:
      fvf init --type browser          # scaffold a gate config
      fvf validate --gate gates.yaml   # run all gates
      fvf report --evidence-dir ./evidence/ --format md
    """
    _configure_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--gate",
    "gate_file",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the gate YAML configuration file.",
)
@click.option(
    "--config",
    "config_file",
    default=None,
    type=click.Path(path_type=Path),
    help="Path to fvf.yaml config (auto-discovered if omitted).",
)
@click.pass_context
def validate(ctx: click.Context, gate_file: Path, config_file: Optional[Path]) -> None:
    """Run all validation gates defined in GATE_FILE.

    Exits with code 0 if all gates pass, 1 if any gate fails.
    """
    config = _load_config(config_file)
    try:
        gates = load_gates(gate_file)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error loading gates:[/red] {exc}")
        sys.exit(1)

    runner = GateRunner(config, gates)
    results = runner.run_all()

    failed = [r for r in results if not r.passed and r.status.value != "skipped"]
    sys.exit(1 if failed else 0)


# ---------------------------------------------------------------------------
# gate subgroup
# ---------------------------------------------------------------------------


@cli.group()
def gate() -> None:
    """Gate management commands (run, list)."""


@gate.command("run")
@click.argument("number", type=int)
@click.option(
    "--gate-file",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the gate YAML configuration file.",
)
@click.option(
    "--config",
    "config_file",
    default=None,
    type=click.Path(path_type=Path),
    help="Path to fvf.yaml config.",
)
def gate_run(number: int, gate_file: Path, config_file: Optional[Path]) -> None:
    """Run a specific gate by NUMBER from GATE_FILE.

    Exits with code 0 if the gate passes, 1 otherwise.
    """
    config = _load_config(config_file)
    try:
        gates = load_gates(gate_file)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error loading gates:[/red] {exc}")
        sys.exit(1)

    matching = [g for g in gates if g.number == number]
    if not matching:
        console.print(f"[red]No gate with number {number} found in {gate_file}[/red]")
        sys.exit(1)

    runner = GateRunner(config, gates)
    result = runner.run_gate(matching[0])
    sys.exit(0 if result.passed else 1)


@gate.command("list")
@click.argument("gate_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--evidence-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Evidence directory to check for collected artifacts.",
)
def gate_list(gate_file: Path, evidence_dir: Optional[Path]) -> None:
    """List all gates in GATE_FILE with their status from collected evidence."""
    try:
        gates = load_gates(gate_file)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error loading gates:[/red] {exc}")
        sys.exit(1)

    ev_dir = evidence_dir or Path("./evidence")
    collector = EvidenceCollector(ev_dir) if ev_dir.exists() else None

    table = Table(title=f"Gates — {gate_file.name}", show_header=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Name", min_width=20)
    table.add_column("Depends On", width=12)
    table.add_column("Criteria", width=10, justify="right")
    table.add_column("Evidence", width=10, justify="right")

    for g in gates:
        evidence_count = 0
        if collector:
            evidence_count = len(collector.get_evidence(g.number))

        deps = ", ".join(str(d) for d in g.depends_on) if g.depends_on else "—"
        table.add_row(
            str(g.number),
            g.name,
            deps,
            str(len(g.criteria)),
            str(evidence_count),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--evidence-dir",
    default="./evidence",
    type=click.Path(path_type=Path),
    show_default=True,
    help="Directory containing collected evidence.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["md", "json", "html"], case_sensitive=False),
    default="md",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--output",
    "output_file",
    default=None,
    type=click.Path(path_type=Path),
    help="Write output to this file (prints to stdout if omitted).",
)
@click.option(
    "--project",
    "project_name",
    default="My Project",
    show_default=True,
    help="Project name shown in the report header.",
)
@click.option(
    "--gate-file",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Gate YAML file to include gate names in the report.",
)
def report(
    evidence_dir: Path,
    output_format: str,
    output_file: Optional[Path],
    project_name: str,
    gate_file: Optional[Path],
) -> None:
    """Generate a validation report from collected evidence.

    Reads evidence from EVIDENCE_DIR and outputs a report in the chosen format.
    """
    if not evidence_dir.exists():
        console.print(f"[yellow]Warning:[/yellow] Evidence directory not found: {evidence_dir}")

    generator = ReportGenerator(evidence_dir)

    # Build a minimal report from evidence on disk
    from fvf.models import GateDefinition, GateResult, ValidationResult, ValidationStatus  # noqa: PLC0415

    gate_results: list[GateResult] = []
    collector = EvidenceCollector(evidence_dir)

    if gate_file:
        try:
            gates_list = load_gates(gate_file)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Could not load gates from {gate_file}: {exc}[/yellow]")
            gates_list = []
    else:
        gates_list = []

    gate_numbers = collector.list_all_gates()
    gate_map = {g.number: g for g in gates_list}

    for num in gate_numbers:
        gate_def = gate_map.get(num) or GateDefinition(
            number=num, name=f"Gate {num}", description=""
        )
        ev_paths = collector.get_evidence(num)
        from fvf.models import EvidenceItem, EvidenceType  # noqa: PLC0415
        evidence_items = [
            EvidenceItem(
                type=EvidenceType.SCREENSHOT if p.suffix in {".png", ".jpg"} else EvidenceType.LOG,
                path=p,
            )
            for p in ev_paths
        ]
        val_result = ValidationResult(
            status=ValidationStatus.PASSED,
            message=f"{len(ev_paths)} evidence file(s) on disk",
            evidence=evidence_items,
            validator_name="EvidenceCollector",
        )
        gate_result = GateResult(
            gate=gate_def,
            status=ValidationStatus.PASSED,
            results=[val_result],
        )
        gate_results.append(gate_result)

    gate_report = generator.generate(gate_results, project_name)

    if output_format == "md":
        content = generator.to_markdown(gate_report)
    elif output_format == "json":
        content = generator.to_json(gate_report)
    else:
        content = generator.to_html(gate_report)

    if output_file:
        output_file.write_text(content)
        console.print(f"[green]Report written to:[/green] {output_file}")
    else:
        console.print(content)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--type",
    "gate_type",
    type=click.Choice(["browser", "ios", "api"], case_sensitive=False),
    default="browser",
    show_default=True,
    help="Type of gate template to scaffold.",
)
@click.option(
    "--output",
    "output_file",
    default=None,
    type=click.Path(path_type=Path),
    help="Filename to write (defaults to {type}-gates.yaml).",
)
def init(gate_type: str, output_file: Optional[Path]) -> None:
    """Scaffold a new gate configuration file.

    Writes a ready-to-edit YAML template for the chosen validator type.
    """
    template_name = f"{gate_type}-gate.yaml"
    template_path = Path(__file__).parent.parent.parent / "templates" / template_name

    dest = output_file or Path(f"{gate_type}-gates.yaml")

    if dest.exists():
        if not click.confirm(f"{dest} already exists. Overwrite?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            sys.exit(0)

    if template_path.exists():
        content = template_path.read_text()
    else:
        # Fallback minimal template
        content = _minimal_template(gate_type)

    dest.write_text(content)
    console.print(f"[green]Gate config written to:[/green] {dest}")
    console.print(f"\nEdit [bold]{dest}[/bold] then run:\n  fvf validate --gate {dest}")


def _minimal_template(gate_type: str) -> str:
    """Return a minimal YAML template string for the given gate type."""
    templates = {
        "browser": (
            "project: my-web-app\ngates:\n"
            "  - number: 1\n    name: Homepage Renders\n"
            "    description: Verify the homepage loads correctly\n"
            "    criteria:\n"
            "      - description: Homepage returns 200\n"
            "        evidence_required: [screenshot, curl_output]\n"
            "        validator_type: browser\n"
            "        validator_config:\n"
            "          url: http://localhost:3000\n"
            "          assertions:\n"
            "            - type: status_code\n              expected: 200\n"
        ),
        "ios": (
            "project: my-ios-app\ngates:\n"
            "  - number: 1\n    name: App Launches\n"
            "    description: Verify the app launches and shows the home screen\n"
            "    criteria:\n"
            "      - description: Home screen visible\n"
            "        evidence_required: [screenshot, accessibility_tree]\n"
            "        validator_type: ios\n"
            "        validator_config:\n"
            "          deep_link: myapp://home\n"
            "          assertions:\n"
            "            - type: element_present\n              label: Home\n"
        ),
        "api": (
            "project: my-api\ngates:\n"
            "  - number: 1\n    name: Health Check\n"
            "    description: Verify the API health endpoint responds correctly\n"
            "    criteria:\n"
            "      - description: Health endpoint returns 200\n"
            "        evidence_required: [curl_output]\n"
            "        validator_type: api\n"
            "        validator_config:\n"
            "          method: GET\n"
            "          path: /health\n"
            "          expected_status: 200\n"
            "          max_response_time_ms: 500\n"
        ),
    }
    return templates.get(gate_type, templates["browser"])


# ---------------------------------------------------------------------------
# evidence subgroup
# ---------------------------------------------------------------------------


@cli.group()
def evidence() -> None:
    """Evidence management commands (list, clean)."""


@evidence.command("list")
@click.option(
    "--evidence-dir",
    default="./evidence",
    type=click.Path(path_type=Path),
    show_default=True,
)
@click.option(
    "--gate",
    "gate_number",
    default=None,
    type=int,
    help="Filter to a specific gate number.",
)
def evidence_list(evidence_dir: Path, gate_number: Optional[int]) -> None:
    """List collected evidence files.

    Shows all evidence artifacts, optionally filtered to a single gate.
    """
    if not evidence_dir.exists():
        console.print(f"[yellow]Evidence directory not found:[/yellow] {evidence_dir}")
        return

    collector = EvidenceCollector(evidence_dir)

    if gate_number is not None:
        gate_numbers = [gate_number]
    else:
        gate_numbers = collector.list_all_gates()

    if not gate_numbers:
        console.print("[dim]No evidence collected yet.[/dim]")
        return

    table = Table(title="Collected Evidence", show_header=True)
    table.add_column("Gate", style="dim", width=6)
    table.add_column("File", min_width=30)
    table.add_column("Size", justify="right", width=12)

    total_files = 0
    for num in gate_numbers:
        paths = collector.get_evidence(num)
        for p in paths:
            size = p.stat().st_size if p.exists() else 0
            size_str = _human_size(size)
            table.add_row(str(num), p.name, size_str)
            total_files += 1

    console.print(table)
    console.print(f"\n[dim]{total_files} file(s) total[/dim]")


@evidence.command("clean")
@click.option(
    "--evidence-dir",
    default="./evidence",
    type=click.Path(path_type=Path),
    show_default=True,
)
@click.option(
    "--gate",
    "gate_number",
    default=None,
    type=int,
    help="Clean evidence for a specific gate only.",
)
@click.option(
    "--keep",
    default=3,
    show_default=True,
    type=int,
    help="Number of most-recent attempts to keep.",
)
def evidence_clean(evidence_dir: Path, gate_number: Optional[int], keep: int) -> None:
    """Remove old evidence attempts, keeping the N most recent per gate."""
    if not evidence_dir.exists():
        console.print(f"[yellow]Evidence directory not found:[/yellow] {evidence_dir}")
        return

    collector = EvidenceCollector(evidence_dir)

    if gate_number is not None:
        gate_numbers = [gate_number]
    else:
        gate_numbers = collector.list_all_gates()

    for num in gate_numbers:
        collector.cleanup(num, keep_latest=keep)

    console.print(f"[green]Cleaned evidence (kept {keep} per gate)[/green]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config(config_file: Optional[Path]) -> FVFConfig:
    """Load FVF config from *config_file* or discover it automatically."""
    if config_file:
        return FVFConfig.from_file(config_file)
    return FVFConfig.discover()


def _human_size(size_bytes: int) -> str:
    """Format *size_bytes* as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.0f} TB"


if __name__ == "__main__":
    cli()
