"""Optimization result reporting — markdown + JSON serialization."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OptResult:
    """Result of a single optimization iteration."""

    iteration: int
    metric: float
    config: dict[str, Any]
    desc: str
    keep: bool
    elapsed: float = 0.0


@dataclass
class OptimizationReport:
    """Full optimization run report."""

    baseline_metric: float
    final_metric: float
    iterations: int
    log: list[OptResult]
    final_config: dict[str, Any]
    wfa: dict | None = None

    @property
    def improvement_pct(self) -> float:
        if self.baseline_metric == 0:
            return 0.0
        return (self.final_metric - self.baseline_metric) / abs(self.baseline_metric) * 100

    @property
    def kept(self) -> int:
        return sum(1 for r in self.log if r.keep)

    def to_markdown(self) -> str:
        lines = [
            "# Optimization Report",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Baseline | {self.baseline_metric:.4f} |",
            f"| Final | **{self.final_metric:.4f}** |",
            f"| Improvement | {self.improvement_pct:+.1f}% |",
            f"| Iterations | {self.iterations} |",
            f"| Kept | {self.kept}/{len(self.log)} |",
            "",
            "## Final Config",
            "",
            "```json",
            json.dumps(self.final_config, indent=2),
            "```",
            "",
            "## Iteration Log",
            "",
            "| Iter | Metric | Keep | Description |",
            "|------|--------|------|-------------|",
        ]

        for r in self.log:
            k = "Y" if r.keep else "-"
            lines.append(f"| {r.iteration} | {r.metric:.4f} | {k} | {r.desc} |")

        if self.wfa:
            lines += [
                "",
                "## WFA Validation",
                "",
                "| Round | IS Period | OOS Period | IS Sharpe | OOS Sharpe |",
                "|-------|-----------|------------|-----------|------------|",
            ]
            for i, (is_s, is_e, oos_s, oos_e) in enumerate(self.wfa["periods"]):
                lines.append(
                    f"| {i} | {is_s}..{is_e} | {oos_s}..{oos_e} "
                    f"| {self.wfa['is_sharpes'][i]:.4f} "
                    f"| {self.wfa['oos_sharpes'][i]:.4f} |"
                )
            lines += [
                "",
                f"**Efficiency: {self.wfa['efficiency']:.4f} "
                f"-> {self.wfa['verdict']}**",
            ]

        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "baseline_metric": self.baseline_metric,
            "final_metric": self.final_metric,
            "improvement_pct": self.improvement_pct,
            "iterations": self.iterations,
            "kept": self.kept,
            "final_config": self.final_config,
            "log": [
                {"iter": r.iteration, "metric": r.metric, "keep": r.keep,
                 "desc": r.desc, "elapsed": r.elapsed}
                for r in self.log
            ],
            "wfa": self.wfa,
        }, indent=2)

    def save(self, path: str, fmt: str = "md") -> None:
        content = self.to_markdown() if fmt == "md" else self.to_json()
        with open(path, "w") as f:
            f.write(content)
