"""Backtest report and artifact generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.services.backtestsys_plugin.config.loader import BacktestConfig
from app.services.backtestsys_plugin.core.types import Trade
from app.services.backtestsys_plugin.evaluation.metrics import MetricsReport


def _pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    return f"{value:+.2%}"


def _num(value: float | None, digits: int = 3) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    return f"{value:.{digits}f}"


class MarkdownReporter:
    """Generate and save markdown backtest reports."""

    @staticmethod
    def generate(
        metrics: MetricsReport,
        config: BacktestConfig,
        equity_curve: list[float],
    ) -> str:
        """Return a markdown string summarising the backtest run."""
        name = config.backtest.name
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        lines: list[str] = []
        lines.append(f"# Backtest Report: {name}\n")

        lines.append("## Metadata\n")
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        lines.append(f"| Generated | {now} |")
        lines.append(f"| Symbol | {config.data.symbol} |")
        lines.append(f"| Timeframe | {config.data.timeframe} |")
        lines.append(f"| Period | {config.data.start} — {config.data.end} |")
        lines.append(f"| Initial Capital | {config.backtest.initial_capital:,.2f} |")
        lines.append(f"| Equity Points | {len(equity_curve)} |")
        lines.append("")

        lines.append("## Performance Summary\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Return | {metrics.total_return:.4f} |")
        lines.append(f"| Annual Return | {metrics.annual_return:.4f} |")
        lines.append(f"| Sharpe Ratio | {metrics.sharpe_ratio:.2f} |")
        lines.append(f"| Sortino Ratio | {metrics.sortino_ratio:.2f} |")
        lines.append(f"| Calmar Ratio | {metrics.calmar_ratio:.2f} |")
        lines.append(f"| Max Drawdown | {metrics.max_drawdown:.4f} |")
        lines.append("")

        lines.append("## Trade Statistics\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Trades | {metrics.total_trades} |")
        lines.append(f"| Win Rate | {metrics.win_rate:.2%} |")
        lines.append(f"| Profit Factor | {metrics.profit_factor:.2f} |")
        lines.append(f"| Expectancy | {metrics.expectancy:.2f} |")
        lines.append("")

        lines.append("## Futures\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Funding PnL | {metrics.total_funding_pnl:.2f} |")
        lines.append(f"| Liquidations | {metrics.total_liquidations} |")
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def save(content: str, path: str | Path) -> Path:
        """Write *content* to *path*, creating parent dirs as needed."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p


