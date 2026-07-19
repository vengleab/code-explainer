"""
backend/generate_pandas.py — Vercel Python service (WSGI entrypoint: generate_pandas:app).

POST { "code": "<python source>", "ms": 1100 } -> image/gif bytes.

This mirrors the trace/render pipeline in pandasgif.py (kept at the repo root
as a standalone CLI) but adapted to run inside a stateless function:
  - writes the GIF to an in-memory buffer instead of disk
  - loads bundled fonts instead of scanning the local filesystem
  - bounds untrusted, user-submitted code with a step cap, a wall-clock
    timeout, and a restricted exec() environment (see SAFETY NOTE below)
  - adds pandas and numpy to the allowed-imports list so users can run
    DataFrame operations

The key difference from generate.py is that any variable that is a DataFrame
or Series is drawn as a real grid/table, with diff highlighting:
  - brand-new columns        -> green header + green cells
  - cells whose value changed -> green
  - filter detection: rows kept vs dropped with strikethrough

SAFETY NOTE: this endpoint executes arbitrary user-submitted Python. The
restrictions below (AST denylist + reduced builtins + import allowlist +
timeout + step cap) block the obvious escape routes (file/network/process
access, dunder introspection) but this is best-effort sandboxing in the
same process, not a real isolation boundary (no seccomp/gVisor/VM). Do not
treat this as safe to expose to hostile traffic without adding real
sandboxing, auth, or rate limiting in front of it.
"""
import ast
import base64
import builtins as _builtins
import copy
import io
import json
import os
import sys
import time
import types
from urllib.parse import parse_qs

try:  # package import in dev (imported as backend.generate_pandas)
    from .theme import get_palette
except ImportError:  # top-level module on the serverless runtime
    from theme import get_palette

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------
MAX_CODE_LEN = 4000
MAX_STEPS = 200
TRACE_TIMEOUT_SECONDS = 5
MS_MIN, MS_MAX = 200, 2000

ALLOWED_IMPORTS = {
    "math", "random", "string", "itertools", "functools", "collections",
    "datetime", "re", "json", "statistics", "decimal", "fractions",
    "pandas", "numpy", "pd", "np",
}

SAFE_BUILTIN_NAMES = {
    "print", "range", "len", "str", "int", "float", "bool", "list", "dict",
    "set", "frozenset", "tuple", "sum", "min", "max", "sorted", "reversed",
    "enumerate", "zip", "map", "filter", "abs", "round", "all", "any",
    "isinstance", "issubclass", "type", "chr", "ord", "divmod", "pow",
    "repr", "format", "slice", "iter", "next", "bytes", "bytearray",
    "complex", "object", "Exception", "ValueError", "TypeError", "KeyError",
    "IndexError", "StopIteration", "ZeroDivisionError", "AttributeError",
    "RuntimeError", "ArithmeticError", "OverflowError", "NotImplementedError",
    "AssertionError", "NameError", "True", "False", "None",
}

DENIED_CALL_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "input", "breakpoint", "help",
    "memoryview", "classmethod", "staticmethod", "super", "property",
}


class UnsafeCodeError(Exception):
    pass


class StepLimitExceeded(Exception):
    pass


class ExecutionTimeout(Exception):
    pass


