"""
backend/numpy_model.py — turn a NumPy snippet into a drawable model (no images).

This is the odd one out among the backend services: it renders nothing. The
NumPy page animates on an HTML canvas in the browser (arrows sweeping
source -> result, cells filling in, a speed slider), so what the frontend needs
is not a GIF but *data*: the real arrays, and a description of the single
operation to animate.

Two sources of truth, deliberately:

  - real NumPy, for values      — the snippet is exec'd, so `np.random.seed`,
                                  dtypes, broadcasting and the result of the
                                  expression are genuinely NumPy's, not a
                                  reimplementation.
  - the AST, for geometry       — the drawing needs the *rectangle* a slice
                                  selects (r0:r1, c0:c1), which the resulting
                                  array no longer knows. Slice bounds are read
                                  off the syntax and resolved against the real
                                  shape via slice().indices().

The last "animatable" statement wins: an assignment or bare expression whose
value is a subscript (slice or boolean mask) or arithmetic on arrays. Everything
before it is executed first, so `B = A + 1` then `C = B[0:2]` works.

Sandboxing is the shared one (sandbox.py): AST denylist, reduced builtins,
guarded __import__, code-length cap, plus the same settrace wall-clock guard the
tracer uses — signal.alarm is avoided on purpose (main-thread only, which the
serverless runtime does not guarantee).
"""
import ast
import io
import sys
import time
import warnings

try:  # package import in dev (imported as backend.numpy_model)
    from .sandbox import (check_safe, make_restricted_globals, MAX_CODE_LEN,
                          ExecutionTimeout, TRACE_TIMEOUT_SECONDS)
except ImportError:  # top-level module on the serverless runtime
    from sandbox import (check_safe, make_restricted_globals, MAX_CODE_LEN,
                         ExecutionTimeout, TRACE_TIMEOUT_SECONDS)

# numpy only — no pandas here, and no filesystem/network modules.
ALLOWED_IMPORTS = {
    "numpy", "np", "math", "random", "itertools", "functools", "statistics",
}

# Drawing limits: cells carry readable numbers only while they stay large.
MAX_DIM = 12    # per axis, for 2-D arrays
MAX_1D = 20     # a source 1-D array is drawn as a single row, so it can be longer
MAX_CELLS = MAX_DIM * MAX_DIM  # ceiling for a 1-D *result*, which wraps in the strip
ROUND_DP = 4    # trims float noise (0.30000000000000004) out of the payload

CMP_SYMBOLS = {
    ast.Gt: ">", ast.Lt: "<", ast.GtE: ">=", ast.LtE: "<=",
    ast.Eq: "==", ast.NotEq: "!=",
}
BIN_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


class ModelError(Exception):
    """User-facing problem with the snippet (message is shown in the sidebar)."""


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def _timeout_guard(deadline):
    """A sys.settrace callback that aborts pure-Python loops past `deadline`."""
    def tracer(frame, event, arg):
        if event == "line" and time.monotonic() > deadline:
            raise ExecutionTimeout(f"execution exceeded {TRACE_TIMEOUT_SECONDS}s")
        return tracer
    return tracer


def _run(statements, namespace, deadline):
    """Exec top-level statements in order, under the wall-clock guard."""
    real_stdout = sys.stdout
    sys.stdout = io.StringIO()  # user prints are irrelevant here; keep logs clean
    sys.settrace(_timeout_guard(deadline))
    try:
        with warnings.catch_warnings():
            # NumPy warns on divide-by-zero/invalid rather than raising; a NaN
            # that reaches the payload is caught by _to_grid instead.
            warnings.simplefilter("ignore")
            for stmt in statements:
                module = ast.Module(body=[stmt], type_ignores=[])
                exec(compile(ast.fix_missing_locations(module), "<snippet>", "exec"), namespace)
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout


def _eval(node, namespace, deadline):
    """Evaluate one expression node in the snippet's namespace."""
    sys.settrace(_timeout_guard(deadline))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            expression = ast.Expression(body=node)
            return eval(compile(ast.fix_missing_locations(expression), "<snippet>", "eval"), namespace)
    finally:
        sys.settrace(None)


