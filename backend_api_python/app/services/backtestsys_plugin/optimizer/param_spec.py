"""Declarative parameter specification and container.

Each parameter carries metadata (layer, bounds, danger flag) that drives
the optimizer's search strategy.

Layers:
    0 = Signal params (channel shape, smoothing). Rarely tuned.
    1 = Structure params (which levels, long/short, min hold). Optimized first.
    2 = Sizing params (pos frac, TP fracs). Optimized after structure is set.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParamSpec:
    """Specification for a single optimizable parameter."""

    name: str
    default: Any
    layer: int  # 0=signal, 1=structure, 2=sizing
    bounds: tuple[float, float] | None = None  # continuous range
    step: float | None = None  # grid step within bounds
    options: list | None = None  # discrete choices (overrides bounds)
    danger: bool = False  # historically hurts — skip or deprioritize
    description: str = ""

    def get_grid(self) -> list:
        """Generate search grid from bounds/step or options."""
        if self.options is not None:
            return list(self.options)
        if self.bounds is not None and self.step is not None:
            lo, hi = self.bounds
            vals = []
            v = lo
            while v <= hi + self.step * 0.01:  # float tolerance
                vals.append(round(v, 10))
                v += self.step
            return vals
        return [self.default]

    def get_neighborhood(self, current: Any, n_steps: int = 1) -> list:
        """Get ±n_steps around current value (for Layer 0 fine-tuning)."""
        if self.options is not None:
            return list(self.options)
        if self.bounds is not None and self.step is not None:
            lo, hi = self.bounds
            vals = set()
            for d in range(-n_steps, n_steps + 1):
                v = round(current + d * self.step, 10)
                if lo <= v <= hi:
                    vals.add(v)
            return sorted(vals)
        return [current]

    def validate(self, value: Any) -> bool:
        """Check if value is within allowed range."""
        if self.options is not None:
            return value in self.options
        if self.bounds is not None:
            lo, hi = self.bounds
            if isinstance(value, (int, float)):
                return lo <= value <= hi
        return True


class StrategyParams:
    """Container for a set of parameter specifications with current values.

    Provides grid generation, validation, and serialization.
    """

    def __init__(self, specs: list[ParamSpec]) -> None:
        self._specs: dict[str, ParamSpec] = {s.name: s for s in specs}
        self._values: dict[str, Any] = {s.name: copy.deepcopy(s.default) for s in specs}

    @property
    def spec_names(self) -> list[str]:
        return list(self._specs.keys())

    def get(self, name: str) -> Any:
        return self._values[name]

    def set(self, name: str, value: Any) -> None:
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError(f"Unknown parameter: {name}")
        if not spec.validate(value):
            raise ValueError(
                f"Parameter '{name}' value {value!r} outside bounds "
                f"{spec.bounds or spec.options}"
            )
        self._values[name] = value

    def get_spec(self, name: str) -> ParamSpec:
        return self._specs[name]

    def get_grid(self, name: str) -> list:
        return self._specs[name].get_grid()

    def get_neighborhood(self, name: str, n_steps: int = 1) -> list:
        return self._specs[name].get_neighborhood(self._values[name], n_steps)

    def validate(self, name: str, value: Any) -> bool:
        spec = self._specs.get(name)
        if spec is None:
            return False
        return spec.validate(value)

    def get_layer(self, layer: int) -> list[ParamSpec]:
        """Get all specs for a given layer, sorted by name."""
        return sorted(
            [s for s in self._specs.values() if s.layer == layer],
            key=lambda s: s.name,
        )

    def get_values(self) -> dict[str, Any]:
        return copy.deepcopy(self._values)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._values)

    @classmethod
    def from_dict(cls, specs: list[ParamSpec], d: dict[str, Any]) -> StrategyParams:
        params = cls(specs)
        for name, value in d.items():
            if name in params._specs:
                params._values[name] = value
        return params

    def clone(self) -> StrategyParams:
        """Deep copy of this parameter set."""
        new = StrategyParams(list(self._specs.values()))
        new._values = copy.deepcopy(self._values)
        return new

    def summary(self) -> str:
        """Human-readable summary grouped by layer."""
        lines = []
        for layer in sorted(set(s.layer for s in self._specs.values())):
            label = {0: "Signal", 1: "Structure", 2: "Sizing"}.get(layer, f"L{layer}")
            lines.append(f"  [{label}]")
            for spec in self.get_layer(layer):
                v = self._values[spec.name]
                danger = " ⚠" if spec.danger else ""
                lines.append(f"    {spec.name} = {v}{danger}")
        return "\n".join(lines)
