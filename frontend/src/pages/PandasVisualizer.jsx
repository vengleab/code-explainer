import React, { useState, useEffect, useRef, useCallback } from 'react';
import CodeEditor from '../components/CodeEditor.jsx';
import { fetchPandasModel } from '../services/api.js';
import { getC_, heat, fmt, colorExpr } from '../utils/visualizer.jsx';

const CW = 960;
const CH = 560;

// ── Backend Model fetching is delegated to services/api.js ────────────────────

function labelOf(viz) {
  const t = viz.target;
  return `${t.out} = ${t.expr}`;
}

function captionFor(viz) {
  const t = viz.target;
  if (t.mode === 'align') {
    return `${t.out} = ${t.expr}  →  Index Alignment: matching keys combine values; missing keys result in NaN`;
  }
  if (t.mode === 'concat') {
    return `${t.out} = ${t.expr}  →  pd.concat: stacks rows in order and preserves index labels`;
  }
  const srcDf = viz.dfs[t.source] || Object.values(viz.dfs)[0];
  const resDf = t.result || viz.dfs[t.out];
  return `${t.out} = ${t.expr}  →  [${srcDf.shape[0]}×${srcDf.shape[1]}] to [${resDf.shape[0]}×${resDf.shape[1]}] DataFrame`;
}

function noteFor(mode) {
  if (mode === 'align') return 'Index Alignment: + − × ÷ or merge align on index labels; matching labels operate, unmatched labels produce NaN.';
  if (mode === 'concat') return 'Concat stacks Series/DataFrame rows in order, keeping all index labels without alignment.';
  if (mode === 'groupby') return 'GroupBy splits data into groups by key column and applies aggregation (mean/sum) per group.';
  if (mode === 'filter') return 'Boolean mask filters rows matching condition, returning matching DataFrame rows.';
  if (mode === 'sort') return 'sort_values reorders DataFrame rows by target column values.';
  if (mode === 'fillna') return 'fillna replaces missing NaN cells with specified default values.';
  return 'DataFrame operation transforms input table columns/rows into output DataFrame.';
}

const MODE_LABEL = {
  align: 'Index Alignment (a + b)',
  concat: 'Concat (stack rows)',
  groupby: 'GroupBy & Aggregation',
  filter: 'Filter (mask)',
  transform: 'New Column / Op',
  sort: 'Sort & Rank',
  slice: 'Head / Slice',
  fillna: 'Missing Values (fillna)',
};

