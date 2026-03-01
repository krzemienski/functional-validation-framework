"""Evidence collection and persistence for validation gates."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from fvf.models import EvidenceItem

logger = logging.getLogger(__name__)


class EvidenceCollector:
    """Collect, organise, and manage evidence artifacts produced by gate runs.

    Evidence is stored under ``{base_dir}/gate-{N}/{timestamp}/`` with a
    ``manifest.json`` describing every artifact saved in that attempt.

    Directory structure::

        evidence/
          gate-1/
            20240101-120000/
              manifest.json
              screenshot-browser-1234.png
              api-curl-1234.txt
          gate-2/
            20240101-120010/
              manifest.json
              ...
    """

    _TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"

    def __init__(self, base_dir: Path) -> None:
        """Initialise the collector with a base evidence directory.

        Args:
            base_dir: Root directory where all gate evidence sub-directories
                will be created.  Created if it does not exist.
        """
        self._base_dir = base_dir
        base_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("EvidenceCollector initialised at %s", base_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self, gate_number: int, items: list[EvidenceItem]) -> Path:
        """Save a list of evidence items for a gate attempt.

        Each call creates a new timestamped sub-directory so that multiple
        attempts are preserved independently.

        Args:
            gate_number: The gate's number (1-based).
            items: Evidence items to persist.

        Returns:
            Path to the timestamped attempt directory that was created.
        """
        timestamp = datetime.utcnow().strftime(self._TIMESTAMP_FORMAT)
        attempt_dir = self._gate_dir(gate_number) / timestamp
        attempt_dir.mkdir(parents=True, exist_ok=True)

        saved: list[EvidenceItem] = []
        for item in items:
            try:
                saved_item = self._save_item(item, attempt_dir)
                saved.append(saved_item)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to save evidence item %s: %s", item.path, exc
                )

        self._generate_manifest(saved, attempt_dir)
        logger.info(
            "Gate %d evidence: %d item(s) saved to %s",
            gate_number,
            len(saved),
            attempt_dir,
        )
        return attempt_dir

    def get_evidence(self, gate_number: int) -> list[Path]:
        """Return all evidence file paths across all attempts for a gate.

        Args:
            gate_number: The gate's number (1-based).

        Returns:
            Sorted list of file paths (oldest attempt first).
        """
        gate_dir = self._gate_dir(gate_number)
        if not gate_dir.exists():
            return []

        paths: list[Path] = []
        for attempt_dir in sorted(gate_dir.iterdir()):
            if attempt_dir.is_dir():
                paths.extend(
                    p for p in sorted(attempt_dir.iterdir())
                    if p.is_file() and p.name != "manifest.json"
                )
        return paths

    def get_latest(self, gate_number: int) -> Path | None:
        """Return the path to the most recent attempt directory for a gate.

        Args:
            gate_number: The gate's number (1-based).

        Returns:
            Path to the latest attempt directory, or ``None`` if no evidence
            has been collected for this gate.
        """
        gate_dir = self._gate_dir(gate_number)
        if not gate_dir.exists():
            return None

        attempt_dirs = sorted(
            (d for d in gate_dir.iterdir() if d.is_dir()),
            reverse=True,
        )
        return attempt_dirs[0] if attempt_dirs else None

    def cleanup(self, gate_number: int, keep_latest: int = 3) -> None:
        """Remove old attempt directories for a gate, keeping the most recent N.

        Args:
            gate_number: The gate's number (1-based).
            keep_latest: Number of most-recent attempts to retain (default: 3).
        """
        gate_dir = self._gate_dir(gate_number)
        if not gate_dir.exists():
            return

        attempt_dirs = sorted(
            (d for d in gate_dir.iterdir() if d.is_dir()),
            reverse=True,
        )
        to_remove = attempt_dirs[keep_latest:]
        for old_dir in to_remove:
            try:
                shutil.rmtree(old_dir)
                logger.debug("Removed old evidence dir: %s", old_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not remove %s: %s", old_dir, exc)

        if to_remove:
            logger.info(
                "Cleaned gate %d evidence: removed %d old attempt(s), kept %d",
                gate_number,
                len(to_remove),
                min(keep_latest, len(attempt_dirs)),
            )

    def list_all_gates(self) -> list[int]:
        """Return a sorted list of gate numbers that have collected evidence.

        Returns:
            Sorted list of gate numbers.
        """
        if not self._base_dir.exists():
            return []

        gate_numbers: list[int] = []
        for child in self._base_dir.iterdir():
            if child.is_dir() and child.name.startswith("gate-"):
                try:
                    gate_numbers.append(int(child.name.split("-", 1)[1]))
                except ValueError:
                    pass
        return sorted(gate_numbers)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _gate_dir(self, gate_number: int) -> Path:
        """Return the directory for a specific gate's evidence.

        Args:
            gate_number: The gate's number (1-based).

        Returns:
            Path of the form ``{base_dir}/gate-{N}/``.
        """
        return self._base_dir / f"gate-{gate_number}"

    def _save_item(self, item: EvidenceItem, target_dir: Path) -> EvidenceItem:
        """Copy an evidence file to *target_dir*.

        If the source file does not exist (e.g. the validator did not produce
        it), the item is returned unchanged and a warning is logged.

        Args:
            item: Evidence item whose ``path`` points to the source file.
            target_dir: Destination directory.

        Returns:
            A new :class:`~fvf.models.EvidenceItem` whose ``path`` points to
            the copy in *target_dir*.
        """
        if not item.path.exists():
            logger.warning("Evidence file missing, skipping: %s", item.path)
            return item

        dest = target_dir / item.path.name
        # Avoid name collision by appending a counter
        counter = 1
        while dest.exists():
            stem = item.path.stem
            suffix = item.path.suffix
            dest = target_dir / f"{stem}-{counter}{suffix}"
            counter += 1

        shutil.copy2(item.path, dest)
        logger.debug("Saved evidence: %s → %s", item.path, dest)

        return EvidenceItem(
            type=item.type,
            path=dest,
            timestamp=item.timestamp,
            metadata=item.metadata,
        )

    def _generate_manifest(self, items: list[EvidenceItem], target_dir: Path) -> None:
        """Write a ``manifest.json`` describing all saved evidence items.

        Args:
            items: Evidence items that were saved to *target_dir*.
            target_dir: Directory where ``manifest.json`` will be written.
        """
        manifest: dict = {
            "generated_at": datetime.utcnow().isoformat(),
            "item_count": len(items),
            "items": [
                {
                    "type": item.type.value,
                    "path": item.path.name,
                    "timestamp": item.timestamp.isoformat(),
                    "metadata": item.metadata,
                    "size_bytes": item.size_bytes(),
                }
                for item in items
            ],
        }
        manifest_path = target_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        logger.debug("Manifest written: %s", manifest_path)
