"""Abstract base class for all FVF validators."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from fvf.models import EvidenceItem, GateCriteria, ValidationResult

logger = logging.getLogger(__name__)


class Validator(ABC):
    """Abstract base class that every validator must implement.

    Subclasses handle a specific validation surface (browser, iOS, API, …)
    and are responsible for:

    1. Running assertions defined in a :class:`~fvf.models.GateCriteria`.
    2. Capturing timestamped evidence artifacts (screenshots, logs, etc.).

    The ``validate`` and ``capture_evidence`` methods are intentionally
    separate so that callers can collect evidence even when assertions fail.
    """

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def validate(self, criteria: GateCriteria) -> ValidationResult:
        """Run all assertions defined in *criteria*.

        This method **must not** raise; validation failures should be
        expressed through the returned :class:`~fvf.models.ValidationResult`
        with an appropriate :class:`~fvf.models.ValidationStatus`.

        Args:
            criteria: The gate criterion that specifies what to check and how.

        Returns:
            A :class:`~fvf.models.ValidationResult` with status, message,
            collected evidence, and timing information.
        """
        ...

    @abstractmethod
    def capture_evidence(self, output_dir: Path) -> list[EvidenceItem]:
        """Capture evidence artifacts and write them to *output_dir*.

        This may be called independently of ``validate`` — for example,
        to gather a baseline snapshot before running assertions.

        Args:
            output_dir: Directory where evidence files should be saved.

        Returns:
            A list of :class:`~fvf.models.EvidenceItem` instances pointing
            to the files that were written.
        """
        ...

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-readable name for this validator, derived from the class name."""
        return self.__class__.__name__

    # ------------------------------------------------------------------
    # Protected helpers
    # ------------------------------------------------------------------

    def _ensure_dir(self, path: Path) -> Path:
        """Create *path* (and parents) if it does not already exist.

        Args:
            path: Directory path to create.

        Returns:
            The same *path*, guaranteed to exist on return.
        """
        path.mkdir(parents=True, exist_ok=True)
        logger.debug("[%s] Ensured directory: %s", self.name, path)
        return path

    def _log_start(self, criteria: GateCriteria) -> None:
        """Emit a debug log at the start of ``validate``."""
        logger.debug("[%s] Starting validation: %s", self.name, criteria.description)

    def _log_result(self, result: ValidationResult) -> None:
        """Emit an appropriate log line after ``validate`` completes."""
        level = logging.DEBUG if result.passed else logging.WARNING
        logger.log(
            level,
            "[%s] Validation %s: %s",
            self.name,
            result.status.value.upper(),
            result.message,
        )
