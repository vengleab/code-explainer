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
except ImportError:  # top-level module on the serverless runtime
    from theme import get_palette

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
    safe = {name: getattr(_builtins, name) for name in SAFE_BUILTIN_NAMES if hasattr(_builtins, name)}
    safe["__import__"] = _safe_import
    return {"__builtins__": safe, "__name__": "__snippet__"}


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
            last = max((getattr(n, "lineno", node.lineno) for n in ast.walk(node)),
                       default=node.lineno)
            loops.append(dict(header=node.lineno, start=node.lineno, end=last,
                              target=node.target.id, iterable=node.iter.id))
    return loops


def showable(v):
    if isinstance(v, (types.ModuleType, types.FunctionType, types.BuiltinFunctionType,
                      type, types.MethodType)):
        return False
    return True


def snapshot_vars(frame):
    out = {}
    for k, v in frame.f_locals.items():
        if k.startswith("__") or not showable(v):
            continue
        try:
            out[k] = copy.deepcopy(v)
        except Exception:
            out[k] = repr(v)
    return out


def trace(source):
    check_safe(source)
    code = compile(source, "<snippet>", "exec")
    buf = io.StringIO()
    steps = []
    final_vars = {}
    start = time.monotonic()

    def tracer(frame, event, arg):
        nonlocal final_vars
        if frame.f_code.co_filename != "<snippet>":
            return tracer
        if event == "line":
            if len(steps) >= MAX_STEPS:
                raise StepLimitExceeded(f"step limit ({MAX_STEPS}) reached")
            if time.monotonic() - start > TRACE_TIMEOUT_SECONDS:
                raise ExecutionTimeout(f"tracing exceeded {TRACE_TIMEOUT_SECONDS}s")
            steps.append(dict(line=frame.f_lineno,
                              vars=snapshot_vars(frame),
                              stdout=buf.getvalue()))
        elif event == "return":
            # Fires once the traced frame finishes (including via an
            # unwinding exception) — this is the only point where the
            # effect of the *last* executed line is observable, since the
            # "line" event above snapshots vars *before* each line runs.
            final_vars = snapshot_vars(frame)
        return tracer

    import sys
    real_stdout = sys.stdout
    sys.stdout = buf
    sys.settrace(tracer)

    err = None
    try:
        exec(code, make_restricted_globals())
    except (StepLimitExceeded, ExecutionTimeout) as e:
        err = str(e)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout

    steps.append(dict(line=None, vars=(final_vars or (steps[-1]["vars"] if steps else {})),
                      stdout=buf.getvalue(), final=True, error=err))
    return steps


# --------------------------------------------------------------------------
# STAGE 2 — RENDER (identical layout/logic to codegif.py, bundled fonts)
# --------------------------------------------------------------------------
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
MONO = os.path.join(FONT_DIR, "RobotoMono-Regular.ttf")
MONO_B = os.path.join(FONT_DIR, "RobotoMono-Bold.ttf")

# Color palettes live in theme.py (shared with generate_pandas.py + the
# frontend). A palette dict `pal` is threaded through render() per request so
# the "dark"/"light" toggle in the UI matches the exported GIF.
KEYWORDS = {"for","in","while","if","else","elif","def","return","print","import",
            "from","and","or","not","True","False","None","class","with","as","try",
            "except","break","continue","range","len","yield"}


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def load_fonts(cs):
    return dict(code=_font(MONO, cs), kw=_font(MONO, cs),
                title=_font(MONO_B, 28), pill=_font(MONO_B, 30),
                var=_font(MONO, 32), tag=_font(MONO, 26),
                out=_font(MONO, 30), lst=_font(MONO, 32))


def draw_code_line(d, x, y, text, f, pal):
    cx, tok, ins, sc, i = x, "", False, "", 0
    def flush(col):
        nonlocal cx, tok
        if tok:
            d.text((cx, y), tok, font=f["code"], fill=col); cx += d.textlength(tok, font=f["code"]); tok=""
    while i < len(text):
        ch = text[i]
        if ins:
            tok += ch
            if ch == sc:
                d.text((cx,y),tok,font=f["code"],fill=pal["s"]); cx+=d.textlength(tok,font=f["code"]); tok=""; ins=False
            i+=1; continue
        if ch in ("'",'"'):
            flush(pal["kw"] if tok in KEYWORDS else pal["code"]); ins=True; sc=ch; tok=ch; i+=1; continue
        if ch.isalnum() or ch=="_":
            tok+=ch
        else:
            flush(pal["kw"] if tok in KEYWORDS else pal["code"])
            d.text((cx,y),ch,font=f["code"],fill=pal["code"]); cx+=d.textlength(ch,font=f["code"])
        i+=1
    flush(pal["kw"] if tok in KEYWORDS else pal["code"])


