import React, { useState, useEffect, useRef, useCallback } from 'react';
import CodeEditor from '../components/CodeEditor.jsx';
import { fetchPandasModel } from '../services/api.js';

const CW = 960;
const CH = 560;

function getC_(theme) {
  const isDark = theme === 'dark';
  return {
    dark: isDark ? '#f1f5f9' : '#141413',
    light: isDark ? '#0f172a' : '#ffffff',
    mid: isDark ? '#94a3b8' : '#64748b',
    lgray: isDark ? 'rgba(255, 255, 255, 0.12)' : '#e8e6dc',
    orange: isDark ? '#f97316' : '#d97757',
    blue: isDark ? '#38bdf8' : '#0284c7',
    green: isDark ? '#10b981' : '#059669',
    purple: isDark ? '#a855f7' : '#9333ea',
  };
}

function heat(v, lo, hi, isDark = false) {
  if (typeof v !== 'number' || isNaN(v)) {
    return isDark ? [45, 55, 72] : [230, 225, 215];
  }
  let x = hi <= lo ? 0.5 : Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  const a = isDark ? [30, 58, 138] : [130, 160, 190];
  const b = isDark ? [30, 41, 59] : [240, 236, 225];
  const c = isDark ? [217, 119, 87] : [217, 119, 87];
  const mix = (p, q, t) => [
    Math.round(p[0] + (q[0] - p[0]) * t),
    Math.round(p[1] + (q[1] - p[1]) * t),
    Math.round(p[2] + (q[2] - p[2]) * t),
  ];
  return x < 0.5 ? mix(a, b, x * 2) : mix(b, c, (x - 0.5) * 2);
}

function fmt(v) {
  if (typeof v === 'number') {
    return Number.isInteger(v) ? v : Math.round(v * 10) / 10;
  }
  return String(v);
}

// ── Backend Model fetching is delegated to services/api.js ────────────────────

function colorExpr(s) {
  const parts = s.split(/(-?\d+\.?\d*)|(>=|<=|==|!=|[+\-*/><])/g);
  return parts.map((p, i) => {
    if (!p) return null;
    if (/^-?\d+\.?\d*$/.test(p)) return <span key={i} style={{ color: '#f0b47a' }}>{p}</span>;
    if (/^(>=|<=|==|!=|[+\-*/><])$/.test(p)) return <span key={i} style={{ color: '#8fb9e0' }}>{p}</span>;
    return <span key={i}>{p}</span>;
  });
}

function labelOf(viz) {
  const t = viz.target;
  return `${t.out} = ${t.expr}`;
}

function captionFor(viz) {
  const t = viz.target;
  const srcDf = viz.dfs[t.source] || Object.values(viz.dfs)[0];
  const resDf = t.result || viz.dfs[t.out];
  return `${t.out} = ${t.expr}  →  [${srcDf.shape[0]}×${srcDf.shape[1]}] to [${resDf.shape[0]}×${resDf.shape[1]}] DataFrame`;
}

function noteFor(mode) {
  if (mode === 'groupby') return 'GroupBy splits data into groups by key column and applies aggregation (mean/sum) per group.';
  if (mode === 'filter') return 'Boolean mask filters rows matching condition, returning matching DataFrame rows.';
  if (mode === 'sort') return 'sort_values reorders DataFrame rows by target column values.';
  if (mode === 'fillna') return 'fillna replaces missing NaN cells with specified default values.';
  return 'DataFrame operation transforms input table columns/rows into output DataFrame.';
}

const MODE_LABEL = {
  groupby: 'GroupBy & Aggregation',
  filter: 'Filter (mask)',
  transform: 'New Column / Op',
  sort: 'Sort & Rank',
  slice: 'Head / Slice',
  fillna: 'Missing Values (fillna)',
};

