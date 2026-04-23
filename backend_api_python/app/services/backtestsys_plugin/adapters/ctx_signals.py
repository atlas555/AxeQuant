"""Expose backTestSys signals to QD's StrategyScriptContext as `ctx.signal(...)`.

Design:
- `attach_signals(ctx)` binds a callable `ctx.signal(name, **params)` at context
  construction. Called BEFORE the user script runs, so the sandbox (`safe_exec`)
  sees an already-bound method — no import required from user code.

- Signals are computed on the full bars dataframe and cached per (name, params).
  Per-bar access returns a `_RowView` that resolves attributes against:
    1. `SignalFrame.metadata["channels"]` DataFrame columns (preferred — multi-column signals)
    2. `SignalFrame.values` Series (fallback — single-series signals)

- On first access we auto-discover the registered signal modules.
"""

from __future__ import annotations

from typing import Any

from app.services.backtestsys_plugin.signals.registry import SignalRegistry

_discovered = False


def _ensure_discovered() -> None:
    global _discovered
    if not _discovered:
        SignalRegistry.auto_discover()
        _discovered = True


class _RowView:
    """Attribute-style access to a row of a signal's per-bar output.

    Resolution order:
      1. metadata["channels"] DataFrame column at current index
      2. values Series at current index
    """

    __slots__ = ("_channels_row", "_value")

    def __init__(self, channels_row, value):
        self._channels_row = channels_row
        self._value = value

    def __getattr__(self, name: str) -> Any:
        if name == "value":
            return self._value
        if self._channels_row is not None:
            try:
                return self._channels_row[name]
            except KeyError:
                pass
        raise AttributeError(
            f"Signal has no field '{name}'. "
            f"Available: {list(self._channels_row.index) if self._channels_row is not None else ['value']}"
        )


class _SignalProxy:
    """Memoized per-bar signal accessor, bound to a StrategyScriptContext."""

    def __init__(self, ctx):
        self._ctx = ctx
        self._cache: dict[tuple, Any] = {}

    def __call__(self, name: str, **params) -> _RowView:
        _ensure_discovered()
        key = (name, tuple(sorted(params.items())))
        frame = self._cache.get(key)
        if frame is None:
            signal = SignalRegistry.create(name, **params)
            frame = signal.compute(self._ctx._bars_df)
            self._cache[key] = frame

        idx = self._ctx.current_index
        channels = frame.metadata.get("channels") if frame.metadata else None
        channels_row = channels.iloc[idx] if channels is not None else None
        value = frame.values.iloc[idx] if frame.values is not None else None
        return _RowView(channels_row, value)


def attach_signals(ctx) -> None:
    """Install ctx.signal on a StrategyScriptContext instance."""
    ctx.signal = _SignalProxy(ctx)
