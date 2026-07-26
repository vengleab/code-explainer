"""
backend/execution/masks.py — detect boolean-mask array filters in a snippet's AST.

Mirrors the "geometry from the AST, values from real execution" split described in
CLAUDE.md's NumPy page section, applied here to the generic multi-step pandas/GIF
tracer: whether a smaller array is a *filtered* subset of a bigger one (rather than
a slice) can't be told apart from the values alone — a slice and a mask result look
identical once you only have the surviving values (see composer.py's `_order_grids`,
which deliberately stopped guessing this from ndarray index labels). So this module
reads the one shape of code that unambiguously says "filter": a bare

    child = parent[parent <cmp> constant]

Only this exact shape is recognized; anything else (a slice, a mask against a
different array, a variable threshold, a multi-condition mask) yields no match,
which keeps the caller's default (no highlighting) safe.
"""
import ast
from dataclasses import dataclass

CMP_OPS = {
    ast.Gt: ">", ast.Lt: "<", ast.GtE: ">=", ast.LtE: "<=",
    ast.Eq: "==", ast.NotEq: "!=",
}

_APPLY = {
    ">": lambda arr, t: arr > t,
    "<": lambda arr, t: arr < t,
    ">=": lambda arr, t: arr >= t,
    "<=": lambda arr, t: arr <= t,
    "==": lambda arr, t: arr == t,
    "!=": lambda arr, t: arr != t,
}


@dataclass(frozen=True)
class ArrayMaskFilter:
    """A detected `child = parent[parent <cmp> thresh]` assignment."""
    child_name: str
    parent_name: str
    cmp: str
    thresh: float


def apply_cmp(arr, cmp, thresh):
    """Elementwise `arr <cmp> thresh` for one of the six comparison symbols."""
    return _APPLY[cmp](arr, thresh)


def find_array_mask_filters(source):
    """Return every top-level `child = parent[parent <cmp> constant]` assignment."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    filters = []
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        value = node.value
        if not (isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name)):
            continue
        parent_name = value.value.id
        compare_node = value.slice
        if not (isinstance(compare_node, ast.Compare) and len(compare_node.ops) == 1
                and len(compare_node.comparators) == 1):
            continue
        left = compare_node.left
        if not (isinstance(left, ast.Name) and left.id == parent_name):
            continue
        op_type = type(compare_node.ops[0])
        if op_type not in CMP_OPS:
            continue
        comparator = compare_node.comparators[0]
        if not (isinstance(comparator, ast.Constant) and isinstance(comparator.value, (int, float))):
            continue
        filters.append(ArrayMaskFilter(
            child_name=node.targets[0].id, parent_name=parent_name,
            cmp=CMP_OPS[op_type], thresh=comparator.value,
        ))
    return filters
