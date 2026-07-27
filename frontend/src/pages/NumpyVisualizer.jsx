import React, { useState, useEffect, useRef, useCallback } from 'react';
import CodeEditor from '../components/CodeEditor.jsx';
import { fetchNumpyModel } from '../services/api.js';
import { getC_, heat, fmt, colorExpr } from '../utils/visualizer.jsx';

const CW = 960;
const CH = 560;

// Mentioned in the sidebar copy only — backend/numpy_model.py does the enforcing.
const MAX_DIM = 12;

/** Display glyph for a raw operator — used on the canvas, where math reads better. */
const OP_SYM = { '+': '+', '-': '−', '*': '×', '/': '÷' };
const OP_WORD = { '+': 'add', '-': 'subtract', '*': 'multiply', '/': 'divide' };

function compare(v, cmp, t) {
  switch (cmp) {
    case '>':
      return v > t;
    case '<':
      return v < t;
    case '>=':
      return v >= t;
    case '<=':
      return v <= t;
    case '==':
      return v === t;
    case '!=':
      return v !== t;
    default:
      return false;
  }
}

/** Min/max over a 2-D array, used to scale the heat ramp to the actual data. */
function range2d(m) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const row of m) {
    for (const v of row) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (!Number.isFinite(lo)) return { lo: 0, hi: 1 };
  return { lo, hi };
}

// ── Talking to the backend ───────────────────────────────────────────────
// The snippet is executed by /api/visualize-numpy (backend/numpy_model.py):
// real NumPy computes the values, the AST supplies the slice geometry, and what
// comes back is the model this canvas animates:
//
//   { arrays: {name: 2-D list}, oneD: {name: bool},
//     target: {mode, a, b?, op?, operand?, cmp?, thresh?,
//              r0, r1, c0, c1, out, oneD, result} }
//
// Parsing deliberately does NOT happen here as well — one source of truth for
// what a snippet means, and it is the one that actually runs NumPy.
// ── Backend Model fetching is delegated to services/api.js ────────────────────



// ── Sidebar and canvas copy, derived from the model ──────────────────────

/**
 * Render the target expression the way it should read on screen. The backend
 * sends structure (mode, operator, resolved bounds) and this is where it becomes
 * text, so the maths glyphs (× ÷ −) stay a frontend concern.
 */
function labelOf(viz) {
  const t = viz.target;
  if (t.mode === 'slice') {
    return viz.oneD[t.a] ? `${t.a}[${t.c0}:${t.c1}]` : `${t.a}[${t.r0}:${t.r1}, ${t.c0}:${t.c1}]`;
  }
  if (t.mode === 'filter') return `${t.a}[${t.a} ${t.cmp} ${t.thresh}]`;
  if (t.mode === 'scalar') return `${t.a} ${OP_SYM[t.op]} ${t.operand}`;
  return `${t.a} ${OP_SYM[t.op]} ${t.b}`;
}

function captionFor(viz) {
  const t = viz.target;
  const A = viz.arrays[t.a];
  const R = A.length;
  const Cn = A[0].length;
  const label = labelOf(viz);
  if (t.mode === 'slice') {
    const selR = t.r1 - t.r0;
    const selC = t.c1 - t.c0;
    const shape = viz.oneD[t.a] ? `${selC} contiguous values` : `a ${selR}×${selC} contiguous sub-array`;
    return `${label}  →  ${shape} (start included, stop excluded)`;
  }
  if (t.mode === 'filter') {
    const k = t.result[0].length;
    return `${label}  →  boolean mask gathers ${k} values, row-major, into a flat 1-D array`;
  }
  if (t.mode === 'scalar') {
    return `${label}  →  the scalar broadcasts to every one of the ${R * Cn} cells at once`;
  }
  return `${label}  →  element-wise: ${t.out}[i,j] = ${t.a}[i,j] ${OP_SYM[t.op]} ${t.b}[i,j]`;
}

