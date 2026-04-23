"""Translate ScriptStrategy ctx.buy/sell/close semantics → exchange order kwargs.

Pure function. No exchange calls, no I/O. Caller submits the returned dict to
the CCXT adapter.
"""

from __future__ import annotations

from typing import Any


def translate_action(action: dict[str, Any], symbol: str,
                     default_type: str = "market") -> dict[str, Any]:
    """Convert a ScriptContext action dict to exchange order kwargs.

    ScriptContext appends to `ctx._orders` entries like:
        {"action": "buy", "price": 50000, "amount": 0.1}
        {"action": "sell", "price": None, "amount": 0.05}
        {"action": "close"}

    Returns CCXT-style order kwargs:
        {"symbol": "BTC/USDT", "side": "buy", "type": "limit",
         "amount": 0.1, "price": 50000}
    """
    op = action.get("action")

    if op in ("buy", "sell"):
        amount = action.get("amount")
        price = action.get("price")
        if amount is None or amount <= 0:
            raise ValueError(f"{op} requires positive amount (got {amount!r})")
        order = {
            "symbol": symbol,
            "side": op,
            "type": "limit" if price is not None else default_type,
            "amount": float(amount),
        }
        if price is not None:
            order["price"] = float(price)
        return order

    if op == "close":
        # Caller inspects current position to determine side + amount.
        return {"symbol": symbol, "type": "close"}

    raise ValueError(f"unknown action {op!r}")


def translate_close(position: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    """Given a current position dict, return the order kwargs that flattens it."""
    size = float(position.get("size") or 0.0)
    side = position.get("side")
    if size <= 0 or side not in ("long", "short"):
        return None  # nothing to close
    return {
        "symbol": symbol,
        "side": "sell" if side == "long" else "buy",
        "type": "market",
        "amount": size,
        "params": {"reduceOnly": True},
    }
