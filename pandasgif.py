"""pandasgif.py — prototype: visualize pandas step-by-step as a morphing table.

Same trace-based idea as codegif, but any variable that is a DataFrame is drawn
as a real grid, and the diff vs the previous step is highlighted:
  - brand-new columns        -> green header + green cells
  - cells whose value changed -> green
  - a shrink in row count     -> caption "filtered R -> r rows"
"""
import sys, io, copy, os
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

def _find_font(bold=False):
    names = (["DejaVuSansMono-Bold.ttf","consolab.ttf"] if bold
             else ["DejaVuSansMono.ttf","consola.ttf","Menlo.ttc"])
    dirs = ["/usr/share/fonts/truetype/dejavu","/Library/Fonts","/System/Library/Fonts",
            r"C:\Windows\Fonts", os.path.expanduser("~/Library/Fonts")]
    for n in names:
        for d in dirs:
            p=os.path.join(d,n)
            if os.path.exists(p): return p
    return None
MONO, MONO_B = _find_font(), (_find_font(True) or _find_font())
def _f(p,s):
    try: return ImageFont.truetype(p,s) if p else ImageFont.load_default()
    except Exception: return ImageFont.load_default()

C = dict(bg=(30,30,46), panel=(40,42,58), code=(205,214,244), kw=(203,166,247),
         s=(166,227,161), gutter=(98,104,128), hl=(69,71,110), bar=(250,179,135),
         title=(137,180,250), muted=(127,132,156), grid=(70,72,90),
         head=(180,190,220), cell=(205,214,244), new=(166,227,161),
         newbg=(38,58,50), zebra=(35,37,52), cap=(249,226,175))
KW={"for","in","while","if","else","elif","def","return","print","import","and","or","not","True","False","None"}

FC=_f(MONO,17); FT=_f(MONO_B,14); FH=_f(MONO_B,15); FCELL=_f(MONO,15); FCAP=_f(MONO,15)

def snap(frame):
    out={}
    for k,v in frame.f_locals.items():
        if k.startswith("__"): continue
        if isinstance(v,(pd.DataFrame,pd.Series)):
            out[k]=v.copy()
        elif isinstance(v,(int,float,str,bool)) or (hasattr(v,"dtype") and getattr(v,"shape",None)==()):
            try: out[k]=copy.copy(v)
            except Exception: pass
    return out

def as_frame(obj):
    if isinstance(obj,pd.DataFrame): return obj
    if isinstance(obj,pd.Series): return obj.to_frame(name=(obj.name if obj.name is not None else "value"))
    return None

def trace(source):
    code=compile(source,"<snip>","exec"); steps=[]; buf=io.StringIO()
    def tr(fr,ev,arg):
        if fr.f_code.co_filename!="<snip>" or fr.f_code.co_name!="<module>":
            return None  # don't step into lambdas/functions (e.g. inside .apply)
        if ev=="line": steps.append(dict(line=fr.f_lineno, dfs=snap(fr), out=buf.getvalue()))
        return tr
    real=sys.stdout; sys.stdout=buf; sys.settrace(tr)
    ns={"__name__":"__snip__"}
    try: exec(code,ns)
    finally: sys.settrace(None); sys.stdout=real
    final_dfs={}
    for k,v in ns.items():
        if k.startswith("__"): continue
        if isinstance(v,(pd.DataFrame,pd.Series)): final_dfs[k]=v.copy()
        elif isinstance(v,(int,float,str,bool)) or (hasattr(v,"dtype") and getattr(v,"shape",None)==()):
            try: final_dfs[k]=copy.copy(v)
            except Exception: pass
    steps.append(dict(line=None, dfs=final_dfs, out=buf.getvalue(), final=True))
    return steps

def draw_code(d,x,y,text):
    cx,tok,ins,sc,i=x,"",False,"",0
    def fl(col):
        nonlocal cx,tok
        if tok: d.text((cx,y),tok,font=FC,fill=col); cx+=d.textlength(tok,font=FC); tok=""
    while i<len(text):
        ch=text[i]
        if ins:
            tok+=ch
            if ch==sc: d.text((cx,y),tok,font=FC,fill=C["s"]); cx+=d.textlength(tok,font=FC); tok=""; ins=False
            i+=1; continue
        if ch in ("'",'"'): fl(C["kw"] if tok in KW else C["code"]); ins=True; sc=ch; tok=ch; i+=1; continue
        if ch.isalnum() or ch=="_": tok+=ch
        else:
            fl(C["kw"] if tok in KW else C["code"]); d.text((cx,y),ch,font=FC,fill=C["code"]); cx+=d.textlength(ch,font=FC)
        i+=1
    fl(C["kw"] if tok in KW else C["code"])

