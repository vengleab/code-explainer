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
    from .pysyntax import iter_tokens
except ImportError:  # top-level module on the serverless runtime
    from theme import get_palette
    from pysyntax import iter_tokens

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
    safe_builtins = {name: getattr(_builtins, name) for name in SAFE_BUILTIN_NAMES if hasattr(_builtins, name)}
    safe_builtins["__import__"] = _safe_import
    return {"__builtins__": safe_builtins, "__name__": "__snippet__"}


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


# Quality presets: map a user-facing label to (code_size, scale_factor).
QUALITY_PRESETS = {
    "low":    {"code_size": 11, "scale": 0.6},
    "medium": {"code_size": 17, "scale": 1.0},
    "high":   {"code_size": 24, "scale": 1.4},
}


def load_fonts(code_size=17):
    """Return a dict of fonts scaled relative to code_size."""
    title_size = max(10, int(code_size * 0.82))
    header_size = max(10, int(code_size * 0.88))
    caption_size = max(9, int(code_size * 0.82))
    return {
        "code":    _font(MONO,   code_size),
        "title":   _font(MONO_B, title_size),
        "header":  _font(MONO_B, header_size),
        "cell":    _font(MONO,   code_size),
        "caption": _font(MONO,   caption_size),
    }


# Default (medium-quality) module-level fonts kept for backward compat.
FONT_CODE    = _font(MONO,   17)
FONT_TITLE   = _font(MONO_B, 14)
FONT_HEADER  = _font(MONO_B, 15)
FONT_CELL    = _font(MONO,   15)
FONT_CAPTION = _font(MONO,   15)

# Color palettes live in theme.py (shared with generate.py + the frontend).
# A palette dict `palette_colors` is threaded through render() per request so
# the UI's "dark"/"light" toggle matches the exported GIF.

# Token classification lives in pysyntax.iter_tokens (shared with generate.py
# and the frontend editor).


# --------------------------------------------------------------------------
# STAGE 1 — TRACE (adapted from pandasgif.py)
# --------------------------------------------------------------------------
def snap(frame):
    """Snapshot local variables, deep-copying DataFrames/Series and scalars."""
    snapshot = {}
    for name, value in frame.f_locals.items():
        if name.startswith("__"):
            continue
        if isinstance(value, (pd.DataFrame, pd.Series)):
            snapshot[name] = value.copy()
        elif isinstance(value, (int, float, str, bool)) or (
            hasattr(value, "dtype") and getattr(value, "shape", None) == ()
        ):
            try:
                snapshot[name] = copy.copy(value)
            except Exception:
                pass
    return snapshot


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
    compiled = compile(source, "<snip>", "exec")
    steps = []
    stdout_buffer = io.StringIO()
    start_time = time.monotonic()

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != "<snip>" or frame.f_code.co_name != "<module>":
            return None  # don't step into lambdas/functions (e.g. inside .apply)
        if event == "line":
            if len(steps) >= MAX_STEPS:
                raise StepLimitExceeded(f"step limit ({MAX_STEPS}) reached")
            if time.monotonic() - start_time > TRACE_TIMEOUT_SECONDS:
                raise ExecutionTimeout(f"tracing exceeded {TRACE_TIMEOUT_SECONDS}s")
            steps.append(dict(line=frame.f_lineno, dfs=snap(frame), out=stdout_buffer.getvalue()))
        return tracer

    real_stdout = sys.stdout
    sys.stdout = stdout_buffer
    sys.settrace(tracer)

    namespace = make_restricted_globals()
    error_message = None
    try:
        exec(compiled, namespace)
    except (StepLimitExceeded, ExecutionTimeout) as e:
        error_message = str(e)
    except Exception as e:
        error_message = f"{type(e).__name__}: {e}"
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout

    # Build final state from the namespace
    final_dfs = {}
    for name, value in namespace.items():
        if name.startswith("__"):
            continue
        if isinstance(value, (pd.DataFrame, pd.Series)):
            final_dfs[name] = value.copy()
        elif isinstance(value, (int, float, str, bool)) or (
            hasattr(value, "dtype") and getattr(value, "shape", None) == ()
        ):
            try:
                final_dfs[name] = copy.copy(value)
            except Exception:
                pass
    steps.append(dict(line=None, dfs=final_dfs, out=stdout_buffer.getvalue(), final=True, error=error_message))
    return steps


