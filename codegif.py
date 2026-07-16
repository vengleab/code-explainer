"""
codegif.py — turn any Python snippet into an execution GIF.

Usage:
    python codegif.py                     # runs the built-in demo
    python codegif.py myscript.py         # trace a file
    python codegif.py myscript.py -o out.gif --ms 900

The GIF has four auto-populated panels:
  - code          : the source, with the current line highlighted
  - execution order: the sequence of line numbers actually run
  - variables     : every local, with values that changed shown in green
  - list progress : if the current line is `for x in <list>`, shows done/current/waiting
  - printed output: captured stdout so far

Nothing here is specific to the fruits example — it all comes from the trace.
"""
import sys, io, copy, ast, argparse, os
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# STAGE 1 — TRACE (fully generic)
# --------------------------------------------------------------------------
def find_for_loops(source):
    """Map each `for` header line -> (target_name, iterable_name) when the
    iterable is a simple Name.  Also record each loop's line span so we can
    tell which loop encloses the current line."""
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

SKIP_TYPES = ()  # filled after imports; keeps only "showable" values
def showable(v):
    import types
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
    code = compile(source, "<snippet>", "exec")
    buf = io.StringIO()
    steps = []

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != "<snippet>":
            return tracer
        if event == "line":
            steps.append(dict(line=frame.f_lineno,
                              vars=snapshot_vars(frame),
                              stdout=buf.getvalue()))
        return tracer

    real = sys.stdout
    sys.stdout = buf
    sys.settrace(tracer)
    err = None
    try:
        exec(code, {"__name__": "__snippet__"})
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        sys.settrace(None)
        sys.stdout = real

    steps.append(dict(line=None, vars=(steps[-1]["vars"] if steps else {}),
                      stdout=buf.getvalue(), final=True, error=err))
    return steps

# --------------------------------------------------------------------------
# STAGE 2 — RENDER
# --------------------------------------------------------------------------
def _find_font(bold=False):
    names = (["DejaVuSansMono-Bold.ttf","consolab.ttf","courbd.ttf"] if bold
             else ["DejaVuSansMono.ttf","consola.ttf","cour.ttf","Menlo.ttc","Monaco.ttf"])
    dirs = [
        "/usr/share/fonts/truetype/dejavu",           # Linux
        "/Library/Fonts", "/System/Library/Fonts",     # macOS
        "/System/Library/Fonts/Supplemental",
        r"C:\Windows\Fonts",                            # Windows
        os.path.expanduser("~/Library/Fonts"),
    ]
    for n in names:
        for d in dirs:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
        # let the platform's font system try to resolve a bare name too
        try:
            ImageFont.truetype(n, 12); return n
        except Exception:
            pass
    return None

MONO   = _find_font(bold=False)
MONO_B = _find_font(bold=True) or MONO

def _font(path, size):
    try:
        return ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()
C = dict(bg=(30,30,46), panel=(40,42,58), console=(24,24,37), gutter=(98,104,128),
         code=(205,214,244), kw=(203,166,247), s=(166,227,161), hl=(69,71,110),
         bar=(250,179,135), title=(137,180,250), muted=(127,132,156), out=(166,227,161),
         cur_bg=(54,66,110), cur_bd=(137,180,250), cur_tx=(180,205,255),
         done_bg=(46,47,62), done_tx=(96,100,120), wait_tx=(205,214,244),
         pill_bg=(46,47,62), pill_tx=(150,156,180), pill_cur=(137,180,250), pill_cur_tx=(20,22,38),
         name=(245,194,231), val=(249,226,175), changed=(166,227,161), err=(243,139,168))
KEYWORDS = {"for","in","while","if","else","elif","def","return","print","import",
            "from","and","or","not","True","False","None","class","with","as","try",
            "except","break","continue","range","len","yield"}

def load_fonts(cs):
    return dict(code=_font(MONO, cs), kw=_font(MONO, cs),
                title=_font(MONO_B, 14), pill=_font(MONO_B, 15),
                var=_font(MONO, 16), tag=_font(MONO, 13),
                out=_font(MONO, 15), lst=_font(MONO, 16))

def draw_code_line(d, x, y, text, f):
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
                d.text((cx,y),tok,font=f["code"],fill=C["s"]); cx+=d.textlength(tok,font=f["code"]); tok=""; ins=False
            i+=1; continue
        if ch in ("'",'"'):
            flush(C["kw"] if tok in KEYWORDS else C["code"]); ins=True; sc=ch; tok=ch; i+=1; continue
        if ch.isalnum() or ch=="_":
            tok+=ch
        else:
            flush(C["kw"] if tok in KEYWORDS else C["code"])
            d.text((cx,y),ch,font=f["code"],fill=C["code"]); cx+=d.textlength(ch,font=f["code"])
        i+=1
    flush(C["kw"] if tok in KEYWORDS else C["code"])

def active_loop(line, loops):
    cand = [lp for lp in loops if line is not None and lp["start"] <= line <= lp["end"]]
    return min(cand, key=lambda lp: lp["end"]-lp["start"]) if cand else None

