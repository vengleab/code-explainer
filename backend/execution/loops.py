"""
backend/execution/loops.py — loop analysis for the plain-Python visualizer (logic, no drawing).

This is where "which `for` loops exist", "which iteration is each header step on",
and "which list element is current" are decided. Keeping it separate from the
drawing code (render/panels.py) is the point of the refactor: the LoopListPanel receives
a computed index and only styles done/current/waiting — it computes nothing.

The pandas visualizer does not use this module (it tracks DataFrames, not loops).
"""
import ast
import copy

# Same-subpackage sibling: a plain relative import resolves in both environments.
from .models import Loop


def find_for_loops(source):
    """Return the Name-target/Name-iterable `for` loops in `source`.

    Only `for x in xs:` shapes are tracked — the ones we can draw a concrete
    list-progress panel for. `for i in range(3)`, `for x in [1, 2]`, and
    tuple-unpacking targets are intentionally excluded (no named list to show).
    """
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
            loops.append(Loop(header=node.lineno, start=node.lineno, end=last_line,
                              loop_var=node.target.id, iterable_name=node.iter.id))
    return loops


def active_loop(line, loops):
    """Return the innermost loop containing `line`, or None if none does."""
    candidates = [loop for loop in loops if loop.contains(line)]
    if not candidates:
        return None
    return min(candidates, key=lambda loop: loop.span)


def current_index(step, loop):
    """Positional index of the element `loop` is on at `step`, or None / -1.

    Prefers the authoritative index stamped by fix_loop_headers
    (step.loop_indices); on body steps with no stamp, falls back to locating
    the loop variable's value in the sequence. Returns None when the iterable
    isn't a concrete list/tuple at this step, and -1 when the fallback lookup
    fails (e.g. the loop variable was rebound to a value not in the list).

    This is the logic that used to live inline in render(); the test suite's
    resolve_current_idx() helper now just calls this instead of re-implementing
    it, so the two can no longer drift.
    """
    sequence = step.variables.get(loop.iterable_name)
    if not isinstance(sequence, (list, tuple)):
        return None
    forced = step.loop_indices.get(loop.iterable_name)
    if forced is not None:
        return forced
    target_value = step.variables.get(loop.loop_var)
    try:
        return list(sequence).index(target_value)
    except (ValueError, TypeError):
        return -1


def fix_loop_headers(steps, loops):
    """Correct `for`-header snapshots and stamp each step's current list index.

    Two problems are fixed here, both rooted in the tracer snapshotting locals
    *before* a line's bytecode runs (see the comment in tracer.Tracer.trace):

    1. One-iteration lag on headers. For a `for` header the pending bytecode is
       the FOR_ITER that advances the iterator and binds the loop variable, so
       the header snapshot shows the *previous* iteration's binding (or none, on
       first entry) — e.g. "running line 3" the 2nd time still shows
       fruit='apple' when the iteration it initiates is 'banana'. We rewrite the
       header snapshot's loop variable to the value the following body step runs
       with (the post-FOR_ITER binding), so the variables panel is correct.

    2. Which list element is "current". A LoopListPanel highlights one element of
       the iterable per step. Locating it by value (list.index(loop_var)) is wrong
       whenever the list has duplicates — the 3rd 'cat' would resolve to the 1st
       — and breaks entirely if the body rebinds the loop variable. Instead we
       record a *positional* index in step.loop_indices, keyed by iterable, on
       every step (header and body). It counts iterations (0, 1, 2, …) and
       equals len(seq) on the terminating pass — iterator exhausted, loop about
       to exit — so the list renders fully done with nothing current.

    For nested loops over the *same* iterable, the innermost loop (smallest line
    span) wins, matching active_loop()'s choice, so the two never disagree about
    which element is current.

    Only loops in `loops` are touched; while-loops, `for i in range(...)`, and
    loop-free code are left untouched.
    """
    if not loops:
        return steps
    loops_by_header_line = {}
    for loop in loops:
        loops_by_header_line.setdefault(loop.header, []).append(loop)
    iteration_count = {}  # header line -> current 0-based iteration (len(seq) once exhausted)
    prev_line = None
    for i, step in enumerate(steps):
        line = step.line
        # (1) At a loop header: advance that loop's iteration counter, and fix
        #     the header snapshot's loop variable to the binding the iteration
        #     it initiates actually uses (read from the next body step).
        for loop in loops_by_header_line.get(line, []):
            is_re_entry = prev_line is not None and loop.start <= prev_line <= loop.end
            iteration_count[loop.header] = (
                iteration_count.get(loop.header, -1) + 1 if is_re_entry else 0)
            next_step = steps[i + 1] if i + 1 < len(steps) else None
            next_in_body = (next_step and next_step.line is not None
                            and loop.start <= next_step.line <= loop.end)
            if next_in_body and loop.loop_var in next_step.variables:
                step.variables[loop.loop_var] = copy.deepcopy(next_step.variables[loop.loop_var])
        # (2) Stamp the positional index of the innermost active loop per
        #     iterable onto this step (header or body). Innermost-wins keeps
        #     nested loops over one iterable consistent with active_loop().
        if line is not None:
            innermost = {}  # iterable -> (line_span, iteration_idx)
            for loop in loops:
                if loop.start <= line <= loop.end and loop.header in iteration_count:
                    if loop.iterable_name not in innermost or loop.span < innermost[loop.iterable_name][0]:
                        innermost[loop.iterable_name] = (loop.span, iteration_count[loop.header])
            for iterable_name, (_span, idx) in innermost.items():
                step.loop_indices[iterable_name] = idx
        prev_line = line
    return steps
