import React, { useState, useEffect, useRef, useCallback } from 'react';

const CW = 960;
const CH = 560;

// Mentioned in the sidebar copy only — backend/numpy_model.py does the enforcing.
const MAX_DIM = 12;

const C_ = {
  dark: '#141413',
  light: '#faf9f5',
  mid: '#b0aea5',
  lgray: '#e8e6dc',
  orange: '#d97757',
  blue: '#6a9bcc',
  green: '#788c5d',
};

function heat(v, lo, hi) {
  let x = hi <= lo ? 0.5 : Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  const a = [130, 160, 190];
  const b = [240, 236, 225];
  const c = [217, 119, 87];
  const mix = (p, q, t) => [
    Math.round(p[0] + (q[0] - p[0]) * t),
    Math.round(p[1] + (q[1] - p[1]) * t),
    Math.round(p[2] + (q[2] - p[2]) * t),
  ];
  return x < 0.5 ? mix(a, b, x * 2) : mix(b, c, (x - 0.5) * 2);
}

function fmt(v) {
  return Number.isInteger(v) ? v : Math.round(v * 10) / 10;
}

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
const ENDPOINT = '/api/visualize-numpy';

async function fetchModel(code) {
  let response;
  try {
    response = await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
  } catch {
    return { viz: null, error: 'could not reach the server — is the backend running?' };
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    return { viz: null, error: `server returned ${response.status} with an unreadable body` };
  }
  if (!response.ok) return { viz: null, error: payload.error || `server returned ${response.status}` };
  if (!payload.target || !payload.arrays) return { viz: null, error: 'server returned an unexpected payload' };
  return { viz: payload, error: null };
}


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

/** Colourise an expression for the dark `.expr` block (numbers, operators). */
function colorExpr(s) {
  const parts = s.split(/(-?\d+\.?\d*)|(>=|<=|==|!=|[+\-*/><])/g);
  return parts.map((p, i) => {
    if (!p) return null;
    if (/^-?\d+\.?\d*$/.test(p)) return <span key={i} style={{ color: '#f0b47a' }}>{p}</span>;
    if (/^(>=|<=|==|!=|[+\-*/><])$/.test(p)) return <span key={i} style={{ color: '#8fb9e0' }}>{p}</span>;
    return <span key={i}>{p}</span>;
  });
}

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