def active_loop(line, loops):
    cand = [lp for lp in loops if line is not None and lp["start"] <= line <= lp["end"]]
    return min(cand, key=lambda lp: lp["end"]-lp["start"]) if cand else None


def render(step, i, steps, src_lines, loops, dims, f, pal):
    W, H, code_w, PAD, top, lh, n_vars_max = dims
    img = Image.new("RGB",(W,H),pal["bg"]); d = ImageDraw.Draw(img)
    PAD = dims[3]

    d.text((PAD, PAD-8), "execution order", font=f["title"], fill=pal["title"])
    labels = [str(steps[k]["line"]) for k in range(i+1) if steps[k]["line"] is not None]
    win = labels[-10:]; trunc = len(labels) > 10
    tx, ty = PAD, PAD+44
    if trunc:
        d.text((tx, ty+8), "...", font=f["var"], fill=pal["muted"]); tx += 56
    for j, lab in enumerate(win):
        cur = (not step.get("final")) and j == len(win)-1
        txt = "L"+lab; w = d.textlength(txt, font=f["pill"])+36
        d.rounded_rectangle([tx,ty,tx+w,ty+56],12, fill=pal["pill_cur"] if cur else pal["pill_bg"])
        d.text((tx+18,ty+10),txt,font=f["pill"],fill=pal["pill_cur_tx"] if cur else pal["pill_tx"])
        tx += w+12
        if j < len(win)-1:
            d.text((tx,ty+8),">",font=f["var"],fill=pal["muted"]); tx += 32

    cx0 = PAD
    d.rounded_rectangle([cx0, top, cx0+code_w, H-PAD], 20, fill=pal["panel"])
    phase = "finished" if step.get("final") else "running line %s" % step["line"]
    d.text((cx0+32, top+24), "code   step %d/%d  %s" % (i+1, len(steps), phase),
           font=f["title"], fill=pal["title"])
    cur = step["line"]
    n = len(src_lines)
    if n <= 20 or cur is None:
        lo, hi = 0, n
    else:
        lo = max(0, cur-1-9); hi = min(n, lo+20); lo = max(0, hi-20)
    ly = top+88
    for idx in range(lo, hi):
        on = (idx+1) == cur
        if on:
            d.rounded_rectangle([cx0+16, ly-2, cx0+code_w-20, ly+lh-5], 10, fill=pal["hl"])
            d.rectangle([cx0+16, ly-2, cx0+24, ly+lh-5], fill=pal["bar"])
        d.text((cx0+32, ly), "%3d"%(idx+1), font=f["code"], fill=pal["gutter"])
        d.text((cx0+104, ly), ">" if on else " ", font=f["code"], fill=pal["bar"])
        line = src_lines[idx]
        maxc = int((code_w-180)/d.textlength("m", font=f["code"]))
        if len(line) > maxc: line = line[:maxc-1]+"…"
        draw_code_line(d, cx0+140, ly, line, f, pal)
        ly += lh

    rx = cx0+code_w+44; rw = W-rx-PAD

    prev_vars = steps[i-1]["vars"] if i>0 else {}
    vp_h = 80 + max(1, n_vars_max)*56
    d.rounded_rectangle([rx, top, rx+rw, top+vp_h], 20, fill=pal["panel"])
    d.text((rx+32, top+24), "variables  (changed in green)", font=f["title"], fill=pal["title"])
    vy = top+80
    items = list(step["vars"].items())[:10]
    if not items:
        d.text((rx+40, vy), "(none yet)", font=f["var"], fill=pal["muted"])
    for name, v in items:
        d.text((rx+40, vy), name, font=f["var"], fill=pal["name"])
        nx = rx+40+d.textlength(name+" ", font=f["var"])
        d.text((nx, vy), "= ", font=f["var"], fill=pal["muted"]); nx += d.textlength("= ", font=f["var"])
        sval = repr(v); avail = (rx+rw-36)-nx
        while d.textlength(sval, font=f["var"])>avail and len(sval)>4:
            sval = sval[:-4]+"..."
        changed = prev_vars.get(name, "\0__missing__") != v
        d.text((nx, vy), sval, font=f["var"], fill=pal["changed"] if changed else pal["val"])
        vy += 56

    y_cursor = top+vp_h+28
    lp = active_loop(cur, loops)
    have_list = False; seq=None; cidx=None
    if lp:
        seq = step["vars"].get(lp["iterable"])
        if isinstance(seq, (list, tuple)):
            have_list = True
            tv = step["vars"].get(lp["target"])
            try: cidx = list(seq).index(tv)
            except (ValueError, TypeError): cidx = -1
    if loops:
        show_seq = seq if have_list else (step["vars"].get(loops[0]["iterable"]) if isinstance(step["vars"].get(loops[0]["iterable"]),(list,tuple)) else [])
        show_seq = list(show_seq)[:8]
        lbl_it = lp["iterable"] if lp else loops[0]["iterable"]
        lp_h = 80 + max(1,len(show_seq))*76
        d.rounded_rectangle([rx, y_cursor, rx+rw, y_cursor+lp_h], 20, fill=pal["panel"])
        d.text((rx+32, y_cursor+24), "list %s  done/current/waiting" % lbl_it, font=f["title"], fill=pal["title"])
        ry = y_cursor+80
        for p, item in enumerate(show_seq):
            rry = ry + p*76; label = "[%d] %r" % (p, item)
            if cidx is not None and cidx >= 0 and p < cidx:
                d.rounded_rectangle([rx+28,rry,rx+rw-28,rry+64],16, fill=pal["done_bg"])
                d.text((rx+52,rry+14),label,font=f["lst"],fill=pal["done_tx"])
                lw=d.textlength(label,font=f["lst"]); d.line([rx+52,rry+34,rx+52+lw,rry+34],fill=pal["done_tx"],width=3)
                d.text((rx+rw-128,rry+18),"done",font=f["tag"],fill=pal["done_tx"])
            elif cidx is not None and p == cidx:
                d.rounded_rectangle([rx+28,rry,rx+rw-28,rry+64],16, fill=pal["cur_bg"], outline=pal["cur_bd"], width=3)
                d.text((rx+52,rry+14),label,font=f["lst"],fill=pal["cur_tx"])
                d.text((rx+rw-208,rry+18),"<- current",font=f["tag"],fill=pal["cur_tx"])
            else:
                for sgx in range(rx+28, int(rx+rw-28), 24):
                    d.line([sgx,rry,min(sgx+12,rx+rw-28),rry],fill=pal["muted"],width=1)
                    d.line([sgx,rry+64,min(sgx+12,rx+rw-28),rry+64],fill=pal["muted"],width=1)
                d.line([rx+28,rry,rx+28,rry+64],fill=pal["muted"],width=1)
                d.line([rx+rw-28,rry,rx+rw-28,rry+64],fill=pal["muted"],width=1)
                d.text((rx+52,rry+14),label,font=f["lst"],fill=pal["wait_tx"])
                d.text((rx+rw-148,rry+18),"waiting",font=f["tag"],fill=pal["muted"])
        y_cursor += lp_h+28

    d.rounded_rectangle([rx, y_cursor, rx+rw, H-PAD], 20, fill=pal["console"])
    d.text((rx+32, y_cursor+24), "printed output", font=f["title"], fill=pal["title"])
    oly = y_cursor+80
    maxo = int((H-PAD - oly)/44)
    for line in step["stdout"].splitlines()[-maxo:]:
        d.text((rx+40, oly), line[:48], font=f["out"], fill=pal["out"]); oly += 44
    if step.get("error"):
        d.text((rx+40, oly), ("! "+step["error"])[:48], font=f["out"], fill=pal["err"])
    return img