function noteFor(mode, op) {
  if (mode === 'slice')
    return 'A slice returns a contiguous rectangular sub-array. Endpoints are half-open: start included, stop excluded.';
  if (mode === 'filter')
    return 'Boolean-mask indexing returns a 1-D array of the values where the condition is True — the grid shape is lost.';
  if (mode === 'scalar')
    return `Broadcasting: the single number is ${OP_WORD[op]}ed into every cell simultaneously — no loop, same output shape.`;
  return `Element-wise ${OP_WORD[op]}: two same-shape arrays combine cell by cell into a new array.`;
}

const MODE_LABEL = {
  slice: 'Index / Slice',
  filter: 'Filter (mask)',
  scalar: 'op · scalar',
  array: 'op · array',
};



// ── Examples ─────────────────────────────────────────────────────────────

const EXAMPLES = [
  {
    name: 'Index / Slice',
    code: `import numpy as np

np.random.seed(12345)
A = np.random.randint(0, 100, (8, 8))

C = A[1:5, 1:5]`,
  },
  {
    name: 'Filter (mask)',
    code: `import numpy as np

np.random.seed(12345)
A = np.random.randint(0, 100, (8, 8))

C = A[A > 50]`,
  },
  {
    name: 'op · scalar',
    code: `import numpy as np

np.random.seed(12345)
A = np.random.randint(0, 100, (8, 8))

C = A + 10`,
  },
  {
    name: 'op · array',
    code: `import numpy as np

np.random.seed(7)
A = np.random.randint(0, 100, (8, 8))
B = np.random.randint(1, 10, (8, 8))

C = A * B`,
  },
  {
    name: 'Literal grid',
    code: `import numpy as np

A = np.array([
    [5, 12, 47, 63],
    [21, 88, 34, 70],
    [9, 56, 18, 42],
])

C = A[:, 1:3]`,
  },
  {
    name: 'arange',
    code: `import numpy as np

A = np.arange(1, 37).reshape(6, 6)

C = A[2:5, :4]`,
  },
];

const LS_KEY = 'numpy_vis_code';
const DEFAULT_CODE = EXAMPLES[0].code;

