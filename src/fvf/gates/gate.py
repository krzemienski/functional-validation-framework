"""Gate runner — executes numbered validation gates in order."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from fvf.config import FVFConfig
from fvf.gates.evidence import EvidenceCollector
from fvf.models import (
    GateCriteria,
    GateDefinition,
    GateResult,
    ValidationResult,
    ValidationStatus,
)
from fvf.validators.base import Validator

logger = logging.getLogger(__name__)
console = Console()


def load_gates(path: Path) -> list[GateDefinition]:
    """Load gate definitions from a YAML configuration file.

    Args:
        path: Path to a gate YAML file (see ``templates/`` for examples).

    Returns:
        Sorted list of :class:`~fvf.models.GateDefinition` instances.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the YAML is missing required fields.
    """
    if not path.exists():
        raise FileNotFoundError(f"Gate config not found: {path}")

    with path.open() as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}

    raw_gates: list[dict[str, Any]] = data.get("gates", [])
    if not raw_gates:
        raise ValueError(f"No 'gates' key found in {path}")

    gates: list[GateDefinition] = []
    for raw in raw_gates:
        raw_criteria = raw.get("criteria", [])
        criteria = [GateCriteria(**c) for c in raw_criteria]
        gate = GateDefinition(
            number=raw["number"],
            name=raw["name"],
            description=raw.get("description", ""),
            criteria=criteria,
            depends_on=raw.get("depends_on", []),
        )
        gates.append(gate)

    gates.sort(key=lambda g: g.number)
    logger.debug("Loaded %d gates from %s", len(gates), path)
    return gates


class GateRunner:
    """Orchestrates running numbered validation gates in dependency order.

    Gates are executed sequentially (or in parallel if ``config.parallel_gates``
    is enabled). A gate whose ``depends_on`` list references a gate that failed
    will be skipped automatically.

    Usage::

        config = FVFConfig()
        gates = load_gates(Path("gates.yaml"))
        runner = GateRunner(config, gates)
        results = runner.run_all()
    """

    def __init__(self, config: FVFConfig, gates: list[GateDefinition]) -> None:
        """Initialise the runner with configuration and gate definitions.

        Args:
            config: FVF project configuration.
            gates: Ordered list of gate definitions to execute.
        """
        self._config = config
        self._gates = sorted(gates, key=lambda g: g.number)
        self._collector = EvidenceCollector(config.resolved_evidence_dir())
        logger.debug("GateRunner initialised with %d gates", len(gates))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_gate(self, gate: GateDefinition) -> GateResult:
        """Execute a single gate — all its criteria — and collect evidence.

        Args:
            gate: Gate definition to run.

        Returns:
            :class:`~fvf.models.GateResult` summarising the outcome of all
            criteria within the gate.
        """
        logger.info("Running gate %d: %s", gate.number, gate.name)
        results: list[ValidationResult] = []

        for criteria in gate.criteria:
            validator = self._get_validator(criteria.validator_type)
            try:
                result = validator.validate(criteria)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Unhandled exception in validator '%s'", criteria.validator_type
                )
                result = ValidationResult(
                    status=ValidationStatus.ERROR,
                    message=f"Validator raised an exception: {exc}",
                    validator_name=criteria.validator_type,
                )
            results.append(result)

        # Determine overall gate status
        if any(r.status == ValidationStatus.ERROR for r in results):
            gate_status = ValidationStatus.ERROR
        elif any(r.status == ValidationStatus.FAILED for r in results):
            gate_status = ValidationStatus.FAILED
        else:
            gate_status = ValidationStatus.PASSED

        gate_result = GateResult(
            gate=gate,
            status=gate_status,
            results=results,
        )

        # Persist evidence
        all_evidence = gate_result.total_evidence
        if all_evidence:
            self._collector.collect(gate.number, all_evidence)

        status_str = gate_status.value.upper()
        if gate_status == ValidationStatus.PASSED:
            console.print(f"  [bold green]PASS[/bold green] Gate {gate.number}: {gate.name}")
        else:
            console.print(
                f"  [bold red]{status_str}[/bold red] Gate {gate.number}: {gate.name}"
            )
            for r in results:
                if r.failed:
                    console.print(f"       [yellow]→[/yellow] {r.message}")

        return gate_result

    def run_all(self) -> list[GateResult]:
        """Run all gates in number order, respecting dependencies.

        A gate is skipped if any gate listed in its ``depends_on`` did not pass.

        Returns:
            Ordered list of :class:`~fvf.models.GateResult` instances (one per gate).
        """
        completed: list[GateResult] = []
        failed_gate_numbers: set[int] = set()

        console.print(f"\n[bold]Running {len(self._gates)} validation gate(s)[/bold]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Validating…", total=len(self._gates))

            for gate in self._gates:
                progress.update(task, description=f"Gate {gate.number}: {gate.name}")

                # Check dependencies
                if not self._check_dependencies(gate, completed, failed_gate_numbers):
                    skipped = GateResult(
                        gate=gate,
                        status=ValidationStatus.SKIPPED,
                        results=[
                            ValidationResult(
                                status=ValidationStatus.SKIPPED,
                                message=(
                                    f"Skipped — dependency gate(s) "
                                    f"{gate.depends_on} did not pass"
                                ),
                                validator_name="GateRunner",
                            )
                        ],
                    )
                    completed.append(skipped)
                    failed_gate_numbers.add(gate.number)
                    console.print(
                        f"  [dim]SKIP[/dim] Gate {gate.number}: {gate.name} "
                        f"(dependency failed)"
                    )
                    progress.advance(task)
                    continue

                gate_result = self.run_gate(gate)
                completed.append(gate_result)
                if not gate_result.passed:
                    failed_gate_numbers.add(gate.number)

                progress.advance(task)

        self._print_summary(completed)
        return completed

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_validator(self, validator_type: str) -> Validator:
        """Instantiate the correct :class:`~fvf.validators.base.Validator` subclass.

        Args:
            validator_type: String key from the gate criteria (e.g. ``"browser"``).

        Returns:
            An initialised validator instance.

        Raises:
            ValueError: If *validator_type* is not recognised.
        """
        from fvf.validators.api import APIValidator  # noqa: PLC0415
        from fvf.validators.browser import BrowserValidator  # noqa: PLC0415
        from fvf.validators.ios import IOSValidator  # noqa: PLC0415
        from fvf.validators.screenshot import ScreenshotValidator  # noqa: PLC0415

        registry: dict[str, type[Validator]] = {
            "browser": BrowserValidator,
            "ios": IOSValidator,
            "api": APIValidator,
            "screenshot": ScreenshotValidator,
        }
        cls = registry.get(validator_type.lower())
        if cls is None:
            raise ValueError(
                f"Unknown validator_type '{validator_type}'. "
                f"Valid options: {list(registry)}"
            )
        return cls(self._config)

    def _check_dependencies(
        self,
        gate: GateDefinition,
        completed: list[GateResult],
        failed_numbers: set[int],
    ) -> bool:
        """Return True if all of *gate*'s dependencies have passed.

        Args:
            gate: Gate whose dependencies to check.
            completed: List of already-completed gate results.
            failed_numbers: Set of gate numbers that did not pass.

        Returns:
            True if all declared dependencies passed (or there are none).
        """
        if not gate.depends_on:
            return True
        for dep_number in gate.depends_on:
            if dep_number in failed_numbers:
                logger.debug(
                    "Gate %d blocked — dependency gate %d failed",
                    gate.number,
                    dep_number,
                )
                return False
            # If the dependency hasn't run yet, block
            dep_results = [r for r in completed if r.gate.number == dep_number]
            if not dep_results:
                logger.debug(
                    "Gate %d blocked — dependency gate %d has not run yet",
                    gate.number,
                    dep_number,
                )
                return False
        return True

    def _print_summary(self, results: list[GateResult]) -> None:
        """Print a rich summary table of all gate results.

        Args:
            results: All gate results to summarise.
        """
        table = Table(title="\nValidation Summary", show_header=True, header_style="bold")
        table.add_column("#", style="dim", width=4)
        table.add_column("Gate", min_width=20)
        table.add_column("Status", justify="center", width=10)
        table.add_column("Duration", justify="right", width=12)
        table.add_column("Evidence", justify="right", width=10)

        for gate_result in results:
            status = gate_result.status
            if status == ValidationStatus.PASSED:
                status_display = "[bold green]PASS[/bold green]"
            elif status == ValidationStatus.SKIPPED:
                status_display = "[dim]SKIP[/dim]"
            else:
                status_display = f"[bold red]{status.value.upper()}[/bold red]"

            duration_s = gate_result.duration_ms / 1000
            duration_str = f"{duration_s:.1f}s"
            evidence_count = len(gate_result.total_evidence)

            table.add_row(
                str(gate_result.gate.number),
                gate_result.gate.name,
                status_display,
                duration_str,
                str(evidence_count),
            )

        passed = sum(1 for r in results if r.passed)
        total = len(results)
        console.print(table)
        console.print(
            f"\n[bold]Result: {passed}/{total} gates passed[/bold]"
            + (" ✓" if passed == total else " ✗")
        )
