"""
backend/generate_pandas.py — Vercel Python service (WSGI entrypoint: generate_pandas:app).

The pandas execution-GIF endpoint (POST/GET /api/generate-pandas). Same wiring as
generate.py, but with pandas/numpy added to the import allowlist, the pandas
tracer (snapshots DataFrames/Series), and the pandas composer (draws DataFrames
as diff-highlighted grids). The shared layers do the work:

  runtime/sandbox.py     — is the submitted code safe to run?
  execution/tracer.py    — run it, snapshot DataFrames/Series/scalars (PandasTracer)
  render/{canvas,panels,composer}.py — draw code + DataFrame grids + scalar strip
  visualizer.py          — the trace -> compose -> encode pipeline
  runtime/serverless.py  — the HTTP/WSGI protocol + GIF encoding

See CLAUDE.md for the architecture overview.
"""
try:  # package import in dev (imported as backend.generate_pandas)
    from .visualizer import PandasVisualizer
    from .runtime.serverless import make_app, encode_gif
    from .runtime.sandbox import STDLIB_IMPORTS
except ImportError:  # top-level module on the serverless runtime
    from visualizer import PandasVisualizer
    from runtime.serverless import make_app, encode_gif
    from runtime.sandbox import STDLIB_IMPORTS

# This endpoint additionally allows pandas/numpy (and their common aliases).
ALLOWED_IMPORTS = STDLIB_IMPORTS | {"pandas", "numpy", "pd", "np"}

# Denser presets than the plain-Python endpoint: table grids read fine smaller.
QUALITY_PRESETS = {
    "low":    {"code_size": 11, "scale": 0.6},
    "medium": {"code_size": 17, "scale": 1.0},
    "high":   {"code_size": 24, "scale": 1.4},
}

_visualizer = PandasVisualizer(ALLOWED_IMPORTS)

# Re-exported for tests and the golden-image tooling.
build_frames = _visualizer.build_frames

app = make_app("/api/generate-pandas", _visualizer, QUALITY_PRESETS)
