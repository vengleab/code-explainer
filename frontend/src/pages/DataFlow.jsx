import React, { useState, useEffect, useRef, useCallback } from "react";

const N = 8;
const OPS = { "+": (a, b) => a + b, "\u00d7": (a, b) => a * b };

// geometry (canvas coords)
const CW = 42, CH = 30, GAP = 8, TOP = 96;
const XA = 70, XB = 120, XC = 690;
const rowY = (i) => TOP + i * (CH + GAP);
const midY = TOP + (N * (CH + GAP)) / 2 - GAP / 2;
const BAR_L = 372, BAR_R = 452, OP_CX = 412, OP_CY = midY;
const W = 780, H = TOP + N * (CH + GAP) + 24;

function getCol(theme) {
    const isDark = theme === "dark";
    return {
        bg: isDark ? "transparent" : "transparent",
        panel: isDark ? "#211b16" : "#ffffff",
        ink: isDark ? "#f5f1ea" : "#1c1917",
        sub: isDark ? "#a39c90" : "#78716c",
        line: isDark ? "rgba(255, 255, 255, 0.12)" : "#ddd5c7",
        cellEmpty: isDark ? "#2a2119" : "#f0ece3",
        emptyTxt: isDark ? "#78716c" : "#a39c90",
        blue: isDark ? "#38bdf8" : "#0284c7",
        green: isDark ? "#10b981" : "#059669",
        amber: isDark ? "#f59e0b" : "#d97706",
        greenSoft: isDark ? "rgba(16, 185, 129, 0.15)" : "#e2f0e6",
        greenLine: isDark ? "rgba(16, 185, 129, 0.4)" : "#8cc7a3",
        opBg: isDark ? "#2a2119" : "#fbf9f3",
        opStroke: isDark ? "rgba(255, 255, 255, 0.18)" : "#ddd5c7",
    };
}

const heat = (v, lo, hi, isDark = false) => {
    const x = hi <= lo ? 0.5 : Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
    const a = isDark ? [30, 58, 138] : [176, 205, 214];
    const b = isDark ? [30, 41, 59] : [245, 240, 228];
    const c = isDark ? [180, 83, 9] : [230, 179, 118];
    const mix = (p, q, t) => p.map((pv, i) => Math.round(pv + (q[i] - pv) * t));
    const col = x < 0.5 ? mix(a, b, x * 2) : mix(b, c, (x - 0.5) * 2);
    return `rgb(${col[0]},${col[1]},${col[2]})`;
};

const clamp01 = (x) => Math.max(0, Math.min(1, x));
const UD = 900, DISPATCH = 560, CD = 820;

// quadratic bezier point
const qbez = (p0, p1, p2, t) => {
    const u = 1 - t;
    return [
        u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
        u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
    ];
};

// ---------- state model (pure) ----------
function modelAt(el, mode, lanes, total, A, B, CC, opSym) {
    let filled = new Set(), activeIdx = [], inP = 0, outP = 0, opHot = false, dispatching = false, curLabel = "";
    const numChunks = Math.ceil(N / lanes);
    if (mode === "loop") {
        const cur = Math.floor(el / UD), sub = (el % UD) / UD;
        for (let i = 0; i < cur; i++) filled.add(i);
        if (el >= total) for (let i = 0; i < N; i++) filled.add(i);
        else if (cur < N) {
            activeIdx = [cur]; inP = clamp01(sub / 0.42);
            opHot = sub >= 0.42 && sub < 0.62; outP = clamp01((sub - 0.62) / 0.38);
            if (sub > 0.62) filled.add(cur);
            if (A[cur] !== undefined) curLabel = `${A[cur]} ${opSym} ${B[cur]} = ${CC[cur]}`;
        }
    } else {
        if (el < DISPATCH && el < total) dispatching = true;
        const t = Math.max(0, el - DISPATCH);
        const chunk = Math.floor(t / CD), sub = (t % CD) / CD;
        for (let k = 0; k < chunk; k++) for (let j = 0; j < lanes; j++) filled.add(k * lanes + j);
        if (el >= total) for (let i = 0; i < N; i++) filled.add(i);
        else if (!dispatching && chunk < numChunks) {
            for (let j = 0; j < lanes; j++) { const idx = chunk * lanes + j; if (idx < N) activeIdx.push(idx); }
            inP = clamp01(sub / 0.5); opHot = sub >= 0.4 && sub < 0.62; outP = clamp01((sub - 0.55) / 0.45);
            if (sub > 0.55) activeIdx.forEach((i) => filled.add(i));
        }
    }
    return { filled, activeIdx, inP, outP, opHot, dispatching, curLabel };
}

