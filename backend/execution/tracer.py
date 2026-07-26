"""
backend/execution/tracer.py — execute a snippet under sys.settrace and snapshot each line.

`Tracer` (Template Method) owns the machinery that is identical for both
visualizers: the AST safety pre-check, compiling, the settrace line/return loop,
the MAX_STEPS / wall-clock guards, stdout capture, and building the synthetic
final step. Subclasses fill in the parts that genuinely differ:

  - PythonTracer  — records every line in the snippet (including inside functions
                    the snippet defines), snapshots *all* showable locals, and
                    reads final state from the module frame's `return` event.
  - PandasTracer  — records only module-level lines (so pandas internals and
                    .apply() lambdas don't explode the step count), snapshots
                    only DataFrames/Series/scalars, and reads final state by
                    scanning the namespace after exec().

IMPORTANT (see fix_loop_headers in loops.py): the "line" event fires *before*
the line's bytecode runs, so each step snapshots state as it is *entering* that
line. Loop headers are corrected downstream for this.
"""
import copy
import io
import sys
import time
import types

# Same-subpackage sibling — resolves in both environments, so it stays OUT of the
# try below (a shared block would make a cross-package failure report *this* line).
from .models import Step, PythonStep

try:  # dev: backend.execution.tracer, so `..` is backend
    from ..runtime.sandbox import (check_safe, make_restricted_globals,
                                   StepLimitExceeded, ExecutionTimeout,
                                   MAX_STEPS, TRACE_TIMEOUT_SECONDS)
except ImportError:  # Vercel: execution.tracer, so `..` is beyond the top level
    from runtime.sandbox import (check_safe, make_restricted_globals,
                                 StepLimitExceeded, ExecutionTimeout,
                                 MAX_STEPS, TRACE_TIMEOUT_SECONDS)


class Tracer:
    """Base tracer. Subclass and override the four hooks below."""

    filename = "<snippet>"          # compile name + the frame filter key
    trace_into_nonrecording = True  # keep tracing (vs stop) in frames we don't record
    track_return = False            # capture final state from the return event?

    def __init__(self, allowed_imports):
        self.allowed_imports = allowed_imports

    # ── hooks ─────────────────────────────────────────────────────────
    def should_record(self, frame):
        """Whether line events in `frame` become steps."""
        return frame.f_code.co_filename == self.filename

    def snapshot(self, frame):
        """Return the {name: value} snapshot drawn for this line. Override."""
        raise NotImplementedError

    def final_variables(self, namespace, last_return_vars, steps):
        """Return the {name: value} state for the synthetic final step. Override."""
        raise NotImplementedError

    def make_step(self, line, variables, stdout, is_final=False, error=None):
        """Build the Step subclass this tracer emits. Override."""
        raise NotImplementedError

    # ── template method ───────────────────────────────────────────────
    def trace(self, source):
        """Execute `source` under the sandbox and return the list of Steps."""
        check_safe(source, self.allowed_imports)
        compiled = compile(source, self.filename, "exec")
        steps = []
        stdout_buffer = io.StringIO()
        start_time = time.monotonic()
        last_return_vars = [None]  # boxed so the closure can rebind it

        def tracer(frame, event, arg):
            if not self.should_record(frame):
                return tracer if self.trace_into_nonrecording else None
            if event == "line":
                if len(steps) >= MAX_STEPS:
                    raise StepLimitExceeded(f"step limit ({MAX_STEPS}) reached")
                if time.monotonic() - start_time > TRACE_TIMEOUT_SECONDS:
                    raise ExecutionTimeout(f"tracing exceeded {TRACE_TIMEOUT_SECONDS}s")
                steps.append(self.make_step(frame.f_lineno, self.snapshot(frame),
                                            stdout_buffer.getvalue()))
            elif event == "return" and self.track_return:
                # Fires once the traced frame finishes; the only point where the
                # effect of the *last* executed line is observable (line events
                # snapshot *before* each line runs).
                last_return_vars[0] = self.snapshot(frame)
            return tracer

        real_stdout = sys.stdout
        sys.stdout = stdout_buffer
        sys.settrace(tracer)
        namespace = make_restricted_globals(self.allowed_imports)
        error_message = None
        try:
            exec(compiled, namespace)
        except (StepLimitExceeded, ExecutionTimeout) as e:
            error_message = str(e)
        except Exception as e:
            error_message = f"{type(e).__name__}: {e}"
        finally:
            sys.settrace(None)
            sys.stdout = real_stdout

        final_vars = self.final_variables(namespace, last_return_vars[0], steps)
        steps.append(self.make_step(None, final_vars, stdout_buffer.getvalue(),
                                    is_final=True, error=error_message))
        return steps


def _is_showable(value):
    """Exclude modules, functions, classes, and methods from the variables panel."""
    return not isinstance(value, (types.ModuleType, types.FunctionType,
                                  types.BuiltinFunctionType, type, types.MethodType))


class PythonTracer(Tracer):
    """Plain-Python tracer: deep-copies every showable local per line."""

    filename = "<snippet>"
    track_return = True

    def snapshot(self, frame):
        snapshot = {}
        for name, value in frame.f_locals.items():
            if name.startswith("__") or not _is_showable(value):
                continue
            try:
                snapshot[name] = copy.deepcopy(value)
            except Exception:
                snapshot[name] = repr(value)
        return snapshot

    def final_variables(self, namespace, last_return_vars, steps):
        if last_return_vars:
            return last_return_vars
        return steps[-1].variables if steps else {}

    def make_step(self, line, variables, stdout, is_final=False, error=None):
        return PythonStep(line=line, variables=variables, stdout=stdout,
                          is_final=is_final, error=error)


def _snapshot_pandas_value(value):
    """Copy DataFrames/Series/scalars for the snapshot; return (kept, copy)."""
    import pandas as pd  # local import: only the pandas tracer needs it
    if isinstance(value, (pd.DataFrame, pd.Series)):
        return True, value.copy()
    is_scalar = isinstance(value, (int, float, str, bool)) or (
        hasattr(value, "dtype") and getattr(value, "shape", None) == ())
    if is_scalar:
        try:
            return True, copy.copy(value)
        except Exception:
            return False, None
    return False, None


class PandasTracer(Tracer):
    """Pandas tracer: snapshots DataFrames/Series/scalars, module-level lines only."""

    filename = "<snip>"
    trace_into_nonrecording = False  # don't step into pandas internals / .apply lambdas

    def should_record(self, frame):
        return frame.f_code.co_filename == self.filename and frame.f_code.co_name == "<module>"

    def snapshot(self, frame):
        snapshot = {}
        for name, value in frame.f_locals.items():
            if name.startswith("__"):
                continue
            kept, copied = _snapshot_pandas_value(value)
            if kept:
                snapshot[name] = copied
        return snapshot

    def final_variables(self, namespace, last_return_vars, steps):
        # Pandas reads end state from the namespace, not a return event.
        final = {}
        for name, value in namespace.items():
            if name.startswith("__"):
                continue
            kept, copied = _snapshot_pandas_value(value)
            if kept:
                final[name] = copied
        return final

    def make_step(self, line, variables, stdout, is_final=False, error=None):
        return Step(line=line, variables=variables, stdout=stdout,
                    is_final=is_final, error=error)