def render(step, i, steps, src_lines, loops, dims, f):
    W, H, code_w, PAD, top, lh, n_vars_max = dims
    img = Image.new("RGB",(W,H),C["bg"]); d = ImageDraw.Draw(img)

    # trail (sliding window of last 14 executed line labels)
    d.text((PAD, PAD-4), "execution order", font=f["title"], fill=C["title"])
    labels = [str(steps[k]["line"]) for k in range(i+1) if steps[k]["line"] is not None]
    win = labels[-14:]; trunc = len(labels) > 14
    tx, ty = PAD, PAD+22
    if trunc:
        d.text((tx, ty+4), "...", font=f["var"], fill=C["muted"]); tx += 28
    for j, lab in enumerate(win):
        cur = (not step.get("final")) and j == len(win)-1
        txt = "L"+lab; w = d.textlength(txt, font=f["pill"])+18
        d.rounded_rectangle([tx,ty,tx+w,ty+28],6, fill=C["pill_cur"] if cur else C["pill_bg"])
        d.text((tx+9,ty+5),txt,font=f["pill"],fill=C["pill_cur_tx"] if cur else C["pill_tx"])
        tx += w+6
        if j < len(win)-1:
            d.text((tx,ty+4),">",font=f["var"],fill=C["muted"]); tx += 16

    # code panel
    cx0 = PAD
    d.rounded_rectangle([cx0, top, cx0+code_w, H-PAD], 10, fill=C["panel"])
    phase = "finished" if step.get("final") else "running line %s" % step["line"]
    d.text((cx0+16, top+12), "code   step %d/%d  %s" % (i+1, len(steps), phase),
           font=f["title"], fill=C["title"])
    cur = step["line"]
    # window the code if long
    n = len(src_lines)
    if n <= 18 or cur is None:
        lo, hi = 0, n
    else:
        lo = max(0, cur-1-8); hi = min(n, lo+18); lo = max(0, hi-18)
    ly = top+44
    for idx in range(lo, hi):
        on = (idx+1) == cur
        if on:
            d.rounded_rectangle([cx0+8, ly-1, cx0+code_w-10, ly+lh-5], 5, fill=C["hl"])
            d.rectangle([cx0+8, ly-1, cx0+12, ly+lh-5], fill=C["bar"])
        d.text((cx0+16, ly), "%3d"%(idx+1), font=f["code"], fill=C["gutter"])
        d.text((cx0+52, ly), ">" if on else " ", font=f["code"], fill=C["bar"])
        line = src_lines[idx]
        # clip overly long lines to panel width
        maxc = int((code_w-90)/d.textlength("m", font=f["code"]))
        if len(line) > maxc: line = line[:maxc-1]+"\u2026"
        draw_code_line(d, cx0+70, ly, line, f)
        ly += lh

    # right column
    rx = cx0+code_w+22; rw = W-rx-PAD

    # variables panel
    prev_vars = steps[i-1]["vars"] if i>0 else {}
    vp_h = 40 + max(1, n_vars_max)*28
    d.rounded_rectangle([rx, top, rx+rw, top+vp_h], 10, fill=C["panel"])
    d.text((rx+16, top+12), "variables  (changed in green)", font=f["title"], fill=C["title"])
    vy = top+40
    items = list(step["vars"].items())[:10]
    if not items:
        d.text((rx+20, vy), "(none yet)", font=f["var"], fill=C["muted"])
    for name, v in items:
        d.text((rx+20, vy), name, font=f["var"], fill=C["name"])
        nx = rx+20+d.textlength(name+" ", font=f["var"])
        d.text((nx, vy), "= ", font=f["var"], fill=C["muted"]); nx += d.textlength("= ", font=f["var"])
        sval = repr(v); avail = (rx+rw-18)-nx
        while d.textlength(sval, font=f["var"])>avail and len(sval)>4:
            sval = sval[:-4]+"..."
        changed = prev_vars.get(name, "\0__missing__") != v
        d.text((nx, vy), sval, font=f["var"], fill=C["changed"] if changed else C["val"])
        vy += 28

    # list-progress panel (only if current line sits in a for-over-a-list)
    y_cursor = top+vp_h+14
    lp = active_loop(cur, loops)
    have_list = False; seq=None; cidx=None
    if lp:
        seq = step["vars"].get(lp["iterable"])
        if isinstance(seq, (list, tuple)):
            have_list = True
            tv = step["vars"].get(lp["target"])
            try: cidx = list(seq).index(tv)
            except (ValueError, TypeError): cidx = -1
    if loops:  # reserve/draw the panel whenever the program has a qualifying loop
        show_seq = seq if have_list else (step["vars"].get(loops[0]["iterable"]) if isinstance(step["vars"].get(loops[0]["iterable"]),(list,tuple)) else [])
        show_seq = list(show_seq)[:8]
        lbl_it = lp["iterable"] if lp else loops[0]["iterable"]
        lp_h = 40 + max(1,len(show_seq))*38
        d.rounded_rectangle([rx, y_cursor, rx+rw, y_cursor+lp_h], 10, fill=C["panel"])
        d.text((rx+16, y_cursor+12), "list %s  done/current/waiting" % lbl_it, font=f["title"], fill=C["title"])
        ry = y_cursor+40
        for p, item in enumerate(show_seq):
            rry = ry + p*38; label = "[%d] %r" % (p, item)
            if cidx is not None and cidx >= 0 and p < cidx:
                d.rounded_rectangle([rx+14,rry,rx+rw-14,rry+32],8, fill=C["done_bg"])
                d.text((rx+26,rry+7),label,font=f["lst"],fill=C["done_tx"])
                lw=d.textlength(label,font=f["lst"]); d.line([rx+26,rry+17,rx+26+lw,rry+17],fill=C["done_tx"],width=2)
                d.text((rx+rw-64,rry+9),"done",font=f["tag"],fill=C["done_tx"])
            elif cidx is not None and p == cidx:
                d.rounded_rectangle([rx+14,rry,rx+rw-14,rry+32],8, fill=C["cur_bg"], outline=C["cur_bd"], width=2)
                d.text((rx+26,rry+7),label,font=f["lst"],fill=C["cur_tx"])
                d.text((rx+rw-104,rry+9),"<- current",font=f["tag"],fill=C["cur_tx"])
            else:
                for sgx in range(rx+14, int(rx+rw-14), 12):
                    d.line([sgx,rry,min(sgx+6,rx+rw-14),rry],fill=C["muted"],width=1)
                    d.line([sgx,rry+32,min(sgx+6,rx+rw-14),rry+32],fill=C["muted"],width=1)
                d.line([rx+14,rry,rx+14,rry+32],fill=C["muted"],width=1)
                d.line([rx+rw-14,rry,rx+rw-14,rry+32],fill=C["muted"],width=1)
                d.text((rx+26,rry+7),label,font=f["lst"],fill=C["wait_tx"])
                d.text((rx+rw-74,rry+9),"waiting",font=f["tag"],fill=C["muted"])
        y_cursor += lp_h+14

    # output panel (fills remaining space)
    d.rounded_rectangle([rx, y_cursor, rx+rw, H-PAD], 10, fill=C["console"])
    d.text((rx+16, y_cursor+12), "printed output", font=f["title"], fill=C["title"])
    oly = y_cursor+40
    maxo = int((H-PAD - oly)/22)
    for line in step["stdout"].splitlines()[-maxo:]:
        d.text((rx+20, oly), line[:48], font=f["out"], fill=C["out"]); oly += 22
    if step.get("error"):
        d.text((rx+20, oly), ("! "+step["error"])[:48], font=f["out"], fill=C["err"])
    return img

