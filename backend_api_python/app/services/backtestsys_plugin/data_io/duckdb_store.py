"""DuckDB query layer over DataAuto CSV files.

Provides SQL access to OHLCV data without data migration.
DuckDB reads CSV files directly with near-Parquet performance.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

_SAFE_PATTERN = re.compile(r'^[A-Za-z0-9_:.\-/]+$')


class DuckDBStore:
    """Query OHLCV data via DuckDB (reads CSV files directly)."""

    def __init__(self, data_dir: str):
        import duckdb
        self.data_dir = Path(data_dir)
        self.con = duckdb.connect(":memory:")

    def query(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """Load OHLCV data using DuckDB for the given range.

        Equivalent to DataLoader.load() but uses SQL under the hood.
        """
        if not _SAFE_PATTERN.match(symbol):
            raise ValueError(f"Invalid symbol: {symbol!r}")
        if not _SAFE_PATTERN.match(timeframe):
            raise ValueError(f"Invalid timeframe: {timeframe!r}")
        pattern = str(self.data_dir / symbol / timeframe / "*.csv")
        start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
        end_ms = int(
            (pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1))
            .timestamp() * 1000
        )

        sql = f"""
            SELECT
                timestamp,
                CAST(open AS DOUBLE) as open,
                CAST(high AS DOUBLE) as high,
                CAST(low AS DOUBLE) as low,
                CAST(close AS DOUBLE) as close,
                CAST(volume AS DOUBLE) as volume
            FROM read_csv_auto('{pattern}')
            WHERE timestamp >= {start_ms} AND timestamp <= {end_ms}
            ORDER BY timestamp
        """
        df = self.con.execute(sql).fetchdf()

        # Convert timestamp to UTC DatetimeIndex
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        return df

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.con.close()

    def sql(self, query: str) -> pd.DataFrame:
        """Execute arbitrary SQL and return DataFrame."""
        return self.con.execute(query).fetchdf()

    def close(self):
        self.con.close()
