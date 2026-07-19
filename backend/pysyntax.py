"""
backend/pysyntax.py — single-line Python tokenizer shared by the GIF renderers.

Both generate.py (draw_code_line) and generate_pandas.py (draw_code) call
iter_tokens() so the two renderers classify code identically, and the category
names match the syntax keys in theme.py's PALETTES — so drawing a token is just
`fill = pal.get(category, pal["code"])`.

The category set mirrors the frontend editor tokenizer (CodeEditor.jsx) and the
.tok-* CSS classes, so the on-screen editor and the exported GIF agree:

    com  string(s)  num  const  kw  storage  builtin  func  dec  op  code

This is a lightweight regex/scanner, not a full lexer (see the "limitations"
note in the plan): color-only (no bold/italic), no parameter/self detection,
and brackets/commas/colons/dots stay default ("code").
"""
import re

# storage.type — `def`/`class` get their own color in Monokai (cyan)
STORAGE = {"def", "class"}
# constant.language
CONSTANTS = {"True", "False", "None"}
# control keywords (everything else keyword-like)
KEYWORDS = {
    "and", "as", "assert", "async", "await", "break", "continue", "del",
    "elif", "else", "except", "finally", "for", "from", "global", "if",
    "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass",
    "raise", "return", "try", "while", "with", "yield",
}
BUILTINS = {
    "print", "len", "range", "int", "str", "float", "list", "dict", "set",
    "tuple", "bool", "abs", "all", "any", "enumerate", "zip", "map",
    "filter", "sorted", "reversed", "sum", "min", "max", "open", "input",
    "isinstance", "type", "super", "self",
}

# Groups: 1 comment | 2 string | 3 number | 4 decorator | 5 identifier | 6 operator.
# Operators exclude brackets/comma/colon/dot (Python punctuation → default color).
_TOKEN_RE = re.compile(
    r"(#.*)"
    r"|([rbfuRBFU]{0,3}(?:'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"))"
    r"|(0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+|(?:\d[\d_]*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?[jJ]?)"
    r"|(@[A-Za-z_]\w*)"
    r"|([A-Za-z_]\w*)"
    r"|(\*\*=?|//=?|<<=?|>>=?|==|!=|<=|>=|:=|->|[-+*/%&|^@~]=?|[=<>])"
)


def _classify_ident(ident, prev_word, next_ch):
    if ident in KEYWORDS:
        return "kw"
    if ident in STORAGE:
        return "storage"
    if ident in CONSTANTS:
        return "const"
    if prev_word in ("def", "class"):
        return "func"           # the name being defined
    if ident in BUILTINS:
        return "builtin"
    if next_ch == "(":
        return "func"           # a call: name(
    return "code"


def iter_tokens(line):
    """Tokenize one line of Python → list of (text, category) spans.

    Spans concatenate back to the original line (whitespace/punctuation between
    matches is emitted as its own "code" span), so a renderer can draw each span
    left-to-right at the running cursor.
    """
    out = []
    last = 0
    prev_word = ""
    for m in _TOKEN_RE.finditer(line):
        if m.start() > last:
            out.append((line[last:m.start()], "code"))  # gaps: spaces, () , : .
        comment, string, number, decorator, ident, operator = m.groups()
        if comment is not None:
            out.append((comment, "com"))
        elif string is not None:
            out.append((string, "s"))
        elif number is not None:
            out.append((number, "num"))
        elif decorator is not None:
            out.append((decorator, "dec"))
        elif operator is not None:
            out.append((operator, "op"))
        elif ident is not None:
            next_ch = line[m.end()] if m.end() < len(line) else ""
            out.append((ident, _classify_ident(ident, prev_word, next_ch)))
            prev_word = ident
        last = m.end()
    if last < len(line):
        out.append((line[last:], "code"))
    return out
