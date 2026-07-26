"""
backend/execution/models.py — the data layer shared by both visualizers.

These are plain value objects (dataclasses). They hold *what happened* during a
traced run; they contain no tracing logic and no drawing code. The tracer
(tracer.py) produces them, the loop analysis (loops.py) annotates them, and the
panels (panels.py) read them — so a reader can lean on named attributes
(step.variables, loop.iterable_name) instead of stringly-typed dict keys.

Naming note: a `Step`'s `variables` dict holds every value visible at that line.
For the pandas visualizer those values include DataFrames/Series (which the
pandas composer draws as grids) alongside plain scalars — so the field is called
`variables`, not `dataframes`, because it honestly holds both.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Loop:
    """A `for` loop we can draw list-progress for (Name target over a Name iterable).

    `header`/`start`/`end` are 1-based source line numbers: `header` is the
    `for` line, `start`..`end` spans the header through the last body line.
    """
    header: int
    start: int
    end: int
    loop_var: str        # the loop variable name, e.g. "x" in `for x in xs`
    iterable_name: str   # the iterated name, e.g. "xs" in `for x in xs`

    def contains(self, line: Optional[int]) -> bool:
        """True if `line` falls within this loop's header-to-body span."""
        return line is not None and self.start <= line <= self.end

    @property
    def span(self) -> int:
        """Line spread; smaller span means a more deeply nested (inner) loop."""
        return self.end - self.start


@dataclass
class Step:
    """One traced line: the source line about to run, plus a snapshot of state.

    A `line` of None marks the synthetic final step (execution finished), whose
    `variables` hold the end-of-run state and whose `error` is set if the run
    raised or hit a limit.
    """
    line: Optional[int]
    variables: dict = field(default_factory=dict)
    stdout: str = ""
    is_final: bool = False
    error: Optional[str] = None


@dataclass
class PythonStep(Step):
    """A plain-Python step, additionally tracking loop progress.

    `loop_indices` maps an iterable's name to the positional index the loop is
    on at this step (stamped by loops.fix_loop_headers); it equals len(seq) on
    the terminating pass, so the list renders fully done.
    """
    loop_indices: dict = field(default_factory=dict)