const EXAMPLES = [
  {
    name: 'Index Align (a + b)',
    code: `import pandas as pd

a = pd.Series([10, 25], index=['x', 'y'])
b = pd.Series([30, 45], index=['y', 'z'])

c = a + b`,
  },
  {
    name: 'Concat Series',
    code: `import pandas as pd

a = pd.Series([10, 25], index=['x', 'y'])
b = pd.Series([30, 45], index=['p', 'q'])

c = pd.concat([a, b])`,
  },
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

export default function PandasVisualizer({
  theme = 'light',
  layout = 'split',
  splitRatio = 33,
  isResizing = false,
  splitContainerRef,
  onMouseDown,
  onResetSplit,
}) {
  const [code, setCode] = useState(() => localStorage.getItem(LS_KEY) || DEFAULT_CODE);
  const [applied, setApplied] = useState(null);
  const [result, setResult] = useState({ viz: null, error: null });
  const [loading, setLoading] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [replayKey, setReplayKey] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [stepIndex, setStepIndex] = useState(0);
  const animProgressRef = useRef(0);
  const isScrubbingRef = useRef(false);

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
      isScrubbingRef.current = false;
      animProgressRef.current = 0;
      setStepIndex(0);
      setIsPlaying(true);
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

  // ── Canvas Render Loop ────────────────────────────────────────────
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

    let anim = animProgressRef.current;
    let animationFrameId;

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
          const h = 8;
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

    const label = (x, y, s, col, sz) => {
      ctx.save();
      ctx.fillStyle = col || C_.mid;
      ctx.font = `${sz || 13}px var(--font-sans), sans-serif`;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(s, x, y);
      ctx.restore();
    };

    // Series/DataFrame Cell with inner Index Chip Badge
    const drawSeriesCell = (x, y, w, h, idxLabel, val, fillCol, ring, txtVal) => {
      ctx.save();
      const isNan = txtVal === 'NaN' || txtVal === null || (typeof txtVal === 'number' && isNaN(txtVal));
      if (ring) {
        ctx.strokeStyle = ring;
        ctx.lineWidth = 2.4;
      } else {
        ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.12)' : '#e8e6dc';
        ctx.lineWidth = 1;
      }
      ctx.fillStyle = isNan ? (isDark ? 'rgb(60, 30, 35)' : 'rgb(253, 242, 240)') : `rgb(${fillCol[0]},${fillCol[1]},${fillCol[2]})`;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x + 1.5, y + 1.5, w - 3, h - 3, 6);
      else ctx.rect(x + 1.5, y + 1.5, w - 3, h - 3);
      ctx.fill();
      ctx.stroke();

      // Index Chip Badge (inner pill)
      const chipW = Math.min(38, w * 0.35);
      ctx.fillStyle = isDark ? 'rgba(255, 255, 255, 0.16)' : 'rgba(255, 255, 255, 0.85)';
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x + 5, y + 5, chipW, h - 10, 4);
      else ctx.rect(x + 5, y + 5, chipW, h - 10);
      ctx.fill();

      ctx.fillStyle = C_.dark;
      ctx.font = `600 ${Math.min(13, h * 0.38)}px var(--font-mono), monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(idxLabel), x + 5 + chipW / 2, y + h / 2);

      // Value section
      if (txtVal !== undefined && txtVal !== '') {
        ctx.fillStyle = isNan ? C_.nan : C_.dark;
        ctx.font = `${isNan ? '600' : '500'} ${Math.min(14, h * 0.38)}px var(--font-mono), monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(isNan ? 'NaN' : String(fmt(txtVal)), x + 5 + chipW + (w - 5 - chipW) / 2, y + h / 2);
      }
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

    // ── Single DataFrame Table Renderer ─────────────────────────────────
    const drawGridTable = (ox, oy, dfDict, title, titleColor, activeRows = null, activeCols = null, progress = 1.0) => {
      const cols = dfDict.columns;
      const rows = dfDict.data;
      const idxs = dfDict.index;
      const { lo, hi } = getMinMax(rows);

      const cellW = Math.min(74, Math.max(52, (CW * 0.40) / (cols.length + 1)));
      const cellH = Math.min(38, (CH - 160) / (rows.length + 2));

      label(ox, oy - 10, `${title}  (${rows.length}×${cols.length})`, titleColor || C_.mid);

      const headerCol = isDark ? [45, 55, 72] : [228, 226, 218];
      // Index header chip
      ctx.save();
      ctx.fillStyle = `rgb(${headerCol[0]},${headerCol[1]},${headerCol[2]})`;
      ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.12)' : '#e8e6dc';
      ctx.lineWidth = 1;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(ox + 1.5, oy + 1.5, cellW - 3, cellH - 3, 4);
      else ctx.rect(ox + 1.5, oy + 1.5, cellW - 3, cellH - 3);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = C_.mid;
      ctx.font = `${Math.min(12, cellH * 0.35)}px var(--font-sans), sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('idx', ox + cellW / 2, oy + cellH / 2);
      ctx.restore();

      for (let j = 0; j < cols.length; j++) {
        const isColInSel = activeCols === null || activeCols.length === 0 || activeCols.includes(j);
        const colBg = isColInSel ? (isDark ? [40, 60, 90] : [218, 226, 235]) : headerCol;
        ctx.save();
        ctx.fillStyle = `rgb(${colBg[0]},${colBg[1]},${colBg[2]})`;
        ctx.strokeStyle = isColInSel ? C_.orange : (isDark ? 'rgba(255, 255, 255, 0.12)' : '#e8e6dc');
        ctx.lineWidth = isColInSel ? 1.8 : 1;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(ox + (j + 1) * cellW + 1.5, oy + 1.5, cellW - 3, cellH - 3, 4);
        else ctx.rect(ox + (j + 1) * cellW + 1.5, oy + 1.5, cellW - 3, cellH - 3);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = isColInSel ? C_.dark : C_.mid;
        ctx.font = `${Math.min(12, cellH * 0.35)}px var(--font-sans), sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(cols[j], ox + (j + 1) * cellW + cellW / 2, oy + cellH / 2);
        ctx.restore();
      }

      const din = rows.length + cols.length;
      const front = progress * (din + 2);

      for (let i = 0; i < rows.length; i++) {
        const ry = oy + (i + 1) * cellH;
        const rowInSel = activeRows === null || activeRows.length === 0 || activeRows.includes(i);
        const idxCol = rowInSel ? (isDark ? [35, 45, 60] : [236, 234, 228]) : (isDark ? [25, 33, 48] : [245, 243, 238]);

        // Index cell
        ctx.save();
        ctx.fillStyle = `rgb(${idxCol[0]},${idxCol[1]},${idxCol[2]})`;
        ctx.strokeStyle = rowInSel ? C_.orange : (isDark ? 'rgba(255, 255, 255, 0.12)' : '#e8e6dc');
        ctx.lineWidth = rowInSel ? 1.5 : 1;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(ox + 1.5, ry + 1.5, cellW - 3, cellH - 3, 4);
        else ctx.rect(ox + 1.5, ry + 1.5, cellW - 3, cellH - 3);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = rowInSel ? C_.dark : C_.mid;
        ctx.font = `${Math.min(12, cellH * 0.34)}px var(--font-mono), monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(idxs[i] ?? i), ox + cellW / 2, ry + cellH / 2);
        ctx.restore();

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
            col = isDark ? [30, 41, 59] : [236, 234, 228];
            tcol = C_.mid;
          } else {
            col = typeof val === 'number' ? heat(val, lo, hi, isDark) : (isDark ? [40, 55, 75] : [242, 238, 226]);
            ring = C_.orange;
            rw = 1.8;
            tcol = C_.dark;
          }

          ctx.save();
          ctx.fillStyle = `rgb(${col[0]},${col[1]},${col[2]})`;
          ctx.strokeStyle = ring || (isDark ? 'rgba(255, 255, 255, 0.12)' : '#e8e6dc');
          ctx.lineWidth = rw;
          ctx.beginPath();
          if (ctx.roundRect) ctx.roundRect(ox + (j + 1) * cellW + 1.5, ry + 1.5, cellW - 3, cellH - 3, 4);
          else ctx.rect(ox + (j + 1) * cellW + 1.5, ry + 1.5, cellW - 3, cellH - 3);
          ctx.fill();
          ctx.stroke();

          if (on && val !== null && val !== undefined && val !== '') {
            const isNan = val === 'NaN' || val === null || (typeof val === 'number' && isNaN(val));
            ctx.fillStyle = isNan ? C_.nan : tcol;
            ctx.font = `${Math.min(13, cellH * 0.34)}px var(--font-mono), monospace`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(isNan ? 'NaN' : String(fmt(val)), ox + (j + 1) * cellW + cellW / 2, ry + cellH / 2);
          }
          ctx.restore();
        }
      }

      return {
        width: (cols.length + 1) * cellW,
        height: (rows.length + 1) * cellH,
        cellW,
        cellH,
      };
    };

    // ── Two Input Index Alignment Renderer (a + b) ─────────────────────
    const drawAlign = (nameA, dfA, nameB, dfB, resDf, progress) => {
      const cellW = 160;
      const cellH = 44;
      const gap = 12;
      const colX_a = 54;
      const colX_b = 260;
      const colX_out = CW - 54 - cellW;

      const rowsA = dfA.data;
      const idxA = dfA.index;
      const rowsB = dfB.data;
      const idxB = dfB.index;

      const resRows = resDf.data;
      const resIdx = resDf.index;

      const topA = (CH - 100 - rowsA.length * (cellH + gap)) / 2 + 40;
      const topB = (CH - 100 - rowsB.length * (cellH + gap)) / 2 + 40;
      const topU = (CH - 100 - resRows.length * (cellH + gap)) / 2 + 40;

      label(colX_a, topA - 14, nameA, C_.mid, 15);
      label(colX_b, topB - 14, nameB, C_.mid, 15);
      label(colX_out, topU - 14, `${viz.target.out} = ${viz.target.expr} (index union)`, C_.orange, 14);

      // Draw Series A
      for (let i = 0; i < rowsA.length; i++) {
        const val = rowsA[i][0];
        const iy = topA + i * (cellH + gap);
        drawSeriesCell(colX_a, iy, cellW, cellH, idxA[i], val, heat(val, 0, 100, isDark), null, val);
      }

      // Draw Series B
      for (let i = 0; i < rowsB.length; i++) {
        const val = rowsB[i][0];
        const iy = topB + i * (cellH + gap);
        drawSeriesCell(colX_b, iy, cellW, cellH, idxB[i], val, heat(val, 0, 100, isDark), null, val);
      }

      // Draw Output Rows and Index Match Arrows
      for (let k = 0; k < resRows.length; k++) {
        const lab = resIdx[k];
        const oy = topU + k * (cellH + gap) + cellH / 2;
        const ai = idxA.indexOf(lab);
        const bi = idxB.indexOf(lab);
        const inA = ai !== -1;
        const inB = bi !== -1;
        const both = inA && inB;

        const t = Math.max(0, Math.min(1, progress * resRows.length - k));

        if (inA) {
          const ay = topA + ai * (cellH + gap) + cellH / 2;
          arrow(colX_a + cellW, ay, colX_out, oy, t, both ? C_.green : C_.nan, 2.2, true);
        }
        if (inB) {
          const by = topB + bi * (cellH + gap) + cellH / 2;
          arrow(colX_b + cellW, by, colX_out, oy, t, both ? C_.green : C_.nan, 2.2, true);
        }

        const val = resRows[k][0];
        const shown = t >= 1;
        const fillCol = shown ? (both ? heat(val, 0, 100, isDark) : (isDark ? [55, 35, 40] : [247, 235, 232])) : (isDark ? [30, 41, 59] : [236, 234, 228]);
        drawSeriesCell(colX_out, topU + k * (cellH + gap), cellW, cellH, lab, val, fillCol, shown ? (both ? C_.green : C_.nan) : null, shown ? val : '');
      }
    };

    // ── Two Input Concat Renderer (pd.concat([a, b])) ─────────────────
    const drawConcat = (nameA, dfA, nameB, dfB, resDf, progress) => {
      const cellW = 160;
      const cellH = 44;
      const gap = 12;
      const colX_a = 54;
      const colX_b = 260;
      const colX_out = CW - 54 - cellW;

      const rowsA = dfA.data;
      const idxA = dfA.index;
      const rowsB = dfB.data;
      const idxB = dfB.index;

      const resRows = resDf.data;
      const resIdx = resDf.index;

      const topA = (CH - 100 - rowsA.length * (cellH + gap)) / 2 + 40;
      const topB = (CH - 100 - rowsB.length * (cellH + gap)) / 2 + 40;
      const topU = (CH - 100 - resRows.length * (cellH + gap)) / 2 + 40;

      label(colX_a, topA - 14, nameA, C_.mid, 15);
      label(colX_b, topB - 14, nameB, C_.mid, 15);
      label(colX_out, topU - 14, `concat([${nameA}, ${nameB}]) (stacks rows)`, C_.blue, 14);

      // Draw Series A
      for (let i = 0; i < rowsA.length; i++) {
        const val = rowsA[i][0];
        const iy = topA + i * (cellH + gap);
        drawSeriesCell(colX_a, iy, cellW, cellH, idxA[i], val, heat(val, 0, 100, isDark), null, val);
      }

      // Draw Series B
      for (let i = 0; i < rowsB.length; i++) {
        const val = rowsB[i][0];
        const iy = topB + i * (cellH + gap);
        drawSeriesCell(colX_b, iy, cellW, cellH, idxB[i], val, heat(val, 0, 100, isDark), null, val);
      }

      // Draw Output Rows and Stack Arrows
      for (let k = 0; k < resRows.length; k++) {
        const t = Math.max(0, Math.min(1, progress * resRows.length - k));
        const oy = topU + k * (cellH + gap) + cellH / 2;
        const isFromA = k < rowsA.length;

        let sx, sy, col;
        if (isFromA) {
          sx = colX_a + cellW;
          sy = topA + k * (cellH + gap) + cellH / 2;
          col = C_.orange;
        } else {
          const bi = k - rowsA.length;
          sx = colX_b + cellW;
          sy = topB + bi * (cellH + gap) + cellH / 2;
          col = C_.blue;
        }

        arrow(sx, sy, colX_out, oy, t, col, 2.2, true);

        const lab = resIdx[k];
        const val = resRows[k][0];
        const shown = t >= 1;
        const fillCol = shown ? heat(val, 0, 100, isDark) : (isDark ? [30, 41, 59] : [236, 234, 228]);
        drawSeriesCell(colX_out, topU + k * (cellH + gap), cellW, cellH, lab, val, fillCol, shown ? col : null, shown ? val : '');
      }
    };

    // ── Main Frame Dispatcher ─────────────────────────────────────────
    const renderFrame = () => {
      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.fillStyle = C_.light;
      ctx.fillRect(0, 0, CW, CH);

      if (!viz) {
        ctx.restore();
        return;
      }

      if (isScrubbingRef.current) {
        anim = animProgressRef.current;
      } else {
        anim = Math.min(1, anim + 0.012 * speed);
        animProgressRef.current = anim;
        setStepIndex(Math.round(anim * 100));
      }

      const t = viz.target;

      if (t.mode === 'select' || t.mode === 'drop') drawSelect(t, anim);
      else if (t.mode === 'filter') drawFilter(t, anim);
      else if (t.mode === 'assign') drawAssign(t, anim);
      else drawOp(t, anim);

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

      if (isPlaying && anim < 1 && !isScrubbingRef.current) {
        animationFrameId = requestAnimationFrame(renderFrame);
      }
    };

    renderFrame();

    return () => {
      if (animationFrameId) cancelAnimationFrame(animationFrameId);
    };
  }, [viz, speed, replayKey, loading, theme, isPlaying]);

  // ── Actions ────────────────────────────────────────────────────────────
  const handleReplay = () => {
    isScrubbingRef.current = false;
    animProgressRef.current = 0;
    setStepIndex(0);
    setIsPlaying(true);
    setReplayKey((k) => k + 1);
  };

  const togglePlay = () => {
    if (stepIndex >= 100) {
      handleReplay();
      return;
    }
    isScrubbingRef.current = false;
    setIsPlaying((p) => !p);
  };

  const handleJumpFirst = () => {
    setStepIndex(0);
    animProgressRef.current = 0;
    isScrubbingRef.current = true;
    setIsPlaying(false);
    setReplayKey((k) => k + 1);
  };

  const handleStepPrev = () => {
    setStepIndex((s) => {
      const next = Math.max(0, s - 25);
      animProgressRef.current = next / 100;
      return next;
    });
    isScrubbingRef.current = true;
    setIsPlaying(false);
    setReplayKey((k) => k + 1);
  };

  const handleStepNext = () => {
    setStepIndex((s) => {
      const next = Math.min(100, s + 25);
      animProgressRef.current = next / 100;
      return next;
    });
    isScrubbingRef.current = true;
    setIsPlaying(false);
    setReplayKey((k) => k + 1);
  };

  const handleJumpLast = () => {
    setStepIndex(100);
    animProgressRef.current = 1;
    isScrubbingRef.current = true;
    setIsPlaying(false);
    setReplayKey((k) => k + 1);
  };

  const handleScrubberChange = (e) => {
    const val = Number(e.target.value);
    setStepIndex(val);
    animProgressRef.current = val / 100;
    isScrubbingRef.current = true;
    setIsPlaying(false);
    setReplayKey((k) => k + 1);
  };


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

  const isSplit = layout === 'split';

  return (
    <div
      ref={splitContainerRef}
      className={`numpy-vis-container ${isSplit ? 'layout-split' : ''} ${isResizing ? 'is-dragging' : ''}`}
      style={
        isSplit
          ? {
              display: 'grid',
              gridTemplateColumns: `${splitRatio}fr 8px ${100 - splitRatio}fr`,
              gap: '12px',
              alignItems: 'start',
              width: '100%',
            }
          : {
              display: 'flex',
              flexDirection: 'column',
              gap: '20px',
              width: '100%',
            }
      }
    >
      <div className="numpy-vis-sidebar">
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
            Your snippet runs real Pandas on the server. Build DataFrames/Series, then end with an expression to animate — index alignment (<code>a + b</code>), concat (<code>pd.concat([a, b])</code>), column selection (<code>df[['price']]</code>), or mask (<code>df[df['price'] &gt;= 450]</code>).
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
          {viz ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <span
                className="shape-tag"
                style={{
                  background: 'var(--brand-blue-bg)',
                  color: 'var(--brand-blue)',
                  border: '1px solid var(--brand-blue-border)',
                  fontWeight: 600,
                  fontSize: '12px',
                  alignSelf: 'flex-start',
                }}
              >
                {MODE_LABEL[viz.target.mode] || viz.target.mode}
              </span>
              <div className="expr">{colorExpr(labelOf(viz))}</div>
              <div className="note">{noteFor(viz.target.mode)}</div>
            </div>
          ) : (
            <div className="note">Run some Pandas code to detect concept and animate data transformation.</div>
          )}
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

      {isSplit && (
        <div
          className="split-resizer"
          onMouseDown={onMouseDown}
          onDoubleClick={onResetSplit}
          title="Drag to resize columns • Double-click to reset (33/67)"
          role="separator"
          aria-orientation="vertical"
        >
          <div className="resizer-handle" />
        </div>
      )}

      <div className="numpy-vis-canvas-area">
        <div className="numpy-vis-canvas-container">
          <canvas ref={canvasRef} />
          {!viz && (
            <div className="vis-empty-state">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
              </svg>
              {loading ? (
                <>
                  <h4>Running your Pandas…</h4>
                  <p>DataFrame calculations are executing on the server.</p>
                </>
              ) : (
                <>
                  <h4>Nothing to visualize yet</h4>
                  <p>Pick an example or write your own, then press Run to animate it.</p>
                </>
              )}
            </div>
          )}
        </div>

        {/* Slide & Frame Stepper Controller — Same as Code Explainer */}
        <div className="slide-controller" style={{ marginTop: '16px' }}>
          <div className="player-toolbar">
            <button
              type="button"
              className="player-btn"
              onClick={handleJumpFirst}
              disabled={stepIndex === 0}
              title="First Step"
            >
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M6 6h2v12H6zm3.5 6l8.5 6V6z" />
              </svg>
            </button>

            <button
              type="button"
              className="player-btn"
              onClick={handleStepPrev}
              disabled={stepIndex === 0}
              title="Previous Step"
            >
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z" />
              </svg>
            </button>

            <button
              type="button"
              className="player-btn play-pause-btn"
              onClick={togglePlay}
              title={isPlaying ? "Pause" : "Play"}
            >
              {isPlaying ? (
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
              )}
            </button>

            <button
              type="button"
              className="player-btn"
              onClick={handleStepNext}
              disabled={stepIndex === 100}
              title="Next Step"
            >
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z" />
              </svg>
            </button>

            <button
              type="button"
              className="player-btn"
              onClick={handleJumpLast}
              disabled={stepIndex === 100}
              title="Last Step"
            >
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M16 6h2v12h-2zM6 18l8.5-6L6 6z" />
              </svg>
            </button>
          </div>

          <div className="player-scrubber">
            <span className="step-counter">
              Step <strong>{Math.min(4, Math.floor(stepIndex / 25) + 1)}</strong> of 4
            </span>
            <input
              type="range"
              className="player-slider"
              min={0}
              max={100}
              value={stepIndex}
              onChange={handleScrubberChange}
            />
          </div>
        </div>

        {/* Action Buttons Row */}
        <div className="actions-row" style={{ marginTop: '14px', display: 'flex', gap: '8px', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'center' }}>
          <button type="button" className="numpy-action-btn secondary" onClick={handleDownload} style={{ padding: '8px 14px', fontSize: '13px', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ width: 14, height: 14 }}>
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
            Download PNG
          </button>

          <button type="button" className="numpy-action-btn secondary" onClick={handleReset} style={{ padding: '8px 14px', fontSize: '13px', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ width: 14, height: 14 }}>
              <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
              <path d="M3 3v5h5" />
            </svg>
            Reset Code
          </button>

          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginLeft: '8px' }}>
            <span style={{ fontSize: '12px', color: 'var(--text-secondary)', fontWeight: 500 }}>Speed:</span>
            <div className="quality-seg" role="group" aria-label="Speed options">
              {[0.5, 1, 2].map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`quality-btn ${speed === s ? 'active' : ''}`}
                  onClick={() => {
                    setSpeed(s);
                    setIsPlaying(true);
                    isScrubbingRef.current = false;
                    setReplayKey((k) => k + 1);
                  }}
                >
                  {s === 0.5 ? '0.5×' : s === 1 ? '1×' : '2×'}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