# --------------------------------------------------------------------------
# Array validation / conversion
# --------------------------------------------------------------------------
def _is_ndarray(value):
    return type(value).__module__ == "numpy" and hasattr(value, "ndim") and hasattr(value, "dtype")


def _lookup(namespace, name):
    """Fetch an array the drawing needs, or explain that it was never created."""
    if name not in namespace:
        raise ModelError(f'"{name}" is not defined — create it first, e.g. {name} = np.arange(9).reshape(3, 3)')
    return namespace[name]


def _check_numeric(array, name):
    if not _is_ndarray(array):
        raise ModelError(f"{name} is not a NumPy array — build it with np.array(...) or np.arange(...)")
    if array.dtype.kind not in "iufb":
        raise ModelError(f"{name} holds {array.dtype} values — only numbers (and booleans) can be drawn")
    if array.ndim == 0:
        raise ModelError(f"{name} is a single number, not an array")
    if array.ndim > 2:
        raise ModelError(f"{name} is {array.ndim}-D — only 1-D and 2-D arrays can be drawn")


def _check_dims(rows, cols, name, limit_1d=MAX_1D):
    if rows < 1 or cols < 1:
        raise ModelError(f"{name} is empty — nothing to draw")
    if rows == 1:
        if cols > limit_1d:
            raise ModelError(f"{name} has {cols} values — keep 1-D arrays within {limit_1d} so the cells stay readable")
        return
    if rows > MAX_DIM or cols > MAX_DIM:
        raise ModelError(f"{name} is {rows}×{cols} — keep arrays within {MAX_DIM}×{MAX_DIM} so the cells stay readable")


def _to_grid(array, name, limit_1d=MAX_1D):
    """Validate an ndarray and convert it to a JSON-safe 2-D list of numbers.

    `limit_1d` is raised for *results*: a source 1-D array is drawn as one row
    of cells, but a 1-D result goes into the wrapping strip (see drawFilter),
    which reflows and shrinks its cells to fit.
    """
    _check_numeric(array, name)
    rows, cols = (1, array.shape[0]) if array.ndim == 1 else array.shape
    _check_dims(rows, cols, name, limit_1d)

    grid = []
    for row in (array.reshape(1, -1) if array.ndim == 1 else array).tolist():
        out = []
        for value in row:
            number = float(value)
            if number != number or number in (float("inf"), float("-inf")):
                raise ModelError(f"{name} contains NaN or infinity, which cannot be drawn")
            rounded = round(number, ROUND_DP)
            out.append(int(rounded) if rounded == int(rounded) else rounded)
        grid.append(out)
    return grid


