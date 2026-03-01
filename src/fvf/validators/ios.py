"""iOS validator using idb (iOS Development Bridge) for simulator validation."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from fvf.config import FVFConfig
from fvf.models import (
    EvidenceItem,
    EvidenceType,
    GateCriteria,
    ValidationResult,
    ValidationStatus,
)
from fvf.validators.base import Validator

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30  # seconds for subprocess calls


class IOSValidator(Validator):
    """Validate iOS applications running in the Simulator via idb and simctl.

    Uses real device/simulator interactions — no mocks, no stubs.

    Example ``validator_config``::

        deep_link: ils://sessions
        assertions:
          - type: element_present
            label: Sessions
          - type: element_present
            label: New Session
        actions:
          - type: deep_link
            url: ils://home
          - type: tap
            x: 200
            y: 400
          - type: swipe
            start_x: 5
            start_y: 500
            end_x: 300
            end_y: 500
            duration: 0.3
    """

    def __init__(self, config: FVFConfig) -> None:
        """Initialise the iOS validator.

        Args:
            config: FVF project configuration.  ``ios_simulator_udid`` is
                required for most idb operations.
        """
        self._config = config
        self._udid: str = config.ios_simulator_udid or ""
        if not self._udid:
            logger.warning(
                "ios_simulator_udid not set in config — idb commands will target "
                "the booted simulator (may be ambiguous)"
            )
        logger.debug("IOSValidator initialised (udid=%s)", self._udid or "auto")

    # ------------------------------------------------------------------
    # Validator interface
    # ------------------------------------------------------------------

    def validate(self, criteria: GateCriteria) -> ValidationResult:
        """Run iOS simulator assertions defined in *criteria*.

        Args:
            criteria: Gate criterion with ``validator_config`` containing
                optional ``deep_link``, ``actions``, and ``assertions``.

        Returns:
            :class:`~fvf.models.ValidationResult` with PASSED/FAILED status
            and captured evidence (screenshot + accessibility tree).
        """
        self._log_start(criteria)
        start_ms = time.monotonic() * 1000
        vc = criteria.validator_config
        evidence: list[EvidenceItem] = []
        failures: list[str] = []
        successes: list[str] = []

        try:
            # Optional: navigate via deep link first
            if dl := vc.get("deep_link"):
                self._deep_link(dl)
                time.sleep(1.5)  # Allow app to settle

            # Execute pre-assertion actions
            for action in vc.get("actions", []):
                self._execute_action(action)

            # Capture accessibility tree for assertions
            tree = self._get_accessibility_tree()
            evidence_dir = self._config.resolved_evidence_dir()

            # Save accessibility tree as evidence
            tree_path = evidence_dir / f"ios-a11y-{int(time.time())}.json"
            tree_path.write_text(json.dumps(tree, indent=2))
            evidence.append(
                EvidenceItem(
                    type=EvidenceType.ACCESSIBILITY_TREE,
                    path=tree_path,
                    metadata={"udid": self._udid, "validator": self.name},
                )
            )

            # Run assertions
            for assertion in vc.get("assertions", []):
                atype = assertion.get("type", "")
                if atype == "element_present":
                    label = assertion.get("label", "")
                    element = self._find_element(tree, label)
                    ok = element is not None
                    msg = f"element_present: '{label}' {'found' if ok else 'NOT found'} in accessibility tree"
                    (successes if ok else failures).append(msg)

                elif atype == "element_absent":
                    label = assertion.get("label", "")
                    element = self._find_element(tree, label)
                    ok = element is None
                    msg = f"element_absent: '{label}' {'absent' if ok else 'still PRESENT'} in accessibility tree"
                    (successes if ok else failures).append(msg)

                else:
                    logger.warning("[%s] Unknown assertion type: %s", self.name, atype)

            # Always take a screenshot as visual evidence
            screenshot_dir = self._config.resolved_evidence_dir()
            screenshot_path = screenshot_dir / f"ios-screenshot-{int(time.time())}.png"
            self._capture_screenshot(screenshot_path)
            if screenshot_path.exists():
                evidence.append(
                    EvidenceItem(
                        type=EvidenceType.SCREENSHOT,
                        path=screenshot_path,
                        metadata={"udid": self._udid, "validator": self.name},
                    )
                )

        except Exception as exc:  # noqa: BLE001
            duration_ms = time.monotonic() * 1000 - start_ms
            logger.exception("[%s] Unexpected error during iOS validation", self.name)
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message=f"iOS validation error: {exc}",
                evidence=evidence,
                duration_ms=duration_ms,
                validator_name=self.name,
            )

        duration_ms = time.monotonic() * 1000 - start_ms
        if failures:
            status = ValidationStatus.FAILED
            message = f"{len(failures)} assertion(s) failed: " + "; ".join(failures)
        else:
            status = ValidationStatus.PASSED
            message = f"All {len(successes)} assertion(s) passed"

        result = ValidationResult(
            status=status,
            message=message,
            evidence=evidence,
            duration_ms=duration_ms,
            validator_name=self.name,
        )
        self._log_result(result)
        return result

    def capture_evidence(self, output_dir: Path) -> list[EvidenceItem]:
        """Capture screenshot and accessibility tree from the simulator.

        Args:
            output_dir: Directory where evidence files will be saved.

        Returns:
            List of :class:`~fvf.models.EvidenceItem` instances.
        """
        self._ensure_dir(output_dir)
        items: list[EvidenceItem] = []
        ts = int(time.time())

        # Screenshot
        screenshot_path = output_dir / f"ios-evidence-{ts}.png"
        self._capture_screenshot(screenshot_path)
        if screenshot_path.exists():
            items.append(
                EvidenceItem(
                    type=EvidenceType.SCREENSHOT,
                    path=screenshot_path,
                    metadata={"source": "capture_evidence", "udid": self._udid},
                )
            )

        # Accessibility tree
        tree = self._get_accessibility_tree()
        tree_path = output_dir / f"ios-a11y-{ts}.json"
        tree_path.write_text(json.dumps(tree, indent=2))
        items.append(
            EvidenceItem(
                type=EvidenceType.ACCESSIBILITY_TREE,
                path=tree_path,
                metadata={"source": "capture_evidence", "udid": self._udid},
            )
        )

        return items

    # ------------------------------------------------------------------
    # Private — device interaction
    # ------------------------------------------------------------------

    def _capture_screenshot(self, output_path: Path) -> None:
        """Take a simulator screenshot using ``xcrun simctl``.

        Args:
            output_path: Full path (including filename) for the PNG file.
        """
        cmd = ["xcrun", "simctl", "io"]
        if self._udid:
            cmd.append(self._udid)
        else:
            cmd.append("booted")
        cmd += ["screenshot", str(output_path)]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT
            )
            if result.returncode != 0:
                logger.warning(
                    "[%s] screenshot failed (rc=%d): %s",
                    self.name, result.returncode, result.stderr.strip()
                )
        except subprocess.TimeoutExpired:
            logger.warning("[%s] screenshot timed out", self.name)
        except FileNotFoundError:
            logger.warning("[%s] xcrun not found — is Xcode installed?", self.name)

    def _tap(self, x: float, y: float) -> None:
        """Tap at logical coordinate (*x*, *y*) in the simulator.

        Args:
            x: Horizontal logical coordinate (points).
            y: Vertical logical coordinate (points).
        """
        cmd = ["idb", "ui", "tap", str(x), str(y)]
        if self._udid:
            cmd += ["--udid", self._udid]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("[%s] tap failed: %s", self.name, exc)

    def _swipe(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        duration: float = 0.3,
    ) -> None:
        """Swipe from (*start_x*, *start_y*) to (*end_x*, *end_y*).

        Args:
            start_x: Start horizontal coordinate (points).
            start_y: Start vertical coordinate (points).
            end_x: End horizontal coordinate (points).
            end_y: End vertical coordinate (points).
            duration: Swipe duration in seconds.
        """
        cmd = [
            "idb", "ui", "swipe",
            str(start_x), str(start_y),
            str(end_x), str(end_y),
            "--duration", str(duration),
        ]
        if self._udid:
            cmd += ["--udid", self._udid]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("[%s] swipe failed: %s", self.name, exc)

    def _get_accessibility_tree(self) -> dict[str, Any]:
        """Return the simulator's accessibility tree as a parsed dictionary.

        Returns:
            Parsed JSON from ``idb describe --json``, or an empty dict on
            failure.
        """
        cmd = ["idb", "describe", "--json"]
        if self._udid:
            cmd += ["--udid", self._udid]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as exc:
            logger.warning("[%s] accessibility tree fetch failed: %s", self.name, exc)
        return {}

    def _find_element(
        self, tree: dict[str, Any], label: str
    ) -> dict[str, Any] | None:
        """Recursively search *tree* for an element whose label contains *label*.

        Args:
            tree: Accessibility tree dictionary (from ``idb describe``).
            label: Substring to search for in element labels.

        Returns:
            The first matching element dict, or ``None`` if not found.
        """
        if not tree:
            return None

        # Check current node
        node_label = str(tree.get("label", "") or tree.get("AXLabel", ""))
        node_value = str(tree.get("value", "") or tree.get("AXValue", ""))
        if label.lower() in node_label.lower() or label.lower() in node_value.lower():
            return tree

        # Recurse into children
        for child in tree.get("children", []):
            found = self._find_element(child, label)
            if found is not None:
                return found

        return None

    def _deep_link(self, url: str) -> None:
        """Open a deep link URL in the simulator.

        Args:
            url: The deep link URL (e.g. ``ils://sessions``).
        """
        cmd = ["xcrun", "simctl", "openurl"]
        cmd.append(self._udid if self._udid else "booted")
        cmd.append(url)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT
            )
            if result.returncode != 0:
                logger.warning(
                    "[%s] deep_link failed (rc=%d): %s",
                    self.name, result.returncode, result.stderr.strip()
                )
            else:
                logger.debug("[%s] Opened deep link: %s", self.name, url)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("[%s] deep_link error: %s", self.name, exc)

    def _execute_action(self, action: dict[str, Any]) -> None:
        """Dispatch a single action from the ``actions`` config list.

        Args:
            action: Action dict with at minimum a ``type`` key.
        """
        atype = action.get("type", "")
        if atype == "tap":
            self._tap(action.get("x", 0), action.get("y", 0))
        elif atype == "swipe":
            self._swipe(
                action.get("start_x", 5),
                action.get("start_y", 500),
                action.get("end_x", 300),
                action.get("end_y", 500),
                action.get("duration", 0.3),
            )
        elif atype == "deep_link":
            self._deep_link(action.get("url", ""))
            time.sleep(action.get("settle_ms", 1500) / 1000)
        elif atype == "wait":
            time.sleep(action.get("duration_ms", 1000) / 1000)
        else:
            logger.warning("[%s] Unknown action type: %s — skipping", self.name, atype)
