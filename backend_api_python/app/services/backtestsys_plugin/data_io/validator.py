"""OHLCV data quality validation for backtesting pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass
class Check:
    """Result of a single validation check."""

    name: str
    passed: bool
    violation_count: int = 0
    description: str = ""


@dataclass
class ValidationReport:
    """Aggregated result of all validation checks for one symbol."""

    symbol: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def summary(self) -> str:
        if self.passed:
            return f"PASS [{self.symbol}] — all checks passed"
        failed_names = ", ".join(c.name for c in self.checks if not c.passed)
        return f"FAIL [{self.symbol}] — failed: {failed_names}"


class DataValidator:
    """Runs OHLCV quality checks on a DataFrame."""

    def validate(self, df: pd.DataFrame, symbol: str) -> ValidationReport:
        checks = [
            self._check_ohlc_consistency(df),
            self._check_close_in_range(df),
            self._check_volume_positive(df),
            self._check_no_nulls(df),
            self._check_positive_prices(df),
        ]
        return ValidationReport(symbol=symbol, checks=checks)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_ohlc_consistency(df: pd.DataFrame) -> Check:
        violations = int((df["high"] < df["low"]).sum())
        return Check(
            name="ohlc_consistency",
            passed=violations == 0,
            violation_count=violations,
            description="high >= low for all rows",
        )

    @staticmethod
    def _check_close_in_range(df: pd.DataFrame) -> Check:
        violations = int(((df["close"] < df["low"]) | (df["close"] > df["high"])).sum())
        return Check(
            name="close_in_range",
            passed=violations == 0,
            violation_count=violations,
            description="close within [low, high] for all rows",
        )

    @staticmethod
    def _check_volume_positive(df: pd.DataFrame) -> Check:
        violations = int((df["volume"] <= 0).sum())
        return Check(
            name="volume_positive",
            passed=violations == 0,
            violation_count=violations,
            description="volume > 0 for all rows",
        )

    @staticmethod
    def _check_no_nulls(df: pd.DataFrame) -> Check:
        violations = int(df[OHLCV_COLUMNS].isnull().sum().sum())
        return Check(
            name="no_nulls",
            passed=violations == 0,
            violation_count=violations,
            description="no null values in OHLCV columns",
        )

    @staticmethod
    def _check_positive_prices(df: pd.DataFrame) -> Check:
        price_cols = ["open", "high", "low", "close"]
        violations = int((df[price_cols] <= 0).sum().sum())
        return Check(
            name="positive_prices",
            passed=violations == 0,
            violation_count=violations,
            description="all OHLC prices > 0",
        )