# --------------------------------------------------------------------------
# STAGE 2 — RENDER (adapted from pandasgif.py, using bundled fonts)
# --------------------------------------------------------------------------
def draw_code(draw, x, y, text, palette_colors, font_code):
    """Syntax-highlight a single line of Python code."""
    cursor_x = x
    for span_text, category in iter_tokens(text):
        draw.text((cursor_x, y), span_text, font=font_code,
                  fill=palette_colors.get(category, palette_colors["code"]))
        cursor_x += draw.textlength(span_text, font=font_code)


def fmt(value):
    """Format a cell value for display."""
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


def draw_df(draw, x, y, panel_w, name, df, prev_df, palette_colors, fonts, max_rows=7, max_cols=6, row_status=None):
    """Draw a DataFrame as a grid table with diff highlighting.

    All pixel geometry is scaled by ``u`` (the ratio of the active code size to
    the medium baseline of 17) so the table stays laid out correctly at every
    quality preset. At medium (u == 1.0) every value is identical to the
    original hardcoded constants.
    """
    cell_size = fonts["cell"].size
    u = cell_size / 17.0
    def s(v):  # scale a baseline-17 pixel constant to the current size
        return int(round(v * u))
    row_h = s(26)
    text_dy = s(5)

    columns = list(df.columns)[:max_cols]
    new_columns = set(columns) - set(prev_df.columns) if isinstance(prev_df, pd.DataFrame) else set()
    draw.text(
        (x, y),
        "%s   %d rows x %d cols" % (name, df.shape[0], df.shape[1]),
        font=fonts["title"],
        fill=palette_colors["title"],
    )
    y += row_h
    # column widths
    index_col_w = max(s(24), max((len(str(row_label)) for row_label in list(df.index)[:max_rows]), default=1) * s(9) + s(10))
    col_widths = []
    for column in columns:
        cell_texts = [fmt(cell_value) for cell_value in list(df[column])[:max_rows]]
        widest_chars = max([len(str(column))] + [len(text) for text in cell_texts])
        col_widths.append(min(s(160), widest_chars * s(9) + s(18)))
    # clamp total width to the panel: drop rightmost columns that don't fit
    while col_widths and index_col_w + sum(col_widths) > panel_w - s(8):
        col_widths.pop()
        columns = columns[:-1]
    # header
    header_x = x + index_col_w
    draw.rectangle([x, y, x + index_col_w + sum(col_widths), y + row_h], fill=palette_colors["panel"])
    draw.text((x + s(6), y + text_dy), "idx", font=fonts["header"], fill=palette_colors["muted"])
    for j, column in enumerate(columns):
        if column in new_columns:
            draw.rectangle([header_x, y, header_x + col_widths[j], y + row_h], fill=palette_colors["newbg"])
        draw.text(
            (header_x + s(8), y + text_dy),
            str(column)[:16],
            font=fonts["header"],
            fill=palette_colors["new"] if column in new_columns else palette_colors["head"],
        )
        header_x += col_widths[j]
    y += row_h
    # rows
    visible_rows = list(df.index)[:max_rows]
    for row_pos, row_label in enumerate(visible_rows):
        row_state = row_status.get(row_label) if row_status else None
        if row_state == "kept":
            draw.rectangle([x, y, x + index_col_w + sum(col_widths), y + row_h], fill=palette_colors["newbg"])
        elif row_pos % 2:
            draw.rectangle([x, y, x + index_col_w + sum(col_widths), y + row_h], fill=palette_colors["zebra"])
        draw.text((x + s(6), y + text_dy), str(row_label)[:4], font=fonts["cell"], fill=palette_colors["muted"])
        cell_x = x + index_col_w
        for j, column in enumerate(columns):
            cell_value = df.at[row_label, column]
            is_changed = column in new_columns
            if (
                not is_changed
                and isinstance(prev_df, pd.DataFrame)
                and column in prev_df.columns
                and row_label in prev_df.index
            ):
                try:
                    is_changed = prev_df.at[row_label, column] != cell_value
                except Exception:
                    is_changed = False
            if is_changed and row_state != "dropped":
                draw.rectangle([cell_x, y, cell_x + col_widths[j], y + row_h], fill=palette_colors["newbg"])
            text_color = (
                (96, 100, 120)
                if row_state == "dropped"
                else (palette_colors["new"] if (is_changed or row_state == "kept") else palette_colors["cell"])
            )
            cell_text = fmt(cell_value)[:16]
            draw.text((cell_x + s(8), y + text_dy), cell_text, font=fonts["cell"], fill=text_color)
            if row_state == "dropped":
                text_w = draw.textlength(cell_text, font=fonts["cell"])
                draw.line(
                    [cell_x + s(8), y + row_h // 2, cell_x + s(8) + text_w, y + row_h // 2],
                    fill=(96, 100, 120),
                    width=2,
                )
            cell_x += col_widths[j]
        y += row_h
    # overflow indicator
    if df.shape[0] > max_rows:
        draw.text(
            (x + s(6), y + s(4)),
            "... %d more rows" % (df.shape[0] - max_rows),
            font=fonts["cell"],
            fill=palette_colors["muted"],
        )
        y += s(22)
    return y


def render(step, step_idx, steps, src_lines, dims, palette_colors, fonts):
    """Render a single frame: code panel on left, DataFrame grids on right."""
    width, height, code_panel_w, top = dims
    img = Image.new("RGB", (width, height), palette_colors["bg"])
    draw = ImageDraw.Draw(img)
    # code panel — geometry scaled by u so bigger fonts get proportional room
    code_size = fonts["code"].size
    u = code_size / 17.0
    def s(v):
        return int(round(v * u))
    line_step = s(28)
    hl_h = s(24)
    pad = 24
    draw.rounded_rectangle([pad, pad, pad + code_panel_w, height - pad], 10, fill=palette_colors["panel"])
    draw.text(
        (pad + s(14), pad + s(12)),
        "code  step %d/%d" % (step_idx + 1, len(steps)),
        font=fonts["title"],
        fill=palette_colors["title"],
    )
    line_y = pad + s(42)
    current_line = step["line"]
    for line_idx, line_text in enumerate(src_lines):
        is_current_line = (line_idx + 1) == current_line
        if is_current_line:
            draw.rounded_rectangle(
                [pad + s(8), line_y - 1, pad + code_panel_w - s(10), line_y + hl_h], 5, fill=palette_colors["hl"]
            )
            draw.rectangle([pad + s(8), line_y - 1, pad + s(12), line_y + hl_h], fill=palette_colors["bar"])
        draw.text((pad + s(14), line_y), "%2d" % (line_idx + 1), font=fonts["code"], fill=palette_colors["gutter"])
        draw.text((pad + s(44), line_y), ">" if is_current_line else " ", font=fonts["code"], fill=palette_colors["bar"])
        max_text_w = code_panel_w - s(96)
        clipped_text = line_text
        while clipped_text and draw.textlength(clipped_text, font=fonts["code"]) > max_text_w:
            clipped_text = clipped_text[:-1]
        if clipped_text != line_text and clipped_text:
            clipped_text = clipped_text[:-1] + "…"
        draw_code(draw, pad + s(64), line_y, clipped_text, palette_colors, fonts["code"])
        line_y += line_step
    # right column: up to 3 grids (DataFrame/Series) + a scalar strip
    right_x = pad + code_panel_w + 22
    right_w = width - right_x - pad
    y = pad
    prev_snapshot = steps[step_idx - 1]["dfs"] if step_idx > 0 else {}

    def has_changed(name, value):
        prev_value = prev_snapshot.get(name)
        try:
            if isinstance(value, (pd.DataFrame, pd.Series)):
                return (prev_value is None) or (not value.equals(prev_value))
            return prev_value != value
        except Exception:
            return True

    grids = []
    scalars = []
    for name, value in step["dfs"].items():
        frame_df = as_frame(value)
        if frame_df is not None:
            grids.append((name, frame_df, value))
        else:
            scalars.append((name, value))

    # filter detection: a changed table whose rows are a subset of another table
    row_status = {}
    filter_related = set()
    for name, frame_df, original in grids:
        if not has_changed(name, original):
            continue
        for parent_name, parent_df, parent_original in grids:
            if parent_name == name or parent_df.shape[0] <= frame_df.shape[0]:
                continue
            try:
                if set(frame_df.columns) <= set(parent_df.columns) and set(frame_df.index) < set(parent_df.index):
                    kept_labels = set(frame_df.index)
                    row_status[parent_name] = {
                        row_label: ("kept" if row_label in kept_labels else "dropped") for row_label in parent_df.index
                    }
                    filter_related.update({name, parent_name})
                    break
            except TypeError:
                continue

    def display_priority(grid_entry):
        name, frame_df, original = grid_entry
        if name in filter_related:
            return 0
        return 1 if has_changed(name, original) else 2

    grid_gap = int(round(16 * (fonts["code"].size / 17.0)))
    grids.sort(key=display_priority)
    for name, frame_df, original in grids[:3]:
        prev_frame_df = as_frame(prev_snapshot.get(name))
        y = draw_df(draw, right_x, y, right_w, name, frame_df, prev_frame_df, palette_colors, fonts, max_rows=6, row_status=row_status.get(name)) + grid_gap
    if row_status:
        parent_name = next(iter(row_status))
        kept_count = sum(1 for state in row_status[parent_name].values() if state == "kept")
        draw.text(
            (right_x, min(y, height - pad - 24)),
            "filter kept %d of %d rows" % (kept_count, len(row_status[parent_name])),
            font=fonts["caption"],
            fill=palette_colors["cap"],
        )
        y += 24
    if scalars:
        scalars_y = min(y, height - pad - 24)
        scalar_parts = []
        for name, value in scalars[:6]:
            scalar_parts.append("%s=%s" % (name, fmt(value)))
        draw.text((right_x, scalars_y), "scalars:  " + "   ".join(scalar_parts), font=fonts["caption"], fill=palette_colors["cap"])

    # Show error on final frame if present
    if step.get("error"):
        error_y = min(y + 8, height - pad - 24)
        draw.text((right_x, error_y), ("! " + step["error"])[:60], font=fonts["caption"], fill=(243, 139, 168))

    return img


# --------------------------------------------------------------------------
# STAGE 3 — ENCODE (build frames + GIF)
# --------------------------------------------------------------------------
def build_frames(source, ms=1100, code_size=17, scale=1.0, palette="dark"):
    ms = max(MS_MIN, min(MS_MAX, ms))
    palette_colors = get_palette(palette)
    src_lines = source.splitlines()
    steps = trace(source)
    fonts = load_fonts(code_size)
    # All layout constants are baseline-17 values scaled by u, matching the
    # per-frame geometry in render()/draw_df() so nothing clips or overlaps at
    # non-medium quality presets.
    u = code_size / 17.0
    def s(v):
        return int(round(v * u))
    line_step = s(28)
    row_h = s(26)
    grid_gap = s(16)
    probe_img = Image.new("RGB", (8, 8))
    probe_draw = ImageDraw.Draw(probe_img)
    char_w = probe_draw.textlength("m", font=fonts["code"])
    longest_line_len = max((len(line) for line in src_lines), default=20)
    code_panel_w = int(min(max(s(96) + longest_line_len * char_w + s(20), s(380)), s(640)))
    right_w = s(480)
    width = s(24) + code_panel_w + s(22) + right_w + s(24)

    def grid_height(frame_df):
        return row_h + row_h + min(frame_df.shape[0], 6) * row_h + (s(22) if frame_df.shape[0] > 6 else 0)

    max_right_h = 0
    for step in steps:
        step_frames = sorted(
            [as_frame(value) for value in step["dfs"].values() if as_frame(value) is not None],
            key=lambda frame_df: -grid_height(frame_df),
        )[:3]
        right_h = sum(grid_height(frame_df) + grid_gap for frame_df in step_frames)
        if any(as_frame(value) is None for value in step["dfs"].values()):
            right_h += s(28)
        max_right_h = max(max_right_h, right_h)
    top = 24
    height = min(max(s(24) * 2 + s(42) + len(src_lines) * line_step, max_right_h + s(96), s(380)), s(960))

    frames = []
    durations = []
    for i, step in enumerate(steps):
        frame = render(step, i, steps, src_lines, (width, height, code_panel_w, top), palette_colors, fonts)
        if scale != 1.0:
            new_w = max(1, int(frame.width * scale))
            new_h = max(1, int(frame.height * scale))
            frame = frame.resize((new_w, new_h), Image.LANCZOS)
        frames.append(frame)
        durations.append(int(ms * 2.4) if step.get("final") else ms)
    return frames, durations


def encode_gif(frames, durations):
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        optimize=True,
    )
    return buffer.getvalue()


