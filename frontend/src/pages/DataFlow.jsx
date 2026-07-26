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

const COL = {
    bg: "#f4f1ea", panel: "#ffffff", ink: "#2b2a26", sub: "#6b6a63",
    line: "#d9d4c7", cellEmpty: "#ece8dd", emptyTxt: "#b7b1a2",
    blue: "#2f6f9f", green: "#2f8f5b", amber: "#c8792e",
    greenSoft: "#e2f0e6", greenLine: "#8cc7a3", opBg: "#fbf9f3", opStroke: "#d9d4c7",
};
const heat = (v, lo, hi) => {
    const x = hi <= lo ? 0.5 : Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
    const a = [176, 205, 214], b = [245, 240, 228], c = [230, 179, 118];
    const mix = (p, q, t) => p.map((pv, i) => Math.round(pv + (q[i] - pv) * t));
    const col = x < 0.5 ? mix(a, b, x * 2) : mix(b, c, (x - 0.5) * 2);
    return `rgb(${col[0]},${col[1]},${col[2]})`;
};
const clamp01 = (x) => Math.max(0, Math.min(1, x));
const UD = 900, DISPATCH = 560, CD = 820;

// quadratic bezier point
const qbez = (p0, p1, p2, t) => {
    const u = 1 - t;
    return [u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]];
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
function draw(ctx, el, mode, lanes, total, A, B, CC, opSym, lo, hi) {
    const S = modelAt(el, mode, lanes, total, A, B, CC, opSym);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = COL.panel; ctx.fillRect(0, 0, W, H);

    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const mono = (s) => `${s}px ui-monospace, Menlo, monospace`;

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
        cell(XA, y, heat(A[i], lo, hi), st, sw, String(A[i]), COL.ink);
        cell(XB, y, heat(B[i], lo, hi), st, sw, String(B[i]), COL.ink);
        if (S.filled.has(i)) cell(XC, y, heat(CC[i], lo, hi), on ? COL.amber : COL.line, sw, String(CC[i]), COL.ink);
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
// build a fixed palette + nearest-index mapping
function buildPalette() {
    const named = [COL.bg, COL.panel, COL.ink, COL.sub, COL.line, COL.cellEmpty, COL.emptyTxt,
    COL.blue, COL.green, COL.amber, COL.greenSoft, COL.greenLine, COL.opBg, COL.opStroke];
    const hex2rgb = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
    const pal = named.map(hex2rgb);
    for (let i = 0; i <= 24; i++) { // heat ramp samples
        const s = heat(1 + (i / 24) * 80, 1, 81).match(/\d+/g).map(Number); pal.push(s);
    }
    while (pal.length < 256) pal.push([0, 0, 0]);
    return pal.slice(0, 256);
}

export default function DataFlow() {
    const [mode, setMode] = useState("loop");
    const [opSym, setOpSym] = useState("+");
    const [lanes, setLanes] = useState(4);
    const [running, setRunning] = useState(false);
    const [speed, setSpeed] = useState(1);
    const [el, setEl] = useState(0);
    const [exporting, setExporting] = useState(false);

    const canvasRef = useRef(null);
    const raf = useRef(null); const last = useRef(0); const acc = useRef(0);
    const [A] = useState(() => Array.from({ length: N }, () => Math.floor(Math.random() * 9) + 1));
    const [B] = useState(() => Array.from({ length: N }, () => Math.floor(Math.random() * 9) + 1));
    const fn = OPS[opSym];
    const CC = A.map((_, i) => fn(A[i], B[i]));
    const lo = 1, hi = opSym === "\u00d7" ? 81 : 18;
    const numChunks = Math.ceil(N / lanes);
    const total = mode === "loop" ? N * UD : DISPATCH + numChunks * CD;

    const render = useCallback((e) => {
        const ctx = canvasRef.current?.getContext("2d"); if (!ctx) return;
        draw(ctx, e, mode, lanes, total, A, B, CC, opSym, lo, hi);
    }, [mode, lanes, total, A, B, opSym]);

    useEffect(() => { render(el); }, [render, el]);

    const reset = () => { setRunning(false); acc.current = 0; setEl(0); cancelAnimationFrame(raf.current); };
    useEffect(() => { reset(); }, [mode, lanes, opSym]);

    useEffect(() => {
        if (!running) return;
        last.current = performance.now();
        const tick = (now) => {
            acc.current += (now - last.current) * speed; last.current = now;
            if (acc.current >= total) { setEl(total); setRunning(false); return; }
            setEl(acc.current); raf.current = requestAnimationFrame(tick);
        };
        raf.current = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(raf.current);
    }, [running, speed, total]);
    const done = el >= total;

    const exportGif = async () => {
        setExporting(true); setRunning(false);
        await new Promise((r) => setTimeout(r, 30));
        const scale = 1; // keep 1:1 for speed; palette is flat
        const off = document.createElement("canvas"); off.width = W; off.height = H;
        const octx = off.getContext("2d");
        const palette = buildPalette();
        // nearest palette index
        const cache = new Map();
        const nearest = (r, g, b) => {
            const key = (r << 16) | (g << 8) | b;
            if (cache.has(key)) return cache.get(key);
            let bi = 0, bd = 1e9;
            for (let i = 0; i < palette.length; i++) { const p = palette[i]; const d = (p[0] - r) ** 2 + (p[1] - g) ** 2 + (p[2] - b) ** 2; if (d < bd) { bd = d; bi = i; } }
            cache.set(key, bi); return bi;
        };
        const FPS = 20, dt = 1000 / FPS;
        const nFrames = Math.ceil(total / dt) + 8; // + hold at end
        const frames = [];
        for (let k = 0; k < nFrames; k++) {
            const t = Math.min(total, k * dt);
            draw(octx, t, mode, lanes, total, A, B, CC, opSym, lo, hi);
            const img = octx.getImageData(0, 0, W, H).data;
            const idx = new Uint8Array(W * H);
            for (let p = 0, q = 0; p < img.length; p += 4, q++) idx[q] = nearest(img[p], img[p + 1], img[p + 2]);
            frames.push(idx);
        }
        const bytes = encodeGIF(W, H, palette, frames, Math.round(dt / 10));
        const blob = new Blob([bytes], { type: "image/gif" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = `dataflow-${mode}.gif`; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        render(el); setExporting(false);
    };

    const btn = (on) => ({ background: on ? "#2b2a26" : COL.panel, color: on ? "#f4f1ea" : COL.ink, border: "1px solid " + COL.line, padding: "8px 12px", borderRadius: 6, cursor: "pointer" });

    return (
        <div style={{ minHeight: "100vh", background: COL.bg, fontFamily: "Georgia, serif" }}>
            <div style={{ maxWidth: 820, margin: "0 auto", padding: "32px 24px" }}>
                <div style={{ color: COL.sub, fontSize: 12, letterSpacing: 2, textTransform: "uppercase", fontFamily: "ui-monospace, monospace" }}>seminar 02 {"\u00b7"} data flow</div>
                <h1 style={{ color: COL.ink, fontSize: 30, margin: "4px 0 8px" }}>Where the data goes</h1>
                <p style={{ color: COL.sub, fontSize: 14, lineHeight: 1.6, maxWidth: 620 }}>
                    Two inputs, <b style={{ color: COL.blue }}>a</b> and <b style={{ color: COL.blue }}>b</b>, flow through an operation
                    to produce <b style={{ color: COL.amber }}>c</b>. The loop lights <b>one row at a time</b>; the vector fires a{" "}
                    <b style={{ color: COL.green }}>whole SIMD chunk</b> at once. Export the animation as a GIF for your slides.
                </p>

                <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", margin: "18px 0", fontFamily: "ui-monospace, monospace", fontSize: 14 }}>
                    <div style={{ display: "flex", borderRadius: 6, overflow: "hidden", border: "1px solid " + COL.line }}>
                        <button onClick={() => setMode("loop")} style={{ ...btn(mode === "loop"), border: "none", borderRadius: 0 }}>Python loop</button>
                        <button onClick={() => setMode("vector")} style={{ ...btn(mode === "vector"), border: "none", borderRadius: 0 }}>NumPy vector</button>
                    </div>
                    <button onClick={() => setOpSym((s) => (s === "+" ? "\u00d7" : "+"))} style={btn(false)}>op: {opSym}</button>
                    {mode === "vector" && (
                        <div style={{ display: "flex", borderRadius: 6, overflow: "hidden", border: "1px solid " + COL.line }}>
                            {[2, 4, 8].map((l) => (<button key={l} onClick={() => setLanes(l)} style={{ ...btn(lanes === l), border: "none", borderRadius: 0 }}>{l} lanes</button>))}
                        </div>
                    )}
                    <div style={{ flex: 1 }} />
                    <button onClick={() => setSpeed((s) => (s === 1 ? 0.5 : s === 0.5 ? 2 : 1))} style={btn(false)}>speed {speed === 1 ? "1\u00d7" : speed === 0.5 ? "slow" : "fast"}</button>
                    <button onClick={reset} style={btn(false)}>reset</button>
                    <button onClick={() => { if (done) reset(); setRunning((r) => !r); }} style={{ background: COL.amber, color: "#fff", border: "none", padding: "8px 18px", borderRadius: 6, fontWeight: 700, cursor: "pointer" }}>{running ? "pause" : done ? "replay" : "play"}</button>
                    <button onClick={exportGif} disabled={exporting} style={{ background: exporting ? COL.line : COL.green, color: "#fff", border: "none", padding: "8px 16px", borderRadius: 6, fontWeight: 700, cursor: exporting ? "default" : "pointer" }}>{exporting ? "rendering\u2026" : "export GIF"}</button>
                </div>

                <canvas ref={canvasRef} width={W} height={H} style={{ width: "100%", background: COL.panel, borderRadius: 12, border: "1px solid " + COL.line, display: "block" }} />

                <div style={{ display: "grid", gap: 16, gridTemplateColumns: "1fr 1fr", marginTop: 20, color: COL.sub, fontSize: 13 }}>
                    <div style={{ background: COL.panel, border: "1px solid " + COL.line, borderRadius: 10, padding: "12px 16px" }}>
                        <div style={{ color: COL.ink, marginBottom: 4, fontWeight: "bold" }}>Loop</div>
                        One shared operation node, reused N times. Input and output arrows light <b>one row at a time</b> {"\u2014"} that sequential trip through the interpreter is the cost.
                    </div>
                    <div style={{ background: COL.panel, border: "1px solid " + COL.greenLine, borderRadius: 10, padding: "12px 16px" }}>
                        <div style={{ color: COL.ink, marginBottom: 4, fontWeight: "bold" }}>Vector</div>
                        After one <span style={{ color: COL.sub }}>{"\u201c"}call C{"\u201d"}</span> dispatch, a full-height SIMD unit lights <b style={{ color: COL.green }}>{lanes} rows together</b> per pulse.
                    </div>
                </div>
                <p style={{ color: COL.sub, fontSize: 12, marginTop: 12 }}>The GIF captures the current mode, operation, and lane count at 20 fps, looping.</p>
            </div>
        </div>
    );
}