# --------------------------------------------------------------------------
# Finding the statement to animate
# --------------------------------------------------------------------------
def _classify(node):
    """Is this expression node animatable? -> "subscript" | "binop" | None."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        return "subscript"
    if isinstance(node, ast.BinOp) and isinstance(node.left, ast.Name) and type(node.op) in BIN_OPS:
        return "binop"
    return None


def _find_target(tree):
    """Return (index, expression node, output name) of the last animatable stmt."""
    found = None
    for index, stmt in enumerate(tree.body):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            if _classify(stmt.value):
                found = (index, stmt.value, stmt.targets[0].id)
        elif isinstance(stmt, ast.Expr) and _classify(stmt.value):
            found = (index, stmt.value, "result")
    if found is None:
        raise ModelError(
            "add an expression to animate — a slice (C = A[1:5, 1:5]), a mask (C = A[A > 50]) "
            "or arithmetic (C = A + 10, C = A * B)")
    return found


# --------------------------------------------------------------------------
# Index geometry
# --------------------------------------------------------------------------
def _eval_number(node, namespace, deadline, what):
    value = _eval(node, namespace, deadline)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ModelError(f"{what} must be a number")
    if number != number:
        raise ModelError(f"{what} must be a number")
    return number


def _eval_int(node, namespace, deadline, what):
    number = _eval_number(node, namespace, deadline, what)
    if number != int(number):
        raise ModelError(f"{what} must be a whole number, got {number}")
    return int(number)


def _resolve_index(node, size, axis, namespace, deadline):
    """Resolve one index spec to (start, stop, dropped_axis)."""
    if isinstance(node, ast.Slice):
        if node.step is not None:
            step = _eval_int(node.step, namespace, deadline, "a slice step")
            if step != 1:
                raise ModelError(f"slice steps (a:b:{step}) are not supported yet")
        lower = None if node.lower is None else _eval_int(node.lower, namespace, deadline, f"the {axis} start")
        upper = None if node.upper is None else _eval_int(node.upper, namespace, deadline, f"the {axis} stop")
        start, stop, _ = slice(lower, upper).indices(size)
        if stop <= start:
            shown = f"{'' if lower is None else lower}:{'' if upper is None else upper}"
            raise ModelError(f"{shown} selects nothing along the {axis} axis (size {size})")
        return start, stop, False

    index = _eval_int(node, namespace, deadline, f"the {axis} index")
    normalized = index + size if index < 0 else index
    if not 0 <= normalized < size:
        raise ModelError(f"{axis} index {index} is out of bounds for size {size}")
    return normalized, normalized + 1, True


# --------------------------------------------------------------------------
# Target builders
# --------------------------------------------------------------------------
def _build_subscript(node, namespace, deadline):
    name = node.value.id
    array = _lookup(namespace, name)
    _check_numeric(array, f'"{name}"')
    rows, cols = (1, array.shape[0]) if array.ndim == 1 else array.shape
    index = node.slice

    # Boolean mask: A[A > 50]
    if isinstance(index, ast.Compare):
        if len(index.ops) != 1:
            raise ModelError("chained comparisons are not supported — use one condition, e.g. A[A > 50]")
        operator = type(index.ops[0])
        if operator not in CMP_SYMBOLS:
            raise ModelError("that comparison cannot be drawn — use >, <, >=, <=, == or !=")
        if not (isinstance(index.left, ast.Name) and index.left.id == name):
            raise ModelError(f"the mask must test the same array, e.g. {name}[{name} > 50]")
        threshold = _eval_number(index.comparators[0], namespace, deadline, "the mask threshold")
        return {
            "mode": "filter",
            "a": name,
            "cmp": CMP_SYMBOLS[operator],
            "thresh": round(threshold, ROUND_DP),
        }

    # Index / slice: A[1:5, 1:5]
    if array.ndim == 1:
        if isinstance(index, ast.Tuple):
            raise ModelError(f'"{name}" is 1-D — it takes a single index, e.g. {name}[1:5]')
        c0, c1, _ = _resolve_index(index, cols, "value", namespace, deadline)
        return {"mode": "slice", "a": name, "r0": 0, "r1": 1, "c0": c0, "c1": c1}

    specs = list(index.elts) if isinstance(index, ast.Tuple) else [index]
    if len(specs) > 2:
        raise ModelError("only 1-D and 2-D indexing can be drawn")
    r0, r1, _ = _resolve_index(specs[0], rows, "row", namespace, deadline)
    if len(specs) == 2:
        c0, c1, _ = _resolve_index(specs[1], cols, "column", namespace, deadline)
    else:
        c0, c1 = 0, cols
    return {"mode": "slice", "a": name, "r0": r0, "r1": r1, "c0": c0, "c1": c1}


def _build_binop(node, namespace, deadline):
    name = node.left.id
    left = _lookup(namespace, name)
    _check_numeric(left, f'"{name}"')
    operator = BIN_OPS[type(node.op)]

    # Classify by the *runtime* value, so `A * factor` with factor = 3 is a
    # scalar broadcast while `A * B` with an array B is element-wise.
    right = _eval(node.right, namespace, deadline)

    if _is_ndarray(right) and right.ndim > 0:
        if not isinstance(node.right, ast.Name):
            raise ModelError("the right-hand side must be a single array name or a number")
        other = node.right.id
        _check_numeric(right, f'"{other}"')
        if right.shape != left.shape:
            raise ModelError(
                f"shapes do not match: {name} is {'×'.join(map(str, left.shape))}, "
                f"{other} is {'×'.join(map(str, right.shape))}")
        return {"mode": "array", "a": name, "b": other, "op": operator}

    try:
        operand = float(right)
    except (TypeError, ValueError):
        raise ModelError("the right-hand side must be a single array name or a number")
    if operand != operand:
        raise ModelError("the right-hand side must be a number")
    if operator == "/" and operand == 0:
        raise ModelError("division by zero")
    rounded = round(operand, ROUND_DP)
    return {
        "mode": "scalar",
        "a": name,
        "op": operator,
        "operand": int(rounded) if rounded == int(rounded) else rounded,
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def analyze(source):
    """Turn a NumPy snippet into the model the canvas animates.

    Returns {"arrays": {name: 2-D list}, "oneD": {name: bool}, "target": {...}}.
    Raises ModelError for anything the user can fix, UnsafeCodeError /
    ExecutionTimeout from the sandbox layers.
    """
    if not isinstance(source, str) or not source.strip():
        raise ModelError("write some NumPy first")
    if len(source) > MAX_CODE_LEN:
        raise ModelError(f"code too long ({len(source)} chars, max {MAX_CODE_LEN})")

    # Parsed first purely for the better message (check_safe would report the
    # same SyntaxError less helpfully); nothing is executed until check_safe has
    # run and every sandbox layer is in place.
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ModelError(f"SyntaxError: {e.msg} (line {e.lineno})")
    check_safe(source, ALLOWED_IMPORTS)

    target_index, target_node, out_name = _find_target(tree)

    namespace = make_restricted_globals(ALLOWED_IMPORTS)
    deadline = time.monotonic() + TRACE_TIMEOUT_SECONDS
    try:
        # Everything *before* the target statement — the target itself is
        # validated and then evaluated below, so that this module's errors
        # ("shapes do not match: A is 2×2, B is 3×3") reach the user instead of
        # NumPy's raw broadcast traceback. Stopping here also means a later
        # rebinding of A cannot change the frame being drawn.
        _run(tree.body[:target_index], namespace, deadline)
    except (ModelError, ExecutionTimeout):
        raise
    except Exception as e:
        raise ModelError(f"{type(e).__name__}: {e}")

    kind = _classify(target_node)
    build = _build_subscript if kind == "subscript" else _build_binop
    try:
        target = build(target_node, namespace, deadline)
    except (ModelError, ExecutionTimeout):
        raise
    except Exception as e:
        raise ModelError(f"{type(e).__name__}: {e}")

    # Real NumPy computes the result — including dtype and broadcasting rules.
    try:
        result = _eval(target_node, namespace, deadline)
    except (ExecutionTimeout,):
        raise
    except Exception as e:
        raise ModelError(f"{type(e).__name__}: {e}")

    if not _is_ndarray(result):  # e.g. A[2, 3] -> a plain scalar
        raise ModelError("that expression produces a single number, not an array — try a range, e.g. A[0:2, 0:2]")

    target["out"] = out_name
    target["oneD"] = bool(result.ndim <= 1)
    target["result"] = _to_grid(result, f'"{out_name}"', limit_1d=MAX_CELLS)

    # The drawn arrays must convert; other ndarrays are best-effort extras so
    # the sidebar can list their shapes.
    arrays, one_d = {}, {}
    for name in filter(None, [target["a"], target.get("b")]):
        arrays[name] = _to_grid(namespace[name], f'"{name}"')
        one_d[name] = bool(namespace[name].ndim == 1)
    for name, value in namespace.items():
        if name in arrays or name.startswith("__") or not _is_ndarray(value):
            continue
        try:
            arrays[name] = _to_grid(value, f'"{name}"')
            one_d[name] = bool(value.ndim == 1)
        except ModelError:
            continue  # too big / wrong dtype to show — not fatal, just skip it

    return {"arrays": arrays, "oneD": one_d, "target": target}