def check_safe(source):
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise UnsafeCodeError(f"SyntaxError: {e}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    raise UnsafeCodeError(f"import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                raise UnsafeCodeError(f"import of '{node.module}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise UnsafeCodeError(f"access to '{node.attr}' is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in DENIED_CALL_NAMES:
                raise UnsafeCodeError(f"use of '{node.id}' is not allowed")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            raise UnsafeCodeError("async code is not allowed")


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"import of '{name}' is not allowed in this sandbox")
    return _builtins.__import__(name, globals, locals, fromlist, level)


def make_restricted_globals():
    safe = {name: getattr(_builtins, name) for name in SAFE_BUILTIN_NAMES if hasattr(_builtins, name)}
    safe["__import__"] = _safe_import
    return {"__builtins__": safe, "__name__": "__snippet__"}


# --------------------------------------------------------------------------
# Fonts — use bundled RobotoMono from backend/fonts/
# --------------------------------------------------------------------------
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
MONO = os.path.join(FONT_DIR, "RobotoMono-Regular.ttf")
MONO_B = os.path.join(FONT_DIR, "RobotoMono-Bold.ttf")


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


# Fonts used for code rendering and table rendering
FC = _font(MONO, 17)
FT = _font(MONO_B, 14)
FH = _font(MONO_B, 15)
FCELL = _font(MONO, 15)
FCAP = _font(MONO, 15)

# Color palettes live in theme.py (shared with generate.py + the frontend).
# A palette dict `pal` is threaded through render() per request so the UI's
# "dark"/"light" toggle matches the exported GIF.

KW = {
    "for", "in", "while", "if", "else", "elif", "def", "return", "print",
    "import", "and", "or", "not", "True", "False", "None",
}


# --------------------------------------------------------------------------
# STAGE 1 — TRACE (adapted from pandasgif.py)
# --------------------------------------------------------------------------
def snap(frame):
    """Snapshot local variables, deep-copying DataFrames/Series and scalars."""
    out = {}
    for k, v in frame.f_locals.items():
        if k.startswith("__"):
            continue
        if isinstance(v, (pd.DataFrame, pd.Series)):
            out[k] = v.copy()
        elif isinstance(v, (int, float, str, bool)) or (
            hasattr(v, "dtype") and getattr(v, "shape", None) == ()
        ):
            try:
                out[k] = copy.copy(v)
            except Exception:
                pass
    return out


def as_frame(obj):
    """Convert a Series to a DataFrame; pass DataFrames through; else None."""
    if isinstance(obj, pd.DataFrame):
        return obj
    if isinstance(obj, pd.Series):
        return obj.to_frame(name=(obj.name if obj.name is not None else "value"))
    return None


def trace(source):
    """Execute source, collecting per-line snapshots of DataFrames & scalars."""
    check_safe(source)
    code = compile(source, "<snip>", "exec")
    steps = []
    buf = io.StringIO()
    start = time.monotonic()

    def tr(fr, ev, arg):
        if fr.f_code.co_filename != "<snip>" or fr.f_code.co_name != "<module>":
            return None  # don't step into lambdas/functions (e.g. inside .apply)
        if ev == "line":
            if len(steps) >= MAX_STEPS:
                raise StepLimitExceeded(f"step limit ({MAX_STEPS}) reached")
            if time.monotonic() - start > TRACE_TIMEOUT_SECONDS:
                raise ExecutionTimeout(f"tracing exceeded {TRACE_TIMEOUT_SECONDS}s")
            steps.append(dict(line=fr.f_lineno, dfs=snap(fr), out=buf.getvalue()))
        return tr

    real = sys.stdout
    sys.stdout = buf
    sys.settrace(tr)

    ns = make_restricted_globals()
    err = None
    try:
        exec(code, ns)
    except (StepLimitExceeded, ExecutionTimeout) as e:
        err = str(e)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        sys.settrace(None)
        sys.stdout = real

    # Build final state from the namespace
    final_dfs = {}
    for k, v in ns.items():
        if k.startswith("__"):
            continue
        if isinstance(v, (pd.DataFrame, pd.Series)):
            final_dfs[k] = v.copy()
        elif isinstance(v, (int, float, str, bool)) or (
            hasattr(v, "dtype") and getattr(v, "shape", None) == ()
        ):
            try:
                final_dfs[k] = copy.copy(v)
            except Exception:
                pass
    steps.append(dict(line=None, dfs=final_dfs, out=buf.getvalue(), final=True, error=err))
    return steps


# --------------------------------------------------------------------------
# STAGE 2 — RENDER (adapted from pandasgif.py, using bundled fonts)
# --------------------------------------------------------------------------
def draw_code(d, x, y, text, pal):
    """Syntax-highlight a single line of Python code."""
    cx, tok, ins, sc, i = x, "", False, "", 0

    def fl(col):
        nonlocal cx, tok
        if tok:
            d.text((cx, y), tok, font=FC, fill=col)
            cx += d.textlength(tok, font=FC)
            tok = ""

    while i < len(text):
        ch = text[i]
        if ins:
            tok += ch
            if ch == sc:
                d.text((cx, y), tok, font=FC, fill=pal["s"])
                cx += d.textlength(tok, font=FC)
                tok = ""
                ins = False
            i += 1
            continue
        if ch in ("'", '"'):
            fl(pal["kw"] if tok in KW else pal["code"])
            ins = True
            sc = ch
            tok = ch
            i += 1
            continue
        if ch.isalnum() or ch == "_":
            tok += ch
        else:
            fl(pal["kw"] if tok in KW else pal["code"])
            d.text((cx, y), ch, font=FC, fill=pal["code"])
            cx += d.textlength(ch, font=FC)
        i += 1
    fl(pal["kw"] if tok in KW else pal["code"])


def fmt(v):
    """Format a cell value for display."""
    if isinstance(v, float):
        return ("%.2f" % v).rstrip("0").rstrip(".")
    return str(v)


def draw_df(d, x, y, w, name, df, prev, pal, maxr=7, maxc=6, status=None):
    """Draw a DataFrame as a grid table with diff highlighting."""
    cols = list(df.columns)[:maxc]
    new_cols = set(cols) - set(prev.columns) if isinstance(prev, pd.DataFrame) else set()
    d.text(
        (x, y),
        "%s   %d rows x %d cols" % (name, df.shape[0], df.shape[1]),
        font=FT,
        fill=pal["title"],
    )
    y += 26
    # column widths
    idxw = max(24, max((len(str(ix)) for ix in list(df.index)[:maxr]), default=1) * 9 + 10)
    cw = []
    for c in cols:
        vals = [fmt(v) for v in list(df[c])[:maxr]]
        chars = max([len(str(c))] + [len(v) for v in vals])
        cw.append(min(160, chars * 9 + 18))
    # clamp total width to the panel: drop rightmost columns that don't fit
    while cw and idxw + sum(cw) > w - 8:
        cw.pop()
        cols = cols[:-1]
    rowh = 26
    # header
    hx = x + idxw
    d.rectangle([x, y, x + idxw + sum(cw), y + rowh], fill=pal["panel"])
    d.text((x + 6, y + 5), "idx", font=FH, fill=pal["muted"])
    for j, c in enumerate(cols):
        if c in new_cols:
            d.rectangle([hx, y, hx + cw[j], y + rowh], fill=pal["newbg"])
        d.text(
            (hx + 8, y + 5),
            str(c)[:16],
            font=FH,
            fill=pal["new"] if c in new_cols else pal["head"],
        )
        hx += cw[j]
    y += rowh
    # rows
    idx = list(df.index)[:maxr]
    for r, ix in enumerate(idx):
        st = status.get(ix) if status else None
        if st == "kept":
            d.rectangle([x, y, x + idxw + sum(cw), y + rowh], fill=pal["newbg"])
        elif r % 2:
            d.rectangle([x, y, x + idxw + sum(cw), y + rowh], fill=pal["zebra"])
        d.text((x + 6, y + 5), str(ix)[:4], font=FCELL, fill=pal["muted"])
        cxp = x + idxw
        for j, c in enumerate(cols):
            val = df.at[ix, c]
            changed = c in new_cols
            if (
                not changed
                and isinstance(prev, pd.DataFrame)
                and c in prev.columns
                and ix in prev.index
            ):
                try:
                    changed = prev.at[ix, c] != val
                except Exception:
                    changed = False
            if changed and st != "dropped":
                d.rectangle([cxp, y, cxp + cw[j], y + rowh], fill=pal["newbg"])
            col = (
                (96, 100, 120)
                if st == "dropped"
                else (pal["new"] if (changed or st == "kept") else pal["cell"])
            )
            txt = fmt(val)[:16]
            d.text((cxp + 8, y + 5), txt, font=FCELL, fill=col)
            if st == "dropped":
                tw = d.textlength(txt, font=FCELL)
                d.line(
                    [cxp + 8, y + rowh // 2, cxp + 8 + tw, y + rowh // 2],
                    fill=(96, 100, 120),
                    width=2,
                )
            cxp += cw[j]
        y += rowh
    # overflow indicator
    if df.shape[0] > maxr:
        d.text(
            (x + 6, y + 4),
            "... %d more rows" % (df.shape[0] - maxr),
            font=FCELL,
            fill=pal["muted"],
        )
        y += 22
    return y


def render(step, i, steps, src, dims, pal):
    """Render a single frame: code panel on left, DataFrame grids on right."""
    W, H, cw_code, top = dims
    img = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(img)
    # code panel
    PAD = 24
    d.rounded_rectangle([PAD, PAD, PAD + cw_code, H - PAD], 10, fill=pal["panel"])
    d.text(
        (PAD + 14, PAD + 12),
        "code  step %d/%d" % (i + 1, len(steps)),
        font=FT,
        fill=pal["title"],
    )
    ly = PAD + 42
    cur = step["line"]
    for idx, line in enumerate(src):
        on = (idx + 1) == cur
        if on:
            d.rounded_rectangle(
                [PAD + 8, ly - 1, PAD + cw_code - 10, ly + 24], 5, fill=pal["hl"]
            )
            d.rectangle([PAD + 8, ly - 1, PAD + 12, ly + 24], fill=pal["bar"])
        d.text((PAD + 14, ly), "%2d" % (idx + 1), font=FC, fill=pal["gutter"])
        d.text((PAD + 44, ly), ">" if on else " ", font=FC, fill=pal["bar"])
        maxpx = cw_code - 96
        cl = line
        while cl and d.textlength(cl, font=FC) > maxpx:
            cl = cl[:-1]
        if cl != line and cl:
            cl = cl[:-1] + "\u2026"
        draw_code(d, PAD + 64, ly, cl, pal)
        ly += 28
    # right column: up to 3 grids (DataFrame/Series) + a scalar strip
    rx = PAD + cw_code + 22
    rw = W - rx - PAD
    y = PAD
    prev = steps[i - 1]["dfs"] if i > 0 else {}

    def changed(name, v):
        p = prev.get(name)
        try:
            if isinstance(v, (pd.DataFrame, pd.Series)):
                return (p is None) or (not v.equals(p))
            return p != v
        except Exception:
            return True

    grids = []
    scalars = []
    for name, v in step["dfs"].items():
        fr = as_frame(v)
        if fr is not None:
            grids.append((name, fr, v))
        else:
            scalars.append((name, v))

    # filter detection: a changed table whose rows are a subset of another table
    row_status = {}
    filt_pair = set()
    for name, fr, orig in grids:
        if not changed(name, orig):
            continue
        for sname, sf, sorig in grids:
            if sname == name or sf.shape[0] <= fr.shape[0]:
                continue
            try:
                if set(fr.columns) <= set(sf.columns) and set(fr.index) < set(sf.index):
                    keep = set(fr.index)
                    row_status[sname] = {
                        ix: ("kept" if ix in keep else "dropped") for ix in sf.index
                    }
                    filt_pair.update({name, sname})
                    break
            except TypeError:
                continue

    def prio(t):
        n, fr, o = t
        if n in filt_pair:
            return 0
        return 1 if changed(n, o) else 2

    grids.sort(key=prio)
    for name, fr, orig in grids[:3]:
        pf = as_frame(prev.get(name))
        y = draw_df(d, rx, y, rw, name, fr, pf, pal, maxr=6, status=row_status.get(name)) + 16
    if row_status:
        sname = next(iter(row_status))
        kept = sum(1 for s in row_status[sname].values() if s == "kept")
        d.text(
            (rx, min(y, H - PAD - 24)),
            "filter kept %d of %d rows" % (kept, len(row_status[sname])),
            font=FCAP,
            fill=pal["cap"],
        )
        y += 24
    if scalars:
        sy = min(y, H - PAD - 24)
        parts = []
        for name, v in scalars[:6]:
            parts.append("%s=%s" % (name, fmt(v)))
        d.text((rx, sy), "scalars:  " + "   ".join(parts), font=FCAP, fill=pal["cap"])

    # Show error on final frame if present
    if step.get("error"):
        ey = min(y + 8, H - PAD - 24)
        d.text((rx, ey), ("! " + step["error"])[:60], font=FCAP, fill=(243, 139, 168))

    return img


# --------------------------------------------------------------------------
# STAGE 3 — ENCODE (build frames + GIF)
# --------------------------------------------------------------------------
def build_frames(source, ms=1100, palette="dark"):
    ms = max(MS_MIN, min(MS_MAX, ms))
    pal = get_palette(palette)
    src = source.splitlines()
    steps = trace(source)
    dummy = Image.new("RGB", (8, 8))
    dd = ImageDraw.Draw(dummy)
    charw = dd.textlength("m", font=FC)
    longest = max((len(l) for l in src), default=20)
    cw_code = int(min(max(96 + longest * charw + 20, 380), 640))
    right_w = 480
    W = 24 + cw_code + 22 + right_w + 24

    def gh(fr):
        return 26 + 26 + min(fr.shape[0], 6) * 26 + (22 if fr.shape[0] > 6 else 0)

    maxright = 0
    for s in steps:
        frs = sorted(
            [as_frame(v) for v in s["dfs"].values() if as_frame(v) is not None],
            key=lambda f: -gh(f),
        )[:3]
        h = sum(gh(f) + 16 for f in frs)
        if any(as_frame(v) is None for v in s["dfs"].values()):
            h += 28
        maxright = max(maxright, h)
    top = 24
    H = min(max(24 * 2 + 42 + len(src) * 28, maxright + 96, 380), 960)

    frames = []
    durs = []
    for i, s in enumerate(steps):
        frames.append(render(s, i, steps, src, (W, H, cw_code, top), pal))
        durs.append(int(ms * 2.4) if s.get("final") else ms)
    return frames, durs


def encode_gif(frames, durs):
    out = io.BytesIO()
    frames[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durs,
        loop=0,
        disposal=2,
        optimize=True,
    )
    return out.getvalue()


def build_gif_bytes(source, ms=1100, palette="dark"):
    frames, durs = build_frames(source, ms=ms, palette=palette)
    return encode_gif(frames, durs)


# Serverless responses are size-capped (~4.5MB on Vercel), so when the
# per-frame payload would blow past this, send only the animated GIF and let
# the frontend fall back to a plain <img> without the stepper.
FRAMES_BYTES_LIMIT = 2_500_000


def build_json_payload(source, ms=1100, palette="dark"):
    frames, durs = build_frames(source, ms=ms, palette=palette)
    gif = encode_gif(frames, durs)

    frames_b64, total = [], 0
    for fr in frames:
        buf = io.BytesIO()
        fr.save(buf, format="GIF", optimize=True)
        data = buf.getvalue()
        total += len(data)
        if total > FRAMES_BYTES_LIMIT:
            frames_b64 = None
            break
        frames_b64.append(base64.b64encode(data).decode())

    return {"gif": base64.b64encode(gif).decode(), "frames": frames_b64, "durations": durs}


# --------------------------------------------------------------------------
# Vercel entrypoint (WSGI)
#
# This is the "backend" service for pandas visualizations; routes via
# /api/generate-pandas. Separate from the base generate.py endpoint.
# --------------------------------------------------------------------------
STATUS_REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
}


def _status_line(code):
    return f"{code} {STATUS_REASONS.get(code, 'Error')}"


def _json_response(start_response, status, payload):
    body = json.dumps(payload).encode("utf-8")
    start_response(
        _status_line(status),
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


def _gif_response(start_response, gif_bytes):
    start_response(
        _status_line(200),
        [
            ("Content-Type", "image/gif"),
            ("Content-Length", str(len(gif_bytes))),
            # Let external fetchers cache the result instead of re-running
            # the trace every time.
            ("Cache-Control", "public, max-age=86400"),
        ],
    )
    return [gif_bytes]


def _generate_or_error(start_response, code, ms, fmt="gif", palette="dark"):
    if not isinstance(code, str) or not code.strip():
        return _json_response(
            start_response, 400, {"error": "'code' must be a non-empty string"}
        )
    if len(code) > MAX_CODE_LEN:
        return _json_response(
            start_response,
            400,
            {"error": f"code too long (max {MAX_CODE_LEN} characters)"},
        )
    if not isinstance(ms, (int, float)):
        ms = 1100

    try:
        if fmt == "json":
            payload = build_json_payload(code, ms=int(ms), palette=palette)
        else:
            gif_bytes = build_gif_bytes(code, ms=int(ms), palette=palette)
    except (UnsafeCodeError, ExecutionTimeout) as e:
        return _json_response(start_response, 400, {"error": str(e)})
    except Exception as e:
        return _json_response(
            start_response, 500, {"error": f"{type(e).__name__}: {e}"}
        )

    if fmt == "json":
        return _json_response(start_response, 200, payload)
    return _gif_response(start_response, gif_bytes)


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = (environ.get("PATH_INFO") or "/").split("?")[0]

    if path != "/api/generate-pandas":
        return _json_response(start_response, 404, {"error": "not found"})

    if method == "GET":
        # GET with ?c=<base64url(code)>[&ms=N] returns the GIF directly.
        # This gives every snippet a shareable URL that external services
        # (Google Slides "Insert image by URL", chat apps, etc.) can fetch.
        qs = parse_qs(environ.get("QUERY_STRING") or "")
        if "c" in qs:
            try:
                b64 = qs["c"][0]
                code = base64.urlsafe_b64decode(
                    b64 + "=" * (-len(b64) % 4)
                ).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return _json_response(
                    start_response,
                    400,
                    {
                        "error": "invalid 'c' parameter (expected base64url-encoded UTF-8 code)"
                    },
                )
            try:
                ms = int(qs.get("ms", ["1100"])[0])
            except ValueError:
                ms = 1100
            palette = qs.get("pal", ["dark"])[0]
            return _generate_or_error(start_response, code, ms, palette=palette)
        return _json_response(
            start_response,
            200,
            {
                "ok": True,
                "usage": "POST {code, ms, palette} -> image/gif, or GET ?c=<base64url(code)>&ms=N&pal=dark|light -> image/gif",
            },
        )

    if method != "POST":
        return _json_response(
            start_response, 405, {"error": "method not allowed"}
        )

    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        raw = environ["wsgi.input"].read(length) if length else b"{}"
        payload = json.loads(raw or b"{}")
    except (ValueError, json.JSONDecodeError):
        return _json_response(
            start_response, 400, {"error": "invalid JSON body"}
        )

    fmt = "json" if payload.get("format") == "json" else "gif"
    return _generate_or_error(
        start_response, payload.get("code", ""), payload.get("ms", 1100), fmt=fmt,
        palette=payload.get("palette", "dark"),
    )
