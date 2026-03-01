"""Screenshot validator — capture and optionally compare screenshots as evidence."""

from __future__ import annotations

import logging
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


class ScreenshotValidator(Validator):
    """Capture screenshots from a browser or iOS simulator and optionally compare
    them to a reference image.

    When a ``reference_path`` is supplied the validator computes a pixel-level
    similarity score and fails if it falls below the configured *threshold*.
    Without a reference the gate simply captures and stores the screenshot as
    evidence (always passes unless the capture itself fails).

    Example ``validator_config``::

        source: browser          # browser | ios
        url: http://localhost:3000   # required for browser source
        reference_path: ./references/homepage.png   # optional
        threshold: 0.95          # similarity 0.0-1.0 (default 0.95)
        full_page: true          # browser only
    """

    def __init__(self, config: FVFConfig) -> None:
        """Initialise the screenshot validator.

        Args:
            config: FVF project configuration.
        """
        self._config = config
        logger.debug("ScreenshotValidator initialised")

    # ------------------------------------------------------------------
    # Validator interface
    # ------------------------------------------------------------------

    def validate(self, criteria: GateCriteria) -> ValidationResult:
        """Capture a screenshot and optionally compare to a reference.

        Args:
            criteria: Gate criterion with ``validator_config`` controlling the
                source (``browser`` or ``ios``), URL, and optional reference
                comparison settings.

        Returns:
            :class:`~fvf.models.ValidationResult` with PASSED/FAILED status
            and the captured screenshot as evidence.
        """
        self._log_start(criteria)
        start_ms = time.monotonic() * 1000
        vc = criteria.validator_config
        evidence: list[EvidenceItem] = []

        source: str = vc.get("source", "browser").lower()
        evidence_dir = self._config.resolved_evidence_dir()
        ts = int(time.time())
        screenshot_path = evidence_dir / f"screenshot-{source}-{ts}.{self._config.screenshot_format}"

        try:
            if source == "browser":
                url: str = vc.get("url", "")
                if not url:
                    return ValidationResult(
                        status=ValidationStatus.ERROR,
                        message="No URL specified for browser screenshot",
                        validator_name=self.name,
                        duration_ms=0.0,
                    )
                full_page: bool = vc.get("full_page", True)
                self._capture_browser_screenshot(url, screenshot_path, full_page=full_page)
            elif source == "ios":
                self._capture_ios_screenshot(screenshot_path)
            else:
                return ValidationResult(
                    status=ValidationStatus.ERROR,
                    message=f"Unknown source '{source}'. Use 'browser' or 'ios'.",
                    validator_name=self.name,
                    duration_ms=0.0,
                )

            if not screenshot_path.exists():
                duration_ms = time.monotonic() * 1000 - start_ms
                return ValidationResult(
                    status=ValidationStatus.ERROR,
                    message=f"Screenshot file was not created: {screenshot_path}",
                    duration_ms=duration_ms,
                    validator_name=self.name,
                )

            evidence.append(
                EvidenceItem(
                    type=EvidenceType.SCREENSHOT,
                    path=screenshot_path,
                    metadata={"source": source, "validator": self.name},
                )
            )

            # Optional reference comparison
            reference_str: str | None = vc.get("reference_path")
            if reference_str:
                reference_path = Path(reference_str)
                threshold: float = float(vc.get("threshold", 0.95))

                if not reference_path.exists():
                    # Save the current screenshot AS the reference
                    reference_path.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(screenshot_path, reference_path)
                    duration_ms = time.monotonic() * 1000 - start_ms
                    result = ValidationResult(
                        status=ValidationStatus.PASSED,
                        message=f"Reference not found — saved current screenshot as reference: {reference_path}",
                        evidence=evidence,
                        duration_ms=duration_ms,
                        validator_name=self.name,
                    )
                    self._log_result(result)
                    return result

                passed, similarity = self._compare_screenshots(screenshot_path, reference_path, threshold)
                diff_path = evidence_dir / f"screenshot-diff-{ts}.png"
                try:
                    self._generate_diff_image(screenshot_path, reference_path, diff_path)
                    if diff_path.exists():
                        evidence.append(
                            EvidenceItem(
                                type=EvidenceType.SCREENSHOT,
                                path=diff_path,
                                metadata={"type": "diff", "similarity": similarity},
                            )
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] Could not generate diff image: %s", self.name, exc)

                duration_ms = time.monotonic() * 1000 - start_ms
                if passed:
                    status = ValidationStatus.PASSED
                    message = f"Screenshot similarity {similarity:.1%} >= threshold {threshold:.1%}"
                else:
                    status = ValidationStatus.FAILED
                    message = f"Screenshot similarity {similarity:.1%} < threshold {threshold:.1%}"

                result = ValidationResult(
                    status=status,
                    message=message,
                    evidence=evidence,
                    duration_ms=duration_ms,
                    validator_name=self.name,
                )
                self._log_result(result)
                return result

        except Exception as exc:  # noqa: BLE001
            duration_ms = time.monotonic() * 1000 - start_ms
            logger.exception("[%s] Screenshot validation error", self.name)
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message=f"Screenshot validation error: {exc}",
                evidence=evidence,
                duration_ms=duration_ms,
                validator_name=self.name,
            )

        duration_ms = time.monotonic() * 1000 - start_ms
        result = ValidationResult(
            status=ValidationStatus.PASSED,
            message=f"Screenshot captured successfully: {screenshot_path.name}",
            evidence=evidence,
            duration_ms=duration_ms,
            validator_name=self.name,
        )
        self._log_result(result)
        return result

    def capture_evidence(self, output_dir: Path) -> list[EvidenceItem]:
        """Capture a screenshot and save it to *output_dir*.

        This method captures from the browser at ``about:blank`` by default.
        For meaningful evidence, call :meth:`validate` with a full criteria.

        Args:
            output_dir: Directory where the screenshot will be saved.

        Returns:
            List containing one screenshot :class:`~fvf.models.EvidenceItem`,
            or an empty list on failure.
        """
        self._ensure_dir(output_dir)
        ts = int(time.time())
        path = output_dir / f"screenshot-evidence-{ts}.png"
        try:
            self._capture_browser_screenshot("about:blank", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] capture_evidence failed: %s", self.name, exc)
            return []

        if path.exists():
            return [
                EvidenceItem(
                    type=EvidenceType.SCREENSHOT,
                    path=path,
                    metadata={"source": "capture_evidence"},
                )
            ]
        return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _capture_browser_screenshot(
        self, url: str, output_path: Path, *, full_page: bool = True
    ) -> None:
        """Navigate to *url* in a headless Chromium browser and save a screenshot.

        Args:
            url: URL to navigate to before capturing.
            output_path: Destination file path for the PNG screenshot.
            full_page: If True, capture the full scrollable page.
        """
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright && playwright install"
            )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_default_timeout(self._config.browser_timeout)
            page.goto(url, wait_until="networkidle")
            page.screenshot(path=str(output_path), full_page=full_page)
            browser.close()
        logger.debug("[%s] Browser screenshot saved: %s", self.name, output_path)

    def _capture_ios_screenshot(self, output_path: Path) -> None:
        """Take a screenshot from the iOS simulator using ``xcrun simctl``.

        Args:
            output_path: Destination file path for the PNG screenshot.
        """
        import subprocess

        udid = self._config.ios_simulator_udid or "booted"
        cmd = ["xcrun", "simctl", "io", udid, "screenshot", str(output_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(
                f"simctl screenshot failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        logger.debug("[%s] iOS screenshot saved: %s", self.name, output_path)

    def _compare_screenshots(
        self, actual: Path, reference: Path, threshold: float
    ) -> tuple[bool, float]:
        """Compare *actual* and *reference* screenshots pixel by pixel.

        Args:
            actual: Path to the newly captured screenshot.
            reference: Path to the reference (baseline) screenshot.
            threshold: Minimum similarity score (0.0–1.0) to pass.

        Returns:
            A ``(passed, similarity_score)`` tuple where *similarity_score* is
            between 0.0 (completely different) and 1.0 (identical).
        """
        try:
            from PIL import Image, ImageChops  # noqa: PLC0415
        except ImportError:
            raise RuntimeError("Pillow is not installed. Run: pip install Pillow")

        img_actual = Image.open(actual).convert("RGB")
        img_reference = Image.open(reference).convert("RGB")

        # Resize to same dimensions for comparison
        if img_actual.size != img_reference.size:
            img_reference = img_reference.resize(img_actual.size, Image.LANCZOS)

        diff = ImageChops.difference(img_actual, img_reference)
        pixels = list(diff.getdata())
        total_pixels = len(pixels)
        if total_pixels == 0:
            return True, 1.0

        # Compute mean absolute difference per channel, normalised to 0-1
        total_diff = sum(max(r, g, b) for r, g, b in pixels)
        max_possible = total_pixels * 255
        similarity = 1.0 - (total_diff / max_possible)
        passed = similarity >= threshold
        logger.debug("[%s] Similarity: %.4f (threshold %.4f)", self.name, similarity, threshold)
        return passed, similarity

    def _generate_diff_image(
        self, actual: Path, reference: Path, output_path: Path
    ) -> None:
        """Generate a visual diff image highlighting pixel differences.

        Args:
            actual: Path to the actual screenshot.
            reference: Path to the reference screenshot.
            output_path: Destination for the diff image.
        """
        try:
            from PIL import Image, ImageChops, ImageEnhance  # noqa: PLC0415
        except ImportError:
            logger.debug("[%s] Pillow not available — skipping diff image", self.name)
            return

        img_actual = Image.open(actual).convert("RGB")
        img_reference = Image.open(reference).convert("RGB")

        if img_actual.size != img_reference.size:
            img_reference = img_reference.resize(img_actual.size, Image.LANCZOS)

        diff = ImageChops.difference(img_actual, img_reference)
        # Amplify differences for visibility
        enhanced = ImageEnhance.Brightness(diff).enhance(5.0)
        enhanced.save(str(output_path))
        logger.debug("[%s] Diff image saved: %s", self.name, output_path)
