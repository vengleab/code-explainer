"""
backend/runtime/sandbox.py — the safety layer shared by both visualizers.

Both endpoints execute arbitrary user-submitted Python in-process, so this is
the single home for the defense-in-depth that used to be duplicated (verbatim)
in generate.py and generate_pandas.py:

  - check_safe()            — AST pre-check (denylist)
  - make_restricted_globals — reduced __builtins__ + a guarded __import__
  - MAX_CODE_LEN/MAX_STEPS/TRACE_TIMEOUT_SECONDS — the caps the tracer enforces

SAFETY NOTE (unchanged from before): this is best-effort sandboxing in the same
process, NOT a real isolation boundary (no seccomp/gVisor/VM). It blocks the
obvious escape routes (file/network/process access, dunder introspection) but
must be paired with the platform's own maxDuration backstop. Do not weaken any
layer here.

What this module deliberately does NOT own is *which* imports each endpoint
allows — that policy differs per endpoint (the pandas endpoint additionally
allows pandas/numpy; the numpy data endpoint is narrower still), so
`allowed_imports` is passed in by the caller rather than hardcoded here.
`STDLIB_IMPORTS` below is only the shared base list the two GIF endpoints build
their policy from, so the same twelve names are not spelled out twice.
"""
import ast
import builtins as _builtins

# Caps. Kept here because they bound untrusted execution; the tracer reads
# MAX_STEPS/TRACE_TIMEOUT_SECONDS, the HTTP layer reads MAX_CODE_LEN/MS_*.
MAX_CODE_LEN = 4000
MAX_STEPS = 200
TRACE_TIMEOUT_SECONDS = 5
MS_MIN, MS_MAX = 200, 2000

# The stdlib modules both GIF endpoints allow. This is shared *vocabulary*, not
# policy: each endpoint still declares its own ALLOWED_IMPORTS (generate.py uses
# this as-is, generate_pandas.py adds pandas/numpy, and numpy_model.py sets a
# deliberately narrower list of its own). Widening this widens both GIF
# endpoints at once — that is the point, but weigh it as a security change.
STDLIB_IMPORTS = frozenset({
    "math", "random", "string", "itertools", "functools", "collections",
    "datetime", "re", "json", "statistics", "decimal", "fractions",
})

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
    """Raised by check_safe() when the AST pre-check rejects the source."""


class StepLimitExceeded(Exception):
    """Raised inside the trace callback when MAX_STEPS is reached."""


class ExecutionTimeout(Exception):
    """Raised inside the trace callback when TRACE_TIMEOUT_SECONDS elapses."""


def check_safe(source, allowed_imports):
    """Reject obviously-unsafe source before it is ever exec()'d.

    Denylist: imports outside `allowed_imports`, dunder attribute access,
    unsafe builtin names (open/eval/exec/getattr/...), and async constructs.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise UnsafeCodeError(f"SyntaxError: {e}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in allowed_imports:
                    raise UnsafeCodeError(f"import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in allowed_imports:
                raise UnsafeCodeError(f"import of '{node.module}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise UnsafeCodeError(f"access to '{node.attr}' is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in DENIED_CALL_NAMES:
                raise UnsafeCodeError(f"use of '{node.id}' is not allowed")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            raise UnsafeCodeError("async code is not allowed")


def make_restricted_globals(allowed_imports):
    """Build the globals dict user code runs under: reduced builtins + guarded import.

    The `__import__` guard is a closure bound to this call's `allowed_imports`,
    so there is no module-global import policy — each caller supplies its own.
    """
    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.split(".")[0] not in allowed_imports:
            raise ImportError(f"import of '{name}' is not allowed in this sandbox")
        return _builtins.__import__(name, globals, locals, fromlist, level)

    safe_builtins = {name: getattr(_builtins, name) for name in SAFE_BUILTIN_NAMES if hasattr(_builtins, name)}
    safe_builtins["__import__"] = _safe_import
    return {"__builtins__": safe_builtins, "__name__": "__snippet__"}
