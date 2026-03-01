"""Functional Validation Framework — real systems, real evidence, no mocks."""

__version__ = "0.1.0"

from fvf.config import FVFConfig
from fvf.gates.evidence import EvidenceCollector
from fvf.gates.gate import GateRunner, load_gates
from fvf.gates.report import ReportGenerator
from fvf.models import (
    EvidenceItem,
    EvidenceType,
    GateCriteria,
    GateDefinition,
    GateReport,
    GateResult,
    ValidationResult,
    ValidationStatus,
)
from fvf.validators.api import APIValidator
from fvf.validators.base import Validator
from fvf.validators.browser import BrowserValidator
from fvf.validators.ios import IOSValidator
from fvf.validators.screenshot import ScreenshotValidator

__all__ = [
    "__version__",
    # Config
    "FVFConfig",
    # Models
    "EvidenceItem",
    "EvidenceType",
    "GateCriteria",
    "GateDefinition",
    "GateReport",
    "GateResult",
    "ValidationResult",
    "ValidationStatus",
    # Validators
    "Validator",
    "BrowserValidator",
    "IOSValidator",
    "APIValidator",
    "ScreenshotValidator",
    # Gates
    "GateRunner",
    "load_gates",
    "EvidenceCollector",
    "ReportGenerator",
]
