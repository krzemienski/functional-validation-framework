"""Gate report generation in Markdown, JSON, and HTML formats."""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from pathlib import Path

from fvf.models import GateReport, GateResult, ValidationStatus

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate human-readable and machine-readable validation reports.

    Supports three output formats:

    - **Markdown** — tables with emoji status indicators and evidence links.
    - **JSON** — full structured report for CI consumption.
    - **HTML** — self-contained HTML with embedded base64 screenshots.
    """

    def __init__(self, evidence_dir: Path) -> None:
        """Initialise the report generator.

        Args:
            evidence_dir: Root evidence directory (used to resolve screenshot
                paths when generating HTML with embedded images).
        """
        self._evidence_dir = evidence_dir
        logger.debug("ReportGenerator initialised (evidence_dir=%s)", evidence_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, results: list[GateResult], project_name: str) -> GateReport:
        """Build a :class:`~fvf.models.GateReport` from a list of gate results.

        Args:
            results: All gate results to include in the report.
            project_name: Display name for the project (used as the report title).

        Returns:
            Populated :class:`~fvf.models.GateReport` instance.
        """
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        evidence_count = sum(len(r.total_evidence) for r in results)

        report = GateReport(
            project_name=project_name,
            gates=results,
            total_gates=len(results),
            passed=passed,
            failed=failed,
            evidence_count=evidence_count,
            generated_at=datetime.utcnow(),
        )
        logger.info(
            "Report generated: %d/%d gates passed, %d evidence items",
            passed,
            len(results),
            evidence_count,
        )
        return report

    def to_markdown(self, report: GateReport) -> str:
        """Render the report as a GitHub-flavoured Markdown string.

        Args:
            report: The gate report to render.

        Returns:
            Markdown string suitable for writing to a ``.md`` file or posting
            as a PR comment.
        """
        lines: list[str] = [
            f"# Functional Validation Report — {report.project_name}",
            "",
            f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Gates | {report.total_gates} |",
            f"| Passed | {report.passed} ✅ |",
            f"| Failed | {report.failed} {'❌' if report.failed else '—'} |",
            f"| Evidence Items | {report.evidence_count} |",
            f"| Pass Rate | {report.pass_rate:.0%} |",
            "",
            "## Gate Results",
            "",
            "| # | Gate | Status | Duration | Evidence |",
            "|---|------|--------|----------|----------|",
        ]

        for gate_result in report.gates:
            lines.append(self._format_gate_row(gate_result))

        lines += ["", "## Gate Details", ""]

        for gate_result in report.gates:
            emoji = self._status_emoji(gate_result.status)
            lines += [
                f"### {emoji} Gate {gate_result.gate.number}: {gate_result.gate.name}",
                "",
            ]
            if gate_result.gate.description:
                lines += [gate_result.gate.description, ""]

            if gate_result.gate.depends_on:
                deps = ", ".join(str(d) for d in gate_result.gate.depends_on)
                lines += [f"**Depends on:** Gates {deps}", ""]

            lines += [
                "| Validator | Status | Message | Duration |",
                "|-----------|--------|---------|----------|",
            ]
            for result in gate_result.results:
                result_emoji = self._status_emoji(result.status)
                duration = f"{result.duration_ms:.0f}ms"
                msg = result.message.replace("|", "\\|")[:80]
                lines.append(
                    f"| {result.validator_name or 'unknown'} "
                    f"| {result_emoji} {result.status.value} "
                    f"| {msg} "
                    f"| {duration} |"
                )

            evidence = gate_result.total_evidence
            if evidence:
                lines += ["", f"**Evidence ({len(evidence)} item(s)):**", ""]
                for item in evidence:
                    rel_path = item.path.name
                    lines.append(f"- `{item.type.value}`: [{rel_path}]({item.path})")

            lines.append("")

        if report.all_passed:
            lines += [
                "---",
                "",
                "> ✅ **All gates passed.** The system meets all functional validation criteria.",
            ]
        else:
            lines += [
                "---",
                "",
                f"> ❌ **{report.failed} gate(s) failed.** Review the details above and fix the failing criteria.",
            ]

        return "\n".join(lines)

    def to_json(self, report: GateReport) -> str:
        """Render the report as a compact JSON string.

        Args:
            report: The gate report to serialise.

        Returns:
            Indented JSON string.
        """
        data = {
            "project_name": report.project_name,
            "generated_at": report.generated_at.isoformat(),
            "summary": {
                "total_gates": report.total_gates,
                "passed": report.passed,
                "failed": report.failed,
                "pass_rate": round(report.pass_rate, 4),
                "evidence_count": report.evidence_count,
                "all_passed": report.all_passed,
            },
            "gates": [
                {
                    "number": gr.gate.number,
                    "name": gr.gate.name,
                    "description": gr.gate.description,
                    "status": gr.status.value,
                    "duration_ms": round(gr.duration_ms, 1),
                    "attempt": gr.attempt_number,
                    "timestamp": gr.timestamp.isoformat(),
                    "depends_on": gr.gate.depends_on,
                    "results": [
                        {
                            "validator": r.validator_name,
                            "status": r.status.value,
                            "message": r.message,
                            "duration_ms": round(r.duration_ms, 1),
                            "evidence": [
                                {
                                    "type": e.type.value,
                                    "path": str(e.path),
                                    "timestamp": e.timestamp.isoformat(),
                                    "metadata": e.metadata,
                                }
                                for e in r.evidence
                            ],
                        }
                        for r in gr.results
                    ],
                }
                for gr in report.gates
            ],
        }
        return json.dumps(data, indent=2)

    def to_html(self, report: GateReport) -> str:
        """Render the report as a self-contained HTML document.

        Screenshots are embedded as base64 data URIs so the file is fully
        portable without an external evidence directory.

        Args:
            report: The gate report to render.

        Returns:
            Complete HTML string.
        """
        gate_rows_html = ""
        for gr in report.gates:
            emoji = self._status_emoji(gr.status)
            duration = f"{gr.duration_ms / 1000:.1f}s"
            evidence_count = len(gr.total_evidence)
            gate_rows_html += (
                f"<tr>"
                f"<td>{gr.gate.number}</td>"
                f"<td>{gr.gate.name}</td>"
                f"<td>{emoji} {gr.status.value}</td>"
                f"<td>{duration}</td>"
                f"<td>{evidence_count}</td>"
                f"</tr>\n"
            )

        gate_details_html = ""
        for gr in report.gates:
            emoji = self._status_emoji(gr.status)
            screenshots_html = ""
            for item in gr.total_evidence:
                if item.type.value == "screenshot" and item.path.exists():
                    try:
                        b64 = base64.b64encode(item.path.read_bytes()).decode()
                        screenshots_html += (
                            f'<img src="data:image/png;base64,{b64}" '
                            f'alt="{item.path.name}" '
                            f'style="max-width:100%;border:1px solid #ddd;margin:8px 0;" />'
                            f'<br><small>{item.path.name}</small>'
                        )
                    except Exception:  # noqa: BLE001
                        screenshots_html += f"<p><em>Could not embed: {item.path.name}</em></p>"

            results_html = "".join(
                f"<tr><td>{r.validator_name}</td>"
                f"<td>{self._status_emoji(r.status)} {r.status.value}</td>"
                f"<td>{r.message[:120]}</td>"
                f"<td>{r.duration_ms:.0f}ms</td></tr>"
                for r in gr.results
            )

            gate_details_html += f"""