def fmt(v):
    if isinstance(v,float): return ("%.2f"%v).rstrip("0").rstrip(".")
    return str(v)

def draw_df(d, x, y, w, name, df, prev, maxr=7, maxc=6, status=None):
    cols=list(df.columns)[:maxc]
    new_cols=set(cols)-set(prev.columns) if isinstance(prev,pd.DataFrame) else set()
    d.text((x, y), "%s   %d rows x %d cols" % (name, df.shape[0], df.shape[1]),
           font=FT, fill=C["title"])
    y+=26
    # column widths
    idxw=max(24, max((len(str(ix)) for ix in list(df.index)[:maxr]), default=1)*9+10)
    cw=[]
    for c in cols:
        vals=[fmt(v) for v in list(df[c])[:maxr]]
        chars=max([len(str(c))]+[len(v) for v in vals])
        cw.append(min(160, chars*9+18))
    # clamp total width to the panel: drop rightmost columns that don't fit
    while cw and idxw+sum(cw) > w-8:
        cw.pop(); cols=cols[:-1]
    hidden = df.shape[1]-len(cols)
    rowh=26
    # header
    hx=x+idxw
    d.rectangle([x,y,x+idxw+sum(cw),y+rowh], fill=C["panel"])
    d.text((x+6,y+5),"idx",font=FH,fill=C["muted"])
    for j,c in enumerate(cols):
        if c in new_cols: d.rectangle([hx,y,hx+cw[j],y+rowh], fill=C["newbg"])
        d.text((hx+8,y+5), str(c)[:16], font=FH, fill=C["new"] if c in new_cols else C["head"])
        hx+=cw[j]
    y+=rowh
    # rows
    idx=list(df.index)[:maxr]
    for r,ix in enumerate(idx):
        st = status.get(ix) if status else None
        if st=="kept":
            d.rectangle([x,y,x+idxw+sum(cw),y+rowh], fill=C["newbg"])
        elif r%2: d.rectangle([x,y,x+idxw+sum(cw),y+rowh], fill=C["zebra"])
        d.text((x+6,y+5),str(ix)[:4],font=FCELL,fill=C["muted"])
        cxp=x+idxw
        for j,c in enumerate(cols):
            val=df.at[ix,c]; changed=c in new_cols
            if not changed and isinstance(prev,pd.DataFrame) and c in prev.columns and ix in prev.index:
                try: changed = (prev.at[ix,c]!=val)
                except Exception: changed=False
            if changed and st!="dropped": d.rectangle([cxp,y,cxp+cw[j],y+rowh], fill=C["newbg"])
            col = (96,100,120) if st=="dropped" else (C["new"] if (changed or st=="kept") else C["cell"])
            txt=fmt(val)[:16]
            d.text((cxp+8,y+5), txt, font=FCELL, fill=col)
            if st=="dropped":
                tw=d.textlength(txt,font=FCELL)
                d.line([cxp+8,y+rowh//2,cxp+8+tw,y+rowh//2],fill=(96,100,120),width=2)
            cxp+=cw[j]
        y+=rowh
    # grid lines
    if df.shape[0]>maxr:
        d.text((x+6,y+4),"... %d more rows"%(df.shape[0]-maxr),font=FCELL,fill=C["muted"]); y+=22
    return y

def render(step,i,steps,src,dims):
    W,H,cw_code,top=dims
    img=Image.new("RGB",(W,H),C["bg"]); d=ImageDraw.Draw(img)
    # code panel
    PAD=24
    d.rounded_rectangle([PAD,PAD,PAD+cw_code,H-PAD],10,fill=C["panel"])
    d.text((PAD+14,PAD+12),"code  step %d/%d"%(i+1,len(steps)),font=FT,fill=C["title"])
    ly=PAD+42; cur=step["line"]
    for idx,line in enumerate(src):
        on=(idx+1)==cur
        if on:
            d.rounded_rectangle([PAD+8,ly-1,PAD+cw_code-10,ly+24],5,fill=C["hl"])
            d.rectangle([PAD+8,ly-1,PAD+12,ly+24],fill=C["bar"])
        d.text((PAD+14,ly),"%2d"%(idx+1),font=FC,fill=C["gutter"])
        d.text((PAD+44,ly),">" if on else " ",font=FC,fill=C["bar"])
        maxpx = cw_code - 96
        cl = line
        while cl and d.textlength(cl, font=FC) > maxpx:
            cl = cl[:-1]
        if cl != line and cl: cl = cl[:-1] + "\u2026"
        draw_code(d,PAD+64,ly,cl); ly+=28
    # right column: up to 3 grids (DataFrame/Series) + a scalar strip
    rx=PAD+cw_code+22; rw=W-rx-PAD; y=PAD
    prev=steps[i-1]["dfs"] if i>0 else {}
    def changed(name,v):
        p=prev.get(name)
        try:
            if isinstance(v,(pd.DataFrame,pd.Series)): return (p is None) or (not v.equals(p))
            return p!=v
        except Exception: return True
    grids=[]; scalars=[]
    for name,v in step["dfs"].items():
        fr=as_frame(v)
        if fr is not None: grids.append((name,fr,v))
        else: scalars.append((name,v))
    # filter detection: a changed table whose rows are a subset of another table
    row_status={}; filt_pair=set()
    for name,fr,orig in grids:
        if not changed(name,orig): continue
        for sname,sf,sorig in grids:
            if sname==name or sf.shape[0]<=fr.shape[0]: continue
            try:
                if set(fr.columns)<=set(sf.columns) and set(fr.index)<set(sf.index):
                    keep=set(fr.index)
                    row_status[sname]={ix:("kept" if ix in keep else "dropped") for ix in sf.index}
                    filt_pair.update({name,sname})
                    break
            except TypeError:
                continue
    def prio(t):
        n,fr,o=t
        if n in filt_pair: return 0
        return 1 if changed(n,o) else 2
    grids.sort(key=prio)
    for name,fr,orig in grids[:3]:
        pf=as_frame(prev.get(name))
        y=draw_df(d,rx,y,rw,name,fr,pf,maxr=6,status=row_status.get(name))+16
    if row_status:
        sname=next(iter(row_status)); kept=sum(1 for s in row_status[sname].values() if s=="kept")
        d.text((rx, min(y,H-PAD-24)), "filter kept %d of %d rows"%(kept,len(row_status[sname])),
               font=FCAP, fill=C["cap"])
        y+=24
    if scalars:
        sy=min(y, H-PAD-24)
        parts=[]
        for name,v in scalars[:6]:
            parts.append("%s=%s"%(name,fmt(v)))
        d.text((rx, sy), "scalars:  "+"   ".join(parts), font=FCAP, fill=C["cap"])
    return img

def build(source,out="pandas_out.gif",ms=1100):
    src=source.splitlines(); steps=trace(source)
    dummy=Image.new("RGB",(8,8)); dd=ImageDraw.Draw(dummy)
    charw=dd.textlength("m",font=FC)
    longest=max((len(l) for l in src),default=20)
    cw_code=int(min(max(96+longest*charw+20, 380), 640))
    right_w=480
    W=24+cw_code+22+right_w+24
    def gh(fr): return 26+26+min(fr.shape[0],6)*26 + (22 if fr.shape[0]>6 else 0)
    maxright=0
    for s in steps:
        frs=sorted([as_frame(v) for v in s["dfs"].values() if as_frame(v) is not None], key=lambda f:-gh(f))[:3]
        h=sum(gh(f)+16 for f in frs)
        if any(as_frame(v) is None for v in s["dfs"].values()): h+=28
        maxright=max(maxright,h)
    top=24
    H=min(max(24*2+42+len(src)*28, maxright+96, 380), 960)
    frames=[render(s,i,steps,src,(W,H,cw_code,top)) for i,s in enumerate(steps)]
    durs=[int(ms*2.4) if s.get("final") else ms for s in steps]
    frames[0].save(out,save_all=True,append_images=frames[1:],duration=durs,loop=0,disposal=2,optimize=True)
    print(len(frames),"frames",f"{W}x{H} ->",out); return out

DEMO="""\
import pandas as pd

df = pd.DataFrame({
    'name': ['Ann', 'Bo', 'Cy', 'Di'],
    'dept': ['eng', 'eng', 'sales', 'sales'],
    'salary': [90, 60, 50, 55],
})
df['bonus'] = df['salary'] * 0.1
high = df[df['salary'] > 55]
"""

if __name__=="__main__":
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument("file",nargs="?"); ap.add_argument("-o",default="pandas_out.gif"); ap.add_argument("--ms",type=int,default=1100)
    a=ap.parse_args()
    build(open(a.file).read() if a.file else DEMO, a.o, a.ms)
