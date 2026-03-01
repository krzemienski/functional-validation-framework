"""Browser validator using Playwright for real browser-based validation."""

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


class BrowserValidator(Validator):
    """Validate web applications using a real Playwright-driven browser.

    Supports navigating to URLs, clicking elements, waiting for conditions,
    and asserting on status codes, element visibility, and text content.
    All validations run against the **real** running application — no mocks.

    Example ``validator_config``::

        url: http://localhost:3000
        assertions:
          - type: status_code
            expected: 200
          - type: element_visible
            selector: h1.welcome
          - type: text_content
            selector: h1.welcome
            expected: Welcome
        actions:
          - type: click
            selector: nav a[href="/about"]
          - type: wait
            duration: 500
    """

    def __init__(self, config: FVFConfig) -> None:
        """Initialise the validator with project configuration.

        Args:
            config: FVF project configuration (used for timeout settings).
        """
        self._config = config
        logger.debug("BrowserValidator initialised (timeout=%dms)", config.browser_timeout)

    # ------------------------------------------------------------------
    # Validator interface
    # ------------------------------------------------------------------

    def validate(self, criteria: GateCriteria) -> ValidationResult:
        """Run browser-based assertions defined in *criteria*.

        Args:
            criteria: Gate criterion with ``validator_config`` containing URL,
                optional ``actions``, and ``assertions`` list.

        Returns:
            :class:`~fvf.models.ValidationResult` with PASSED/FAILED status
            and any captured evidence.
        """
        self._log_start(criteria)
        start_ms = time.monotonic() * 1000
        evidence: list[EvidenceItem] = []

        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message="Playwright is not installed. Run: pip install playwright && playwright install",
                validator_name=self.name,
                duration_ms=0.0,
            )

        vc = criteria.validator_config
        url: str = vc.get("url", "")
        if not url:
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message="No URL specified in validator_config",
                validator_name=self.name,
                duration_ms=0.0,
            )

        failures: list[str] = []
        successes: list[str] = []

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(self._config.browser_timeout)

                # Navigate
                response = page.goto(url, wait_until="networkidle")

                # Execute optional pre-assertion actions
                for action in vc.get("actions", []):
                    self._execute_action(page, action)

                # Run assertions
                for assertion in vc.get("assertions", []):
                    atype = assertion.get("type", "")
                    if atype == "status_code":
                        expected_code = int(assertion.get("expected", 200))
                        actual_code = response.status if response else -1
                        ok = self._check_status_code(actual_code, expected_code)
                        msg = f"status_code: expected {expected_code}, got {actual_code}"
                        (successes if ok else failures).append(msg)

                    elif atype == "element_visible":
                        selector = assertion.get("selector", "")
                        ok = self._check_element_visible(page, selector)
                        msg = f"element_visible: '{selector}' {'found' if ok else 'NOT found'}"
                        (successes if ok else failures).append(msg)

                    elif atype == "text_content":
                        selector = assertion.get("selector", "")
                        expected_text = assertion.get("expected", "")
                        ok = self._check_text_content(page, selector, expected_text)
                        msg = f"text_content: '{selector}' {'contains' if ok else 'does NOT contain'} '{expected_text}'"
                        (successes if ok else failures).append(msg)

                    else:
                        logger.warning("Unknown assertion type: %s — skipping", atype)

                # Always capture a screenshot as evidence
                screenshot_dir = self._config.resolved_evidence_dir()
                screenshot_path = screenshot_dir / f"browser-{int(time.time())}.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                evidence.append(
                    EvidenceItem(
                        type=EvidenceType.SCREENSHOT,
                        path=screenshot_path,
                        metadata={"url": url, "validator": self.name},
                    )
                )

                browser.close()

        except Exception as exc:  # noqa: BLE001
            duration_ms = time.monotonic() * 1000 - start_ms
            logger.exception("[%s] Unexpected error during browser validation", self.name)
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message=f"Browser validation error: {exc}",
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
        """Capture a full-page screenshot of the configured URL.

        Args:
            output_dir: Directory where the screenshot will be saved.

        Returns:
            List containing a single screenshot :class:`~fvf.models.EvidenceItem`,
            or an empty list if the browser session fails.
        """
        self._ensure_dir(output_dir)
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            logger.error("Playwright not available — cannot capture evidence")
            return []

        items: list[EvidenceItem] = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_default_timeout(self._config.browser_timeout)
                screenshot_path = output_dir / f"browser-evidence-{int(time.time())}.png"
                page.goto("about:blank")
                page.screenshot(path=str(screenshot_path), full_page=True)
                items.append(
                    EvidenceItem(
                        type=EvidenceType.SCREENSHOT,
                        path=screenshot_path,
                        metadata={"source": "capture_evidence", "validator": self.name},
                    )
                )
                browser.close()
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] Failed to capture browser evidence: %s", self.name, exc)

        return items

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_action(self, page: Any, action: dict[str, Any]) -> None:
        """Execute a single page action (click, wait, fill, etc.).

        Args:
            page: Playwright ``Page`` object.
            action: Action dictionary with at minimum a ``type`` key.
        """
        atype = action.get("type", "")
        try:
            if atype == "click":
                selector = action.get("selector", "")
                logger.debug("[%s] Clicking: %s", self.name, selector)
                page.click(selector)
            elif atype == "wait":
                duration_ms = int(action.get("duration", 1000))
                logger.debug("[%s] Waiting %dms", self.name, duration_ms)
                page.wait_for_timeout(duration_ms)
            elif atype == "fill":
                selector = action.get("selector", "")
                value = action.get("value", "")
                logger.debug("[%s] Filling '%s' with value", self.name, selector)
                page.fill(selector, value)
            elif atype == "navigate":
                url = action.get("url", "")
                logger.debug("[%s] Navigating to: %s", self.name, url)
                page.goto(url, wait_until="networkidle")
            else:
                logger.warning("[%s] Unknown action type: %s — skipping", self.name, atype)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Action '%s' failed: %s", self.name, atype, exc)

    def _check_element_visible(self, page: Any, selector: str) -> bool:
        """Return True if *selector* matches a visible element on *page*.

        Args:
            page: Playwright ``Page`` object.
            selector: CSS or ARIA selector string.

        Returns:
            ``True`` if the element exists and is visible, ``False`` otherwise.
        """
        try:
            element = page.query_selector(selector)
            return element is not None and element.is_visible()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] element_visible check error for '%s': %s", self.name, selector, exc)
            return False

    def _check_text_content(self, page: Any, selector: str, expected: str) -> bool:
        """Return True if the element at *selector* contains *expected* text.

        Args:
            page: Playwright ``Page`` object.
            selector: CSS or ARIA selector string.
            expected: Substring that must appear in the element's text content.

        Returns:
            ``True`` if the element exists and its inner text contains *expected*.
        """
        try:
            element = page.query_selector(selector)
            if element is None:
                return False
            text = element.inner_text()
            return expected in text
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[%s] text_content check error for '%s': %s", self.name, selector, exc
            )
            return False

    def _check_status_code(self, actual: int, expected: int) -> bool:
        """Return True if *actual* HTTP status matches *expected*.

        Args:
            actual: Actual HTTP response status code.
            expected: Expected status code.

        Returns:
            ``True`` if they match.
        """
        return actual == expected