export default function NumpyVisualizer() {
  const [code, setCode] = useState(() => localStorage.getItem(LS_KEY) || DEFAULT_CODE);
  const [applied, setApplied] = useState(null); // the code the current frame shows
  const [result, setResult] = useState({ viz: null, error: null });
  const [loading, setLoading] = useState(true);
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
    const answer = await fetchModel(next);
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

  // Animate whatever is in the editor on first paint.
  useEffect(() => {
    run(code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        ctx.strokeStyle = '#e8e6dc';
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
        ctx.font = `${ts}px "Courier New", monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(txt), x + s / 2, y + s / 2);
      }
      ctx.restore();
    };

    const arrow = (x1, y1, x2, y2, prog, col, w, head) => {
      ctx.save();
      ctx.strokeStyle = '#e8e6dc';
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
      ctx.font = '13px "Poppins", sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(s, x, y);
      ctx.restore();
    };

    const centered = (s, col) => {
      ctx.save();
      ctx.fillStyle = col;
      ctx.font = '15px "Poppins", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(s, CW / 2, CH / 2);
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
          let col = heat(A[i][j], lo, hi);
          let ring = null;
          let rw = 1;
          let tcol = C_.dark;
          if (inSel) {
            ring = C_.orange;
            rw = 2;
          } else {
            col = [236, 234, 228];
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
          const col = on ? heat(val, lo, hi) : [236, 234, 228];
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
          const col = pass ? heat(A[i][j], lo, hi) : [236, 234, 228];
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
        const col = on ? heat(kept[k].v, lo, hi) : [236, 234, 228];
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
          cellBox(axO + j * cell, oy + i * cell, cell, heat(val, ra.lo, ra.hi), null, 1, fmt(val), C_.dark, Math.min(13, cell * 0.32));
        }
      }

      // input B
      if (!scalar) {
        for (let i = 0; i < R; i++) {
          for (let j = 0; j < Cn; j++) {
            const val = B[i][j];
            cellBox(bxO + j * cell, oy + i * cell, cell, heat(val, rb.lo, rb.hi), null, 1, fmt(val), C_.dark, Math.min(13, cell * 0.32));
          }
        }
      }

      // beam
      const leftEdge = (scalar ? axO : bxO) + gw;
      ctx.save();
      ctx.fillStyle = `rgba(106, 155, 204, ${(40 + 120 * tt) / 255})`;
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
      ctx.fillStyle = scalar && tt > 0 ? '#e2eef8' : '#fbf9f3';
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(opX - 29, ny - 22, 58, 44, 10);
      else ctx.rect(opX - 29, ny - 22, 58, 44);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = C_.dark;
      ctx.font = '22px "Courier New", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(OP_SYM[t.op], opX, ny - 4);

      if (scalar) {
        ctx.fillStyle = C_.orange;
        ctx.font = '14px "Courier New", monospace';
        ctx.fillText(String(t.operand), opX, ny + 14);

        // broadcast burst rings
        ctx.strokeStyle = `rgba(217, 119, 87, ${(150 * (1 - tt)) / 255})`;
        ctx.lineWidth = 2;
        ctx.beginPath();
        const rr = tt * Math.min(gw, gh) * 0.6;
        ctx.ellipse(opX, ny, Math.max(0.1, rr / 2), Math.max(0.1, rr / 2), 0, 0, Math.PI * 2);
        ctx.stroke();
      } else {
        ctx.fillStyle = C_.mid;
        ctx.font = '10px "Poppins", sans-serif';
        ctx.fillText('elem', opX, ny + 14);
      }
      ctx.restore();

      // output C
      for (let i = 0; i < R; i++) {
        for (let j = 0; j < Cn; j++) {
          const res = C[i][j];
          const on = tt >= 1;
          const col = on ? heat(res, rc.lo, rc.hi) : [236, 234, 228];
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
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, CW, CH);

      if (!viz) {
        centered(loading ? 'Running your NumPy…' : 'Write some NumPy on the left, then press Run.', C_.mid);
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
      ctx.fillStyle = 'rgba(250, 249, 245, 0.92)';
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(40, CH - 40, CW - 80, 26, 6);
      else ctx.rect(40, CH - 40, CW - 80, 26);
      ctx.fill();

      ctx.fillStyle = C_.dark;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.font = '13px "Poppins", sans-serif';
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
  }, [viz, speed, replayKey, loading]);

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

  return (
    <div className="numpy-vis-container">
      <style>{`
        .numpy-vis-container {
          display: flex;
          min-height: 100vh;
          padding: 20px;
          gap: 20px;
          background: linear-gradient(135deg, #faf9f5 0%, #f5f3ee 100%);
          color: #141413;
          font-family: 'Poppins', system-ui, -apple-system, sans-serif;
          width: 100%;
          box-sizing: border-box;
        }

        .numpy-vis-sidebar {
          width: 340px;
          flex-shrink: 0;
          background: rgba(255, 255, 255, 0.95);
          backdrop-filter: blur(10px);
          padding: 24px;
          border-radius: 12px;
          box-shadow: 0 10px 30px rgba(20, 20, 19, 0.1);
          overflow-y: auto;
          max-height: calc(100vh - 40px);
        }

        .numpy-vis-sidebar h1 {
          font-family: 'Lora', Georgia, serif;
          font-size: 24px;
          font-weight: 500;
          margin: 0 0 8px 0;
          color: #141413;
        }

        .numpy-vis-sidebar .subtitle {
          color: #b0aea5;
          font-size: 14px;
          margin-bottom: 28px;
          line-height: 1.5;
        }

        .control-section {
          margin-bottom: 26px;
        }

        .control-section h3 {
          font-size: 15px;
          font-weight: 600;
          margin: 0 0 14px 0;
          display: flex;
          align-items: center;
          gap: 8px;
          color: #141413;
        }

        .control-section h3::before {
          content: '•';
          color: #d97757;
          font-weight: bold;
        }

        .code-input {
          width: 100%;
          background: #faf9f5;
          padding: 12px;
          border-radius: 8px;
          font-family: 'Courier New', monospace;
          font-size: 12.5px;
          line-height: 1.55;
          margin-bottom: 10px;
          border: 1px solid #e8e6dc;
          color: #141413;
          box-sizing: border-box;
          min-height: 200px;
          resize: vertical;
          tab-size: 4;
          white-space: pre;
          overflow-wrap: normal;
          overflow-x: auto;
        }

        .code-input:focus {
          outline: none;
          border-color: #d97757;
          box-shadow: 0 0 0 2px rgba(217, 119, 87, 0.1);
          background: #fff;
        }

        .error-box {
          background: #fdf1ec;
          border: 1px solid #d97757;
          color: #8c4529;
          border-radius: 8px;
          padding: 10px 12px;
          font-family: 'Courier New', monospace;
          font-size: 12px;
          line-height: 1.5;
          margin-bottom: 10px;
          word-break: break-word;
        }

        .shape-tags {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-top: 10px;
        }

        .shape-tag {
          background: #faf9f5;
          border: 1px solid #e8e6dc;
          border-radius: 5px;
          padding: 3px 8px;
          font-family: 'Courier New', monospace;
          font-size: 11.5px;
          color: #6b6a63;
        }

        .control-group {
          margin-bottom: 18px;
        }

        .control-group label {
          display: block;
          font-size: 14px;
          font-weight: 500;
          margin-bottom: 8px;
        }

        .slider-container {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .slider-container input[type=range] {
          flex: 1;
          height: 4px;
          background: #e8e6dc;
          border-radius: 2px;
          outline: none;
          -webkit-appearance: none;
        }

        .slider-container input[type=range]::-webkit-slider-thumb {
          -webkit-appearance: none;
          width: 16px;
          height: 16px;
          background: #d97757;
          border-radius: 50%;
          cursor: pointer;
          transition: all .2s;
        }

        .slider-container input[type=range]::-webkit-slider-thumb:hover {
          transform: scale(1.1);
          background: #c86641;
        }

        .slider-container input[type=range]::-moz-range-thumb {
          width: 16px;
          height: 16px;
          background: #d97757;
          border-radius: 50%;
          border: none;
          cursor: pointer;
        }

        .value-display {
          font-family: 'Courier New', monospace;
          font-size: 12px;
          color: #b0aea5;
          min-width: 64px;
          text-align: right;
        }

        .mode-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 8px;
        }

        .mode-btn {
          background: #faf9f5;
          border: 1px solid #e8e6dc;
          padding: 10px 8px;
          border-radius: 8px;
          font-size: 13px;
          font-weight: 500;
          cursor: pointer;
          transition: all .15s;
          color: #141413;
          text-align: center;
        }

        .mode-btn:hover {
          border-color: #d97757;
        }

        .mode-btn.active {
          background: #d97757;
          color: #fff;
          border-color: #d97757;
        }

        .mode-btn.readonly {
          cursor: default;
          opacity: .5;
        }

        .mode-btn.readonly:hover {
          border-color: #e8e6dc;
        }

        .mode-btn.readonly.active {
          opacity: 1;
        }

        .expr {
          background: #141413;
          color: #f5f3ee;
          font-family: 'Courier New', monospace;
          font-size: 14px;
          padding: 12px 14px;
          border-radius: 8px;
          margin-top: 6px;
          line-height: 1.5;
          word-break: break-word;
        }

        .note {
          font-size: 12.5px;
          color: #b0aea5;
          line-height: 1.5;
          margin-top: 10px;
        }

        .button {
          background: #d97757;
          color: #fff;
          border: none;
          padding: 10px 16px;
          border-radius: 6px;
          font-size: 14px;
          font-weight: 500;
          cursor: pointer;
          transition: all .2s;
          width: 100%;
        }

        .button:hover {
          background: #c86641;
          transform: translateY(-1px);
        }

        .button.secondary {
          background: #6a9bcc;
        }

        .button.secondary:hover {
          background: #5a8bb8;
        }

        .button.tertiary {
          background: #788c5d;
        }

        .button.tertiary:hover {
          background: #6b7b52;
        }

        .button.is-dirty {
          box-shadow: 0 0 0 3px rgba(217, 119, 87, 0.18);
        }

        .button:disabled {
          background: #cfccc2;
          cursor: default;
          transform: none;
        }

        .button-row {
          display: flex;
          gap: 8px;
        }

        .button-row .button {
          flex: 1;
        }

        .kbd {
          font-family: 'Courier New', monospace;
          font-size: 12px;
          opacity: .8;
        }

        .numpy-vis-canvas-area {
          flex: 1;
          display: flex;
          align-items: center;
          justify-content: center;
          min-width: 0;
        }

        .numpy-vis-canvas-container {
          width: 100%;
          max-width: 960px;
          border-radius: 12px;
          overflow: hidden;
          box-shadow: 0 20px 40px rgba(20, 20, 19, .1);
          background: #fff;
        }

        .numpy-vis-canvas-container canvas {
          display: block;
          width: 100% !important;
          height: auto !important;
        }

        @media (max-width: 900px) {
          .numpy-vis-container {
            flex-direction: column;
          }

          .numpy-vis-sidebar {
            width: 100%;
            max-height: none;
          }
        }
      `}</style>

      <div className="numpy-vis-sidebar">
        <h1>Lattice Arithmetic</h1>
        <div className="subtitle">
          Write a little NumPy — <b>indexing &amp; slicing</b>, <b>boolean filtering</b>, or <b>+ − × ÷</b> against a
          scalar or another array — and the diagram animates how input maps to output.
        </div>

        <div className="control-section">
          <h3>Code</h3>
          <textarea
            className="code-input"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={handleEditorKeyDown}
            spellCheck={false}
            autoCapitalize="off"
            autoComplete="off"
            autoCorrect="off"
            rows={12}
          />
          {error && <div className="error-box">{error}</div>}
          <button
            className={`button ${dirty ? 'is-dirty' : ''}`}
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
          <div className="mode-grid">
            {Object.entries(MODE_LABEL).map(([key, text]) => (
              <div
                key={key}
                className={`mode-btn readonly ${viz && viz.target.mode === key ? 'active' : ''}`}
                aria-current={viz && viz.target.mode === key}
              >
                {text}
              </div>
            ))}
          </div>
          <div className="expr">{viz ? colorExpr(`${viz.target.out} = ${labelOf(viz)}`) : '—'}</div>
          <div className="note">
            {viz ? noteFor(viz.target.mode, viz.target.op) : 'Run some code to see which NumPy concept it exercises.'}
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
            <button className="button" onClick={handleReplay}>
              ↻ Replay
            </button>
            <button className="button secondary" onClick={handleReset}>
              Reset
            </button>
          </div>
          <button className="button tertiary" style={{ marginTop: '8px' }} onClick={handleDownload}>
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
