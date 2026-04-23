# Upstream Patch Registry

Every file in this fork that diverges from upstream `brokermr810/QuantDinger`.
Goal: **make every merge deterministic**. Before merging upstream, inspect this
list; after merging, verify each patch still applies cleanly.

**Policy:**
- Keep patches small (ideally ≤ 5 lines per file).
- Prefer adding new files over editing upstream files.
- Document *what*, *where*, *why*, and *how to remove*.

---

## backend_api_python/app/services/strategy_script_runtime.py

- **Upstream commit at patch time:** `65d8f1a` (2026-04-23)
- **Lines added:** +5 after line 125 (end of `StrategyScriptContext.__init__`)
- **What:** import `attach_signals` and call it on the new context instance
- **Why:** expose backTestSys signals to user ScriptStrategy code via `ctx.signal(name, **params)` without requiring imports (which `safe_exec` blocks)
- **Removal:** delete the 5-line patch block; `backtestsys_plugin/adapters/ctx_signals.py` becomes unused dead code (also delete if desired)
- **Risk on upstream merge:** if upstream renames `StrategyScriptContext` or restructures `__init__`, re-apply manually; patch is localized and idempotent
