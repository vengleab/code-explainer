"""
backend/generate.py — Vercel Python service (WSGI entrypoint: generate:app).

POST { "code": "<python source>", "ms": 900 } -> image/gif bytes.

This mirrors the trace/render pipeline in codegif.py (kept at the repo root
as a standalone CLI) but adapted to run inside a stateless function:
  - writes the GIF to an in-memory buffer instead of disk
  - loads bundled fonts instead of scanning the local filesystem
  - bounds untrusted, user-submitted code with a step cap, a wall-clock
    timeout, and a restricted exec() environment (see SAFETY NOTE below)

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
import time
import types
from urllib.parse import parse_qs

try:  # package import in dev (imported as backend.generate)
    from .theme import get_palette
    from .pysyntax import iter_tokens
except ImportError:  # top-level module on the serverless runtime
    from theme import get_palette
    from pysyntax import iter_tokens

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# Limits
#
# No signal.alarm()/SIGALRM here: it only fires on the main thread, and
# whether Vercel's Python runtime invokes this handler on the main thread
# isn't something to rely on. Instead, TRACE_TIMEOUT_SECONDS is checked
# from inside the per-line trace callback (thread-safe, plain time.monotonic
# comparisons), and MAX_STEPS bounds the render phase indirectly by capping
# frame count. The platform's own `maxDuration` (vercel.json) is the backstop
# for anything neither of those catches (e.g. one pathologically slow line).
# --------------------------------------------------------------------------
MAX_CODE_LEN = 4000
MAX_STEPS = 200
TRACE_TIMEOUT_SECONDS = 5
MS_MIN, MS_MAX = 200, 2000

ALLOWED_IMPORTS = {
    "math", "random", "string", "itertools", "functools", "collections",
    "datetime", "re", "json", "statistics", "decimal", "fractions",
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
# STAGE 1 — TRACE (adapted from codegif.py)
# --------------------------------------------------------------------------
def find_for_loops(source):
    loops = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return loops
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) \
           and isinstance(node.iter, ast.Name):
            last_line = max((getattr(child, "lineno", node.lineno) for child in ast.walk(node)),
                            default=node.lineno)
            loops.append(dict(header=node.lineno, start=node.lineno, end=last_line,
                              target=node.target.id, iterable=node.iter.id))
    return loops


def showable(value):
    if isinstance(value, (types.ModuleType, types.FunctionType, types.BuiltinFunctionType,
                          type, types.MethodType)):
        return False
    return True


def snapshot_vars(frame):
    snapshot = {}
    for name, value in frame.f_locals.items():
        if name.startswith("__") or not showable(value):
            continue
        try:
            snapshot[name] = copy.deepcopy(value)
        except Exception:
            snapshot[name] = repr(value)
    return snapshot


def trace(source):
    check_safe(source)
    compiled = compile(source, "<snippet>", "exec")
    stdout_buffer = io.StringIO()
    steps = []
    final_vars = {}
    start_time = time.monotonic()

    def tracer(frame, event, arg):
        nonlocal final_vars
        if frame.f_code.co_filename != "<snippet>":
            return tracer
        if event == "line":
            if len(steps) >= MAX_STEPS:
                raise StepLimitExceeded(f"step limit ({MAX_STEPS}) reached")
            if time.monotonic() - start_time > TRACE_TIMEOUT_SECONDS:
                raise ExecutionTimeout(f"tracing exceeded {TRACE_TIMEOUT_SECONDS}s")
            steps.append(dict(line=frame.f_lineno,
                              vars=snapshot_vars(frame),
                              stdout=stdout_buffer.getvalue()))
        elif event == "return":
            # Fires once the traced frame finishes (including via an
            # unwinding exception) — this is the only point where the
            # effect of the *last* executed line is observable, since the
            # "line" event above snapshots vars *before* each line runs.
            final_vars = snapshot_vars(frame)
        return tracer

    import sys
    real_stdout = sys.stdout
    sys.stdout = stdout_buffer
    sys.settrace(tracer)

    error_message = None
    try:
        exec(compiled, make_restricted_globals())
    except (StepLimitExceeded, ExecutionTimeout) as e:
        error_message = str(e)
    except Exception as e:
        error_message = f"{type(e).__name__}: {e}"
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout

    steps.append(dict(line=None, vars=(final_vars or (steps[-1]["vars"] if steps else {})),
                      stdout=stdout_buffer.getvalue(), final=True, error=error_message))
    return steps


def fix_loop_headers(steps, loops):
    """Correct the one-iteration lag on `for`-header steps.

    The tracer's "line" event snapshots locals *before* the line's bytecode
    runs (see the comment in trace()). For a `for` header that bytecode is the
    FOR_ITER that advances the iterator and binds the loop variable, so the
    header snapshot shows the *previous* iteration's binding (or no binding, on
    first entry) — e.g. "running line 3" the 2nd time still shows fruit='apple'
    when the iteration it initiates is 'banana'.

    For each header step we:
      - set the loop target to the value the following body step runs with
        (the post-FOR_ITER binding), so the variables panel is correct, and
      - record an explicit current index in step["loop_idx"] keyed by iterable.
        The index is positional (robust to duplicate elements, unlike
        list.index) and equals len(seq) on the terminating pass — where the
        iterator is exhausted and the loop exits — so the list renders as fully
        done with nothing marked current.

    Only loops in `loops` are touched (Name-target/Name-iterable `for` loops);
    while-loops, `for i in range(...)`, and loop-free code are left untouched.
    """
    if not loops:
        return steps
    loops_by_header_line = {}
    for loop in loops:
        loops_by_header_line.setdefault(loop["header"], []).append(loop)
    iteration_count = {}
    prev_line = None
    for i, step in enumerate(steps):
        line = step["line"]
        for loop in loops_by_header_line.get(line, []):
            is_re_entry = prev_line is not None and loop["start"] <= prev_line <= loop["end"]
            iteration_idx = iteration_count.get(loop["header"], -1) + 1 if is_re_entry else 0
            iteration_count[loop["header"]] = iteration_idx
            step.setdefault("loop_idx", {})[loop["iterable"]] = iteration_idx
            next_step = steps[i + 1] if i + 1 < len(steps) else None
            next_in_body = (next_step and next_step["line"] is not None
                            and loop["start"] <= next_step["line"] <= loop["end"])
            if next_in_body and loop["target"] in next_step["vars"]:
                step["vars"][loop["target"]] = copy.deepcopy(next_step["vars"][loop["target"]])
        prev_line = line
    return steps


# --------------------------------------------------------------------------
# STAGE 2 — RENDER (identical layout/logic to codegif.py, bundled fonts)
# --------------------------------------------------------------------------
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
MONO = os.path.join(FONT_DIR, "RobotoMono-Regular.ttf")
MONO_B = os.path.join(FONT_DIR, "RobotoMono-Bold.ttf")

# Color palettes live in theme.py (shared with generate_pandas.py + the
# frontend). A palette dict `palette_colors` is threaded through render() per
# request so the "dark"/"light" toggle in the UI matches the exported GIF.
# Token classification lives in pysyntax.iter_tokens (shared with
# generate_pandas.py).


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def load_fonts(code_size):
    return dict(code=_font(MONO, code_size), kw=_font(MONO, code_size),
                title=_font(MONO_B, 28), pill=_font(MONO_B, 30),
                var=_font(MONO, 32), tag=_font(MONO, 26),
                out=_font(MONO, 30), lst=_font(MONO, 32))


def draw_code_line(draw, x, y, text, fonts, palette_colors):
    # Draw each token span in its palette color (VSCode Monokai / JupyterLab).
    # Classification is shared with the pandas renderer and the frontend editor.
    cursor_x = x
    for span_text, category in iter_tokens(text):
        draw.text((cursor_x, y), span_text, font=fonts["code"],
                  fill=palette_colors.get(category, palette_colors["code"]))
        cursor_x += draw.textlength(span_text, font=fonts["code"])


def active_loop(line, loops):
    candidates = [loop for loop in loops if line is not None and loop["start"] <= line <= loop["end"]]
    return min(candidates, key=lambda loop: loop["end"]-loop["start"]) if candidates else None


def render(step, step_idx, steps, src_lines, loops, dims, fonts, palette_colors):
    width, height, code_panel_w, pad, panel_top, line_height, max_var_rows = dims
    img = Image.new("RGB",(width,height),palette_colors["bg"]); draw = ImageDraw.Draw(img)
    pad = dims[3]

    draw.text((pad, pad-8), "execution order", font=fonts["title"], fill=palette_colors["title"])
    executed_labels = [str(steps[k]["line"]) for k in range(step_idx+1) if steps[k]["line"] is not None]
    visible_labels = executed_labels[-10:]; has_overflow = len(executed_labels) > 10
    pill_x, pill_y = pad, pad+44
    if has_overflow:
        draw.text((pill_x, pill_y+8), "...", font=fonts["var"], fill=palette_colors["muted"]); pill_x += 56
    for j, label in enumerate(visible_labels):
        is_current_pill = (not step.get("final")) and j == len(visible_labels)-1
        pill_text = "L"+label; pill_w = draw.textlength(pill_text, font=fonts["pill"])+36
        draw.rounded_rectangle([pill_x,pill_y,pill_x+pill_w,pill_y+56],12, fill=palette_colors["pill_cur"] if is_current_pill else palette_colors["pill_bg"])
        draw.text((pill_x+18,pill_y+10),pill_text,font=fonts["pill"],fill=palette_colors["pill_cur_tx"] if is_current_pill else palette_colors["pill_tx"])
        pill_x += pill_w+12
        if j < len(visible_labels)-1:
            draw.text((pill_x,pill_y+8),">",font=fonts["var"],fill=palette_colors["muted"]); pill_x += 32

    code_x = pad
    draw.rounded_rectangle([code_x, panel_top, code_x+code_panel_w, height-pad], 20, fill=palette_colors["panel"])
    phase_text = "finished" if step.get("final") else "running line %s" % step["line"]
    draw.text((code_x+32, panel_top+24), "code   step %d/%d  %s" % (step_idx+1, len(steps), phase_text),
              font=fonts["title"], fill=palette_colors["title"])
    current_line = step["line"]
    total_lines = len(src_lines)
    if total_lines <= 20 or current_line is None:
        visible_start, visible_end = 0, total_lines
    else:
        visible_start = max(0, current_line-1-9); visible_end = min(total_lines, visible_start+20); visible_start = max(0, visible_end-20)
    line_y = panel_top+88
    for line_idx in range(visible_start, visible_end):
        is_current_line = (line_idx+1) == current_line
        if is_current_line:
            draw.rounded_rectangle([code_x+16, line_y-2, code_x+code_panel_w-20, line_y+line_height-5], 10, fill=palette_colors["hl"])
            draw.rectangle([code_x+16, line_y-2, code_x+24, line_y+line_height-5], fill=palette_colors["bar"])
        draw.text((code_x+32, line_y), "%3d"%(line_idx+1), font=fonts["code"], fill=palette_colors["gutter"])
        draw.text((code_x+104, line_y), ">" if is_current_line else " ", font=fonts["code"], fill=palette_colors["bar"])
        line_text = src_lines[line_idx]
        max_chars = int((code_panel_w-180)/draw.textlength("m", font=fonts["code"]))
        if len(line_text) > max_chars: line_text = line_text[:max_chars-1]+"…"
        draw_code_line(draw, code_x+140, line_y, line_text, fonts, palette_colors)
        line_y += line_height

    right_x = code_x+code_panel_w+44; right_w = width-right_x-pad

    prev_vars = steps[step_idx-1]["vars"] if step_idx>0 else {}
    vars_panel_h = 80 + max(1, max_var_rows)*56
    draw.rounded_rectangle([right_x, panel_top, right_x+right_w, panel_top+vars_panel_h], 20, fill=palette_colors["panel"])
    draw.text((right_x+32, panel_top+24), "variables  (changed in green)", font=fonts["title"], fill=palette_colors["title"])
    var_y = panel_top+80
    var_items = list(step["vars"].items())[:10]
    if not var_items:
        draw.text((right_x+40, var_y), "(none yet)", font=fonts["var"], fill=palette_colors["muted"])
    for name, value in var_items:
        draw.text((right_x+40, var_y), name, font=fonts["var"], fill=palette_colors["name"])
        text_x = right_x+40+draw.textlength(name+" ", font=fonts["var"])
        draw.text((text_x, var_y), "= ", font=fonts["var"], fill=palette_colors["muted"]); text_x += draw.textlength("= ", font=fonts["var"])
        value_text = repr(value); available_w = (right_x+right_w-36)-text_x
        while draw.textlength(value_text, font=fonts["var"])>available_w and len(value_text)>4:
            value_text = value_text[:-4]+"..."
        is_changed = prev_vars.get(name, "\0__missing__") != value
        draw.text((text_x, var_y), value_text, font=fonts["var"], fill=palette_colors["changed"] if is_changed else palette_colors["val"])
        var_y += 56

    y_cursor = panel_top+vars_panel_h+28
    current_loop = active_loop(current_line, loops)
    has_list = False; sequence=None; current_idx=None
    if current_loop:
        sequence = step["vars"].get(current_loop["iterable"])
        if isinstance(sequence, (list, tuple)):
            has_list = True
            # On `for`-header steps fix_loop_headers() has recorded the true
            # positional index (and len(seq) once the iterator is exhausted);
            # prefer it. On body steps fall back to locating the loop var.
            forced_idx = step.get("loop_idx", {}).get(current_loop["iterable"])
            if forced_idx is not None:
                current_idx = forced_idx
            else:
                target_value = step["vars"].get(current_loop["target"])
                try: current_idx = list(sequence).index(target_value)
                except (ValueError, TypeError): current_idx = -1
    if loops:
        visible_items = sequence if has_list else (step["vars"].get(loops[0]["iterable"]) if isinstance(step["vars"].get(loops[0]["iterable"]),(list,tuple)) else [])
        visible_items = list(visible_items)[:8]
        iterable_name = current_loop["iterable"] if current_loop else loops[0]["iterable"]
        loop_panel_h = 80 + max(1,len(visible_items))*76
        draw.rounded_rectangle([right_x, y_cursor, right_x+right_w, y_cursor+loop_panel_h], 20, fill=palette_colors["panel"])
        draw.text((right_x+32, y_cursor+24), "list %s  done/current/waiting" % iterable_name, font=fonts["title"], fill=palette_colors["title"])
        list_top = y_cursor+80
        for pos, item in enumerate(visible_items):
            item_y = list_top + pos*76; label = "[%d] %r" % (pos, item)
            if current_idx is not None and current_idx >= 0 and pos < current_idx:
                draw.rounded_rectangle([right_x+28,item_y,right_x+right_w-28,item_y+64],16, fill=palette_colors["done_bg"])
                draw.text((right_x+52,item_y+14),label,font=fonts["lst"],fill=palette_colors["done_tx"])
                label_w=draw.textlength(label,font=fonts["lst"]); draw.line([right_x+52,item_y+34,right_x+52+label_w,item_y+34],fill=palette_colors["done_tx"],width=3)
                draw.text((right_x+right_w-128,item_y+18),"done",font=fonts["tag"],fill=palette_colors["done_tx"])
            elif current_idx is not None and pos == current_idx:
                draw.rounded_rectangle([right_x+28,item_y,right_x+right_w-28,item_y+64],16, fill=palette_colors["cur_bg"], outline=palette_colors["cur_bd"], width=3)
                draw.text((right_x+52,item_y+14),label,font=fonts["lst"],fill=palette_colors["cur_tx"])
                draw.text((right_x+right_w-208,item_y+18),"<- current",font=fonts["tag"],fill=palette_colors["cur_tx"])
            else:
                for dash_x in range(right_x+28, int(right_x+right_w-28), 24):
                    draw.line([dash_x,item_y,min(dash_x+12,right_x+right_w-28),item_y],fill=palette_colors["muted"],width=1)
                    draw.line([dash_x,item_y+64,min(dash_x+12,right_x+right_w-28),item_y+64],fill=palette_colors["muted"],width=1)
                draw.line([right_x+28,item_y,right_x+28,item_y+64],fill=palette_colors["muted"],width=1)
                draw.line([right_x+right_w-28,item_y,right_x+right_w-28,item_y+64],fill=palette_colors["muted"],width=1)
                draw.text((right_x+52,item_y+14),label,font=fonts["lst"],fill=palette_colors["wait_tx"])
                draw.text((right_x+right_w-148,item_y+18),"waiting",font=fonts["tag"],fill=palette_colors["muted"])
        y_cursor += loop_panel_h+28

    draw.rounded_rectangle([right_x, y_cursor, right_x+right_w, height-pad], 20, fill=palette_colors["console"])
    draw.text((right_x+32, y_cursor+24), "printed output", font=fonts["title"], fill=palette_colors["title"])
    output_y = y_cursor+80
    max_output_lines = int((height-pad - output_y)/44)
    for output_line in step["stdout"].splitlines()[-max_output_lines:]:
        draw.text((right_x+40, output_y), output_line[:48], font=fonts["out"], fill=palette_colors["out"]); output_y += 44
    if step.get("error"):
        draw.text((right_x+40, output_y), ("! "+step["error"])[:48], font=fonts["out"], fill=palette_colors["err"])
    return img


# --------------------------------------------------------------------------
# STAGE 3 — ENCODE
# --------------------------------------------------------------------------
def build_frames(source, ms=900, code_size=34, palette="dark"):
    ms = max(MS_MIN, min(MS_MAX, ms))
    palette_colors = get_palette(palette)
    src_lines = source.splitlines()
    loops = find_for_loops(source)
    steps = fix_loop_headers(trace(source), loops)
    fonts = load_fonts(code_size)
    probe_img = Image.new("RGB",(10,10)); probe_draw = ImageDraw.Draw(probe_img)

    max_var_rows = max((len(list(step["vars"].items())[:10]) for step in steps), default=1)
    longest_line_len = max((len(line) for line in src_lines), default=20)
    code_panel_w = min(140 + int(longest_line_len*probe_draw.textlength("m", font=fonts["code"])) + 60, 1240)
    code_panel_w = max(code_panel_w, 720)
    code_panel_h = 88 + min(len(src_lines),20)*(code_size+22) + 32

    vars_panel_h = 80 + max(1,max_var_rows)*56
    if loops:
        max_items = min(max((len(step["vars"].get(loops[0]["iterable"], [])) if isinstance(step["vars"].get(loops[0]["iterable"]),(list,tuple)) else 0) for step in steps) or len(loops), 8)
        for loop in loops:
            for step in steps:
                value = step["vars"].get(loop["iterable"])
                if isinstance(value,(list,tuple)): max_items=max(max_items,min(len(value),8))
        loop_panel_h = 80 + max(1,max_items)*76 + 28
    else:
        loop_panel_h = 0
    right_column_h = vars_panel_h + 28 + loop_panel_h + 300
    panel_top = 48+148
    body_h = max(code_panel_h, right_column_h)
    width = code_panel_w + 44 + 880 + 48*2
    height = panel_top + body_h + 48

    line_height = code_size + 22
    dims = (width, height, code_panel_w, 48, panel_top, line_height, max_var_rows)

    frames, durations = [], []
    for i, step in enumerate(steps):
        frames.append(render(step, i, steps, src_lines, loops, dims, fonts, palette_colors))
        durations.append(int(ms*2.6) if step.get("final") else ms)
    return frames, durations


def encode_gif(frames, durations):
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, disposal=2, optimize=True)
    return buffer.getvalue()


def build_gif_bytes(source, ms=900, code_size=34, palette="dark"):
    frames, durations = build_frames(source, ms=ms, code_size=code_size, palette=palette)
    return encode_gif(frames, durations)


# Serverless responses are size-capped (~4.5MB on Vercel), so when the
# per-frame payload would blow past this, send only the animated GIF and let
# the frontend fall back to a plain <img> without the stepper.
FRAMES_BYTES_LIMIT = 2_500_000


def build_json_payload(source, ms=900, code_size=34, palette="dark"):
    frames, durations = build_frames(source, ms=ms, code_size=code_size, palette=palette)
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

    return {"gif": base64.b64encode(gif_bytes).decode(),
            "frames": frames_b64,
            "durations": durations}


# --------------------------------------------------------------------------
# Vercel entrypoint (WSGI)
#
# This is the "backend" service (see vercel.json, entrypoint "generate:app");
# the top-level rewrite "/api/(.*)" routes here, so this only ever needs to
# handle /api/generate. The static frontend is a separate "frontend" service.
# --------------------------------------------------------------------------
STATUS_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found",
                  405: "Method Not Allowed", 500: "Internal Server Error"}


def _status_line(code):
    return f"{code} {STATUS_REASONS.get(code, 'Error')}"


def _json_response(start_response, status, payload):
    body = json.dumps(payload).encode("utf-8")
    start_response(_status_line(status), [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ])
    return [body]


def _gif_response(start_response, gif_bytes):
    start_response(_status_line(200), [
        ("Content-Type", "image/gif"),
        ("Content-Length", str(len(gif_bytes))),
        # Let external fetchers (e.g. Google Slides' image proxy, which
        # re-requests the URL when a deck is opened) cache the result
        # instead of re-running the trace every time.
        ("Cache-Control", "public, max-age=86400"),
    ])
    return [gif_bytes]


def _generate_or_error(start_response, code, ms, output_format="gif", palette="dark"):
    if not isinstance(code, str) or not code.strip():
        return _json_response(start_response, 400, {"error": "'code' must be a non-empty string"})
    if len(code) > MAX_CODE_LEN:
        return _json_response(start_response, 400, {"error": f"code too long (max {MAX_CODE_LEN} characters)"})
    if not isinstance(ms, (int, float)):
        ms = 900

    try:
        if output_format == "json":
            payload = build_json_payload(code, ms=int(ms), palette=palette)
        else:
            gif_bytes = build_gif_bytes(code, ms=int(ms), palette=palette)
    except (UnsafeCodeError, ExecutionTimeout) as e:
        return _json_response(start_response, 400, {"error": str(e)})
    except Exception as e:
        return _json_response(start_response, 500, {"error": f"{type(e).__name__}: {e}"})

    if output_format == "json":
        return _json_response(start_response, 200, payload)
    return _gif_response(start_response, gif_bytes)


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = (environ.get("PATH_INFO") or "/").split("?")[0]

    if path != "/api/generate":
        return _json_response(start_response, 404, {"error": "not found"})

    if method == "GET":
        # GET with ?c=<base64url(code)>[&ms=N] returns the GIF directly.
        # This gives every snippet a shareable URL that external services
        # (Google Slides "Insert image by URL", chat apps, etc.) can fetch —
        # they only keep GIF animation when they download the file themselves.
        query_params = parse_qs(environ.get("QUERY_STRING") or "")
        if "c" in query_params:
            try:
                code_b64 = query_params["c"][0]
                code = base64.urlsafe_b64decode(code_b64 + "=" * (-len(code_b64) % 4)).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return _json_response(start_response, 400, {"error": "invalid 'c' parameter (expected base64url-encoded UTF-8 code)"})
            try:
                ms = int(query_params.get("ms", ["900"])[0])
            except ValueError:
                ms = 900
            palette = query_params.get("pal", ["dark"])[0]
            return _generate_or_error(start_response, code, ms, palette=palette)
        return _json_response(start_response, 200, {
            "ok": True,
            "usage": "POST {code, ms, palette} -> image/gif, or GET ?c=<base64url(code)>&ms=N&pal=dark|light -> image/gif",
        })

    if method != "POST":
        return _json_response(start_response, 405, {"error": "method not allowed"})

    try:
        content_length = int(environ.get("CONTENT_LENGTH") or 0)
        raw_body = environ["wsgi.input"].read(content_length) if content_length else b"{}"
        payload = json.loads(raw_body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return _json_response(start_response, 400, {"error": "invalid JSON body"})

    output_format = "json" if payload.get("format") == "json" else "gif"
    return _generate_or_error(start_response, payload.get("code", ""), payload.get("ms", 900),
                              output_format=output_format, palette=payload.get("palette", "dark"))
