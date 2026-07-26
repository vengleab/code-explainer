"""
backend/visualizer.py — the pipeline orchestrator (Template Method).

`Visualizer.build_frames` owns the fixed pipeline shape shared by both modes:

    trace  →  analyze  →  compose each step into a frame  →  (optional downscale)

Subclasses fill in the two mode-specific steps:

  - _trace_and_analyze(source) — run the right tracer, then any post-processing
    (the plain-Python mode fixes loop headers; pandas has nothing to analyze),
    returning the steps plus any context the composer needs (the loops list).
  - _make_composer(palette, code_size, context) — build the matching composer.

This is the seam that used to be a whole duplicated build_frames() in each
generate*.py file.
"""
try:  # package import in dev (imported as backend.visualizer)
    from .runtime.sandbox import MS_MIN, MS_MAX
    from .render.theme import get_palette
    from .execution.tracer import PythonTracer, PandasTracer
    from .execution.loops import find_for_loops, fix_loop_headers
    from .render.composer import PythonComposer, PandasComposer
except ImportError:  # top-level module on the serverless runtime
    from runtime.sandbox import MS_MIN, MS_MAX
    from render.theme import get_palette
    from execution.tracer import PythonTracer, PandasTracer
    from execution.loops import find_for_loops, fix_loop_headers
    from render.composer import PythonComposer, PandasComposer

from PIL import Image


class Visualizer:
    """Base pipeline. Construct with the endpoint's allowed-import policy."""

    # The plain-Python timings; PandasVisualizer overrides all three. Also read
    # by serverless.make_app, so `default_ms` is defined only here.
    default_ms = 900
    default_code_size = 34
    final_duration_mult = 2.6   # the last frame lingers this many x longer

    def __init__(self, allowed_imports):
        self.allowed_imports = allowed_imports

    # ── hooks ─────────────────────────────────────────────────────────
    def _trace_and_analyze(self, source):
        """Return (steps, context). Override per mode."""
        raise NotImplementedError

    def _make_composer(self, palette_colors, code_size, context):
        """Return the FrameComposer for this mode. Override."""
        raise NotImplementedError

    # ── template method ───────────────────────────────────────────────
    def build_frames(self, source, ms=None, code_size=None, scale=1.0, palette="dark"):
        ms = self.default_ms if ms is None else ms
        code_size = self.default_code_size if code_size is None else code_size
        ms = max(MS_MIN, min(MS_MAX, ms))
        src_lines = source.splitlines()
        steps, context = self._trace_and_analyze(source)
        composer = self._make_composer(get_palette(palette), code_size, context)
        layout = composer.compute_layout(steps, src_lines)

        frames, durations = [], []
        for i, step in enumerate(steps):
            frame = composer.compose(step, i, steps, src_lines, layout)
            if scale != 1.0:
                new_w = max(1, int(frame.width * scale))
                new_h = max(1, int(frame.height * scale))
                frame = frame.resize((new_w, new_h), Image.LANCZOS)
            frames.append(frame)
            durations.append(int(ms * self.final_duration_mult) if step.is_final else ms)
        return frames, durations


class PythonVisualizer(Visualizer):
    # Timings inherited from Visualizer (see the note there).

    def _trace_and_analyze(self, source):
        loops = find_for_loops(source)
        steps = fix_loop_headers(PythonTracer(self.allowed_imports).trace(source), loops)
        return steps, loops

    def _make_composer(self, palette_colors, code_size, context):
        return PythonComposer(palette_colors, code_size, loops=context)


class PandasVisualizer(Visualizer):
    default_ms = 1100
    default_code_size = 17
    final_duration_mult = 2.4

    def _trace_and_analyze(self, source):
        return PandasTracer(self.allowed_imports).trace(source), None

    def _make_composer(self, palette_colors, code_size, context):
        return PandasComposer(palette_colors, code_size)
