"""Pydantic models for the Functional Validation Framework."""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class EvidenceType(str, Enum):
    """Types of evidence that can be collected during validation."""

    SCREENSHOT = "screenshot"
    CURL_OUTPUT = "curl_output"
    ACCESSIBILITY_TREE = "accessibility_tree"
    LOG = "log"
    VIDEO = "video"
    NETWORK_HAR = "network_har"


class ValidationStatus(str, Enum):
    """Status of a validation result."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class EvidenceItem(BaseModel):
    """A single piece of evidence collected during validation."""

    type: EvidenceType
    path: Path
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("path", mode="before")
    @classmethod
    def coerce_path(cls, v: Any) -> Path:
        """Coerce string paths to Path objects."""
        return Path(v) if not isinstance(v, Path) else v

    def exists(self) -> bool:
        """Check if the evidence file exists on disk."""
        return self.path.exists()

    def size_bytes(self) -> int:
        """Return the file size in bytes, or 0 if not found."""
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0


class ValidationResult(BaseModel):
    """Result of a single validation criterion check."""

    status: ValidationStatus
    message: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    duration_ms: float = 0.0
    validator_name: str = ""

    @property
    def passed(self) -> bool:
        """Return True if the validation passed."""
        return self.status == ValidationStatus.PASSED

    @property
    def failed(self) -> bool:
        """Return True if the validation failed or errored."""
        return self.status in (ValidationStatus.FAILED, ValidationStatus.ERROR)

    def add_evidence(self, item: EvidenceItem) -> None:
        """Append an evidence item to this result."""
        self.evidence.append(item)
        logger.debug("Added evidence: %s (%s)", item.path, item.type)


class GateCriteria(BaseModel):
    """A single criterion within a validation gate."""

    description: str
    evidence_required: list[EvidenceType] = Field(default_factory=list)
    validator_type: str
    validator_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_required", mode="before")
    @classmethod
    def coerce_evidence_types(cls, v: Any) -> list[EvidenceType]:
        """Coerce string evidence type values to EvidenceType enums."""
        if not isinstance(v, list):
            return []
        result = []
        for item in v:
            if isinstance(item, EvidenceType):
                result.append(item)
            elif isinstance(item, str):
                # Accept both "screenshot" and "SCREENSHOT"
                try:
                    result.append(EvidenceType(item.lower()))
                except ValueError:
                    logger.warning("Unknown evidence type: %s — skipping", item)
        return result


class GateDefinition(BaseModel):
    """Definition of a validation gate with numbered ordering and dependencies."""

    number: int = Field(ge=1, description="Gate number (1-based, determines execution order)")
    name: str
    description: str = ""
    criteria: list[GateCriteria] = Field(default_factory=list)
    depends_on: list[int] = Field(
        default_factory=list,
        description="Gate numbers that must pass before this gate runs",
    )

    @field_validator("depends_on")
    @classmethod
    def no_self_dependency(cls, v: list[int], info: Any) -> list[int]:
        """Ensure a gate does not list itself as a dependency."""
        number = info.data.get("number")
        if number is not None and number in v:
            raise ValueError(f"Gate {number} cannot depend on itself")
        return v


class GateResult(BaseModel):
    """Result of running a full validation gate."""

    gate: GateDefinition
    status: ValidationStatus
    results: list[ValidationResult] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    attempt_number: int = 1

    @property
    def passed(self) -> bool:
        """Return True if the gate passed."""
        return self.status == ValidationStatus.PASSED

    @property
    def total_evidence(self) -> list[EvidenceItem]:
        """Flatten all evidence across all validation results."""
        items: list[EvidenceItem] = []
        for result in self.results:
            items.extend(result.evidence)
        return items

    @property
    def duration_ms(self) -> float:
        """Sum of all individual validation durations."""
        return sum(r.duration_ms for r in self.results)

    @property
    def failure_messages(self) -> list[str]:
        """Collect messages from all failed/errored results."""
        return [r.message for r in self.results if r.failed]


class GateReport(BaseModel):
    """Aggregated report across all gate results for a project."""

    project_name: str
    gates: list[GateResult] = Field(default_factory=list)
    total_gates: int = 0
    passed: int = 0
    failed: int = 0
    evidence_count: int = 0
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    def model_post_init(self, __context: Any) -> None:
        """Compute derived fields after construction."""
        if self.gates and self.total_gates == 0:
            self.total_gates = len(self.gates)
            self.passed = sum(1 for g in self.gates if g.passed)
            self.failed = self.total_gates - self.passed
            self.evidence_count = sum(len(g.total_evidence) for g in self.gates)

    @property
    def pass_rate(self) -> float:
        """Return pass rate as a value between 0.0 and 1.0."""
        if self.total_gates == 0:
            return 0.0
        return self.passed / self.total_gates

    @property
    def all_passed(self) -> bool:
        """Return True if every gate passed."""
        return self.passed == self.total_gates and self.total_gates > 0