// ---------- canvas drawing ----------
function draw(ctx, el, mode, lanes, total, A, B, CC, opSym, lo, hi, theme) {
    const COL = getCol(theme);
    const isDark = theme === "dark";
    const S = modelAt(el, mode, lanes, total, A, B, CC, opSym);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = COL.panel; ctx.fillRect(0, 0, W, H);

    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const mono = (s) => `${s}px var(--font-mono), ui-monospace, Menlo, monospace`;

    // labels - aligned at uniform Y level and centered over columns
    const labelY = TOP - 16;
    ctx.fillStyle = COL.blue; ctx.font = "bold " + mono(15);
    ctx.fillText("a", XA + CW / 2, labelY);
    ctx.fillText("b", XB + CW / 2, labelY);
    ctx.fillStyle = COL.amber; ctx.fillText("c", XC + CW / 2, labelY);

    const drawFlow = (pts, prog, color, on) => {
        ctx.lineWidth = 2; ctx.strokeStyle = COL.line;
        ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
        ctx.stroke();
        if (on && prog > 0) {
            const nseg = pts.length - 1; const upto = prog * nseg;
            ctx.lineWidth = 3; ctx.strokeStyle = color; ctx.lineCap = "round";
            ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
            for (let i = 1; i <= Math.min(nseg, Math.ceil(upto)); i++) {
                if (i <= upto) ctx.lineTo(pts[i][0], pts[i][1]);
                else { const f = upto - (i - 1); ctx.lineTo(pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f); }
            }
            ctx.stroke(); ctx.lineCap = "butt";
        }
    };
    const inPts = (i) => {
        const sy = rowY(i) + CH / 2;
        if (mode === "loop") { const p0 = [XB + CW, sy], p2 = [OP_CX - 46, OP_CY + (sy - OP_CY) * 0.15], p1 = [(p0[0] + p2[0]) / 2, sy]; return Array.from({ length: 21 }, (_, k) => qbez(p0, p1, p2, k / 20)); }
        return [[XB + CW, sy], [BAR_L, sy]];
    };
    const outPts = (i) => {
        const ey = rowY(i) + CH / 2;
        if (mode === "loop") { const p0 = [OP_CX + 46, OP_CY + (ey - OP_CY) * 0.15], p2 = [XC, ey], p1 = [(p0[0] + p2[0]) / 2, ey]; return Array.from({ length: 21 }, (_, k) => qbez(p0, p1, p2, k / 20)); }
        return [[BAR_R, ey], [XC, ey]];
    };

    for (let i = 0; i < N; i++) {
        const on = S.activeIdx.includes(i);
        drawFlow(inPts(i), S.inP, COL.blue, on);
        drawFlow(outPts(i), S.outP, COL.green, on && S.outP > 0);
    }

    const rr = (x, y, w, h, r) => { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); };

    if (mode === "loop") {
        rr(OP_CX - 46, OP_CY - 27, 92, 54, 10);
        ctx.fillStyle = S.opHot ? COL.greenSoft : COL.opBg; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = S.opHot ? COL.greenLine : COL.opStroke; ctx.stroke();
        ctx.fillStyle = COL.ink; ctx.font = mono(24); ctx.fillText(opSym, OP_CX, OP_CY + 1);
        if (S.curLabel) { ctx.fillStyle = COL.green; ctx.font = "bold " + mono(13); ctx.fillText(S.curLabel, OP_CX, OP_CY + 42); }
    } else {
        rr(BAR_L, TOP - 6, BAR_R - BAR_L, N * (CH + GAP), 10);
        ctx.fillStyle = S.dispatching ? COL.opBg : COL.greenSoft; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = S.dispatching ? COL.opStroke : COL.greenLine; ctx.stroke();
        ctx.fillStyle = COL.ink; ctx.font = mono(22); ctx.fillText(opSym, (BAR_L + BAR_R) / 2, midY - 8);
        ctx.fillStyle = COL.green; ctx.font = "bold " + mono(11); ctx.fillText("SIMD", (BAR_L + BAR_R) / 2, midY + 12);
        if (S.dispatching) { ctx.fillStyle = COL.sub; ctx.font = mono(10); ctx.fillText("calling C\u2026", (BAR_L + BAR_R) / 2, midY + 30); }
    }

    const cell = (x, y, fill, stroke, sw, txt, tcol) => {
        rr(x, y, CW, CH, 4); ctx.fillStyle = fill; ctx.fill();
        ctx.lineWidth = sw; ctx.strokeStyle = stroke; ctx.stroke();
        ctx.fillStyle = tcol; ctx.font = "bold " + mono(14); ctx.fillText(txt, x + CW / 2, y + CH / 2 + 1);
    };
    for (let i = 0; i < N; i++) {
        const on = S.activeIdx.includes(i);
        const st = on ? COL.amber : COL.line, sw = on ? 2.5 : 1, y = rowY(i);
        cell(XA, y, heat(A[i], lo, hi, isDark), st, sw, String(A[i]), COL.ink);
        cell(XB, y, heat(B[i], lo, hi, isDark), st, sw, String(B[i]), COL.ink);
        if (S.filled.has(i)) cell(XC, y, heat(CC[i], lo, hi, isDark), on ? COL.amber : COL.line, sw, String(CC[i]), COL.ink);
        else cell(XC, y, COL.cellEmpty, COL.line, 1, "\u00b7", COL.emptyTxt);
    }
    return total;
}