export default function NumpyVisualizer({
  theme = 'light',
  layout = 'split',
  splitRatio = 33,
  isResizing = false,
  splitContainerRef,
  onMouseDown,
  onResetSplit,
}) {
  const [code, setCode] = useState(() => localStorage.getItem(LS_KEY) || DEFAULT_CODE);
  const [applied, setApplied] = useState(null); // the code the current frame shows
  const [result, setResult] = useState({ viz: null, error: null });
  const [loading, setLoading] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [replayKey, setReplayKey] = useState(0);

  const { viz, error } = result;
  const canvasRef = useRef(null);
  const runSeq = useRef(0); // guards against out-of-order responses

  // ── Run the code ───────────────────────────────────────────────────────
  const run = useCallback(async (src) => {
    const next = typeof src === 'string' ? src : code;
    const seq = ++runSeq.current;
    setLoading(true);
    const answer = await fetchNumpyModel(next);
    if (seq !== runSeq.current) return; // a newer run already answered

    setApplied(next);
    setLoading(false);
    localStorage.setItem(LS_KEY, next);
    if (answer.viz) {
      setResult(answer);
      setReplayKey((k) => k + 1);
    } else {
      // Keep the last good frame on screen and just surface the error.
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
      e.stopPropagation(); // the app-level ⌘+Enter belongs to the explainer route
      run();
    }
  };

  // ── Main canvas render loop ────────────────────────────────────────────
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
    const exprText = viz ? labelOf(viz) : '';

    const cellBox = (x, y, s, col, ring, rw, txt, tcol, ts) => {
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
        ctx.roundRect(x + 1.5, y + 1.5, s - 3, s - 3, 4);
      } else {
        ctx.rect(x + 1.5, y + 1.5, s - 3, s - 3);
      }
      ctx.fill();
      ctx.stroke();

      if (txt !== null && txt !== undefined && txt !== '') {
        ctx.fillStyle = tcol;
        ctx.font = `${ts}px var(--font-mono), monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(txt), x + s / 2, y + s / 2);
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

    const drawSlice = (t, progress) => {
      const A = viz.arrays[t.a];
      const R = A.length;
      const Cn = A[0].length;
      const { lo, hi } = range2d(A);
      const selR = t.r1 - t.r0;
      const selC = t.c1 - t.c0;
      const cell = Math.min(44, (CW - 140) / (Cn + selC + 3), (CH - 150) / R);
      const ox = 54;
      const oy = 80;
      const gh = R * cell;
      const outW = selC * cell;
      const outH = selR * cell;
      const ox2 = CW - 54 - outW;
      const oy2 = oy + (gh - outH) / 2;

      const src1d = viz.oneD[t.a];
      label(ox, oy - 10, src1d ? `${t.a}  (source: ${Cn} values)` : `${t.a}  (source ${R}×${Cn})`, C_.mid);
      label(ox2, oy2 - 10, src1d ? `${exprText}  (${selC})` : `${exprText}  (${selR}×${selC})`, C_.orange);

      const din = selR + selC;
      const front = progress * (din + 2);

      // source grid
      for (let i = 0; i < R; i++) {
        for (let j = 0; j < Cn; j++) {
          const inSel = i >= t.r0 && i < t.r1 && j >= t.c0 && j < t.c1;
          let col = heat(A[i][j], lo, hi, isDark);
          let ring = null;
          let rw = 1;
          let tcol = C_.dark;
          if (inSel) {
            ring = C_.orange;
            rw = 2;
          } else {
            col = isDark ? [30, 41, 59] : [236, 234, 228];
            tcol = C_.mid;
          }
          cellBox(ox + j * cell, oy + i * cell, cell, col, ring, rw, fmt(A[i][j]), tcol, Math.min(14, cell * 0.34));
        }
      }

      // beam corners
      const sx = ox + t.c1 * cell;
      arrow(sx, oy + t.r0 * cell, ox2, oy2, progress, C_.orange, 2.5, true);
      arrow(sx, oy + t.r1 * cell, ox2, oy2 + outH, progress, C_.orange, 2.5, true);

      // output block
      for (let i = 0; i < selR; i++) {
        for (let j = 0; j < selC; j++) {
          const d = i + j;
          const on = d <= front;
          const val = A[t.r0 + i][t.c0 + j];
          const col = on ? heat(val, lo, hi, isDark) : (isDark ? [30, 41, 59] : [236, 234, 228]);
          cellBox(
            ox2 + j * cell,
            oy2 + i * cell,
            cell,
            col,
            on ? C_.orange : null,
            on ? 2 : 1,
            on ? fmt(val) : '',
            C_.dark,
            Math.min(14, cell * 0.34),
          );
        }
      }
    };

    const drawFilter = (t, progress) => {
      const A = viz.arrays[t.a];
      const R = A.length;
      const Cn = A[0].length;
      const { lo, hi } = range2d(A);
      const cell = Math.min(40, (CW - 420) / Cn, (CH - 150) / R);
      const ox = 54;
      const oy = 80;
      const gw = Cn * cell;

      const kept = [];
      for (let i = 0; i < R; i++) {
        for (let j = 0; j < Cn; j++) {
          if (compare(A[i][j], t.cmp, t.thresh)) kept.push({ i, j, v: A[i][j] });
        }
      }
      const reveal = Math.floor(progress * kept.length + 0.0001);
      label(ox, oy - 10, `${t.a}  (mask: value ${t.cmp} ${t.thresh})`, C_.mid);

      for (let i = 0; i < R; i++) {
        for (let j = 0; j < Cn; j++) {
          const pass = compare(A[i][j], t.cmp, t.thresh);
          const col = pass ? heat(A[i][j], lo, hi, isDark) : (isDark ? [30, 41, 59] : [236, 234, 228]);
          cellBox(
            ox + j * cell,
            oy + i * cell,
            cell,
            col,
            pass ? C_.green : null,
            pass ? 2 : 1,
            fmt(A[i][j]),
            pass ? C_.dark : C_.mid,
            Math.min(13, cell * 0.32),
          );
        }
      }

      // result strip — shrink the cells until the whole 1-D array fits
      const stripX = ox + gw + 130;
      let scell = Math.min(34, cell);
      let perRow = Math.max(1, Math.floor((CW - stripX - 54) / scell));
      while (scell > 10 && oy + Math.ceil(kept.length / perRow) * scell > CH - 60) {
        scell -= 2;
        perRow = Math.max(1, Math.floor((CW - stripX - 54) / scell));
      }
      label(stripX, oy - 10, `${exprText}  → 1-D, length ${kept.length}`, C_.green);

      for (let k = 0; k < kept.length; k++) {
        const on = k < reveal;
        const rx = stripX + (k % perRow) * scell;
        const ry = oy + Math.floor(k / perRow) * scell;
        const col = on ? heat(kept[k].v, lo, hi, isDark) : (isDark ? [30, 41, 59] : [236, 234, 228]);
        cellBox(rx, ry, scell, col, on ? C_.green : null, on ? 2 : 1, on ? fmt(kept[k].v) : '', C_.dark, Math.min(12, scell * 0.34));
      }

      if (reveal > 0 && reveal <= kept.length) {
        const k = reveal - 1;
        const src = kept[k];
        const sx = ox + src.j * cell + cell / 2;
        const sy = oy + src.i * cell + cell / 2;
        const dx = stripX + (k % perRow) * scell + scell / 2;
        const dy = oy + Math.floor(k / perRow) * scell + scell / 2;
        const prog = Math.max(0, Math.min(1, progress * kept.length - k));
        arrow(sx, sy, dx, dy, prog, C_.green, 2.2, true);
      }
    };

    const drawOp = (t, progress) => {
      const scalar = t.mode === 'scalar';
      const A = viz.arrays[t.a];
      const B = scalar ? null : viz.arrays[t.b];
      const R = A.length;
      const Cn = A[0].length;
      const ra = range2d(A);
      const rb = B ? range2d(B) : ra;
      const C = t.result; // computed by real NumPy on the server
      const rc = range2d(C);

      const units = scalar ? 2 * Cn + 4 : 3 * Cn + 6;
      const cell = Math.min(40, (CW - 120) / units, (CH - 150) / R);
      const ox = 54;
      const oy = 80;
      const gw = Cn * cell;
      const gh = R * cell;
      const tt = Math.max(0, Math.min(1, (progress - 0.12) / 0.55));

      const axO = ox;
      const bxO = scalar ? 0 : ox + gw + 18;
      const cxO = CW - 54 - gw;
      const opX = scalar ? (ox + gw + cxO) / 2 : (bxO + gw + cxO) / 2;

      label(axO, oy - 10, t.a, C_.mid);
      if (!scalar) label(bxO, oy - 10, t.b, C_.mid);
      label(cxO, oy - 10, `${t.out} = ${exprText}`, C_.blue);

      // input A
      for (let i = 0; i < R; i++) {
        for (let j = 0; j < Cn; j++) {
          const val = A[i][j];
          cellBox(axO + j * cell, oy + i * cell, cell, heat(val, ra.lo, ra.hi, isDark), null, 1, fmt(val), C_.dark, Math.min(13, cell * 0.32));
        }
      }

      // input B
      if (!scalar) {
        for (let i = 0; i < R; i++) {
          for (let j = 0; j < Cn; j++) {
            const val = B[i][j];
            cellBox(bxO + j * cell, oy + i * cell, cell, heat(val, rb.lo, rb.hi, isDark), null, 1, fmt(val), C_.dark, Math.min(13, cell * 0.32));
          }
        }
      }

      // beam
      const leftEdge = (scalar ? axO : bxO) + gw;
      ctx.save();
      ctx.fillStyle = `rgba(56, 189, 248, ${(40 + 120 * tt) / 255})`;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(leftEdge, oy, cxO - leftEdge, gh, 6);
      else ctx.rect(leftEdge, oy, cxO - leftEdge, gh);
      ctx.fill();
      ctx.restore();

      for (const ri of [0, Math.floor(R / 2), R - 1]) {
        const sy = oy + ri * cell + cell / 2;
        arrow(leftEdge, sy, cxO, sy, tt, C_.blue, 2, true);
      }

      // op node badge
      const ny = oy + gh / 2;
      ctx.save();
      ctx.strokeStyle = tt > 0 ? C_.blue : C_.lgray;
      ctx.lineWidth = 2;
      ctx.fillStyle = scalar && tt > 0 ? (isDark ? '#1e293b' : '#e2eef8') : (isDark ? '#1e293b' : '#fbf9f3');
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(opX - 29, ny - 22, 58, 44, 10);
      else ctx.rect(opX - 29, ny - 22, 58, 44);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = C_.dark;
      ctx.font = '22px var(--font-mono), monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(OP_SYM[t.op], opX, ny - 4);

      if (scalar) {
        ctx.fillStyle = C_.orange;
        ctx.font = '14px var(--font-mono), monospace';
        ctx.fillText(String(t.operand), opX, ny + 14);

        // broadcast burst rings
        ctx.strokeStyle = `rgba(249, 115, 22, ${(150 * (1 - tt)) / 255})`;
        ctx.lineWidth = 2;
        ctx.beginPath();
        const rr = tt * Math.min(gw, gh) * 0.6;
        ctx.ellipse(opX, ny, Math.max(0.1, rr / 2), Math.max(0.1, rr / 2), 0, 0, Math.PI * 2);
        ctx.stroke();
      } else {
        ctx.fillStyle = C_.mid;
        ctx.font = '10px var(--font-sans), sans-serif';
        ctx.fillText('elem', opX, ny + 14);
      }
      ctx.restore();

      // output C
      for (let i = 0; i < R; i++) {
        for (let j = 0; j < Cn; j++) {
          const res = C[i][j];
          const on = tt >= 1;
          const col = on ? heat(res, rc.lo, rc.hi, isDark) : (isDark ? [30, 41, 59] : [236, 234, 228]);
          cellBox(
            cxO + j * cell,
            oy + i * cell,
            cell,
            col,
            on ? C_.blue : null,
            on ? 1.6 : 1,
            on ? fmt(res) : '',
            C_.dark,
            Math.min(12, cell * 0.3),
          );
        }
      }
    };

    const renderFrame = () => {
      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.fillStyle = C_.light;
      ctx.fillRect(0, 0, CW, CH);

      if (!viz) {
        // Empty/loading state is drawn as an HTML overlay (see JSX below) so it can
        // share the app's typography and icon language instead of bare canvas text.
        ctx.restore();
        return;
      }

      anim = Math.min(1, anim + 0.012 * speed);
      const t = viz.target;

      if (t.mode === 'slice') drawSlice(t, anim);
      else if (t.mode === 'filter') drawFilter(t, anim);
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

      if (anim < 1) {
        animationFrameId = requestAnimationFrame(renderFrame);
      }
    };

    renderFrame();

    return () => {
      if (animationFrameId) cancelAnimationFrame(animationFrameId);
    };
  }, [viz, speed, replayKey, loading, theme]);

  // ── Actions ────────────────────────────────────────────────────────────
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
    link.download = `numpy-${viz ? viz.target.mode : 'lattice'}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
  };

  const dirty = applied !== null && code !== applied;
  const shapes = viz
    ? Object.entries(viz.arrays).map(([name, grid]) =>
        viz.oneD[name] ? `${name}: ${grid[0].length}` : `${name}: ${grid.length}×${grid[0].length}`,
      )
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
              filename="numpy_script.py"
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
            Your code runs as real NumPy on the server. Build arrays however you like, then end with one
            expression to animate — a slice (<code>A[1:5, 1:5]</code>), a mask (<code>A[A &gt; 50]</code>) or
            arithmetic (<code>A + 10</code>, <code>A * B</code>). The last such expression is the one drawn, and
            arrays stay within {MAX_DIM}×{MAX_DIM} so the cells keep readable numbers.
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
              <div className="expr">{colorExpr(`${viz.target.out} = ${labelOf(viz)}`)}</div>
              <div className="note">{noteFor(viz.target.mode, viz.target.op)}</div>
            </div>
          ) : (
            <div className="note">Run some code to see which NumPy concept it exercises.</div>
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
                  <h4>Running your NumPy…</h4>
                  <p>The array is being computed on the server.</p>
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
      </div>
    </div>
  );
}
