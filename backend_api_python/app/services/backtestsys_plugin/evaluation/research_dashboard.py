"""Experiment-level interactive dashboard generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ResearchDashboardBuilder:
    """Aggregate experiment JSON outputs into one HTML dashboard."""

    def build_html(self, json_paths: list[str | Path]) -> str:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        payloads = [payload for path in json_paths if (payload := self._load(path)) is not None]
        search_rows = self._collect_search_rows(payloads)
        milestone_rows = self._collect_milestone_rows(payloads)
        robustness = self._collect_robustness(payloads)

        search_div = self._build_search_div(search_rows, go)
        heatmap_div, heatmap_controls = self._build_heatmap_section(search_rows, go)
        milestone_div = self._build_milestone_div(milestone_rows, go)
        robustness_div, round_equity_controls = self._build_robustness_div(robustness, go, make_subplots)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Backtest Research Dashboard</title>
  <style>
    :root {{
      --bg: #08111f;
      --panel: #0f172a;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --border: #1e293b;
      --accent: #38bdf8;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #10203d 0%, var(--bg) 55%);
      color: var(--text);
    }}
    .shell {{ max-width: 1500px; margin: 0 auto; padding: 28px; }}
    .hero {{
      background: rgba(15, 23, 42, 0.92);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 22px 24px;
      margin-bottom: 22px;
    }}
    .hero h1 {{ margin: 0 0 8px; font-size: 32px; }}
    .muted {{ color: var(--muted); }}
    .tabs {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }}
    .tab-btn {{
      background: rgba(15, 23, 42, 0.92);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 10px 16px;
      cursor: pointer;
    }}
    .tab-btn.active {{ background: var(--accent); color: #082f49; border-color: transparent; font-weight: 700; }}
    .panel {{
      display: none;
      background: rgba(15, 23, 42, 0.92);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 18px 20px;
      margin-bottom: 18px;
    }}
    .panel.active {{ display: block; }}
    .section-title {{ margin: 0 0 12px; font-size: 22px; }}
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
      min-width: 320px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #0b1220;
      color: var(--text);
      padding: 10px 12px;
    }}
    .controls button {{
      align-self: end;
      min-width: 140px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #0b1220;
      color: var(--text);
      padding: 10px 12px;
      cursor: pointer;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.12);
      vertical-align: top;
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <div class="muted">Research Dashboard</div>
      <h1>Backtest Optimization, WFA, and CPCV</h1>
      <div class="muted">Aggregated from experiment JSON files already produced by the current research workflow.</div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" data-tab="search">Parameter Search</button>
      <button class="tab-btn" data-tab="milestones">Milestone Evolution</button>
      <button class="tab-btn" data-tab="robustness">WFA / CPCV</button>
    </div>

    <section class="panel active" id="tab-search">
      <h2 class="section-title">Parameter Search</h2>
      <h3 class="section-title" style="font-size:18px;margin-top:4px;">Heatmap Explorer</h3>
      {heatmap_controls}
      <div class="plot-wrap">{heatmap_div}</div>
      <div class="plot-wrap">{search_div}</div>
    </section>

    <section class="panel" id="tab-milestones">
      <h2 class="section-title">Milestone Evolution</h2>
      <div class="plot-wrap">{milestone_div}</div>
    </section>

    <section class="panel" id="tab-robustness">
      <h2 class="section-title">WFA / CPCV</h2>
      <h3 class="section-title" style="font-size:18px;margin-top:4px;">WFA Round Equity</h3>
      {round_equity_controls}
      <div class="plot-wrap">{robustness_div}</div>
    </section>
  </div>
  <script>
    const heatmapPayload = {self._to_json_for_script(search_rows)};
    const robustnessPayload = {self._to_json_for_script(robustness if robustness else {})};
    const buttons = Array.from(document.querySelectorAll('.tab-btn'));
    const panels = Array.from(document.querySelectorAll('.panel'));
    buttons.forEach((button) => {{
      button.addEventListener('click', () => {{
        const target = button.dataset.tab;
        buttons.forEach((b) => b.classList.toggle('active', b === button));
        panels.forEach((panel) => panel.classList.toggle('active', panel.id === `tab-${{target}}`));
      }});
    }});

    const sourceSelect = document.getElementById('heatmap-source');
    const xSelect = document.getElementById('heatmap-x');
    const ySelect = document.getElementById('heatmap-y');
    const metricSelect = document.getElementById('heatmap-metric');
    const heatmapDiv = document.getElementById('heatmap-explorer');
    const searchScatterDiv = document.getElementById('search-scatter-chart');
    const permalinkInput = document.getElementById('heatmap-permalink');
    const permalinkCopyButton = document.getElementById('heatmap-copy-link');
    const topMetricSelect = document.getElementById('top-combos-metric');
    const topLimitSelect = document.getElementById('top-combos-limit');
    const topCombosTable = document.getElementById('top-combos-table');
    const topCombosSelect = document.getElementById('top-combos-select');
    let highlightedCandidateKey = '';

    function rowsForSource(source) {{
      return heatmapPayload.filter((row) => row.source === source);
    }}

    function candidateKey(row) {{
      return `${{row.source}}::${{row.label}}`;
    }}

    function candidateStillVisible(source) {{
      if (!highlightedCandidateKey) return true;
      return rowsForSource(source).some((row) => candidateKey(row) === highlightedCandidateKey);
    }}

    function numericParams(rows) {{
      const keys = new Set();
      rows.forEach((row) => Object.entries(row.params || {{}}).forEach(([key, value]) => {{
        if (typeof value === 'number' && Number.isFinite(value)) keys.add(key);
      }}));
      return Array.from(keys);
    }}

    function parseHashState() {{
      const raw = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '';
      return Object.fromEntries(new URLSearchParams(raw).entries());
    }}

    function currentHeatmapState() {{
      return {{
        source: sourceSelect?.value || '',
        x: xSelect?.value || '',
        y: ySelect?.value || '',
        metric: metricSelect?.value || '',
        highlight: highlightedCandidateKey,
      }};
    }}

    function updatePermalink() {{
      if (!permalinkInput) return;
      const state = currentHeatmapState();
      const params = new URLSearchParams();
      Object.entries(state).forEach(([key, value]) => {{
        if (value) params.set(key, value);
      }});
      const link = `${{window.location.pathname}}#${{params.toString()}}`;
      permalinkInput.value = link;
      window.history.replaceState(null, '', `#${{params.toString()}}`);
    }}

    function sortRowsForMetric(rows, metricKey) {{
      return [...rows].sort((left, right) => {{
        const leftValue = Number(left.metrics?.[metricKey]);
        const rightValue = Number(right.metrics?.[metricKey]);
        return rightValue - leftValue;
      }});
    }}

    function renderTopCombos() {{
      if (!topCombosTable || !topMetricSelect || !topLimitSelect || !sourceSelect) return;
      const metricKey = topMetricSelect.value || metricSelect?.value || 'sharpe_ratio';
      const limit = Number(topLimitSelect.value || 5);
      const rows = sortRowsForMetric(rowsForSource(sourceSelect.value), metricKey).slice(0, limit);
      if (topCombosSelect) {{
        topCombosSelect.innerHTML = `<option value=''>none</option>` + rows
          .map((row) => `<option value="${{candidateKey(row)}}">${{row.label}}</option>`)
          .join('');
        if (highlightedCandidateKey) {{
          topCombosSelect.value = highlightedCandidateKey;
        }}
      }}
      const body = rows.map((row, idx) => {{
        const params = Object.entries(row.params || {{}})
          .map(([key, value]) => `${{key}}=${{value}}`)
          .join(', ');
        const activeAttr = candidateKey(row) === highlightedCandidateKey ? " style='background: rgba(56, 189, 248, 0.12);'" : '';
        return `<tr${{activeAttr}}><td>${{idx + 1}}</td><td>${{row.label}}</td><td>${{Number(row.metrics?.[metricKey] || 0).toFixed(3)}}</td><td>${{params || 'n/a'}}</td></tr>`;
      }}).join('');
      topCombosTable.innerHTML = `
        <thead><tr><th>Rank</th><th>Label</th><th>${{metricKey}}</th><th>Params</th></tr></thead>
        <tbody>${{body}}</tbody>
      `;
    }}

    function renderSearchScatter() {{
      if (!searchScatterDiv || !window.Plotly) return;
      const sources = Array.from(new Set(heatmapPayload.map((row) => row.source)));
      const palette = ['#38bdf8', '#22c55e', '#f97316', '#e879f9', '#facc15', '#fb7185'];
      const traces = sources.map((source, idx) => {{
        const group = heatmapPayload.filter((row) => row.source === source);
        return {{
          x: group.map((row) => row.sharpe),
          y: group.map((row) => row.return * 100),
          mode: 'markers',
          name: source,
          text: group.map((row) => row.label),
          customdata: group.map((row) => [candidateKey(row), row.drawdown * 100, row.trades]),
          marker: {{
            size: group.map((row) => Math.max(10, Math.min(28, row.trades / 8 || 10))),
            color: palette[idx % palette.length],
            opacity: 0.75,
            line: {{
              width: group.map((row) => candidateKey(row) === highlightedCandidateKey ? 3 : 1),
              color: group.map((row) => candidateKey(row) === highlightedCandidateKey ? '#facc15' : '#0f172a'),
            }},
          }},
          hovertemplate: '<b>%{{text}}</b><br>Sharpe: %{{x:.3f}}<br>Return: %{{y:.2f}}%<br>Max DD: %{{customdata[1]:.2f}}%<br>Trades: %{{customdata[2]}}<extra></extra>',
        }};
      }});
      Plotly.react(searchScatterDiv, traces, {{
        height: 760,
        template: 'plotly_dark',
        title: 'Sharpe vs Total Return',
        xaxis: {{ title: 'Sharpe Ratio' }},
        yaxis: {{ title: 'Total Return (%)' }},
      }}, {{ responsive: true }});
      if (searchScatterDiv.dataset.handlersBound !== 'true') {{
        searchScatterDiv.on('plotly_click', (event) => {{
          const key = event?.points?.[0]?.customdata?.[0];
          if (key !== undefined) highlightCandidate(key);
        }});
        searchScatterDiv.dataset.handlersBound = 'true';
      }}
    }}

    function highlightCandidate(key) {{
      highlightedCandidateKey = key || '';
      if (topCombosSelect) {{
        topCombosSelect.value = highlightedCandidateKey;
      }}
      renderSearchScatter();
      renderHeatmap();
      renderTopCombos();
    }}

    function repopulateAxes() {{
      if (!sourceSelect || !xSelect || !ySelect) return;
      if (!candidateStillVisible(sourceSelect.value)) {{
        highlightedCandidateKey = '';
      }}
      const rows = rowsForSource(sourceSelect.value);
      const keys = numericParams(rows);
      xSelect.innerHTML = keys.map((key) => `<option value="${{key}}">${{key}}</option>`).join('');
      ySelect.innerHTML = keys.map((key) => `<option value="${{key}}">${{key}}</option>`).join('');
      const hashState = parseHashState();
      if (hashState.x && keys.includes(hashState.x)) {{
        xSelect.value = hashState.x;
      }}
      if (hashState.y && keys.includes(hashState.y)) {{
        ySelect.value = hashState.y;
      }} else if (keys.length > 1) {{
        ySelect.value = keys[1];
      }}
      renderHeatmap();
    }}

    function renderHeatmap() {{
      if (!heatmapDiv || !sourceSelect || !xSelect || !ySelect || !metricSelect || !window.Plotly) return;
      const rows = rowsForSource(sourceSelect.value);
      const xKey = xSelect.value;
      const yKey = ySelect.value;
      const metricKey = metricSelect.value;
      if (!rows.length || !xKey || !yKey) return;

      const xs = Array.from(new Set(rows.map((row) => row.params?.[xKey]).filter((v) => typeof v === 'number'))).sort((a,b) => a-b);
      const ys = Array.from(new Set(rows.map((row) => row.params?.[yKey]).filter((v) => typeof v === 'number'))).sort((a,b) => a-b);
      const z = ys.map((y) => xs.map((x) => null));
      rows.forEach((row) => {{
        const x = row.params?.[xKey];
        const y = row.params?.[yKey];
        const metric = row.metrics?.[metricKey];
        const xi = xs.indexOf(x);
        const yi = ys.indexOf(y);
        if (xi >= 0 && yi >= 0 && typeof metric === 'number') z[yi][xi] = metric;
      }});
      const traces = [{{
        x: xs,
        y: ys,
        z,
        type: 'heatmap',
        colorscale: 'Viridis',
        hovertemplate: `${{xKey}}=%{{x}}<br>${{yKey}}=%{{y}}<br>${{metricKey}}=%{{z:.3f}}<extra></extra>`,
      }}];
      const highlighted = rows.find((row) => candidateKey(row) === highlightedCandidateKey);
      if (highlighted && typeof highlighted.params?.[xKey] === 'number' && typeof highlighted.params?.[yKey] === 'number') {{
        traces.push({{
          x: [highlighted.params[xKey]],
          y: [highlighted.params[yKey]],
          mode: 'markers',
          marker: {{ size: 18, color: '#facc15', symbol: 'diamond-open', line: {{ width: 3, color: '#ffffff' }} }},
          name: highlighted.label,
          hovertemplate: `<b>${{highlighted.label}}</b><br>${{xKey}}=%{{x}}<br>${{yKey}}=%{{y}}<extra></extra>`,
        }});
      }}

      Plotly.react(heatmapDiv, traces, {{
        template: 'plotly_dark',
        height: 620,
        title: `Heatmap: ${{metricKey}} by ${{xKey}} vs ${{yKey}}`,
        xaxis: {{ title: xKey }},
        yaxis: {{ title: yKey }},
        margin: {{ t: 60, b: 50 }},
      }}, {{ responsive: true }});
      if (heatmapDiv.dataset.handlersBound !== 'true') {{
        heatmapDiv.on('plotly_click', (event) => {{
          const point = event?.points?.[0];
          if (!point) return;
          const currentRows = rowsForSource(sourceSelect?.value);
          const currentX = xSelect?.value;
          const currentY = ySelect?.value;
          const clicked = currentRows.find((row) => row.params?.[currentX] === point.x && row.params?.[currentY] === point.y);
          if (clicked) highlightCandidate(candidateKey(clicked));
        }});
        heatmapDiv.dataset.handlersBound = 'true';
      }}
      updatePermalink();
      renderTopCombos();
    }}

    if (sourceSelect) {{
      const hashState = parseHashState();
      if (hashState.source && Array.from(sourceSelect.options).some((option) => option.value === hashState.source)) {{
        sourceSelect.value = hashState.source;
      }}
      if (hashState.metric && Array.from(metricSelect.options).some((option) => option.value === hashState.metric)) {{
        metricSelect.value = hashState.metric;
      }}
      if (hashState.highlight) {{
        highlightedCandidateKey = hashState.highlight;
      }}
      sourceSelect.addEventListener('change', repopulateAxes);
      xSelect.addEventListener('change', renderHeatmap);
      ySelect.addEventListener('change', renderHeatmap);
      metricSelect.addEventListener('change', renderHeatmap);
      topMetricSelect?.addEventListener('change', renderTopCombos);
      topLimitSelect?.addEventListener('change', renderTopCombos);
      topCombosSelect?.addEventListener('change', (event) => {{
        highlightCandidate(event.target.value);
      }});
      permalinkCopyButton?.addEventListener('click', async () => {{
        if (!permalinkInput?.value) return;
        try {{
          await navigator.clipboard.writeText(permalinkInput.value);
          permalinkCopyButton.textContent = 'Copied';
        }} catch (error) {{
          permalinkCopyButton.textContent = 'Copy Failed';
        }}
      }});
      renderSearchScatter();
      repopulateAxes();
    }}

    const roundSelect = document.getElementById('round-equity-select');
    const roundEquityDiv = document.getElementById('round-equity-chart');

    function renderRoundEquity() {{
      if (!roundSelect || !roundEquityDiv || !window.Plotly) return;
      const rounds = robustnessPayload.wfa?.rounds || [];
      const selected = rounds.find((round) => String(round.round_idx) === roundSelect.value);
      if (!selected || !Array.isArray(selected.oos_equity)) return;
      Plotly.react(roundEquityDiv, [{{
        x: selected.oos_equity.map((_, idx) => idx + 1),
        y: selected.oos_equity,
        mode: 'lines',
        line: {{ color: '#38bdf8', width: 2 }},
        name: 'OOS Equity',
      }}], {{
        template: 'plotly_dark',
        height: 420,
        title: `Round ${{Number(selected.round_idx) + 1}} OOS Equity`,
        xaxis: {{ title: 'OOS Bar' }},
        yaxis: {{ title: 'Equity' }},
        margin: {{ t: 60, b: 40 }},
      }}, {{ responsive: true }});
    }}

    if (roundSelect) {{
      roundSelect.addEventListener('change', renderRoundEquity);
      renderRoundEquity();
    }}
  </script>
</body>
</html>"""

    def save(self, json_paths: list[str | Path], output_path: str | Path) -> Path:
        html = self.build_html(json_paths)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return path

    @staticmethod
    def _load(path: str | Path) -> dict[str, Any] | None:
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        data["_source_path"] = str(p)
        return data

    @staticmethod
    def _collect_search_rows(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for payload in payloads:
            results = payload.get("results")
            if not isinstance(results, list):
                continue
            source = Path(payload["_source_path"]).stem
            for row in results:
                metrics = row.get("metrics", {})
                rows.append(
                    {
                        "source": source,
                        "label": row.get("label", source),
                        "sharpe": metrics.get("sharpe_ratio", 0.0),
                        "return": metrics.get("total_return", 0.0),
                        "drawdown": metrics.get("max_drawdown", 0.0),
                        "trades": metrics.get("total_trades", 0),
                        "metrics": metrics,
                        "params": row.get("signal_params") or row.get("leg_weights") or row.get("tp_fracs") or {},
                    }
                )
        return rows

    @staticmethod
    def _build_heatmap_section(rows: list[dict[str, Any]], go) -> tuple[str, str]:
        if not rows:
            return "<p class='muted'>No search rows available for heatmap explorer.</p>", ""

        sources = sorted({row["source"] for row in rows})
        source_options = "".join(f"<option value='{source}'>{source}</option>" for source in sources)
        metric_options = "".join(
            f"<option value='{metric}'>{metric}</option>"
            for metric in ("sharpe_ratio", "total_return", "max_drawdown", "total_trades")
        )
        controls = (
            "<div class='controls'>"
            "<label>Dataset<select id='heatmap-source'>"
            f"{source_options}"
            "</select></label>"
            "<label>X Axis<select id='heatmap-x'></select></label>"
            "<label>Y Axis<select id='heatmap-y'></select></label>"
            "<label>Metric<select id='heatmap-metric'>"
            f"{metric_options}"
            "</select></label>"
            "<label>Permalink<input id='heatmap-permalink' type='text' readonly></label>"
            "<button id='heatmap-copy-link' type='button'>Copy Link</button>"
            "<label>Top Metric<select id='top-combos-metric'>"
            f"{metric_options}"
            "</select></label>"
            "<label>Top N<select id='top-combos-limit'><option value='5'>5</option><option value='10'>10</option><option value='20'>20</option></select></label>"
            "<label>Highlight<select id='top-combos-select'><option value=''>none</option></select></label>"
            "</div>"
            "<table id='top-combos-table'></table>"
        )

        first_source = sources[0]
        first_rows = [row for row in rows if row["source"] == first_source]
        numeric_keys = sorted(
            {
                key
                for row in first_rows
                for key, value in (row.get("params") or {}).items()
                if isinstance(value, (int, float))
            }
        )
        if len(numeric_keys) < 2:
            return "<div id='heatmap-explorer'></div><p class='muted'>No two-dimensional numeric parameter grid found.</p>", controls

        heatmap_html = go.Figure().to_html(include_plotlyjs="cdn", full_html=False, div_id="heatmap-explorer")
        return heatmap_html, controls

    @staticmethod
    def _collect_milestone_rows(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for payload in payloads:
            milestones = payload.get("milestones")
            if isinstance(milestones, list):
                rows: list[dict[str, Any]] = []
                for row in milestones:
                    metrics = row.get("metrics", {})
                    rows.append(
                        {
                            "label": row.get("label", "milestone"),
                            "sharpe": metrics.get("sharpe_ratio", 0.0),
                            "return": metrics.get("total_return", 0.0),
                            "drawdown": metrics.get("max_drawdown", 0.0),
                        }
                    )
                return rows
        return []

    @staticmethod
    def _collect_robustness(payloads: list[dict[str, Any]]) -> dict[str, Any]:
        for payload in payloads:
            if "wfa" in payload and "cpcv" in payload:
                return payload
        return {}

    @staticmethod
    def _build_search_div(rows: list[dict[str, Any]], go) -> str:
        if not rows:
            return "<p class='muted'>No parameter-search JSON files found.</p>"

        fig = go.Figure()
        return fig.to_html(include_plotlyjs="cdn", full_html=False, div_id="search-scatter-chart")

    @staticmethod
    def _build_milestone_div(rows: list[dict[str, Any]], go) -> str:
        if not rows:
            return "<p class='muted'>No milestone benchmark JSON found.</p>"

        labels = [row["label"] for row in rows]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=labels, y=[row["sharpe"] for row in rows], mode="lines+markers", name="Sharpe"))
        fig.add_trace(
            go.Bar(
                x=labels,
                y=[row["return"] * 100 for row in rows],
                name="Total Return (%)",
                marker_color="#38bdf8",
                opacity=0.55,
                yaxis="y2",
            )
        )
        fig.update_layout(
            height=700,
            template="plotly_dark",
            title="Milestone Evolution",
            yaxis=dict(title="Sharpe Ratio"),
            yaxis2=dict(title="Total Return (%)", overlaying="y", side="right"),
        )
        return fig.to_html(include_plotlyjs="cdn", full_html=False)

    @staticmethod
    def _build_robustness_div(payload: dict[str, Any], go, make_subplots) -> tuple[str, str]:
        if not payload:
            return "<p class='muted'>No robustness JSON found.</p>", ""

        wfa = payload.get("wfa", {})
        cpcv = payload.get("cpcv", {})
        wfa_is = wfa.get("is_sharpes", [])
        wfa_oos = wfa.get("oos_sharpes", [])
        cpcv_sharpes = cpcv.get("oos_sharpes", [])
        cpcv_returns = cpcv.get("oos_returns", [])

        fig = make_subplots(
            rows=2,
            cols=2,
            subplot_titles=("WFA IS/OOS Sharpe", "CPCV OOS Sharpe", "CPCV OOS Return", "Robustness Summary"),
            vertical_spacing=0.12,
            horizontal_spacing=0.1,
        )
        if wfa_is or wfa_oos:
            rounds = list(range(1, max(len(wfa_is), len(wfa_oos)) + 1))
            fig.add_trace(go.Bar(x=rounds[:len(wfa_is)], y=wfa_is, name="WFA IS", marker_color="#38bdf8"), row=1, col=1)
            fig.add_trace(go.Bar(x=rounds[:len(wfa_oos)], y=wfa_oos, name="WFA OOS", marker_color="#22c55e"), row=1, col=1)
        if cpcv_sharpes:
            fig.add_trace(go.Bar(x=list(range(1, len(cpcv_sharpes) + 1)), y=cpcv_sharpes, name="CPCV Sharpe", marker_color="#f97316"), row=1, col=2)
        if cpcv_returns:
            fig.add_trace(go.Bar(x=list(range(1, len(cpcv_returns) + 1)), y=[value * 100 for value in cpcv_returns], name="CPCV Return", marker_color="#e879f9"), row=2, col=1)

        summary_lines = [
            f"WFA Verdict: {wfa.get('verdict', 'n/a')}",
            f"WFA Efficiency: {wfa.get('efficiency', 0.0):.3f}" if wfa.get("efficiency") is not None else "WFA Efficiency: n/a",
            f"CPCV Verdict: {cpcv.get('verdict', 'n/a')}",
            f"CPCV Mean OOS Sharpe: {cpcv.get('mean_oos_sharpe', 0.0):.3f}" if cpcv.get("mean_oos_sharpe") is not None else "CPCV Mean OOS Sharpe: n/a",
            f"CPCV Positive-Sharpe Rate: {cpcv.get('pct_positive_sharpe', 0.0) * 100:.2f}%" if cpcv.get("pct_positive_sharpe") is not None else "CPCV Positive-Sharpe Rate: n/a",
        ]
        fig.add_trace(
            go.Scatter(
                x=[0.5] * len(summary_lines),
                y=list(range(len(summary_lines), 0, -1)),
                text=summary_lines,
                mode="text",
                textfont=dict(size=15),
                showlegend=False,
            ),
            row=2,
            col=2,
        )
        fig.update_xaxes(visible=False, row=2, col=2)
        fig.update_yaxes(visible=False, row=2, col=2)
        fig.update_layout(height=900, template="plotly_dark", showlegend=True)

        rounds = wfa.get("rounds", [])
        if rounds:
            options = "".join(
                f"<option value='{round_data['round_idx']}'>Round {round_data['round_idx'] + 1}</option>"
                for round_data in rounds
            )
            controls = (
                "<div class='controls'>"
                "<label>Round<select id='round-equity-select'>"
                f"{options}"
                "</select></label>"
                "</div>"
                "<div id='round-equity-chart'></div>"
            )
        else:
            controls = "<p class='muted'>No WFA round equity payload available.</p>"
        return fig.to_html(include_plotlyjs="cdn", full_html=False), controls

    @staticmethod
    def _to_json_for_script(rows: list[dict[str, Any]]) -> str:
        return json.dumps(rows, ensure_ascii=False)