# --------------------------------------------------------------------------
# STAGE 3 — ENCODE
# --------------------------------------------------------------------------
def build_frames(source, ms=900, code_size=34, palette="dark"):
    ms = max(MS_MIN, min(MS_MAX, ms))
    pal = get_palette(palette)
    src_lines = source.splitlines()
    loops = find_for_loops(source)
    steps = trace(source)
    f = load_fonts(code_size)
    dummy = Image.new("RGB",(10,10)); dd = ImageDraw.Draw(dummy)

    n_vars_max = max((len(list(s["vars"].items())[:10]) for s in steps), default=1)
    longest = max((len(l) for l in src_lines), default=20)
    code_w = min(140 + int(longest*dd.textlength("m", font=f["code"])) + 60, 1240)
    code_w = max(code_w, 720)
    code_h = 88 + min(len(src_lines),20)*(code_size+22) + 32

    vp_h = 80 + max(1,n_vars_max)*56
    if loops:
        max_items = min(max((len(s["vars"].get(loops[0]["iterable"], [])) if isinstance(s["vars"].get(loops[0]["iterable"]),(list,tuple)) else 0) for s in steps) or len(loops), 8)
        for lp in loops:
            for s in steps:
                v = s["vars"].get(lp["iterable"])
                if isinstance(v,(list,tuple)): max_items=max(max_items,min(len(v),8))
        lp_h = 80 + max(1,max_items)*76 + 28
    else:
        lp_h = 0
    right_h = vp_h + 28 + lp_h + 300
    top = 48+148
    body = max(code_h, right_h)
    W = code_w + 44 + 880 + 48*2
    H = top + body + 48

    lh = code_size + 22
    dims = (W, H, code_w, 48, top, lh, n_vars_max)

    frames, durs = [], []
    for i, s in enumerate(steps):
        frames.append(render(s, i, steps, src_lines, loops, dims, f, pal))
        durs.append(int(ms*2.6) if s.get("final") else ms)
    return frames, durs


