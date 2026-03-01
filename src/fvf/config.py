"""Configuration management for the Functional Validation Framework."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_TOML_AVAILABLE = False
try:
    import tomllib  # Python 3.11+

    _TOML_AVAILABLE = True
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]

        _TOML_AVAILABLE = True
    except ImportError:
        pass


class FVFConfig(BaseModel):
    """Central configuration object for FVF.

    Can be loaded from a ``fvf.yaml`` file, from the ``[tool.fvf]`` section
    of a ``pyproject.toml``, or constructed programmatically.
    """

    evidence_dir: Path = Field(
        default=Path("./evidence"),
        description="Directory where collected evidence is stored",
    )
    screenshot_format: str = Field(
        default="png",
        description="Image format for captured screenshots (png or jpg)",
        pattern="^(png|jpg|jpeg|webp)$",
    )
    browser_timeout: int = Field(
        default=30_000,
        ge=1_000,
        description="Playwright browser action timeout in milliseconds",
    )
    ios_simulator_udid: str | None = Field(
        default=None,
        description="UDID of the iOS simulator to target (optional)",
    )
    api_base_url: str | None = Field(
        default=None,
        description="Base URL prepended to relative API validator paths",
    )
    gate_retry_limit: int = Field(
        default=3,
        ge=1,
        description="Maximum number of retry attempts per gate",
    )
    parallel_gates: bool = Field(
        default=False,
        description="Run independent gates in parallel (experimental)",
    )

    model_config = {"arbitrary_types_allowed": True}

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path) -> "FVFConfig":
        """Load configuration from a YAML or TOML file.

        Args:
            path: Absolute or relative path to ``fvf.yaml`` or ``pyproject.toml``.

        Returns:
            A populated :class:`FVFConfig` instance.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If the file extension is not recognised.
        """
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        suffix = path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            with path.open() as fh:
                data: dict[str, Any] = yaml.safe_load(fh) or {}
            logger.debug("Loaded FVF config from YAML: %s", path)
            return cls.from_dict(data)

        if suffix == ".toml":
            if not _TOML_AVAILABLE:
                raise RuntimeError(
                    "TOML support requires Python 3.11+ or 'pip install tomli'"
                )
            with path.open("rb") as fb:
                toml_data = tomllib.load(fb)  # type: ignore[attr-defined]
            fvf_section: dict[str, Any] = (
                toml_data.get("tool", {}).get("fvf", {})
            )
            logger.debug("Loaded FVF config from pyproject.toml [tool.fvf]: %s", path)
            return cls.from_dict(fvf_section)

        raise ValueError(
            f"Unsupported config file extension '{suffix}'. "
            "Use fvf.yaml or pyproject.toml."
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FVFConfig":
        """Construct a :class:`FVFConfig` from a plain dictionary.

        Args:
            data: Dictionary of configuration values (keys match field names).

        Returns:
            A populated :class:`FVFConfig` instance.
        """
        return cls(**{k: v for k, v in data.items() if k in cls.model_fields})

    @classmethod
    def discover(cls, start_dir: Path | None = None) -> "FVFConfig":
        """Walk up the directory tree looking for ``fvf.yaml`` or ``pyproject.toml``.

        Args:
            start_dir: Directory to start the search from (defaults to CWD).

        Returns:
            A :class:`FVFConfig` loaded from the first matching file, or a
            default instance if no config file is found.
        """
        search_dir = (start_dir or Path.cwd()).resolve()
        for directory in [search_dir, *search_dir.parents]:
            for candidate in ("fvf.yaml", "fvf.yml", "pyproject.toml"):
                candidate_path = directory / candidate
                if candidate_path.exists():
                    try:
                        config = cls.from_file(candidate_path)
                        logger.info("Using FVF config from %s", candidate_path)
                        return config
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "Could not load config from %s: %s", candidate_path, exc
                        )
        logger.info("No FVF config file found — using defaults")
        return cls()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def resolved_evidence_dir(self, base: Path | None = None) -> Path:
        """Return the evidence directory resolved relative to *base* (or CWD).

        Args:
            base: Base directory for relative evidence_dir paths.

        Returns:
            Resolved :class:`~pathlib.Path` to the evidence directory.
        """
        root = (base or Path.cwd()).resolve()
        resolved = (root / self.evidence_dir).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved
