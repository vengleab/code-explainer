import React, { useState, useEffect, useRef, useCallback } from 'react';
import CodeEditor from '../components/CodeEditor.jsx';
import { fetchNumpyModel } from '../services/api.js';
import { getC_, heat, fmt, colorExpr } from '../utils/visualizer.jsx';

const CW = 960;
const CH = 560;

// Mentioned in the sidebar copy only — backend/numpy_model.py does the enforcing.
const MAX_DIM = 12;

/** Display glyph for a raw operator — used on the canvas, where math reads better. */
const OP_SYM = { '+': '+', '-': '−', '*': '·', '/': '÷' };
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
  const [isPlaying, setIsPlaying] = useState(true);
  const [isAutoReplay, setIsAutoReplay] = useState(true);
  const [stepIndex, setStepIndex] = useState(0);
  const animProgressRef = useRef(0);
  const isScrubbingRef = useRef(false);
  const [isLaserActive, setIsLaserActive] = useState(false);
  const [laserPos, setLaserPos] = useState({ x: 50, y: 50, visible: false });
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    const handleFsChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFsChange);
    return () => document.removeEventListener('fullscreenchange', handleFsChange);
  }, []);

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
      isScrubbingRef.current = false;
      animProgressRef.current = 0;
      setStepIndex(0);
      setIsPlaying(true);
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

    let anim = animProgressRef.current;
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

    const drawMatrixBracket = (x, y, w, h, col) => {
      ctx.save();
      ctx.strokeStyle = col || (isDark ? 'rgba(255, 255, 255, 0.4)' : '#2d3748');
      ctx.lineWidth = 2.5;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';

      const arm = Math.min(12, Math.max(6, w * 0.15));
      const pad = 6;
      const bx0 = x - pad;
      const bx1 = x + w + pad;
      const by0 = y - 4;
      const by1 = y + h + 4;

      // Left bracket [
      ctx.beginPath();
      ctx.moveTo(bx0 + arm, by0);
      ctx.lineTo(bx0, by0);
      ctx.lineTo(bx0, by1);
      ctx.lineTo(bx0 + arm, by1);
      ctx.stroke();

      // Right bracket ]
      ctx.beginPath();
      ctx.moveTo(bx1 - arm, by0);
      ctx.lineTo(bx1, by0);
      ctx.lineTo(bx1, by1);
      ctx.lineTo(bx1 - arm, by1);
      ctx.stroke();

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

      // Layout units for 4 stages: A, B (or scalar badge), Intermediate Calc (mxO), Result C (cxO)
      const numGrids = scalar ? 3 : 4;
      const units = numGrids * Cn + (scalar ? 6 : 8);
      const cell = Math.min(36, (CW - 100) / units, (CH - 150) / R);
      const ox = 50;
      const oy = 80;
      const gw = Cn * cell;
      const gh = R * cell;
      const tt = Math.max(0, Math.min(1, (progress - 0.12) / 0.55));

      const axO = ox;
      const cxO = CW - 50 - gw;
      const totalAvailable = cxO - (axO + gw) - (numGrids - 2) * gw;
      const gap = Math.max(28, totalAvailable / (numGrids - 1));

      let bxO, opX, eq1X, mxO, eq2X;
      if (scalar) {
        bxO = 0;
        opX = axO + gw + gap / 2;
        eq1X = axO + gw + gap;
        mxO = axO + gw + gap;
        eq2X = (mxO + gw + cxO) / 2;
      } else {
        bxO = axO + gw + gap;
        opX = (axO + gw + bxO) / 2;
        mxO = bxO + gw + gap;
        eq1X = (bxO + gw + mxO) / 2;
        eq2X = (mxO + gw + cxO) / 2;
      }

      // Labels above grids
      label(axO, oy - 10, t.a, C_.mid);
      if (!scalar) label(bxO, oy - 10, t.b, C_.mid);
      label(mxO, oy - 10, scalar ? `${t.a} ${OP_SYM[t.op]} ${t.operand}` : `${t.a} ${OP_SYM[t.op]} ${t.b}`, C_.orange);
      label(cxO, oy - 10, `${t.out}`, C_.blue);

      // 1. Grid A
      for (let i = 0; i < R; i++) {
        for (let j = 0; j < Cn; j++) {
          const val = A[i][j];
          cellBox(axO + j * cell, oy + i * cell, cell, heat(val, ra.lo, ra.hi, isDark), null, 1, fmt(val), C_.dark, Math.min(12, cell * 0.32));
        }
      }

      // 2. Operator badge (opX)
      const ny = oy + gh / 2;
      ctx.save();
      ctx.strokeStyle = tt > 0 ? C_.blue : C_.lgray;
      ctx.lineWidth = 2.5;
      ctx.fillStyle = tt > 0 ? (isDark ? '#1e293b' : '#e2eef8') : (isDark ? '#1e293b' : '#fbf9f3');
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(opX - 21, ny - 21, 42, 42, 10);
      else ctx.rect(opX - 21, ny - 21, 42, 42);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = C_.dark;
      ctx.font = 'bold 28px var(--font-mono), monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(OP_SYM[t.op], opX, ny - (scalar ? 5 : 0));
      if (scalar) {
        ctx.fillStyle = C_.orange;
        ctx.font = 'bold 13px var(--font-mono), monospace';
        ctx.fillText(String(t.operand), opX, ny + 13);
      }
      ctx.restore();

      // 3. Grid B (if not scalar)
      if (!scalar) {
        for (let i = 0; i < R; i++) {
          for (let j = 0; j < Cn; j++) {
            const val = B[i][j];
            cellBox(bxO + j * cell, oy + i * cell, cell, heat(val, rb.lo, rb.hi, isDark), null, 1, fmt(val), C_.dark, Math.min(12, cell * 0.32));
          }
        }

        // Equals badge 1 (eq1X)
        ctx.save();
        ctx.strokeStyle = tt > 0 ? C_.blue : C_.lgray;
        ctx.lineWidth = 2.5;
        ctx.fillStyle = tt > 0 ? (isDark ? '#1e293b' : '#e2eef8') : (isDark ? '#1e293b' : '#fbf9f3');
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(eq1X - 18, ny - 18, 36, 36, 8);
        else ctx.rect(eq1X - 18, ny - 18, 36, 36);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = C_.blue;
        ctx.font = 'bold 24px var(--font-sans), sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('=', eq1X, ny);
        ctx.restore();
      }

      // 4. Intermediate Calculation Grid (mxO) [e.g. 1*3, 2*4]
      const showCalc = tt > 0.15;
      for (let i = 0; i < R; i++) {
        for (let j = 0; j < Cn; j++) {
          const aVal = fmt(A[i][j]);
          const bVal = scalar ? String(t.operand) : fmt(B[i][j]);
          const exprStr = `${aVal}${OP_SYM[t.op]}${bVal}`;
          cellBox(
            mxO + j * cell,
            oy + i * cell,
            cell,
            showCalc ? (isDark ? [30, 41, 59] : [254, 243, 199]) : (isDark ? [15, 23, 42] : [250, 250, 249]),
            showCalc ? C_.orange : null,
            showCalc ? 1.5 : 1,
            showCalc ? exprStr : '',
            C_.dark,
            Math.min(10, cell * 0.25),
          );
        }
      }

      // Equals badge 2 (eq2X)
      ctx.save();
      ctx.strokeStyle = tt > 0.5 ? C_.blue : C_.lgray;
      ctx.lineWidth = 2.5;
      ctx.fillStyle = tt > 0.5 ? (isDark ? '#1e293b' : '#e2eef8') : (isDark ? '#1e293b' : '#fbf9f3');
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(eq2X - 18, ny - 18, 36, 36, 8);
      else ctx.rect(eq2X - 18, ny - 18, 36, 36);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = C_.blue;
      ctx.font = 'bold 24px var(--font-sans), sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('=', eq2X, ny);
      ctx.restore();

      // 5. Final Result Grid C (cxO) [e.g. 3, 8]
      for (let i = 0; i < R; i++) {
        for (let j = 0; j < Cn; j++) {
          const res = C[i][j];
          const on = tt >= 0.7;
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
            Math.min(12, cell * 0.32),
          );
        }
      }

      // Matrix brackets [ ] around all grids
      drawMatrixBracket(axO, oy, gw, gh, isDark ? 'rgba(255, 255, 255, 0.4)' : '#2d3748');
      if (!scalar) {
        drawMatrixBracket(bxO, oy, gw, gh, isDark ? 'rgba(255, 255, 255, 0.4)' : '#2d3748');
      }
      drawMatrixBracket(mxO, oy, gw, gh, C_.orange);
      drawMatrixBracket(cxO, oy, gw, gh, C_.blue);
    };

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
        anim = anim + 0.012 * speed;
        if (anim >= 1) {
          if (isAutoReplay) {
            anim = 0;
          } else {
            anim = 1;
          }
        }
        animProgressRef.current = anim;
        setStepIndex(Math.round(anim * 100));
      }

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

      if (isPlaying && (anim < 1 || isAutoReplay) && !isScrubbingRef.current) {
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

  const handleMouseMove = (e) => {
    if (!isLaserActive || !canvasRef.current) return;
    const rect = canvasRef.current.parentElement.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 100;
    const y = ((e.clientY - rect.top) / rect.height) * 100;
    setLaserPos({ x, y, visible: true });
  };

  const handleMouseLeave = () => {
    setLaserPos((prev) => ({ ...prev, visible: false }));
  };

  const toggleFullscreen = () => {
    const container = canvasRef.current?.parentElement;
    if (!container) return;
    if (!document.fullscreenElement) {
      if (container.requestFullscreen) container.requestFullscreen();
      setIsFullscreen(true);
    } else {
      if (document.exitFullscreen) document.exitFullscreen();
      setIsFullscreen(false);
    }
  };

  const handleCopyImage = async () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    try {
      canvas.toBlob(async (blob) => {
        if (!blob) return;
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        alert('Image copied to clipboard!');
      });
    } catch (e) {
      console.error(e);
    }
  };

  const handleCopySlidesUrl = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      alert('URL copied to clipboard! In Google Slides: Insert → Image → By URL, then paste.');
    } catch (e) {
      console.error(e);
    }
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
        <div
          className={`numpy-vis-canvas-container ${isFullscreen ? 'is-fullscreen' : ''} ${isLaserActive ? 'laser-mode' : ''}`}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
        >
          <canvas ref={canvasRef} />
          {isLaserActive && laserPos.visible && (
            <div
              className="virtual-laser-pointer"
              style={{ left: `${laserPos.x}%`, top: `${laserPos.y}%` }}
            />
          )}

          {isFullscreen && (
            <>
              <button
                type="button"
                className="fullscreen-exit-btn"
                onClick={toggleFullscreen}
                title="Exit Fullscreen (Esc)"
              >
                ✕
              </button>

              <div className="fullscreen-controls-bar">
                <div className="fs-controls-left">
                  <button
                    type="button"
                    className="player-btn fs-btn"
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
                    className="player-btn fs-btn play-pause-btn"
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
                    className="player-btn fs-btn"
                    onClick={handleStepNext}
                    disabled={stepIndex === 100}
                    title="Next Step"
                  >
                    <svg viewBox="0 0 24 24" fill="currentColor">
                      <path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z" />
                    </svg>
                  </button>

                  <span className="fs-step-indicator">
                    Step <strong>{Math.min(4, Math.floor(stepIndex / 25) + 1)}</strong> of 4
                  </span>
                </div>

                <div className="fs-controls-scrubber">
                  <input
                    type="range"
                    className="player-slider fs-slider"
                    min={0}
                    max={100}
                    value={stepIndex}
                    onChange={handleScrubberChange}
                  />
                </div>
              </div>
            </>
          )}
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

            <button
              type="button"
              className={`player-btn ${isAutoReplay ? 'active' : ''}`}
              onClick={() => setIsAutoReplay((r) => !r)}
              title={isAutoReplay ? "Auto Replay ON (Looping)" : "Auto Replay OFF"}
              style={isAutoReplay ? { color: 'var(--accent)', borderColor: 'var(--accent)' } : {}}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
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

        {/* Action Buttons Row — Exact same 5 buttons as Code Explainer */}
        <div className="actions-row">
          <button type="button" className="secondary" onClick={handleCopyImage} title="Copy Canvas Image to Clipboard">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            Copy GIF
          </button>

          <button type="button" className="secondary" onClick={handleDownload} title="Download PNG Screenshot">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Download GIF
          </button>

          <button type="button" className="secondary" onClick={handleCopySlidesUrl} title="Copy URL for Google Slides">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
            Copy URL for Google Slides
          </button>

          <button
            type="button"
            className={`secondary ${isLaserActive ? 'active-laser' : ''}`}
            onClick={() => setIsLaserActive((prev) => !prev)}
            title="Toggle Virtual Laser Pointer for Presentations"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" fill="currentColor" />
              <circle cx="12" cy="12" r="8" strokeDasharray="3 3" />
            </svg>
            {isLaserActive ? 'Laser Pointer ON' : 'Laser Pointer'}
          </button>

          <button type="button" className="secondary" onClick={toggleFullscreen} title="Toggle Fullscreen">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              {isFullscreen ? (
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
              )}
            </svg>
            {isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
          </button>
        </div>
      </div>
    </div>
  );
}
