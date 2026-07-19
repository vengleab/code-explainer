"""
backend/theme.py — shared color palettes for the execution-GIF renderers.

Single source of truth for both generate.py and generate_pandas.py, mirroring
the frontend's theme/code-theme.css so the on-screen editor and the exported
GIF use identical colors (Teaching Slide Design System).

Two palettes are provided, selected per request ("dark" default, "light"):
  - "dark"  → pygments "monokai" code card
  - "light" → pygments "default"/Jupyter code card

RULES baked into these values:
  - Coral/pink (the brand accent) NEVER appears inside code or as a line
    highlight. Line states use the neutral slate/blue/green/red set only.
  - The card fills (`panel`) are the code-card background; `bg` is the navy /
    sky chrome behind the cards.
  - GIFs are opaque RGB, so the translucent line-state tints from the CSS are
    pre-composited here over each card's background.

All values are (R, G, B) tuples.
"""

# Design-system reference colors (see theme/code-theme.css)
_LINE_BLUE = (77, 159, 236)   # --line-blue  #4D9FEC

PALETTES = {
    # ── DARK card (monokai) ────────────────────────────────────────────
    "dark": dict(
        bg=(26, 31, 92),          # navy-dk chrome behind the cards
        panel=(39, 40, 34),       # --code-bg  (code + variables card)
        console=(20, 21, 17),     # darker console surface
        gutter=(107, 112, 133),   # --mute (line numbers)
        # ── Syntax tokens — VSCode built-in "Monokai" ──────────────────
        code=(248, 248, 242),     # default / variable / punctuation (--code-fg)
        kw=(249, 38, 114),        # control keywords  #F92672
        storage=(102, 217, 239),  # def / class       #66D9EF
        s=(230, 219, 116),        # string            #E6DB74
        num=(174, 129, 255),      # number            #AE81FF
        const=(174, 129, 255),    # True/False/None   #AE81FF
        com=(136, 132, 111),      # comment           #88846F
        op=(249, 38, 114),        # operator          #F92672
        func=(166, 226, 46),      # function name     #A6E22E
        builtin=(102, 217, 239),  # print/len/range   #66D9EF
        dec=(166, 226, 46),       # @decorator        #A6E22E
        hl=(53, 56, 54),          # slate 0.13 tint over --code-bg
        bar=_LINE_BLUE,           # current-line gutter bar (was peach)
        title=(77, 159, 236),     # panel headers (blue accent, not coral)
        muted=(107, 112, 133),    # --mute
        out=(74, 222, 128),       # printed output (--line-green)
        cur_bg=(45, 58, 64),      # list "current" fill: blue 0.15 over bg
        cur_bd=_LINE_BLUE,        # list "current" border
        cur_tx=(200, 222, 255),   # list "current" text
        done_bg=(53, 55, 51),     # list "done" fill: gray 0.12 over bg
        done_tx=(107, 112, 133),  # list "done" text (muted)
        wait_tx=(216, 216, 210),  # list "waiting" label
        pill_bg=(42, 52, 128),    # inactive step pill (navy chip)
        pill_tx=(170, 176, 200),  # inactive step pill text
        pill_cur=_LINE_BLUE,      # active step pill (blue, NOT coral)
        pill_cur_tx=(20, 22, 38), # active step pill text
        name=(248, 248, 242),     # variable name (--code-fg, no pink/coral)
        val=(230, 219, 116),      # variable value (--code-string)
        changed=(74, 222, 128),   # changed variable (--line-green)
        err=(248, 113, 113),      # error text (--line-red)
        # pandas-only keys
        grid=(70, 72, 80),        # table gridlines
        head=(248, 248, 242),     # table header text
        cell=(248, 248, 242),     # table cell text
        new=(74, 222, 128),       # changed cell text (--line-green)
        newbg=(41, 51, 40),       # changed cell/col fill: green 0.06 over bg
        zebra=(46, 47, 42),       # alternating row fill
        cap=(230, 219, 116),      # caption text
    ),
    # ── LIGHT card (Jupyter "default") ─────────────────────────────────
    "light": dict(
        bg=(234, 241, 251),       # --sky chrome behind the cards
        panel=(248, 248, 248),    # --code-bg-light (code + variables card)
        console=(240, 240, 240),  # light console surface
        gutter=(107, 112, 133),   # --mute (line numbers)
        # ── Syntax tokens — JupyterLab default CodeMirror ──────────────
        code=(33, 33, 33),        # default / variable  #212121
        kw=(0, 128, 0),           # keywords (def/class too)  #008000
        storage=(0, 128, 0),      # def / class         #008000
        s=(186, 33, 33),          # string              #BA2121
        num=(0, 136, 0),          # number              #008800
        const=(136, 136, 255),    # True/False/None     #8888FF
        com=(64, 128, 128),       # comment             #408080
        op=(120, 0, 194),         # operator            #7800C2
        func=(0, 0, 255),         # function name       #0000FF
        builtin=(0, 128, 0),      # print/len/range     #008000
        dec=(170, 34, 255),       # @decorator          #AA22FF
        hl=(235, 237, 240),       # slate 0.13 tint over --code-bg-light
        bar=_LINE_BLUE,           # current-line gutter bar
        title=(42, 52, 128),      # panel headers (navy accent, not coral)
        muted=(107, 112, 133),    # --mute
        out=(34, 37, 64),         # printed output (ink, readable on light)
        cur_bg=(222, 235, 246),   # list "current" fill: blue 0.15 over bg
        cur_bd=_LINE_BLUE,        # list "current" border
        cur_tx=(34, 37, 64),      # list "current" text (ink)
        done_bg=(237, 238, 239),  # list "done" fill: gray 0.12 over bg
        done_tx=(107, 112, 133),  # list "done" text (muted)
        wait_tx=(80, 84, 100),    # list "waiting" label
        pill_bg=(255, 255, 255),  # inactive step pill (white chip)
        pill_tx=(107, 112, 133),  # inactive step pill text
        pill_cur=_LINE_BLUE,      # active step pill (blue, NOT coral)
        pill_cur_tx=(255, 255, 255),  # active step pill text
        name=(25, 23, 124),       # variable name (--code-variable-light)
        val=(186, 33, 33),        # variable value (--code-string-light)
        changed=(22, 163, 74),    # changed variable (readable green)
        err=(220, 38, 38),        # error text (readable red)
        # pandas-only keys
        grid=(210, 210, 215),     # table gridlines
        head=(34, 37, 64),        # table header text (ink)
        cell=(0, 0, 0),           # table cell text
        new=(22, 163, 74),        # changed cell text (readable green)
        newbg=(238, 246, 241),    # changed cell/col fill: green 0.06 over bg
        zebra=(240, 240, 242),    # alternating row fill
        cap=(107, 112, 133),      # caption text (muted)
    ),
}


def get_palette(name):
    """Return the palette for `name`, falling back to "dark" for anything else."""
    return PALETTES.get(name if name in PALETTES else "dark")