<section>
  <h3>{emoji} Gate {gr.gate.number}: {gr.gate.name}</h3>
  <p>{gr.gate.description}</p>
  <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
    <tr><th>Validator</th><th>Status</th><th>Message</th><th>Duration</th></tr>
    {results_html}
  </table>
  {f'<div style="margin-top:12px">{screenshots_html}</div>' if screenshots_html else ''}
</section>
"""

        pass_color = "#22c55e" if report.all_passed else "#ef4444"
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FVF Report — {report.project_name}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 960px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
    h1 {{ color: {pass_color}; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; }}
    th {{ background: #f9fafb; font-weight: 600; }}
    section {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin: 16px 0; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 9999px; font-size: 0.8em; }}
  </style>
</head>
<body>
  <h1>Functional Validation Report</h1>
  <p><strong>Project:</strong> {report.project_name}</p>
  <p><strong>Generated:</strong> {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>

  <h2>Summary</h2>
  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Total Gates</td><td>{report.total_gates}</td></tr>
    <tr><td>Passed</td><td style="color:#22c55e">{report.passed} ✅</td></tr>
    <tr><td>Failed</td><td style="color:#ef4444">{report.failed} {'❌' if report.failed else '—'}</td></tr>
    <tr><td>Evidence Items</td><td>{report.evidence_count}</td></tr>
    <tr><td>Pass Rate</td><td>{report.pass_rate:.0%}</td></tr>
  </table>

  <h2>Gate Results</h2>
  <table>
    <tr><th>#</th><th>Gate</th><th>Status</th><th>Duration</th><th>Evidence</th></tr>
    {gate_rows_html}
  </table>

  <h2>Gate Details</h2>
  {gate_details_html}
</body>
</html>"""

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _format_gate_row(self, result: GateResult) -> str:
        """Format a gate result as a Markdown table row.

        Args:
            result: Gate result to format.

        Returns:
            Markdown table row string.
        """
        emoji = self._status_emoji(result.status)
        duration = f"{result.duration_ms / 1000:.1f}s"
        evidence_count = len(result.total_evidence)
        return (
            f"| {result.gate.number} "
            f"| {result.gate.name} "
            f"| {emoji} {result.status.value} "
            f"| {duration} "
            f"| {evidence_count} |"
        )

    def _status_emoji(self, status: ValidationStatus) -> str:
        """Return a Unicode emoji for the given validation status.

        Args:
            status: Validation status enum value.

        Returns:
            Emoji string: ✅ PASSED, ❌ FAILED, ⏭ SKIPPED, 💥 ERROR.
        """
        mapping = {
            ValidationStatus.PASSED: "✅",
            ValidationStatus.FAILED: "❌",
            ValidationStatus.SKIPPED: "⏭",
            ValidationStatus.ERROR: "💥",
        }
        return mapping.get(status, "❓")