// ---------- GIF encoder (embedded) ----------
function lzwEncode(indices, minCodeSize) {
    const clear = 1 << minCodeSize, eoi = clear + 1;
    let codeSize = minCodeSize + 1, dict, next;
    const reset = () => { dict = new Map(); for (let i = 0; i < clear; i++) dict.set(String(i), i); return clear + 2; };
    next = reset();
    const out = []; let cur = 0, bits = 0;
    const emit = (code) => { cur |= code << bits; bits += codeSize; while (bits >= 8) { out.push(cur & 255); cur >>= 8; bits -= 8; } };
    emit(clear);
    let prefix = String(indices[0]);
    for (let i = 1; i < indices.length; i++) {
        const c = indices[i], comb = prefix + "," + c;
        if (dict.has(comb)) prefix = comb;
        else { emit(dict.get(prefix)); dict.set(comb, next++); if (next > (1 << codeSize) && codeSize < 12) codeSize++; if (next > 4095) { emit(clear); next = reset(); codeSize = minCodeSize + 1; } prefix = String(c); }
    }
    emit(dict.get(prefix)); emit(eoi);
    if (bits > 0) out.push(cur & 255);
    return out;
}

function encodeGIF(width, height, palette, frames, delayCs) {
    let exp = 1; while ((1 << exp) < palette.length) exp++;
    const tsize = 1 << exp, B = [];
    const push = (...b) => b.forEach((x) => B.push(x & 255));
    const str = (s) => { for (const ch of s) B.push(ch.charCodeAt(0)); };
    const word = (v) => push(v & 255, (v >> 8) & 255);
    str("GIF89a"); word(width); word(height); push(0x80 | (exp - 1), 0, 0);
    for (let i = 0; i < tsize; i++) { const c = palette[i] || [0, 0, 0]; push(c[0], c[1], c[2]); }
    str("!"); push(0xff, 0x0b); str("NETSCAPE2.0"); push(3, 1); word(0); push(0);
    const minCode = Math.max(2, exp);
    for (const f of frames) {
        str("!"); push(0xf9, 4, 0x04); word(delayCs); push(0, 0);
        str(","); word(0); word(0); word(width); word(height); push(0); push(minCode);
        const data = lzwEncode(f, minCode);
        for (let i = 0; i < data.length; i += 255) { const ch = data.slice(i, i + 255); push(ch.length); ch.forEach((b) => push(b)); }
        push(0);
    }
    str(";");
    return Uint8Array.from(B);
}

