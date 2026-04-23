"""Param space schema for autoresearch requests.

Incoming JSON shape:
  {
    "asr_length": {"type": "int", "range": [50, 150], "step": 2, "layer": 1},
    "band_mult":  {"type": "float", "range": [0.1, 0.5], "step": 0.01, "layer": 2},
    "enable_short3": {"type": "bool", "layer": 1},
    "tp_fracs.long1": {"type": "choice", "values": [0.5, 0.75, 1.0], "layer": 2}
  }

Converted to backTestSys `ParamSpec` list → `StrategyParams`.
"""

from __future__ import annotations

from typing import Any


MAX_SEARCH_SIZE = 100_000  # refuse jobs above this — operator must tighten ranges


def parse_param_space(payload: dict[str, dict]) -> list:
    """Validate + convert JSON → list of backTestSys ParamSpec instances.

    Raises ValueError on malformed input. No I/O.
    """
    from app.services.backtestsys_plugin.optimizer.param_spec import ParamSpec

    if not isinstance(payload, dict) or not payload:
        raise ValueError("param_space must be a non-empty dict")

    specs: list = []
    for name, spec in payload.items():
        if not isinstance(spec, dict):
            raise ValueError(f"param '{name}' spec must be a dict")
        t = spec.get("type")
        layer = int(spec.get("layer", 1))

        if t == "int":
            rng = spec.get("range")
            if not rng or len(rng) != 2:
                raise ValueError(f"param '{name}' int requires range [lo, hi]")
            step = spec.get("step", 1)
            default = spec.get("default", rng[0])
            specs.append(ParamSpec(
                name=name, default=int(default), layer=layer,
                bounds=(float(rng[0]), float(rng[1])), step=float(step),
            ))
        elif t == "float":
            rng = spec.get("range")
            if not rng or len(rng) != 2:
                raise ValueError(f"param '{name}' float requires range [lo, hi]")
            step = spec.get("step", 0.01)
            default = spec.get("default", rng[0])
            specs.append(ParamSpec(
                name=name, default=float(default), layer=layer,
                bounds=(float(rng[0]), float(rng[1])), step=float(step),
            ))
        elif t == "bool":
            default = spec.get("default", False)
            specs.append(ParamSpec(
                name=name, default=bool(default), layer=layer,
                options=[True, False],
            ))
        elif t == "choice":
            vals = spec.get("values")
            if not vals:
                raise ValueError(f"param '{name}' choice requires values list")
            default = spec.get("default", vals[0])
            specs.append(ParamSpec(
                name=name, default=default, layer=layer, options=list(vals),
            ))
        else:
            raise ValueError(f"param '{name}' has unknown type: {t!r}")

    return specs


def estimate_space_size(specs: list) -> int:
    """Rough combinatorial size of the grid — gates oversized jobs."""
    size = 1
    for s in specs:
        if s.options is not None:
            size *= max(1, len(s.options))
        elif s.bounds is not None and s.step is not None:
            lo, hi = s.bounds
            size *= max(1, int((hi - lo) / s.step) + 1)
    return size


def check_size_or_raise(specs: list, limit: int = MAX_SEARCH_SIZE) -> int:
    size = estimate_space_size(specs)
    if size > limit:
        raise ValueError(
            f"param space too large: {size:,} combinations > limit {limit:,}. "
            f"Tighten ranges or use choice instead of continuous."
        )
    return size
