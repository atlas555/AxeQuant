"""Strategy plugin registry — parallel to SignalRegistry."""

from __future__ import annotations

from typing import Type

from app.services.backtestsys_plugin.strategies.base import Strategy


class StrategyRegistry:
    """Registry of available Strategy implementations."""

    _registry: dict[str, Type[Strategy]] = {}

    @classmethod
    def register(cls, name: str):
        """Decorator to register a Strategy subclass under a given name."""

        def decorator(strategy_cls: Type[Strategy]) -> Type[Strategy]:
            cls._registry[name] = strategy_cls
            return strategy_cls

        return decorator

    @classmethod
    def create(cls, name: str, **params) -> Strategy:
        """Instantiate a registered strategy by name.

        Raises:
            KeyError: If no strategy is registered under the given name.
        """
        if name not in cls._registry:
            raise KeyError(
                f"Strategy '{name}' not registered. "
                f"Available: {list(cls._registry)}"
            )
        return cls._registry[name](**params)

    @classmethod
    def create_from_config(cls, name: str, cfg) -> Strategy:
        """Instantiate a registered strategy via its ``from_config`` classmethod.

        Each strategy class must implement ``from_config(cfg)`` which knows
        how to extract the relevant fields from a StrategyConfig.

        Raises:
            KeyError: If no strategy is registered under the given name.
        """
        if name not in cls._registry:
            raise KeyError(
                f"Strategy '{name}' not registered. "
                f"Available: {list(cls._registry)}"
            )
        return cls._registry[name].from_config(cfg)

    @classmethod
    def available(cls) -> list[str]:
        """Return a sorted list of all registered strategy names."""
        return sorted(cls._registry.keys())