const EXAMPLES = [
  {
    name: 'Select Column',
    code: `import pandas as pd

df = pd.DataFrame({
    'item': ['Laptop', 'Phone', 'Tablet', 'Monitor'],
    'price': [1200, 800, 450, 300],
    'stock': [15, 30, 8, 25]
})

top_items = df[['price']]`,
  },
  {
    name: 'Filter (Mask)',
    code: `import pandas as pd

df = pd.DataFrame({
    'item': ['Laptop', 'Phone', 'Tablet', 'Monitor'],
    'price': [1200, 800, 450, 300],
    'stock': [15, 30, 8, 25]
})

top = df[df['price'] >= 450]`,
  },
  {
    name: 'GroupBy & Mean',
    code: `import pandas as pd

df = pd.DataFrame({
    'dept': ['eng', 'eng', 'sales', 'sales'],
    'salary': [90, 60, 50, 55],
    'rating': [4.5, 3.8, 4.0, 4.2]
})

summary = df.groupby('dept').mean()`,
  },
  {
    name: 'New Column',
    code: `import pandas as pd

df = pd.DataFrame({
    'name': ['Ann', 'Bo', 'Cy', 'Di'],
    'salary': [90, 60, 50, 55],
})

df['bonus'] = df['salary'] * 0.10`,
  },
  {
    name: 'Sort & Rank',
    code: `import pandas as pd

df = pd.DataFrame({
    'employee': ['Alice', 'Bob', 'Charlie', 'Diana'],
    'score': [88, 95, 70, 91]
})

ranked = df.sort_values(by='score', ascending=False)`,
  },
  {
    name: 'Missing Values',
    code: `import pandas as pd
import numpy as np

df = pd.DataFrame({
    'score_a': [95, np.nan, 88, 70],
    'score_b': [80, 85, np.nan, 90]
})

filled = df.fillna(0)`,
  },
];

const LS_KEY = 'pandas_vis_code';
const DEFAULT_CODE = EXAMPLES[0].code;