class HtmlReporter:
    """Generate interactive HTML backtest dashboard using Plotly."""

    def generate(
        self,
        metrics: MetricsReport,
        equity_curve: list[float],
        *,
        bars: pd.DataFrame | None = None,
        trades: list[Trade] | None = None,
        config: BacktestConfig | None = None,
        equity_start_bar: int = 0,
    ) -> str:
        payload = BacktestArtifactWriter.build_payload(
            metrics=metrics,
            config=config,
            equity_curve=equity_curve,
            trades=trades or [],
            bars=bars,
            equity_start_bar=equity_start_bar,
        )
        return self.generate_from_payload(payload)

    def generate_from_payload(self, payload: dict[str, Any]) -> str:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        metrics = payload["metrics"]
        bars = payload["bars"]
        trades = payload["trades"]
        equity_points = payload["equity"]

        summary_cards = self._render_summary_cards(metrics, payload["meta"])
        equity_div = self._build_equity_chart(equity_points, make_subplots, go)
        overlay_div = self._build_trade_overlay_chart(bars, trades, go)
        analytics_div = self._build_trade_analytics(trades, equity_points, go, make_subplots)
        trade_explorer_div = self._build_trade_explorer_div(go)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{payload['meta']['name']} Dashboard</title>
  <style>
    :root {{
      --bg: #0f172a;
      --panel: #111827;
      --muted: #94a3b8;
      --text: #e5e7eb;
      --accent: #38bdf8;
      --accent-2: #22c55e;
      --danger: #f43f5e;
      --border: #1f2937;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #020617 0%, #0f172a 100%);
      color: var(--text);
    }}
    .shell {{ max-width: 1400px; margin: 0 auto; padding: 28px; }}
    .hero {{
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 18px;
      margin-bottom: 24px;
    }}
    .panel {{
      background: rgba(15, 23, 42, 0.88);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 18px 20px;
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.28);
    }}
    .hero h1 {{ margin: 0 0 8px; font-size: 30px; }}
    .muted {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .card {{
      background: rgba(17, 24, 39, 0.95);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
    }}
    .card .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .card .value {{ font-size: 24px; margin-top: 6px; font-weight: 700; }}
    .tabs {{ display: flex; gap: 10px; margin: 24px 0 16px; flex-wrap: wrap; }}
    .tab-btn {{
      border: 1px solid var(--border);
      background: rgba(17, 24, 39, 0.95);
      color: var(--text);
      border-radius: 999px;
      padding: 10px 16px;
      cursor: pointer;
    }}
    .tab-btn.active {{ background: var(--accent); color: #082f49; border-color: transparent; font-weight: 700; }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .section-title {{ margin: 0 0 12px; font-size: 22px; }}
    .meta-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    .meta-table td {{
      padding: 8px 0;
      border-bottom: 1px solid rgba(148, 163, 184, 0.12);
    }}
    .plot-wrap > div {{ width: 100% !important; }}
    .controls {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin: 10px 0 18px;
    }}
    .controls label {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
    }}
    .controls select {{
      min-width: 180px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #0b1220;
      color: var(--text);
      padding: 10px 12px;
    }}
    .controls input {{
      min-width: 220px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #0b1220;
      color: var(--text);
      padding: 10px 12px;
    }}
    .controls button {{
      align-self: end;
      min-width: 160px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #0b1220;
      color: var(--text);
      padding: 10px 12px;
      cursor: pointer;
    }}
    .detail-card {{
      margin: 0 0 16px;
      background: rgba(17, 24, 39, 0.95);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
    }}
    @media (max-width: 1100px) {{
      .hero {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 680px) {{
      .shell {{ padding: 18px; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <div class="panel">
        <div class="muted">Backtest Dashboard</div>
        <h1>{payload['meta']['name']}</h1>
        <div class="muted">{payload['meta']['symbol']} · {payload['meta']['timeframe']} · {payload['meta']['start']} → {payload['meta']['end']}</div>
        <div class="grid">{summary_cards}</div>
      </div>
      <div class="panel">
        <h2 class="section-title">Run Metadata</h2>
        <table class="meta-table">
          <tr><td>Initial Capital</td><td>{payload['meta']['initial_capital']:,.2f}</td></tr>
          <tr><td>Trades</td><td>{metrics['total_trades']}</td></tr>
          <tr><td>Win Rate</td><td>{_pct(metrics['win_rate'])}</td></tr>
          <tr><td>Profit Factor</td><td>{_num(metrics['profit_factor'])}</td></tr>
          <tr><td>Funding PnL</td><td>{_num(metrics['total_funding_pnl'], 2)}</td></tr>
          <tr><td>Liquidations</td><td>{metrics['total_liquidations']}</td></tr>
        </table>
      </div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" data-tab="summary">Summary</button>
      <button class="tab-btn" data-tab="equity">Equity & Drawdown</button>
      <button class="tab-btn" data-tab="overlay">Trade Overlay</button>
      <button class="tab-btn" data-tab="analytics">Trade Analytics</button>
    </div>

    <section class="tab-panel active panel" id="tab-summary">
      <h2 class="section-title">Summary</h2>
      <p class="muted">Standard backtest artifacts now include Markdown, JSON, and this interactive dashboard. Use the next tabs for bar-level and trade-level detail.</p>
      <div class="plot-wrap">{equity_div}</div>
    </section>

    <section class="tab-panel panel" id="tab-equity">
      <h2 class="section-title">Equity & Drawdown</h2>
      <div class="plot-wrap">{equity_div}</div>
    </section>

    <section class="tab-panel panel" id="tab-overlay">
      <h2 class="section-title">Trade Overlay</h2>
      <div class="muted">Candlestick chart with entry and exit markers derived from the trade log.</div>
      <div class="plot-wrap">{overlay_div}</div>
      <h3 class="section-title" style="font-size:18px;margin-top:18px;">Trade Replay</h3>
      <div class="controls">
        <button id="trade-replay-prev" type="button">Prev</button>
        <button id="trade-replay-play" type="button">Play</button>
        <button id="trade-replay-next" type="button">Next</button>
      </div>
      <div id="trade-replay-summary" class="muted">Replay follows the selected trade, or the first visible trade when nothing is selected.</div>
      <div class="plot-wrap"><div id="trade-replay-chart"></div></div>
    </section>

    <section class="tab-panel panel" id="tab-analytics">
      <h2 class="section-title">Trade Analytics</h2>
      <div class="muted">PnL Distribution, hold-time distribution, MAE / MFE excursion analysis, and monthly return profile.</div>
      <div class="controls">
        <label>Side<select id="trade-side-filter"><option value="all">all</option><option value="long">long</option><option value="short">short</option></select></label>
        <label>X Axis<select id="trade-x-axis"><option value="net_pnl">net_pnl</option><option value="hold_bars">hold_bars</option><option value="mae">mae</option><option value="mfe">mfe</option></select></label>
        <label>Y Axis<select id="trade-y-axis"><option value="mfe">mfe</option><option value="net_pnl">net_pnl</option><option value="hold_bars">hold_bars</option><option value="mae">mae</option></select></label>
        <label>Filter Field<select id="trade-filter-field"><option value="net_pnl">net_pnl</option><option value="hold_bars">hold_bars</option><option value="mae">mae</option><option value="mfe">mfe</option></select></label>
        <label>Min<input id="trade-filter-min" type="number" step="any" placeholder="optional"></label>
        <label>Max<input id="trade-filter-max" type="number" step="any" placeholder="optional"></label>
        <button id="trade-apply-filter" type="button">Apply Filter</button>
        <button id="trade-reset-filter" type="button">Reset Filter</button>
        <button id="trade-export-json" type="button">Export JSON</button>
        <button id="trade-export-csv" type="button">Export CSV</button>
        <button id="trade-clear-selection" type="button">Clear Selection</button>
      </div>
      <div id="trade-selection-summary" class="muted">Selection: all trades</div>
      <div id="selected-trade-card" class="detail-card muted">Click a trade point to inspect it. Box-select trades to brush a subset.</div>
      <div class="plot-wrap">{trade_explorer_div}</div>
      <div class="plot-wrap">{analytics_div}</div>
    </section>
  </div>
  <script>
    const barPayload = {json.dumps(bars, ensure_ascii=False)};
    const tradePayload = {json.dumps(trades, ensure_ascii=False)};
    const buttons = Array.from(document.querySelectorAll('.tab-btn'));
    const panels = Array.from(document.querySelectorAll('.tab-panel'));
    buttons.forEach((button) => {{
      button.addEventListener('click', () => {{
        const target = button.dataset.tab;
        buttons.forEach((b) => b.classList.toggle('active', b === button));
        panels.forEach((panel) => panel.classList.toggle('active', panel.id === `tab-${{target}}`));
      }});
    }});

    const sideFilter = document.getElementById('trade-side-filter');
    const xAxisSelect = document.getElementById('trade-x-axis');
    const yAxisSelect = document.getElementById('trade-y-axis');
    const tradeExplorerDiv = document.getElementById('trade-explorer');
    const tradeClearSelection = document.getElementById('trade-clear-selection');
    const tradeFilterField = document.getElementById('trade-filter-field');
    const tradeFilterMin = document.getElementById('trade-filter-min');
    const tradeFilterMax = document.getElementById('trade-filter-max');
    const tradeApplyFilter = document.getElementById('trade-apply-filter');
    const tradeResetFilter = document.getElementById('trade-reset-filter');
    const tradeExportJson = document.getElementById('trade-export-json');
    const tradeExportCsv = document.getElementById('trade-export-csv');
    const tradeSelectionSummary = document.getElementById('trade-selection-summary');
    const selectedTradeCard = document.getElementById('selected-trade-card');
    const tradeOverlayDiv = document.getElementById('trade-overlay-chart');
    const tradeReplayPrev = document.getElementById('trade-replay-prev');
    const tradeReplayPlay = document.getElementById('trade-replay-play');
    const tradeReplayNext = document.getElementById('trade-replay-next');
    const tradeReplayChart = document.getElementById('trade-replay-chart');
    const tradeReplaySummary = document.getElementById('trade-replay-summary');
    let selectedTradeIds = [];
    let focusedTradeId = null;
    let numericFilter = {{ field: 'net_pnl', min: null, max: null }};
    let tradeReplayTimer = null;
    let tradeReplayStep = 0;

    function sideFilteredTrades() {{
      if (!sideFilter) return tradePayload;
      return tradePayload
        .map((trade, index) => ({{ ...trade, trade_id: index }}))
        .filter((trade) => sideFilter.value === 'all' || trade.side === sideFilter.value);
    }}

    function brushedTrades() {{
      const rows = rangeFilteredTrades();
      if (!selectedTradeIds.length) return rows;
      const selected = new Set(selectedTradeIds);
      return rows.filter((trade) => selected.has(trade.trade_id));
    }}

    function rangeFilteredTrades() {{
      const rows = sideFilteredTrades();
      const field = numericFilter.field || 'net_pnl';
      return rows.filter((trade) => {{
        const value = Number(trade[field]);
        if (!Number.isFinite(value)) return false;
        if (numericFilter.min !== null && value < numericFilter.min) return false;
        if (numericFilter.max !== null && value > numericFilter.max) return false;
        return true;
      }});
    }}

    function updateSelectionSummary() {{
      if (!tradeSelectionSummary) return;
      const rows = brushedTrades();
      const pnl = rows.reduce((acc, trade) => acc + (trade.net_pnl || 0), 0);
      if (!selectedTradeIds.length) {{
        tradeSelectionSummary.textContent = `Selection: all ${{rows.length}} side-filtered trades · Net PnL ${{pnl.toFixed(2)}}`;
        return;
      }}
      tradeSelectionSummary.textContent = `Selection: ${{rows.length}} brushed trades · Net PnL ${{pnl.toFixed(2)}}`;
    }}

    function updateSelectedTradeCard() {{
      if (!selectedTradeCard) return;
      const rows = rangeFilteredTrades();
      const trade = rows.find((row) => row.trade_id === focusedTradeId);
      if (!trade) {{
        selectedTradeCard.textContent = 'Click a trade point to inspect it. Box-select trades to brush a subset.';
        return;
      }}
      selectedTradeCard.textContent = `Selected trade #${{trade.trade_id + 1}} · ${{
        trade.side
      }} · Net PnL ${{
        Number(trade.net_pnl || 0).toFixed(2)
      }} · Hold ${{
        trade.hold_bars
      }} bars · Entry ${{
        Number(trade.entry_price || 0).toFixed(2)
      }} · Exit ${{
        Number(trade.exit_price || 0).toFixed(2)
      }}`;
    }}

    function downloadTradeSelection(format) {{
      const rows = brushedTrades();
      if (!rows.length) return;
      const filter_metadata = {{
        side_filter: sideFilter?.value || 'all',
        numeric_filter: {{
          field: numericFilter.field,
          min: numericFilter.min,
          max: numericFilter.max,
        }},
        selected_trade_ids: [...selectedTradeIds],
      }};
      let content = '';
      let mime = 'application/json';
      let ext = 'json';
      if (format === 'csv') {{
        const headers = Object.keys(rows[0]);
        const escaped = (value) => `"${{String(value ?? '').replaceAll('"', '""')}}"`;
        const lines = [
          `# side_filter=${{filter_metadata.side_filter}}`,
          `# numeric_filter=${{JSON.stringify(filter_metadata.numeric_filter)}}`,
          `# selected_trade_ids=${{JSON.stringify(filter_metadata.selected_trade_ids)}}`,
          headers.join(','),
          ...rows.map((row) => headers.map((header) => escaped(row[header])).join(',')),
        ];
        content = lines.join('\\n');
        mime = 'text/csv;charset=utf-8';
        ext = 'csv';
      }} else {{
        content = JSON.stringify({{ filter_metadata, trades: rows }}, null, 2);
      }}
      const blob = new Blob([content], {{ type: mime }});
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `trade_selection.${{ext}}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    }}

    function replayTargetTrade() {{
      const rows = rangeFilteredTrades();
      return rows.find((row) => row.trade_id === focusedTradeId) || rows[0] || null;
    }}

    function renderTradeReplay() {{
      if (!tradeReplayChart || !window.Plotly || !barPayload.length) return;
      const trade = replayTargetTrade();
      if (!trade) {{
        tradeReplaySummary.textContent = 'Replay unavailable because no trade is visible under the current filters.';
        Plotly.react(tradeReplayChart, [], {{ template: 'plotly_dark', height: 420, title: 'Trade Replay' }}, {{ responsive: true }});
        return;
      }}

      const left = Math.max(0, trade.entry_bar - 2);
      const right = Math.min(barPayload.length - 1, trade.exit_bar + 2);
      const windowBars = barPayload.slice(left, right + 1);
      const maxStep = Math.max(0, trade.exit_bar - trade.entry_bar);
      tradeReplayStep = Math.max(0, Math.min(tradeReplayStep, maxStep));
      const activeBar = Math.min(trade.entry_bar + tradeReplayStep, trade.exit_bar);
      const activeTs = barPayload[activeBar]?.timestamp;
      tradeReplaySummary.textContent = `Trade #${{trade.trade_id + 1}} · step ${{tradeReplayStep + 1}} / ${{maxStep + 1}} · active bar ${{activeTs || 'n/a'}}`;

      const highs = windowBars.map((bar) => bar.high);
      const lows = windowBars.map((bar) => bar.low);
      const yTop = Math.max(...highs);
      const yBottom = Math.min(...lows);

      Plotly.react(tradeReplayChart, [{{
        x: windowBars.map((bar) => bar.timestamp),
        open: windowBars.map((bar) => bar.open),
        high: highs,
        low: lows,
        close: windowBars.map((bar) => bar.close),
        type: 'candlestick',
        name: 'Replay',
      }}, {{
        x: [barPayload[trade.entry_bar]?.timestamp, barPayload[trade.exit_bar]?.timestamp],
        y: [trade.entry_price, trade.exit_price],
        mode: 'markers+lines',
        marker: {{ size: 12, color: '#facc15', symbol: 'diamond' }},
        line: {{ color: '#facc15', width: 2, dash: 'dot' }},
        name: 'Trade Path',
      }}], {{
        template: 'plotly_dark',
        height: 420,
        title: 'Trade Replay',
        xaxis: {{ rangeslider: {{ visible: false }} }},
        margin: {{ t: 60, b: 40 }},
        shapes: activeTs ? [{{
          type: 'line',
          x0: activeTs,
          x1: activeTs,
          y0: yBottom,
          y1: yTop,
          line: {{ color: '#38bdf8', width: 3 }},
        }}] : [],
      }}, {{ responsive: true }});
    }}

    function renderTradeOverlay() {{
      if (!tradeOverlayDiv || !window.Plotly || !barPayload.length) return;
      const traces = [{{
        x: barPayload.map((bar) => bar.timestamp),
        open: barPayload.map((bar) => bar.open),
        high: barPayload.map((bar) => bar.high),
        low: barPayload.map((bar) => bar.low),
        close: barPayload.map((bar) => bar.close),
        type: 'candlestick',
        name: 'Candlestick',
      }}];
      const rows = rangeFilteredTrades();
      const entryRows = rows.filter((trade) => trade.entry_bar >= 0 && trade.entry_bar < barPayload.length);
      const exitRows = rows.filter((trade) => trade.exit_bar >= 0 && trade.exit_bar < barPayload.length);
      traces.push({{
        x: entryRows.map((trade) => barPayload[trade.entry_bar].timestamp),
        y: entryRows.map((trade) => trade.entry_price),
        mode: 'markers',
        marker: {{ symbol: 'triangle-up', size: 11, color: '#22c55e' }},
        name: 'Entries',
        text: entryRows.map((trade) => `${{trade.side}} entry · pnl ${{Number(trade.net_pnl || 0).toFixed(2)}}`),
        hovertemplate: '%{{text}}<extra></extra>',
      }});
      traces.push({{
        x: exitRows.map((trade) => barPayload[trade.exit_bar].timestamp),
        y: exitRows.map((trade) => trade.exit_price),
        mode: 'markers',
        marker: {{ symbol: 'x', size: 10, color: '#f97316' }},
        name: 'Exits',
        text: exitRows.map((trade) => `${{trade.side}} exit · pnl ${{Number(trade.net_pnl || 0).toFixed(2)}}`),
        hovertemplate: '%{{text}}<extra></extra>',
      }});

      const selected = rows.find((trade) => trade.trade_id === focusedTradeId);
      if (selected) {{
        traces.push({{
          x: [barPayload[selected.entry_bar]?.timestamp, barPayload[selected.exit_bar]?.timestamp],
          y: [selected.entry_price, selected.exit_price],
          mode: 'markers+lines',
          marker: {{ size: 16, color: '#facc15', symbol: 'diamond' }},
          line: {{ color: '#facc15', width: 3, dash: 'dot' }},
          name: 'Selected Trade',
          hovertemplate: `Selected trade<br>Entry: %{{x}}<br>Price: %{{y:.2f}}<extra></extra>`,
        }});
      }}

      Plotly.react(tradeOverlayDiv, traces, {{
        height: 760,
        template: 'plotly_dark',
        xaxis_rangeslider_visible: false,
        legend: {{ orientation: 'h' }},
        margin: {{ t: 60, b: 40 }},
      }}, {{ responsive: true }});
    }}

    function renderTradeExplorer() {{
      if (!tradeExplorerDiv || !window.Plotly || !xAxisSelect || !yAxisSelect) return;
      const rows = rangeFilteredTrades();
      const xKey = xAxisSelect.value;
      const yKey = yAxisSelect.value;
      Plotly.react(tradeExplorerDiv, [{{
        x: rows.map((trade) => trade[xKey]),
        y: rows.map((trade) => trade[yKey]),
        text: rows.map((trade, idx) => `Trade ${{idx + 1}} · ${{trade.side}}`),
        customdata: rows.map((trade) => [trade.trade_id]),
        mode: 'markers',
        marker: {{
          size: 11,
          color: rows.map((trade) => trade.net_pnl),
          colorscale: 'RdYlGn',
          showscale: true,
          colorbar: {{ title: 'Net PnL' }},
        }},
        selectedpoints: selectedTradeIds.length
          ? rows
              .map((trade, idx) => selectedTradeIds.includes(trade.trade_id) ? idx : null)
              .filter((idx) => idx !== null)
          : null,
        selected: {{
          marker: {{ size: 14, color: '#facc15', line: {{ color: '#ffffff', width: 1.5 }} }},
        }},
        unselected: {{
          marker: {{ opacity: 0.22 }},
        }},
        hovertemplate: '%{{text}}<br>' + xKey + ': %{{x:.2f}}<br>' + yKey + ': %{{y:.2f}}<extra></extra>',
      }}], {{
        template: 'plotly_dark',
        height: 520,
        title: 'Trade Explorer',
        xaxis: {{ title: xKey }},
        yaxis: {{ title: yKey }},
        dragmode: 'select',
        margin: {{ t: 60, b: 50 }},
      }}, {{ responsive: true }});
      if (tradeExplorerDiv.dataset.handlersBound !== 'true') {{
        tradeExplorerDiv.on('plotly_selected', (event) => {{
          selectedTradeIds = (event?.points || []).map((point) => point.customdata?.[0]).filter((value) => value !== undefined);
          if (selectedTradeIds.length === 1) {{
            focusedTradeId = selectedTradeIds[0];
            tradeReplayStep = 0;
          }}
          updateSelectionSummary();
          updateSelectedTradeCard();
          renderTradeOverlay();
          renderTradeExplorer();
          renderTradeReplay();
        }});
        tradeExplorerDiv.on('plotly_click', (event) => {{
          const point = event?.points?.[0];
          if (!point) return;
          focusedTradeId = point.customdata?.[0];
          tradeReplayStep = 0;
          updateSelectedTradeCard();
          renderTradeOverlay();
          renderTradeReplay();
        }});
        tradeExplorerDiv.dataset.handlersBound = 'true';
      }}
    }}

      if (sideFilter && xAxisSelect && yAxisSelect) {{
      function rerenderTrades(resetSelection = false) {{
        if (resetSelection) {{
          selectedTradeIds = [];
          focusedTradeId = null;
          tradeReplayStep = 0;
        }}
        updateSelectionSummary();
        updateSelectedTradeCard();
        renderTradeOverlay();
        renderTradeExplorer();
        renderTradeReplay();
      }}

      sideFilter.addEventListener('change', () => {{
        rerenderTrades(true);
      }});
      xAxisSelect.addEventListener('change', renderTradeExplorer);
      yAxisSelect.addEventListener('change', renderTradeExplorer);
      tradeApplyFilter?.addEventListener('click', () => {{
        numericFilter = {{
          field: tradeFilterField?.value || 'net_pnl',
          min: tradeFilterMin?.value === '' ? null : Number(tradeFilterMin?.value),
          max: tradeFilterMax?.value === '' ? null : Number(tradeFilterMax?.value),
        }};
        rerenderTrades(true);
      }});
      tradeResetFilter?.addEventListener('click', () => {{
        numericFilter = {{ field: 'net_pnl', min: null, max: null }};
        if (tradeFilterField) tradeFilterField.value = 'net_pnl';
        if (tradeFilterMin) tradeFilterMin.value = '';
        if (tradeFilterMax) tradeFilterMax.value = '';
        rerenderTrades(true);
      }});
      tradeExportJson?.addEventListener('click', () => downloadTradeSelection('json'));
      tradeExportCsv?.addEventListener('click', () => downloadTradeSelection('csv'));
      tradeClearSelection?.addEventListener('click', () => {{
        rerenderTrades(true);
      }});
      tradeReplayPrev?.addEventListener('click', () => {{
        tradeReplayStep = Math.max(0, tradeReplayStep - 1);
        renderTradeReplay();
      }});
      tradeReplayNext?.addEventListener('click', () => {{
        tradeReplayStep += 1;
        renderTradeReplay();
      }});
      tradeReplayPlay?.addEventListener('click', () => {{
        if (tradeReplayTimer) {{
          window.clearInterval(tradeReplayTimer);
          tradeReplayTimer = null;
          tradeReplayPlay.textContent = 'Play';
          return;
        }}
        tradeReplayPlay.textContent = 'Pause';
        tradeReplayTimer = window.setInterval(() => {{
          const trade = replayTargetTrade();
          if (!trade) return;
          const maxStep = Math.max(0, trade.exit_bar - trade.entry_bar);
          if (tradeReplayStep >= maxStep) {{
            window.clearInterval(tradeReplayTimer);
            tradeReplayTimer = null;
            tradeReplayPlay.textContent = 'Play';
            return;
          }}
          tradeReplayStep += 1;
          renderTradeReplay();
        }}, 700);
      }});
      rerenderTrades(false);
    }}
  </script>
</body>
</html>"""

    @staticmethod
    def _render_summary_cards(metrics: dict[str, Any], meta: dict[str, Any]) -> str:
        cards = [
            ("Total Return", _pct(metrics["total_return"])),
            ("Sharpe", _num(metrics["sharpe_ratio"])),
            ("Max DD", _pct(metrics["max_drawdown"])),
            ("Trades", str(metrics["total_trades"])),
            ("Expectancy", f"${_num(metrics['expectancy'], 2)}"),
        ]
        return "".join(
            f"<div class='card'><div class='label'>{label}</div><div class='value'>{value}</div></div>"
            for label, value in cards
        )

    @staticmethod
    def _build_equity_chart(equity_points: list[dict[str, Any]], make_subplots, go) -> str:
        if not equity_points:
            return "<p class='muted'>No equity curve available.</p>"

        timestamps = [point["timestamp"] for point in equity_points]
        equity = np.array([point["equity"] for point in equity_points], dtype=float)
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak * 100

        monthly_returns = pd.Series(dtype=float)
        if timestamps and not str(timestamps[0]).isdigit():
            try:
                monthly_returns = (
                    pd.Series(equity, index=pd.to_datetime(timestamps))
                    .resample("ME")
                    .last()
                    .pct_change()
                    .dropna()
                )
            except (ValueError, TypeError):
                monthly_returns = pd.Series(dtype=float)

        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=False,
            row_heights=[0.5, 0.25, 0.25],
            subplot_titles=("Equity Curve", "Drawdown (%)", "Monthly Returns"),
            vertical_spacing=0.08,
        )
        fig.add_trace(
            go.Scatter(x=timestamps, y=equity, name="Equity", line=dict(color="#38bdf8", width=2)),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=drawdown,
                name="Drawdown",
                fill="tozeroy",
                line=dict(color="#f43f5e", width=1.5),
            ),
            row=2,
            col=1,
        )
        if not monthly_returns.empty:
            colors = ["#22c55e" if v >= 0 else "#f97316" for v in monthly_returns.values]
            fig.add_trace(
                go.Bar(
                    x=[ts.strftime("%Y-%m") for ts in monthly_returns.index],
                    y=monthly_returns.values * 100,
                    marker_color=colors,
                    name="Monthly Return",
                ),
                row=3,
                col=1,
            )
        fig.update_layout(height=950, template="plotly_dark", showlegend=False, margin=dict(t=70, b=40))
        return fig.to_html(include_plotlyjs="cdn", full_html=False)

    @staticmethod
    def _build_trade_overlay_chart(bars: list[dict[str, Any]], trades: list[dict[str, Any]], go) -> str:
        if not bars:
            return "<p class='muted'>No OHLCV bars available for trade overlay.</p>"
        return "<div id='trade-overlay-chart'></div>"

    @staticmethod
    def _build_trade_analytics(
        trades: list[dict[str, Any]],
        equity_points: list[dict[str, Any]],
        go,
        make_subplots,
    ) -> str:
        if not trades:
            return "<p class='muted'>No closed trades available for analytics.</p>"

        pnl = np.array([trade["net_pnl"] for trade in trades], dtype=float)
        hold_bars = np.array([trade["hold_bars"] for trade in trades], dtype=float)
        cumulative = np.cumsum(pnl)

        fig = make_subplots(
            rows=2,
            cols=2,
            subplot_titles=("PnL Distribution", "Hold Bars", "MAE / MFE Excursion", "Side Breakdown"),
            vertical_spacing=0.12,
            horizontal_spacing=0.1,
        )
        fig.add_trace(go.Histogram(x=pnl, marker_color="#38bdf8", name="PnL Distribution"), row=1, col=1)
        fig.add_trace(go.Histogram(x=hold_bars, marker_color="#f59e0b", name="Hold Bars"), row=1, col=2)

        mae = np.array([trade.get("mae", 0.0) for trade in trades], dtype=float)
        mfe = np.array([trade.get("mfe", 0.0) for trade in trades], dtype=float)
        fig.add_trace(
            go.Scatter(
                x=mae,
                y=mfe,
                mode="markers",
                marker=dict(
                    size=10,
                    color=pnl,
                    colorscale="RdYlGn",
                    showscale=True,
                    colorbar=dict(title="Net PnL"),
                ),
                text=[f"Trade {idx + 1}" for idx in range(len(trades))],
                hovertemplate=(
                    "%{text}<br>MAE: %{x:.2f}<br>MFE: %{y:.2f}<br>"
                    "Net PnL: %{marker.color:.2f}<extra></extra>"
                ),
                name="MAE / MFE",
            ),
            row=2,
            col=1,
        )

        side_counts: dict[str, int] = {}
        for trade in trades:
            side_counts[trade["side"]] = side_counts.get(trade["side"], 0) + 1
        fig.add_trace(
            go.Bar(
                x=list(side_counts.keys()),
                y=list(side_counts.values()),
                marker_color=["#22c55e", "#f97316"][: len(side_counts)],
                name="Side Breakdown",
            ),
            row=2,
            col=2,
        )

        fig.update_layout(height=860, template="plotly_dark", showlegend=False, margin=dict(t=60, b=40))
        return fig.to_html(include_plotlyjs="cdn", full_html=False)

    @staticmethod
    def _build_trade_explorer_div(go) -> str:
        fig = go.Figure()
        return fig.to_html(include_plotlyjs="cdn", full_html=False, div_id="trade-explorer")

    def save(self, html: str, path: str | Path) -> Path:
        """Write HTML content to path, creating parent dirs as needed."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
        return p


class BacktestArtifactWriter:
    """Persist markdown, JSON, and HTML dashboard artifacts for a backtest run."""

    @staticmethod
    def build_payload(
        *,
        metrics: MetricsReport,
        config: BacktestConfig | None,
        equity_curve: list[float],
        trades: list[Trade],
        bars: pd.DataFrame | None,
        equity_start_bar: int,
    ) -> dict[str, Any]:
        metric_dict = metrics.to_dict()
        bar_records = BacktestArtifactWriter._serialize_bars(bars)
        equity_records = BacktestArtifactWriter._serialize_equity(
            equity_curve=equity_curve,
            bars=bars,
            equity_start_bar=equity_start_bar,
        )
        trade_records = [BacktestArtifactWriter._serialize_trade(trade) for trade in trades]

        meta = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "name": config.backtest.name if config else "Backtest Report",
            "symbol": config.data.symbol if config else "",
            "timeframe": config.data.timeframe if config else "",
            "start": config.data.start if config else "",
            "end": config.data.end if config else "",
            "initial_capital": config.backtest.initial_capital if config else 0.0,
        }

        return {
            "meta": meta,
            "metrics": metric_dict,
            "equity": equity_records,
            "bars": bar_records,
            "trades": trade_records,
        }

    @staticmethod
    def save_all(
        *,
        metrics: MetricsReport,
        config: BacktestConfig,
        equity_curve: list[float],
        trades: list[Trade],
        bars: pd.DataFrame | None,
        equity_start_bar: int,
        base_path: str | Path,
    ) -> dict[str, Path]:
        base = Path(base_path)
        payload = BacktestArtifactWriter.build_payload(
            metrics=metrics,
            config=config,
            equity_curve=equity_curve,
            trades=trades,
            bars=bars,
            equity_start_bar=equity_start_bar,
        )
        markdown = MarkdownReporter.generate(metrics, config, equity_curve)
        html = HtmlReporter().generate_from_payload(payload)

        md_path = MarkdownReporter.save(markdown, base.with_suffix(".md"))
        html_path = HtmlReporter().save(html, base.with_suffix(".html"))
        json_path = BacktestArtifactWriter._save_json(payload, base.with_suffix(".json"))
        return {"markdown": md_path, "html": html_path, "json": json_path}

    @staticmethod
    def save_result(result: Any, base_path: str | Path) -> dict[str, Path]:
        return BacktestArtifactWriter.save_all(
            metrics=result.metrics,
            config=result.config,
            equity_curve=result.equity_curve,
            trades=result.trades,
            bars=getattr(result, "bars", None),
            equity_start_bar=getattr(result, "equity_start_bar", 0),
            base_path=base_path,
        )

    @staticmethod
    def _serialize_bars(bars: pd.DataFrame | None) -> list[dict[str, Any]]:
        if bars is None or bars.empty:
            return []
        required = [col for col in ("open", "high", "low", "close", "volume") if col in bars.columns]
        frame = bars[required].copy()
        return [
            {
                "timestamp": str(idx),
                **{col: float(row[col]) for col in required},
            }
            for idx, row in frame.iterrows()
        ]

    @staticmethod
    def _serialize_equity(
        *,
        equity_curve: list[float],
        bars: pd.DataFrame | None,
        equity_start_bar: int,
    ) -> list[dict[str, Any]]:
        timestamps: list[str]
        if bars is not None and not bars.empty:
            index = list(bars.index[equity_start_bar:equality_end(equity_start_bar, len(equity_curve), len(bars))])
            timestamps = [str(ts) for ts in index]
        else:
            timestamps = [str(i) for i in range(len(equity_curve))]

        if len(timestamps) != len(equity_curve):
            timestamps = [str(i) for i in range(len(equity_curve))]
        return [
            {"timestamp": ts, "equity": float(eq)}
            for ts, eq in zip(timestamps, equity_curve)
        ]

    @staticmethod
    def _serialize_trade(trade: Trade) -> dict[str, Any]:
        return {
            "entry_bar": trade.entry_bar,
            "exit_bar": trade.exit_bar,
            "side": trade.side.value,
            "entry_price": float(trade.entry_price),
            "exit_price": float(trade.exit_price),
            "quantity": float(trade.quantity),
            "leverage": trade.leverage,
            "fee": float(trade.fee),
            "gross_pnl": float(trade.gross_pnl),
            "net_pnl": float(trade.net_pnl),
            "funding_pnl": float(trade.funding_pnl),
            "hold_bars": trade.hold_bars,
            "mae": float(trade.mae),
            "mfe": float(trade.mfe),
            "is_liquidated": trade.is_liquidated,
        }

    @staticmethod
    def _save_json(payload: dict[str, Any], path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return p


def equality_end(start: int, equity_len: int, bars_len: int) -> int:
    """Cap equity alignment end index to available bars."""
    return min(start + equity_len, bars_len)
