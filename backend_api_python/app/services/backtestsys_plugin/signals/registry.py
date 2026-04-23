"""Signal registry for discovering and instantiating signals by name."""

from __future__ import annotations

from typing import Type

from app.services.backtestsys_plugin.signals.base import Signal


class SignalRegistry:
    """Registry of available Signal implementations."""

    _registry: dict[str, Type[Signal]] = {}

    @classmethod
    def register(cls, name: str):
        """Decorator to register a Signal subclass under a given name."""

        def decorator(signal_cls: Type[Signal]) -> Type[Signal]:
            cls._registry[name] = signal_cls
            return signal_cls

        return decorator

    @classmethod
    def create(cls, name: str, **params) -> Signal:
        """Instantiate a registered signal by name.

        Raises:
            KeyError: If no signal is registered under the given name.
        """
        if name not in cls._registry:
            raise KeyError(f"Unknown signal: '{name}'")
        return cls._registry[name](**params)

    @classmethod
    def auto_discover(cls):
        """Import all modules in signals/technical/ to trigger @register decorators."""
        import importlib
        import pkgutil

        import app.services.backtestsys_plugin.signals.technical as pkg

        for _importer, modname, _ispkg in pkgutil.iter_modules(pkg.__path__):
            importlib.import_module(f"app.services.backtestsys_plugin.signals.technical.{modname}")

    @classmethod
    def available(cls) -> list[str]:
        """Return a sorted list of all registered signal names."""
        return sorted(cls._registry.keys())
