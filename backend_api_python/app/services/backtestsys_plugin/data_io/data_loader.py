"""DataLoader for reading crypto perpetual futures data from CSV files.

Expected directory layout:
    {data_dir}/{SYMBOL}/{timeframe}/{YYYY-MM}.csv

Each CSV must have a ``timestamp`` column (Unix milliseconds) plus one or more
float data columns.  The classic set is open/high/low/close/volume, but extra
columns (e.g. quote_volume, num_trades, taker_buy_volume,
taker_buy_quote_volume) are loaded automatically when present.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


class DataLoader:
    """Load and filter OHLCV (and extended) data from the DataAuto CSV file tree."""

    def __init__(self, data_dir: str) -> None:
        """Initialise with path to the data_future directory."""
        self.data_dir = Path(data_dir)

    def load(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """Read CSV files for *symbol*/*timeframe* that overlap [start, end].

        Parameters
        ----------
        symbol : str
            Trading pair directory name, e.g. ``"BTCUSDT:USDT"``.
        timeframe : str
            Timeframe subdirectory, e.g. ``"1h"``, ``"15m"``.
        start, end : str
            ISO-format date strings defining the inclusive query range,
            e.g. ``"2020-01-01"``, ``"2020-03-31"``.

        Returns
        -------
        pd.DataFrame
            DataFrame indexed by UTC ``DatetimeIndex`` with float columns
            for all data present in the CSV (at minimum ``open``, ``high``,
            ``low``, ``close``, ``volume``; may also include
            ``quote_volume``, ``num_trades``, etc.).

        Raises
        ------
        FileNotFoundError
            If the symbol directory does not exist.
        """
        symbol_dir = self.data_dir / symbol / timeframe

        # SEC-001: Guard against path traversal
        resolved = (self.data_dir / symbol / timeframe).resolve()
        if not str(resolved).startswith(str(self.data_dir.resolve())):
            raise ValueError(
                f"Path traversal detected: '{symbol}/{timeframe}' escapes data directory"
            )

        if not (self.data_dir / symbol).exists():
            raise FileNotFoundError(
                f"Symbol directory not found: {self.data_dir / symbol}"
            )

        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)

        # Determine which monthly CSV files overlap with [start, end].
        csv_files = self._find_overlapping_files(symbol_dir, start_ts, end_ts)

        if not csv_files:
            return self._empty_frame()

        frames: list[pd.DataFrame] = []
        for fp in csv_files:
            df = pd.read_csv(fp)
            frames.append(df)

        combined = pd.concat(frames, ignore_index=True)

        # Convert timestamp (ms) to DatetimeIndex with UTC.
        combined["timestamp"] = pd.to_datetime(combined["timestamp"], unit="ms", utc=True)
        combined.set_index("timestamp", inplace=True)

        # Ensure float dtype for all non-index columns.
        for col in combined.columns:
            combined[col] = combined[col].astype(float)

        # Sort ascending.
        combined.sort_index(inplace=True)

        # Filter to [start, end].
        combined = combined.loc[start_ts:end_ts]

        return combined

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_overlapping_files(
        directory: Path,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> list[Path]:
        """Return sorted list of CSV paths whose month overlaps [start, end]."""
        if not directory.exists():
            return []

        files: list[Path] = []
        for fp in sorted(directory.glob("*.csv")):
            # Filename like 2020-01.csv
            stem = fp.stem  # "2020-01"
            try:
                file_month_start = pd.Timestamp(f"{stem}-01", tz="UTC")
            except ValueError:
                continue
            file_month_end = file_month_start + pd.offsets.MonthEnd(0) + pd.Timedelta(
                hours=23, minutes=59, seconds=59, milliseconds=999
            )
            # Overlap check: file_month_end >= start AND file_month_start <= end
            if file_month_end >= start and file_month_start <= end:
                files.append(fp)
        return files

    @staticmethod
    def _empty_frame() -> pd.DataFrame:
        """Return an empty DataFrame with the expected schema."""
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df.index = pd.DatetimeIndex([], tz="UTC")
        for col in df.columns:
            df[col] = df[col].astype(float)
        return df
