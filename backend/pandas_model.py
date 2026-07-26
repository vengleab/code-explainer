"""
backend/pandas_model.py — turn a Pandas snippet into a JSON drawable model.

Analyzes Pandas expressions into a structured JSON payload ({ dfs, target })
so the browser can animate DataFrames on an HTML5 canvas.
"""
import ast
import io
import sys
import time
import warnings

try:
    from .runtime.sandbox import (check_safe, make_restricted_globals, MAX_CODE_LEN,
                                  ExecutionTimeout, TRACE_TIMEOUT_SECONDS)
except ImportError:
    from runtime.sandbox import (check_safe, make_restricted_globals, MAX_CODE_LEN,
                                 ExecutionTimeout, TRACE_TIMEOUT_SECONDS)

ALLOWED_IMPORTS = {
    "pandas", "pd", "numpy", "np", "math", "random", "itertools", "functools", "statistics"
}

MAX_ROWS = 12
MAX_COLS = 10
ROUND_DP = 4

CMP_SYMBOLS = {
    ast.Gt: ">", ast.Lt: "<", ast.GtE: ">=", ast.LtE: "<=",
    ast.Eq: "==", ast.NotEq: "!=",
}


class ModelError(Exception):
    """User-facing problem with the snippet (message is shown in the sidebar)."""


def _timeout_guard(deadline):
    def tracer(frame, event, arg):
        if event == "line" and time.monotonic() > deadline:
            raise ExecutionTimeout(f"execution exceeded {TRACE_TIMEOUT_SECONDS}s")
        return tracer
    return tracer


def _run(statements, namespace, deadline):
    real_stdout = sys.stdout
    sys.stdout = io.StringIO()
    sys.settrace(_timeout_guard(deadline))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for stmt in statements:
                module = ast.Module(body=[stmt], type_ignores=[])
                exec(compile(ast.fix_missing_locations(module), "<snippet>", "exec"), namespace)
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout


def _eval(node, namespace, deadline):
    sys.settrace(_timeout_guard(deadline))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            expression = ast.Expression(body=node)
            return eval(compile(ast.fix_missing_locations(expression), "<snippet>", "eval"), namespace)
    finally:
        sys.settrace(None)


def _is_df_or_series(val):
    mod = getattr(type(val), "__module__", "")
    name = getattr(type(val), "__name__", "")
    return "pandas" in mod and name in ("DataFrame", "Series")


def _to_df_dict(obj, name):
    if not _is_df_or_series(obj):
        raise ModelError(f'"{name}" is not a Pandas DataFrame or Series')
    
    import pandas as pd
    import numpy as np

    df = obj.to_frame() if isinstance(obj, pd.Series) else obj.copy()

    if len(df.index) > MAX_ROWS:
        df = df.iloc[:MAX_ROWS]
    if len(df.columns) > MAX_COLS:
        df = df.iloc[:, :MAX_COLS]

    columns = [str(c) for c in df.columns]
    index_labels = [str(i) for i in df.index]

    data = []
    dtypes = {}
    for col in df.columns:
        dtypes[str(col)] = str(df[col].dtype)

    for i in range(len(df)):
        row = []
        for col in df.columns:
            val = df.iloc[i][col]
            if pd.isna(val):
                row.append("NaN")
            elif isinstance(val, (int, np.integer)):
                row.append(int(val))
            elif isinstance(val, (float, np.floating)):
                rounded = round(float(val), ROUND_DP)
                row.append(int(rounded) if rounded == int(rounded) else rounded)
            else:
                row.append(str(val))
        data.append(row)

    return {
        "columns": columns,
        "index": index_labels,
        "data": data,
        "dtypes": dtypes,
        "shape": [len(df), len(df.columns)],
    }


def _classify_target(stmt):
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], (ast.Name, ast.Subscript)):
        target_node = stmt.targets[0]
        out_name = target_node.id if isinstance(target_node, ast.Name) else "df"
        return stmt.value, out_name
    elif isinstance(stmt, ast.Expr):
        return stmt.value, "result"
    return None, None