# --------------------------------------------------------------------------
# STAGE 3 — ENCODE + orchestration
# --------------------------------------------------------------------------
def build_gif(source, out_path, ms=900, code_size=17):
    src_lines = source.splitlines()
    loops = find_for_loops(source)
    steps = trace(source)
    f = load_fonts(code_size)
    dummy = Image.new("RGB",(10,10)); dd = ImageDraw.Draw(dummy)

    n_vars_max = max((len(list(s["vars"].items())[:10]) for s in steps), default=1)
    longest = max((len(l) for l in src_lines), default=20)
    code_w = min(70 + int(longest*dd.textlength("m", font=f["code"])) + 30, 620)
    code_w = max(code_w, 360)
    code_h = 44 + min(len(src_lines),18)*(code_size+11) + 16

    # right column height estimate
    vp_h = 40 + max(1,n_vars_max)*28
    if loops:
        max_items = min(max((len(s["vars"].get(loops[0]["iterable"], [])) if isinstance(s["vars"].get(loops[0]["iterable"]),(list,tuple)) else 0) for s in steps) or len(loops), 8)
        for lp in loops:
            for s in steps:
                v = s["vars"].get(lp["iterable"])
                if isinstance(v,(list,tuple)): max_items=max(max_items,min(len(v),8))
        lp_h = 40 + max(1,max_items)*38 + 14
    else:
        lp_h = 0
    right_h = vp_h + 14 + lp_h + 150  # +150 min for output panel
    top = 24+74
    body = max(code_h, right_h)
    W = code_w + 22 + 440 + 24*2
    H = top + body + 24

    lh = code_size + 11
    dims = (W, H, code_w, 24, top, lh, n_vars_max)

    frames, durs = [], []
    for i, s in enumerate(steps):
        frames.append(render(s, i, steps, src_lines, loops, dims, f))
        durs.append(int(ms*2.6) if s.get("final") else ms)
    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=durs, loop=0, disposal=2, optimize=True)
    print(f"{len(frames)} frames  {W}x{H}  -> {out_path}")
    return out_path

DEMO = """\
fruits = ['apple', 'banana', 'cherry']

for fruit in fruits:
    print("-------------------")
    print(fruit)
"""

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="Python file to trace (omit for demo)")
    ap.add_argument("-o", "--out", default="out.gif")
    ap.add_argument("--ms", type=int, default=900, help="ms per frame")
    a = ap.parse_args()
    src = open(a.file).read() if a.file else DEMO
    build_gif(src, a.out, ms=a.ms)

# python codegif.py test.py -o test.gif