def build_gif_bytes(source, ms=1100, code_size=17, scale=1.0, palette="dark"):
    frames, durations = build_frames(source, ms=ms, code_size=code_size, scale=scale, palette=palette)
    return encode_gif(frames, durations)


# Serverless responses are size-capped (~4.5MB on Vercel), so when the
# per-frame payload would blow past this, send only the animated GIF and let
# the frontend fall back to a plain <img> without the stepper.
FRAMES_BYTES_LIMIT = 2_500_000


def build_json_payload(source, ms=1100, code_size=17, scale=1.0, palette="dark"):
    frames, durations = build_frames(source, ms=ms, code_size=code_size, scale=scale, palette=palette)
    gif_bytes = encode_gif(frames, durations)

    frames_b64, total_bytes = [], 0
    for frame in frames:
        frame_buffer = io.BytesIO()
        frame.save(frame_buffer, format="GIF", optimize=True)
        frame_bytes = frame_buffer.getvalue()
        total_bytes += len(frame_bytes)
        if total_bytes > FRAMES_BYTES_LIMIT:
            frames_b64 = None
            break
        frames_b64.append(base64.b64encode(frame_bytes).decode())

    return {"gif": base64.b64encode(gif_bytes).decode(), "frames": frames_b64, "durations": durations}


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