function buildPalette(COL, isDark) {
    const named = [COL.bg, COL.panel, COL.ink, COL.sub, COL.line, COL.cellEmpty, COL.emptyTxt,
    COL.blue, COL.green, COL.amber, COL.greenSoft, COL.greenLine, COL.opBg, COL.opStroke];
    const hex2rgb = (h) => h.startsWith('#') ? [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)] : [100, 100, 100];
    const pal = named.map(hex2rgb);
    for (let i = 0; i <= 24; i++) { // heat ramp samples
        const s = heat(1 + (i / 24) * 80, 1, 81, isDark).match(/\d+/g).map(Number); pal.push(s);
    }
    while (pal.length < 256) pal.push([0, 0, 0]);
    return pal.slice(0, 256);
}

export default function DataFlow({
    theme = "light",
    layout = "split",
    splitRatio = 50,
    isResizing = false,
    splitContainerRef,
    onMouseDown,
    onResetSplit,
}) {
    const [opSym, setOpSym] = useState("+");
    const [lanes, setLanes] = useState(4);
    const [running, setRunning] = useState(false);
    const [speed, setSpeed] = useState(1);
    const [el, setEl] = useState(0);
    const [exporting, setExporting] = useState(false);

    const COL = getCol(theme);
    const canvasLoopRef = useRef(null);
    const canvasVectorRef = useRef(null);
    const raf = useRef(null);
    const last = useRef(0);
    const acc = useRef(0);
    const [A] = useState(() => Array.from({ length: N }, () => Math.floor(Math.random() * 9) + 1));
    const [B] = useState(() => Array.from({ length: N }, () => Math.floor(Math.random() * 9) + 1));

    const fn = OPS[opSym];
    const CC = A.map((_, i) => fn(A[i], B[i]));
    const lo = 1, hi = opSym === "\u00d7" ? 81 : 18;

    const totalLoop = N * UD;
    const numChunks = Math.ceil(N / lanes);
    const totalVector = DISPATCH + numChunks * CD;
    const maxTotal = Math.max(totalLoop, totalVector);

    const render = useCallback((elapsed) => {
        const elLoop = Math.min(elapsed, totalLoop);
        const elVector = Math.min(elapsed, totalVector);

        const ctxLoop = canvasLoopRef.current?.getContext("2d");
        if (ctxLoop) {
            draw(ctxLoop, elLoop, "loop", lanes, totalLoop, A, B, CC, opSym, lo, hi, theme);
        }

        const ctxVector = canvasVectorRef.current?.getContext("2d");
        if (ctxVector) {
            draw(ctxVector, elVector, "vector", lanes, totalVector, A, B, CC, opSym, lo, hi, theme);
        }
    }, [lanes, totalLoop, totalVector, A, B, CC, opSym, lo, hi, theme]);

    useEffect(() => { render(el); }, [render, el]);

    const reset = () => {
        setRunning(false);
        acc.current = 0;
        setEl(0);
        cancelAnimationFrame(raf.current);
    };

    useEffect(() => { reset(); }, [lanes, opSym]);

    useEffect(() => {
        if (!running) return;
        last.current = performance.now();
        const tick = (now) => {
            acc.current += (now - last.current) * speed;
            last.current = now;
            if (acc.current >= maxTotal) {
                setEl(maxTotal);
                setRunning(false);
                return;
            }
            setEl(acc.current);
            raf.current = requestAnimationFrame(tick);
        };
        raf.current = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(raf.current);
    }, [running, speed, maxTotal]);

    const done = el >= maxTotal;

    const exportGif = async () => {
        setExporting(true);
        setRunning(false);
        await new Promise((r) => setTimeout(r, 30));

        const margin = 20;
        const headerH = 50;
        const totalW = W * 2 + margin * 3;
        const totalH = H + headerH + margin;

        const off = document.createElement("canvas");
        off.width = totalW;
        off.height = totalH;
        const octx = off.getContext("2d");
        const isDark = theme === "dark";
        const palette = buildPalette(COL, isDark);

        const cache = new Map();
        const nearest = (r, g, b) => {
            const key = (r << 16) | (g << 8) | b;
            if (cache.has(key)) return cache.get(key);
            let bi = 0, bd = 1e9;
            for (let i = 0; i < palette.length; i++) {
                const p = palette[i];
                const d = (p[0] - r) ** 2 + (p[1] - g) ** 2 + (p[2] - b) ** 2;
                if (d < bd) { bd = d; bi = i; }
            }
            cache.set(key, bi);
            return bi;
        };

        const canvasL = document.createElement("canvas"); canvasL.width = W; canvasL.height = H;
        const ctxL = canvasL.getContext("2d");
        const canvasV = document.createElement("canvas"); canvasV.width = W; canvasV.height = H;
        const ctxV = canvasV.getContext("2d");

        const FPS = 20, dt = 1000 / FPS;
        const nFrames = Math.ceil(maxTotal / dt) + 8;
        const frames = [];

        for (let k = 0; k < nFrames; k++) {
            const t = Math.min(maxTotal, k * dt);
            const eLoop = Math.min(t, totalLoop);
            const eVector = Math.min(t, totalVector);

            draw(ctxL, eLoop, "loop", lanes, totalLoop, A, B, CC, opSym, lo, hi, theme);
            draw(ctxV, eVector, "vector", lanes, totalVector, A, B, CC, opSym, lo, hi, theme);

            octx.fillStyle = COL.panel;
            octx.fillRect(0, 0, totalW, totalH);

            octx.fillStyle = COL.ink;
            octx.font = "bold 20px var(--font-sans), system-ui, sans-serif";
            octx.fillText("Python Loop", margin, margin + 24);
            octx.fillText("Python NumPy", W + margin * 2, margin + 24);

            octx.drawImage(canvasL, margin, headerH);
            octx.drawImage(canvasV, W + margin * 2, headerH);

            const img = octx.getImageData(0, 0, totalW, totalH).data;
            const idx = new Uint8Array(totalW * totalH);
            for (let p = 0, q = 0; p < img.length; p += 4, q++) {
                idx[q] = nearest(img[p], img[p + 1], img[p + 2]);
            }
            frames.push(idx);
        }

        const bytes = encodeGIF(totalW, totalH, palette, frames, Math.round(dt / 10));
        const blob = new Blob([bytes], { type: "image/gif" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `dataflow-side-by-side.gif`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        render(el);
        setExporting(false);
    };

    return (
        <div style={{
            width: "100%",
            maxWidth: 1320,
            margin: "0 auto",
            background: "var(--card-bg)",
            border: "1px solid var(--card-border)",
            borderRadius: "var(--radius-lg)",
            boxShadow: "var(--card-shadow)",
            padding: "28px 32px",
            boxSizing: "border-box",
            backdropFilter: "blur(16px)"
        }}>
            <div style={{ color: "var(--text-muted)", fontSize: 12, letterSpacing: 2, textTransform: "uppercase", fontFamily: "var(--font-mono)", fontWeight: 600, marginBottom: 4 }}>
                seminar 02 {"\u00b7"} memory & data flow analysis
            </div>
            <h1 style={{ color: "var(--text-main)", fontSize: 26, fontWeight: 700, margin: "4px 0 10px", fontFamily: "var(--font-sans)" }}>
                Python Loop vs. NumPy Vectorization
            </h1>
            <p style={{ color: "var(--text-sub)", fontSize: 14, lineHeight: 1.6, maxWidth: 840, margin: "0 0 20px" }}>
                Side-by-side comparison of data flow for element-wise operation <b style={{ color: COL.blue }}>a {opSym} b = c</b>.
                Standard Python loops process boxed scalar objects sequentially through the CPython interpreter, whereas NumPy leverages contiguous memory blocks and pre-compiled C code with SIMD instructions to operate on whole vector lanes at once.
            </p>

            {/* Global Controls */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", margin: "20px 0 24px", fontFamily: "var(--font-mono)", fontSize: 13 }}>
                <button onClick={() => setOpSym((s) => (s === "+" ? "\u00d7" : "+"))} className="mode-btn" style={{ background: "var(--surface-bg)", border: "1px solid var(--surface-border)", color: "var(--text-main)" }}>
                    op: {opSym}
                </button>

                <div style={{ flex: 1 }} />

                <button onClick={() => setSpeed((s) => (s === 1 ? 0.5 : s === 0.5 ? 2 : 1))} className="mode-btn" style={{ background: "var(--surface-bg)", border: "1px solid var(--surface-border)", color: "var(--text-main)" }}>
                    speed {speed === 1 ? "1\u00d7" : speed === 0.5 ? "slow" : "fast"}
                </button>
                <button onClick={reset} className="mode-btn" style={{ background: "var(--surface-bg)", border: "1px solid var(--surface-border)", color: "var(--text-main)" }}>
                    reset
                </button>
                <button onClick={() => { if (done) reset(); setRunning((r) => !r); }} className="mode-btn active" style={{ background: "var(--accent-gradient)", color: "#fff", fontWeight: 700 }}>
                    {running ? "pause" : done ? "replay" : "play"}
                </button>
                <button onClick={exportGif} disabled={exporting} className="mode-btn" style={{ background: exporting ? "var(--surface-bg)" : "var(--brand-blue-bg)", border: "1px solid var(--brand-blue-border)", color: "var(--brand-blue)", fontWeight: 700 }}>
                    {exporting ? "rendering\u2026" : "export side-by-side GIF"}
                </button>
            </div>

            {/* Side-by-side Grid */}
            <div
                ref={splitContainerRef}
                className={`numpy-vis-container ${layout === 'split' ? 'layout-split' : ''} ${isResizing ? 'is-dragging' : ''}`}
                style={
                    layout === 'split'
                        ? {
                              display: "grid",
                              gridTemplateColumns: `${splitRatio}fr 8px ${100 - splitRatio}fr`,
                              gap: 12,
                              alignItems: "start",
                              width: "100%",
                          }
                        : {
                              display: "flex",
                              flexDirection: "column",
                              gap: 20,
                              width: "100%",
                          }
                }
            >
                {/* Left Card: Python Loop */}
                <div style={{
                    background: "var(--surface-bg)",
                    border: "1px solid var(--surface-border)",
                    borderRadius: "var(--radius-lg)",
                    padding: 20,
                    display: "flex",
                    flexDirection: "column",
                    gap: 12
                }}>
                    <div>
                        <h2 style={{ color: "var(--text-main)", fontSize: 22, fontWeight: 700, margin: "0 0 6px", fontFamily: "var(--font-sans)" }}>
                            Python Loop
                        </h2>
                        <p style={{ color: "var(--text-sub)", fontSize: 13, margin: 0, lineHeight: 1.5 }}>
                            <b style={{ color: "var(--warn-text, #e11d48)" }}>High Control & Interpreter Overhead</b> {"\u2014"} step-by-step dynamic execution
                        </p>
                    </div>

                    <canvas
                        ref={canvasLoopRef}
                        width={W}
                        height={H}
                        style={{
                            width: "100%",
                            height: "auto",
                            background: COL.panel,
                            borderRadius: "var(--radius-md)",
                            border: "1px solid var(--card-border)",
                            display: "block"
                        }}
                    />

                    <div style={{
                        background: "var(--card-bg)",
                        border: "1px solid var(--card-border)",
                        borderRadius: "var(--radius-md)",
                        padding: "12px 16px",
                        fontSize: 13,
                        color: "var(--text-sub)",
                        lineHeight: 1.5
                    }}>
                        Iterates element-by-element through Python bytecode. Every step incurs <b>dynamic type checking</b>, pointer dereferencing across boxed scalar objects, and interpreter evaluation loop overhead.
                    </div>
                </div>

                {layout === 'split' && (
                    <div
                        className="split-resizer"
                        onMouseDown={onMouseDown}
                        onDoubleClick={onResetSplit}
                        title="Drag to resize columns • Double-click to reset (50/50)"
                        role="separator"
                        aria-orientation="vertical"
                    >
                        <div className="resizer-handle" />
                    </div>
                )}

                {/* Right Card: Python NumPy */}
                <div style={{
                    background: "var(--surface-bg)",
                    border: "1px solid var(--surface-border)",
                    borderRadius: "var(--radius-lg)",
                    padding: 20,
                    display: "flex",
                    flexDirection: "column",
                    gap: 12
                }}>
                    <div>
                        <h2 style={{ color: "var(--text-main)", fontSize: 22, fontWeight: 700, margin: "0 0 6px", fontFamily: "var(--font-sans)" }}>
                            Python NumPy
                        </h2>
                        <p style={{ color: "var(--text-sub)", fontSize: 13, margin: 0, lineHeight: 1.5 }}>
                            <b style={{ color: "var(--ok-text, #d97706)" }}>Up to ~100× Faster</b> {"\u2014"} vectorized C execution with SIMD hardware registers
                        </p>
                    </div>

                    <canvas
                        ref={canvasVectorRef}
                        width={W}
                        height={H}
                        style={{
                            width: "100%",
                            height: "auto",
                            background: COL.panel,
                            borderRadius: "var(--radius-md)",
                            border: "1px solid var(--card-border)",
                            display: "block"
                        }}
                    />

                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, fontFamily: "var(--font-mono)", fontSize: 12, margin: "2px 0" }}>
                        <span style={{ color: "var(--text-muted)", fontSize: 12, fontWeight: 600 }}>SIMD Hardware Lanes:</span>
                        <div className="mode-toggle-group">
                            {[2, 4, 8].map((l) => (
                                <button key={l} onClick={() => setLanes(l)} className={`mode-btn ${lanes === l ? "active" : ""}`}>
                                    {l} lanes
                                </button>
                            ))}
                        </div>
                    </div>

                    <div style={{
                        background: "var(--card-bg)",
                        border: "1px solid var(--ok-border, var(--card-border))",
                        borderRadius: "var(--radius-md)",
                        padding: "12px 16px",
                        fontSize: 13,
                        color: "var(--text-sub)",
                        lineHeight: 1.5
                    }}>
                        Incurs only a single <span style={{ color: "var(--text-muted)" }}>{"\u201c"}C-dispatch{"\u201d"}</span> overhead call. Homogeneous, contiguous memory storage allows CPU SIMD vector units to execute <b style={{ color: COL.green }}>{lanes} element lanes simultaneously</b>.
                    </div>
                </div>
            </div>

            <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 20, fontFamily: "var(--font-sans)" }}>
                Synchronized real-time simulation comparing scalar CPython bytecode execution against compiled C-level vectorization at 20 fps. Export side-by-side GIF for lecture & seminar slides.
            </p>
        </div>
    );
}