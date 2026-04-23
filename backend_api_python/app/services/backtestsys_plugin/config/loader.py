"""YAML config loader with Pydantic v2 validation for backtesting."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


# ── Sub-models ────────────────────────────────────────────────────────────────

class BacktestMeta(BaseModel):
    name: str = "Unnamed"
    initial_capital: float = 10_000.0


class DataConfig(BaseModel):
    data_dir: str = "DataAuto/data_future"
    symbol: str
    timeframe: str
    start: str
    end: str


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")  # allow strategy-specific fields (e.g. rebalance)
    type: str
    entry_long_signal: str = ""
    entry_long_threshold: float = 0.0
    entry_short_signal: str = ""
    entry_short_threshold: float = 0.0
    risk_per_trade: float = 0.01
    leverage: int = 1
    stop_loss_atr_mult: float = 2.0
    take_profit_rr: float = 1.5
    enable_breakout_levels: bool = True
    enable_short3_level: bool = True
    allowed_entry_hours_utc: list[int] = Field(default_factory=list)
    allowed_long_entry_hours_utc: list[int] = Field(default_factory=list)
    allowed_short_entry_hours_utc: list[int] = Field(default_factory=list)
    enabled_long_levels: list[str] = Field(default_factory=list)
    enabled_short_levels: list[str] = Field(default_factory=list)
    tp_fracs_by_level: dict[str, float] = Field(default_factory=dict)
    leg_weights_by_level: dict[str, float] = Field(default_factory=dict)
    min_hold_bars: int = 0
    sl_bars_confirm: int = 1
    pos_frac_long: float = 0.0
    pos_frac_short: float = 0.0


class FeeConfig(BaseModel):
    maker: float = 0.0002
    taker: float = 0.0005


class MarginConfig(BaseModel):
    mode: str = "isolated"
    maintenance_rate: float = 0.004


class FundingConfig(BaseModel):
    enabled: bool = False


class SlippageConfig(BaseModel):
    enabled: bool = False
    spread_bps: float = 5.0
    impact_coeff: float = 0.1


class ExecutionConfig(BaseModel):
    match_mode: str = "next_bar_open"
    fees: FeeConfig = FeeConfig()
    margin: MarginConfig = MarginConfig()
    funding: FundingConfig = FundingConfig()
    slippage: SlippageConfig = SlippageConfig()


class EvaluationConfig(BaseModel):
    risk_free_rate: float = 0.0
    metrics: list[str] = Field(default_factory=list)


class TrialLoggerConfig(BaseModel):
    enabled: bool = True
    registry: str = "backtest.result/experiments/registry.json"


class DefenseConfig(BaseModel):
    trial_logger: TrialLoggerConfig = TrialLoggerConfig()


# ── Top-level model ──────────────────────────────────────────────────────────

class BacktestConfig(BaseModel):
    backtest: BacktestMeta
    data: DataConfig
    signals: dict[str, dict]
    strategy: StrategyConfig
    execution: ExecutionConfig
    evaluation: EvaluationConfig
    defense: DefenseConfig


# ── Loader function ──────────────────────────────────────────────────────────

def load_config(path: str) -> BacktestConfig:
    """Read a YAML config file and return a validated BacktestConfig.

    Raises:
        FileNotFoundError: If the config file does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return BacktestConfig(**raw)