export default function PandasVisualizer({ theme = 'light', onSwitchToNumpy }) {
  const [code, setCode] = useState(() => localStorage.getItem(LS_KEY) || DEFAULT_CODE);
  const [applied, setApplied] = useState(null);
  const [result, setResult] = useState({ viz: null, error: null });
  const [loading, setLoading] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [replayKey, setReplayKey] = useState(0);

  const { viz, error } = result;
  const canvasRef = useRef(null);
  const runSeq = useRef(0);

  const run = useCallback(async (src) => {
    const next = typeof src === 'string' ? src : code;
    const seq = ++runSeq.current;
    setLoading(true);
    const answer = await fetchPandasModel(next);
    if (seq !== runSeq.current) return;

    setApplied(next);
    setLoading(false);
    localStorage.setItem(LS_KEY, next);
    if (answer.viz) {
      setResult(answer);
      setReplayKey((k) => k + 1);
    } else {
      setResult((prev) => ({ viz: prev.viz, error: answer.error }));
    }
  }, [code]);

  const loadExample = (src) => {
    setCode(src);
    run(src);
  };

  const handleEditorKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      run();
    }
  };

  // ── Canvas Render Loop (Matches NumPy Visualizer 100%) ────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const C_ = getC_(theme);
    const isDark = theme === 'dark';
    const dpr = window.devicePixelRatio || 2;
    canvas.width = CW * dpr;
    canvas.height = CH * dpr;

    let anim = 0;
    let animationFrameId;

    const cellBox = (x, y, w, h, col, ring, rw, txt, tcol, ts, fontMono = true) => {
      ctx.save();
      if (ring) {
        ctx.strokeStyle = ring;
        ctx.lineWidth = rw;
      } else {
        ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.12)' : '#e8e6dc';
        ctx.lineWidth = 1;
      }
      ctx.fillStyle = `rgb(${col[0]},${col[1]},${col[2]})`;
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(x + 1.5, y + 1.5, w - 3, h - 3, 4);
      } else {
        ctx.rect(x + 1.5, y + 1.5, w - 3, h - 3);
      }
      ctx.fill();
      ctx.stroke();

      if (txt !== null && txt !== undefined && txt !== '') {
        ctx.fillStyle = tcol;
        const fontFam = fontMono ? 'var(--font-mono), monospace' : 'var(--font-sans), sans-serif';
        ctx.font = `${ts}px ${fontFam}`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(txt), x + w / 2, y + h / 2);
      }
      ctx.restore();
    };

    const arrow = (x1, y1, x2, y2, prog, col, w, head) => {
      ctx.save();
      ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.15)' : '#e8e6dc';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();

      if (prog > 0) {
        const px = x1 + (x2 - x1) * prog;
        const py = y1 + (y2 - y1) * prog;
        ctx.strokeStyle = col;
        ctx.lineWidth = w;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(px, py);
        ctx.stroke();

        if (head && prog > 0.985) {
          const a = Math.atan2(y2 - y1, x2 - x1);
          const h = 7;
          ctx.fillStyle = col;
          ctx.beginPath();
          ctx.moveTo(x2, y2);
          ctx.lineTo(x2 - h * Math.cos(a - 0.4), y2 - h * Math.sin(a - 0.4));
          ctx.lineTo(x2 - h * Math.cos(a + 0.4), y2 - h * Math.sin(a + 0.4));
          ctx.closePath();
          ctx.fill();
        }
      }
      ctx.restore();
    };

    const label = (x, y, s, col) => {
      ctx.save();
      ctx.fillStyle = col || C_.mid;
      ctx.font = '13px var(--font-sans), sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(s, x, y);
      ctx.restore();
    };

    const getMinMax = (dfData) => {
      let lo = Infinity, hi = -Infinity;
      for (const row of dfData) {
        for (const val of row) {
          if (typeof val === 'number' && !isNaN(val)) {
            if (val < lo) lo = val;
            if (val > hi) hi = val;
          }
        }
      }
      if (!Number.isFinite(lo)) return { lo: 0, hi: 100 };
      return { lo, hi };
    };

    const drawGridTable = (ox, oy, dfDict, title, titleColor, activeRows = null, activeCols = null, progress = 1.0) => {
      const cols = dfDict.columns;
      const rows = dfDict.data;
      const idxs = dfDict.index;
      const { lo, hi } = getMinMax(rows);

      const cellW = Math.min(74, Math.max(52, (CW * 0.40) / (cols.length + 1)));
      const cellH = Math.min(38, (CH - 160) / (rows.length + 2));

      label(ox, oy - 10, `${title}  (${rows.length}×${cols.length})`, titleColor || C_.mid);

      // Header row: index header + column names
      const headerCol = isDark ? [45, 55, 72] : [228, 226, 218];
      cellBox(ox, oy, cellW, cellH, headerCol, null, 1, 'idx', C_.mid, Math.min(12, cellH * 0.35), false);

      for (let j = 0; j < cols.length; j++) {
        const isColInSel = activeCols === null || activeCols.length === 0 || activeCols.includes(j);
        const colBg = isColInSel ? (isDark ? [40, 60, 90] : [218, 226, 235]) : headerCol;
        cellBox(ox + (j + 1) * cellW, oy, cellW, cellH, colBg, isColInSel ? C_.orange : null, isColInSel ? 1.8 : 1, cols[j], isColInSel ? C_.dark : C_.mid, Math.min(12, cellH * 0.35), false);
      }

      // Data rows with wave fill animation
      const din = rows.length + cols.length;
      const front = progress * (din + 2);

      for (let i = 0; i < rows.length; i++) {
        const ry = oy + (i + 1) * cellH;
        const rowInSel = activeRows === null || activeRows.length === 0 || activeRows.includes(i);

        // Index cell
        const idxCol = rowInSel ? (isDark ? [35, 45, 60] : [236, 234, 228]) : (isDark ? [25, 33, 48] : [245, 243, 238]);
        cellBox(ox, ry, cellW, cellH, idxCol, rowInSel ? C_.orange : null, rowInSel ? 1.5 : 1, idxs[i] ?? i, rowInSel ? C_.dark : C_.mid, Math.min(12, cellH * 0.34));

        for (let j = 0; j < cols.length; j++) {
          const colInSel = activeCols === null || activeCols.length === 0 || activeCols.includes(j);
          const cellInSel = rowInSel && colInSel;

          const val = rows[i][j];
          const d = i + j;
          const on = d <= front;

          let col;
          let ring = null;
          let rw = 1;
          let tcol = C_.dark;

          if (!cellInSel) {
            // UNSELECTED CELL (Row or Column not in selection): GREY OUT EXACTLY LIKE NUMPY
            col = isDark ? [30, 41, 59] : [236, 234, 228];
            tcol = C_.mid;
          } else {
            // SELECTED CELL: HEAT COLOR + ORANGE RING LIKE NUMPY
            col = typeof val === 'number' ? heat(val, lo, hi, isDark) : (isDark ? [40, 55, 75] : [242, 238, 226]);
            ring = C_.orange;
            rw = 1.8;
            tcol = C_.dark;
          }

          cellBox(
            ox + (j + 1) * cellW,
            ry,
            cellW,
            cellH,
            col,
            ring,
            rw,
            on ? fmt(val) : '',
            tcol,
            Math.min(13, cellH * 0.34)
          );
        }
      }

      return {
        width: (cols.length + 1) * cellW,
        height: (rows.length + 1) * cellH,
        cellW,
        cellH,
      };
    };

    const renderFrame = () => {
      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.fillStyle = C_.light;
      ctx.fillRect(0, 0, CW, CH);

      if (!viz || !viz.dfs || !viz.target) {
        ctx.fillStyle = C_.mid;
        ctx.font = '15px var(--font-sans), sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(loading ? 'Running your Pandas code…' : 'Write Pandas code on the left, then press Run.', CW / 2, CH / 2);
        ctx.restore();
        return;
      }

      anim = Math.min(1, anim + 0.012 * speed);
      const t = viz.target;
      const srcDf = viz.dfs[t.source] || Object.values(viz.dfs)[0];
      const resDf = t.result || viz.dfs[t.out];

      const oy = 80;
      const ox = 54;

      const activeRows = t.active_rows || null;
      const activeCols = t.active_cols || null;
      const sBox = drawGridTable(ox, oy, srcDf, t.source, C_.mid, activeRows, activeCols, 1.0);
      const ox2 = CW - 54 - (resDf.columns.length + 1) * sBox.cellW;

      const rBox = drawGridTable(ox2, oy, resDf, `${t.out} = ${t.expr}`, C_.orange, null, null, anim);

      // Beam corners sweeping from source selection bounds directly to output block
      let c0 = 0;
      let c1 = srcDf.columns.length;
      if (activeCols && activeCols.length > 0) {
        c0 = Math.min(...activeCols);
        c1 = Math.max(...activeCols) + 1;
      }

      let r0 = 0;
      let r1 = srcDf.data.length;
      if (activeRows && activeRows.length > 0) {
        r0 = Math.min(...activeRows);
        r1 = Math.max(...activeRows) + 1;
      }

      const sx = ox + (c1 + 1) * sBox.cellW;
      const topY = oy + (r0 + 1) * sBox.cellH;
      const botY = oy + (r1 + 1) * sBox.cellH;

      arrow(sx, topY, ox2, oy + sBox.cellH, anim, C_.orange, 2.5, true);
      arrow(sx, botY, ox2, oy + rBox.height, anim, C_.orange, 2.5, true);

      // Bottom caption overlay
      ctx.save();
      ctx.fillStyle = isDark ? 'rgba(15, 23, 42, 0.92)' : 'rgba(250, 249, 245, 0.92)';
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(40, CH - 40, CW - 80, 26, 6);
      else ctx.rect(40, CH - 40, CW - 80, 26);
      ctx.fill();

      ctx.fillStyle = C_.dark;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.font = '13px var(--font-sans), sans-serif';
      ctx.fillText(captionFor(viz), 52, CH - 27);
      ctx.restore();

      ctx.restore();

      if (anim < 1) {
        animationFrameId = requestAnimationFrame(renderFrame);
      }
    };

    renderFrame();

    return () => {
      if (animationFrameId) cancelAnimationFrame(animationFrameId);
    };
  }, [viz, speed, replayKey, loading, theme]);

  const handleReplay = () => setReplayKey((k) => k + 1);

  const handleReset = () => {
    setCode(DEFAULT_CODE);
    setSpeed(1);
    run(DEFAULT_CODE);
  };

  const handleDownload = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const link = document.createElement('a');
    link.download = `pandas-${viz ? viz.target.mode : 'df'}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
  };

  const dirty = applied !== null && code !== applied;
  const shapes = viz && viz.dfs
    ? Object.entries(viz.dfs).map(([name, df]) => `${name}: ${df.shape[0]}×${df.shape[1]}`)
    : [];

  return (
    <div className="numpy-vis-container">
      <div className="numpy-vis-sidebar">
        <div className="vis-mode-switch">
          <button
            type="button"
            className="vis-mode-btn"
            onClick={onSwitchToNumpy}
          >
            NumPy Visualizer
          </button>
          <button
            type="button"
            className="vis-mode-btn active"
          >
            Pandas Visualizer
          </button>
        </div>

        <h1>Lattice DataFrames</h1>
        <div className="subtitle">
          Write Pandas operations — <b>column selection</b>, <b>groupby aggregations</b>, <b>filtering</b>, <b>sorting</b>, or <b>missing values</b> — and the diagram animates how input DataFrames map to output.
        </div>

        <div className="control-section">
          <h3>Code</h3>
          <div style={{ marginBottom: 12 }}>
            <CodeEditor
              value={code}
              onChange={setCode}
              filename="pandas_script.py"
              palette={theme}
              onKeyDown={handleEditorKeyDown}
            />
          </div>
          {error && <div className="error-box">{error}</div>}
          <button
            className={`numpy-action-btn ${dirty ? 'is-dirty' : ''}`}
            onClick={() => run()}
            disabled={loading}
          >
            {loading ? 'Running…' : <>▶ Run <span className="kbd">⌘↵</span></>}
          </button>
          <div className="note">
            Your snippet runs real Pandas on the server. Build DataFrames, then end with an expression to animate — a column selection (<code>df[['price']]</code>), a mask (<code>df[df['price'] &gt;= 450]</code>) or groupby (<code>df.groupby('dept').mean()</code>).
          </div>
          {shapes.length > 0 && (
            <div className="shape-tags">
              {shapes.map((s) => (
                <span className="shape-tag" key={s}>
                  {s}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="control-section">
          <h3>Examples</h3>
          <div className="mode-grid">
            {EXAMPLES.map((ex) => (
              <button
                key={ex.name}
                className={`mode-btn ${code === ex.code ? 'active' : ''}`}
                onClick={() => loadExample(ex.code)}
              >
                {ex.name}
              </button>
            ))}
          </div>
        </div>

        <div className="control-section">
          <h3>Concept detected</h3>
          <div className="mode-grid">
            {Object.entries(MODE_LABEL).map(([key, text]) => (
              <div
                key={key}
                className={`mode-btn readonly ${viz && viz.target.mode === key ? 'active' : ''}`}
              >
                {text}
              </div>
            ))}
          </div>
          <div className="expr">{viz ? colorExpr(labelOf(viz)) : '—'}</div>
          <div className="note">
            {viz ? noteFor(viz.target.mode) : 'Run some code to see which Pandas concept it exercises.'}
          </div>
        </div>

        <div className="control-section">
          <h3>Playback</h3>
          <div className="control-group">
            <label>Animation speed</label>
            <div className="slider-container">
              <input
                type="range"
                min="0.2"
                max="3"
                step="0.1"
                value={speed}
                onChange={(e) => {
                  setSpeed(parseFloat(e.target.value));
                  setReplayKey((k) => k + 1);
                }}
              />
              <span className="value-display">{Number(speed).toFixed(1)}</span>
            </div>
          </div>
        </div>

        <div className="control-section">
          <h3>Actions</h3>
          <div className="button-row">
            <button className="numpy-action-btn secondary" onClick={handleReplay}>
              ↻ Replay
            </button>
            <button className="numpy-action-btn secondary" onClick={handleReset}>
              Reset
            </button>
          </div>
          <button className="numpy-action-btn tertiary" style={{ marginTop: '8px' }} onClick={handleDownload}>
            ⬇ Download PNG
          </button>
        </div>
      </div>

      <div className="numpy-vis-canvas-area">
        <div className="numpy-vis-canvas-container">
          <canvas ref={canvasRef} />
        </div>
      </div>
    </div>
  );
}