def _find_target(tree):
    found = None
    for index, stmt in enumerate(tree.body):
        expr_node, out_name = _classify_target(stmt)
        if expr_node is not None:
            found = (index, expr_node, out_name)
    if found is None:
        raise ModelError(
            "add a Pandas expression to animate — e.g. summary = df.groupby('dept').mean(), "
            "top = df[df['salary'] > 55], or df['bonus'] = df['salary'] * 0.1"
        )
    return found


def analyze(source):
    if not isinstance(source, str) or not source.strip():
        raise ModelError("write some Pandas code first")
    if len(source) > MAX_CODE_LEN:
        raise ModelError(f"code too long ({len(source)} chars, max {MAX_CODE_LEN})")

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ModelError(f"SyntaxError: {e.msg} (line {e.lineno})")

    check_safe(source, ALLOWED_IMPORTS)

    target_index, target_node, out_name = _find_target(tree)
    namespace = make_restricted_globals(ALLOWED_IMPORTS)
    deadline = time.monotonic() + TRACE_TIMEOUT_SECONDS

    try:
        _run(tree.body[:target_index], namespace, deadline)
    except (ModelError, ExecutionTimeout):
        raise
    except Exception as e:
        raise ModelError(f"{type(e).__name__}: {e}")

    try:
        res = _eval(target_node, namespace, deadline)
    except (ExecutionTimeout,):
        raise
    except Exception as e:
        raise ModelError(f"{type(e).__name__}: {e}")

    # Extract target metadata
    mode = "transform"
    source_name = "df"

    if isinstance(target_node, ast.Call):
        func = target_node.func
        if isinstance(func, ast.Attribute):
            attr_name = func.attr
            if attr_name in ("groupby", "mean", "sum", "count", "min", "max"):
                mode = "groupby"
            elif attr_name in ("sort_values", "sort_index"):
                mode = "sort"
            elif attr_name in ("fillna", "dropna", "replace"):
                mode = "fillna"
            elif attr_name in ("head", "tail", "iloc", "loc"):
                mode = "slice"
            if isinstance(func.value, ast.Name):
                source_name = func.value.id
            elif isinstance(func.value, ast.Call) and isinstance(func.value.func, ast.Attribute) and isinstance(func.value.func.value, ast.Name):
                source_name = func.value.func.value.id

    elif isinstance(target_node, ast.Subscript):
        if isinstance(target_node.value, ast.Name):
            source_name = target_node.value.id
        slice_spec = target_node.slice
        if isinstance(slice_spec, (ast.List, ast.Constant, ast.Str)):
            mode = "slice"  # Column selection e.g. df[['price']]
        else:
            mode = "filter" # Row filter e.g. df[df['price'] >= 450]

    result_dict = _to_df_dict(res, out_name)

    dfs = {}
    for name, val in list(namespace.items()):
        if not name.startswith("__") and _is_df_or_series(val):
            try:
                dfs[name] = _to_df_dict(val, name)
            except ModelError:
                continue

    if out_name not in dfs:
        dfs[out_name] = result_dict

    # Compute active matching row and column indices for source DataFrame
    active_rows = None
    active_cols = None
    if source_name in namespace and _is_df_or_series(namespace[source_name]) and _is_df_or_series(res):
        import pandas as pd
        src_obj = namespace[source_name]
        src_df = src_obj.to_frame() if isinstance(src_obj, pd.Series) else src_obj
        res_df = res.to_frame() if isinstance(res, pd.Series) else res

        try:
            res_idx_set = set(res_df.index)
            active_rows = [i for i, idx in enumerate(src_df.index) if idx in res_idx_set]
        except Exception:
            active_rows = None

        try:
            res_cols_set = set(str(c) for c in res_df.columns)
            active_cols = [j for j, col in enumerate(src_df.columns) if str(col) in res_cols_set]
            if len(active_cols) == len(src_df.columns) and mode == "filter":
                active_cols = None  # All columns active when doing a row filter
        except Exception:
            active_cols = None

    target = {
        "mode": mode,
        "source": source_name,
        "out": out_name,
        "expr": ast.unparse(target_node) if hasattr(ast, "unparse") else out_name,
        "result": result_dict,
        "active_rows": active_rows,
        "active_cols": active_cols,
    }

    return {"dfs": dfs, "target": target}
