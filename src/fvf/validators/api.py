"""API validator using httpx for real HTTP endpoint validation."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

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


class APIValidator(Validator):
    """Validate REST API endpoints by making real HTTP requests.

    Checks status codes, response schemas, response timing, and JSON path
    values. All requests go to the **real** running server — no mocks.

    Example ``validator_config``::

        method: GET
        path: /api/v1/sessions
        expected_status: 200
        max_response_time_ms: 2000
        assertions:
          - type: json_path
            path: $.data
            expected_type: list
          - type: json_key_exists
            key: total
    """

    def __init__(self, config: FVFConfig) -> None:
        """Initialise the API validator.

        Args:
            config: FVF project configuration (used for base URL and timeouts).
        """
        self._config = config
        logger.debug(
            "APIValidator initialised (base_url=%s)", config.api_base_url or "none"
        )

    # ------------------------------------------------------------------
    # Validator interface
    # ------------------------------------------------------------------

    def validate(self, criteria: GateCriteria) -> ValidationResult:
        """Make a real HTTP request and assert on the response.

        Args:
            criteria: Gate criterion with ``validator_config`` describing the
                request method, path, headers, body, and assertions.

        Returns:
            :class:`~fvf.models.ValidationResult` with status and collected
            evidence (curl command + response body files).
        """
        self._log_start(criteria)
        start_ms = time.monotonic() * 1000
        vc = criteria.validator_config
        evidence: list[EvidenceItem] = []
        failures: list[str] = []
        successes: list[str] = []

        # Build request parameters
        method: str = vc.get("method", "GET").upper()
        path: str = vc.get("path", "/")
        base_url: str = vc.get("base_url") or self._config.api_base_url or ""
        url = f"{base_url.rstrip('/')}{path}" if base_url else path
        headers: dict[str, str] = vc.get("headers", {})
        body: Any = vc.get("body")
        expected_status: int = int(vc.get("expected_status", 200))
        max_response_time_ms: int = int(vc.get("max_response_time_ms", 10_000))
        timeout_s: float = self._config.browser_timeout / 1000  # reuse browser timeout

        if not url.startswith(("http://", "https://")):
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message=f"Invalid URL '{url}' — set api_base_url in config or provide full URL",
                duration_ms=0.0,
                validator_name=self.name,
            )

        try:
            request_start = time.monotonic()
            with httpx.Client(timeout=timeout_s) as client:
                response = client.request(
                    method,
                    url,
                    headers=headers,
                    json=body if isinstance(body, (dict, list)) else None,
                    content=body if isinstance(body, (str, bytes)) else None,
                )
            duration_ms_actual = (time.monotonic() - request_start) * 1000

            # Parse JSON body if available
            response_json: Any = None
            try:
                response_json = response.json()
            except Exception:  # noqa: BLE001
                pass

            # Capture evidence
            evidence_dir = self._config.resolved_evidence_dir()
            ts = int(time.time())

            curl_path = evidence_dir / f"api-curl-{ts}.txt"
            curl_path.write_text(
                self._format_curl(method, url, headers, body) + "\n\n---\n\n"
                + f"Status: {response.status_code}\n"
                + f"Duration: {duration_ms_actual:.0f}ms\n\n"
                + response.text[:10_000]
            )
            evidence.append(
                EvidenceItem(
                    type=EvidenceType.CURL_OUTPUT,
                    path=curl_path,
                    metadata={"method": method, "url": url, "status": response.status_code},
                )
            )

            # Run status check
            ok = self._check_status(response.status_code, expected_status)
            msg = f"status: expected {expected_status}, got {response.status_code}"
            (successes if ok else failures).append(msg)

            # Run response time check
            ok = self._check_response_time(duration_ms_actual, max_response_time_ms)
            msg = f"response_time: {duration_ms_actual:.0f}ms (max {max_response_time_ms}ms)"
            (successes if ok else failures).append(msg)

            # Run additional assertions
            for assertion in vc.get("assertions", []):
                atype = assertion.get("type", "")
                if atype == "json_path" and response_json is not None:
                    path_expr = assertion.get("path", "")
                    expected_val = assertion.get("expected")
                    expected_type = assertion.get("expected_type")
                    result_val = self._resolve_json_path(response_json, path_expr)
                    if expected_type:
                        type_map = {"list": list, "dict": dict, "str": str, "int": int, "bool": bool}
                        expected_python_type = type_map.get(expected_type)
                        ok = expected_python_type is not None and isinstance(result_val, expected_python_type)
                        msg = f"json_path '{path_expr}' type: expected {expected_type}, got {type(result_val).__name__}"
                    elif expected_val is not None:
                        ok = result_val == expected_val
                        msg = f"json_path '{path_expr}': expected {expected_val!r}, got {result_val!r}"
                    else:
                        ok = result_val is not None
                        msg = f"json_path '{path_expr}': {'present' if ok else 'NOT present'}"
                    (successes if ok else failures).append(msg)

                elif atype == "json_key_exists" and response_json is not None:
                    key = assertion.get("key", "")
                    ok = isinstance(response_json, dict) and key in response_json
                    msg = f"json_key_exists '{key}': {'found' if ok else 'NOT found'}"
                    (successes if ok else failures).append(msg)

                elif atype == "json_schema":
                    schema = assertion.get("schema", {})
                    ok = self._check_json_schema(response_json, schema)
                    msg = f"json_schema: {'valid' if ok else 'INVALID'}"
                    (successes if ok else failures).append(msg)

                else:
                    logger.warning("[%s] Unknown assertion type: %s", self.name, atype)

        except httpx.ConnectError as exc:
            duration_ms = time.monotonic() * 1000 - start_ms
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message=f"Connection refused to {url}: {exc}",
                evidence=evidence,
                duration_ms=duration_ms,
                validator_name=self.name,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = time.monotonic() * 1000 - start_ms
            logger.exception("[%s] Unexpected error during API validation", self.name)
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message=f"API validation error: {exc}",
                evidence=evidence,
                duration_ms=duration_ms,
                validator_name=self.name,
            )

        total_duration_ms = time.monotonic() * 1000 - start_ms
        if failures:
            status = ValidationStatus.FAILED
            message = f"{len(failures)} check(s) failed: " + "; ".join(failures)
        else:
            status = ValidationStatus.PASSED
            message = f"All {len(successes)} check(s) passed"

        result = ValidationResult(
            status=status,
            message=message,
            evidence=evidence,
            duration_ms=total_duration_ms,
            validator_name=self.name,
        )
        self._log_result(result)
        return result

    def capture_evidence(self, output_dir: Path) -> list[EvidenceItem]:
        """Capture a simple health-check response as evidence.

        Args:
            output_dir: Directory where evidence files will be saved.

        Returns:
            List containing a curl-output :class:`~fvf.models.EvidenceItem`,
            or an empty list if the request fails.
        """
        self._ensure_dir(output_dir)
        items: list[EvidenceItem] = []
        base_url = self._config.api_base_url
        if not base_url:
            logger.warning("[%s] No api_base_url configured — skipping evidence capture", self.name)
            return items

        url = f"{base_url.rstrip('/')}/health"
        ts = int(time.time())
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(url)
            out_path = output_dir / f"api-health-{ts}.txt"
            out_path.write_text(
                f"GET {url}\nStatus: {response.status_code}\n\n{response.text[:5000]}"
            )
            items.append(
                EvidenceItem(
                    type=EvidenceType.CURL_OUTPUT,
                    path=out_path,
                    metadata={"url": url, "status": response.status_code},
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Evidence capture failed: %s", self.name, exc)

        return items

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_status(self, actual: int, expected: int) -> bool:
        """Return True if *actual* matches *expected* HTTP status code."""
        return actual == expected

    def _check_response_time(self, duration_ms: float, max_ms: int) -> bool:
        """Return True if *duration_ms* is within *max_ms*."""
        return duration_ms <= max_ms

    def _check_json_schema(self, response_json: Any, schema: dict[str, Any]) -> bool:
        """Basic JSON schema type/key validation (no jsonschema dependency).

        Args:
            response_json: Parsed JSON response body.
            schema: Dict with optional ``type`` and ``required`` keys.

        Returns:
            True if the response matches the schema constraints.
        """
        if not schema:
            return True
        schema_type = schema.get("type")
        if schema_type == "object" and not isinstance(response_json, dict):
            return False
        if schema_type == "array" and not isinstance(response_json, list):
            return False
        if isinstance(response_json, dict):
            for required_key in schema.get("required", []):
                if required_key not in response_json:
                    return False
        return True

    def _resolve_json_path(self, data: Any, path: str) -> Any:
        """Resolve a simple JSONPath expression (``$.key.nested``) against *data*.

        Supports dot-notation paths and array index notation (``[0]``).

        Args:
            data: Parsed JSON value.
            path: JSONPath string starting with ``$``.

        Returns:
            The resolved value, or ``None`` if the path cannot be resolved.
        """
        if not path or path == "$":
            return data
        # Strip leading "$."
        normalized = path.lstrip("$").lstrip(".")
        parts = normalized.replace("[", ".").replace("]", "").split(".")
        current = data
        for part in parts:
            if not part:
                continue
            try:
                if isinstance(current, dict):
                    current = current.get(part)
                elif isinstance(current, list):
                    current = current[int(part)]
                else:
                    return None
            except (KeyError, IndexError, ValueError, TypeError):
                return None
        return current

    def _format_curl(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Any,
    ) -> str:
        """Format the request as an equivalent curl command string for evidence.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Full request URL.
            headers: Request headers dict.
            body: Request body (dict, list, str, or None).

        Returns:
            Multi-line curl command string.
        """
        parts = [f"curl -X {method} '{url}'"]
        for key, value in headers.items():
            parts.append(f"  -H '{key}: {value}'")
        if body is not None:
            if isinstance(body, (dict, list)):
                body_str = json.dumps(body)
                parts.append(f"  -H 'Content-Type: application/json'")
                parts.append(f"  -d '{body_str}'")
            else:
                parts.append(f"  -d '{body}'")
        return " \\\n".join(parts)

    def _check_json_path(self, response_json: Any, path: str, expected: Any) -> bool:
        """Return True if the value at *path* in *response_json* equals *expected*.

        Args:
            response_json: Parsed JSON response.
            path: JSONPath expression.
            expected: Expected value for equality check.

        Returns:
            True if the resolved value equals *expected*.
        """
        actual = self._resolve_json_path(response_json, path)
        return actual == expected