def _generate_or_error(start_response, code, ms, output_format="gif", palette="dark", quality="medium"):
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

    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["medium"])
    code_size = preset["code_size"]
    scale = preset["scale"]

    try:
        if output_format == "json":
            payload = build_json_payload(code, ms=int(ms), code_size=code_size, scale=scale, palette=palette)
        else:
            gif_bytes = build_gif_bytes(code, ms=int(ms), code_size=code_size, scale=scale, palette=palette)
    except (UnsafeCodeError, ExecutionTimeout) as e:
        return _json_response(start_response, 400, {"error": str(e)})
    except Exception as e:
        return _json_response(
            start_response, 500, {"error": f"{type(e).__name__}: {e}"}
        )

    if output_format == "json":
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
        query_params = parse_qs(environ.get("QUERY_STRING") or "")
        if "c" in query_params:
            try:
                code_b64 = query_params["c"][0]
                code = base64.urlsafe_b64decode(
                    code_b64 + "=" * (-len(code_b64) % 4)
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
                ms = int(query_params.get("ms", ["1100"])[0])
            except ValueError:
                ms = 1100
            palette = query_params.get("pal", ["dark"])[0]
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
        content_length = int(environ.get("CONTENT_LENGTH") or 0)
        raw_body = environ["wsgi.input"].read(content_length) if content_length else b"{}"
        payload = json.loads(raw_body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return _json_response(
            start_response, 400, {"error": "invalid JSON body"}
        )

    output_format = "json" if payload.get("format") == "json" else "gif"
    return _generate_or_error(
        start_response, payload.get("code", ""), payload.get("ms", 1100), output_format=output_format,
        palette=payload.get("palette", "dark"),
        quality=payload.get("quality", "medium"),
    )
