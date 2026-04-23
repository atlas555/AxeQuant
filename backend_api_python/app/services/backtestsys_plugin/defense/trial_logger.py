"""Trial logger — append-only JSON registry of backtest runs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.backtestsys_plugin.evaluation.metrics import MetricsReport


class TrialLogger:
    """Append backtest trial results to a JSON registry file."""

    def __init__(self, registry_path: str) -> None:
        self._path = Path(registry_path)

    # ── Public API ────────────────────────────────────────────────

    def log(
        self,
        config_dict: dict,
        metrics: MetricsReport,
        status: str = "success",
    ) -> None:
        """Append a trial entry to the registry file."""
        data = self._read_registry()
        entry = {
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "status": status,
            "config": config_dict,
            "metrics": metrics.to_dict(),
        }
        data["trials"].append(entry)
        self._write_registry(data)

    @property
    def total_trials(self) -> int:
        """Return the number of trials recorded so far."""
        return len(self._read_registry()["trials"])

    # ── Internals ─────────────────────────────────────────────────

    def _read_registry(self) -> dict:
        """Read registry JSON; return fresh structure on missing/corrupt."""
        if not self._path.exists():
            return {"trials": []}
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "trials" not in data:
                self._backup_corrupt()
                return {"trials": []}
            return data
        except (json.JSONDecodeError, OSError):
            self._backup_corrupt()
            return {"trials": []}

    def _backup_corrupt(self) -> None:
        """Rename corrupt registry to a timestamped .bak file."""
        if self._path.exists():
            import logging
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            backup = self._path.with_suffix(f".{ts}.bak")
            self._path.rename(backup)
            logging.getLogger(__name__).warning(
                "Corrupt registry backed up to %s", backup
            )

    def _write_registry(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