def encode_gif(frames, durs):
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   duration=durs, loop=0, disposal=2, optimize=True)
    return out.getvalue()


def build_gif_bytes(source, ms=900, code_size=34, palette="dark"):
    frames, durs = build_frames(source, ms=ms, code_size=code_size, palette=palette)
    return encode_gif(frames, durs)


# Serverless responses are size-capped (~4.5MB on Vercel), so when the
# per-frame payload would blow past this, send only the animated GIF and let
# the frontend fall back to a plain <img> without the stepper.
FRAMES_BYTES_LIMIT = 2_500_000


def build_json_payload(source, ms=900, code_size=34, palette="dark"):
    frames, durs = build_frames(source, ms=ms, code_size=code_size, palette=palette)
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

    return {"gif": base64.b64encode(gif).decode(),
            "frames": frames_b64,
            "durations": durs}


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


def _generate_or_error(start_response, code, ms, fmt="gif", palette="dark"):
    if not isinstance(code, str) or not code.strip():
        return _json_response(start_response, 400, {"error": "'code' must be a non-empty string"})
    if len(code) > MAX_CODE_LEN:
        return _json_response(start_response, 400, {"error": f"code too long (max {MAX_CODE_LEN} characters)"})
    if not isinstance(ms, (int, float)):
        ms = 900

    try:
        if fmt == "json":
            payload = build_json_payload(code, ms=int(ms), palette=palette)
        else:
            gif_bytes = build_gif_bytes(code, ms=int(ms), palette=palette)
    except (UnsafeCodeError, ExecutionTimeout) as e:
        return _json_response(start_response, 400, {"error": str(e)})
    except Exception as e:
        return _json_response(start_response, 500, {"error": f"{type(e).__name__}: {e}"})

    if fmt == "json":
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
        qs = parse_qs(environ.get("QUERY_STRING") or "")
        if "c" in qs:
            try:
                b64 = qs["c"][0]
                code = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return _json_response(start_response, 400, {"error": "invalid 'c' parameter (expected base64url-encoded UTF-8 code)"})
            try:
                ms = int(qs.get("ms", ["900"])[0])
            except ValueError:
                ms = 900
            palette = qs.get("pal", ["dark"])[0]
            return _generate_or_error(start_response, code, ms, palette=palette)
        return _json_response(start_response, 200, {
            "ok": True,
            "usage": "POST {code, ms, palette} -> image/gif, or GET ?c=<base64url(code)>&ms=N&pal=dark|light -> image/gif",
        })

    if method != "POST":
        return _json_response(start_response, 405, {"error": "method not allowed"})

    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        raw = environ["wsgi.input"].read(length) if length else b"{}"
        payload = json.loads(raw or b"{}")
    except (ValueError, json.JSONDecodeError):
        return _json_response(start_response, 400, {"error": "invalid JSON body"})

    fmt = "json" if payload.get("format") == "json" else "gif"
    return _generate_or_error(start_response, payload.get("code", ""), payload.get("ms", 900),
                              fmt=fmt, palette=payload.get("palette", "dark"))
