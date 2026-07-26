"""
backend/generate.py — Vercel Python service (WSGI entrypoint: generate:app).

The plain-Python execution-GIF endpoint (POST/GET /api/generate). This file is
now just wiring: it declares this endpoint's import policy and quality presets,
then assembles the shared layers into a WSGI app. The actual work lives in:

  runtime/sandbox.py     — is the submitted code safe to run?
  execution/tracer.py    — run it under sys.settrace, snapshot each line (PythonTracer)
  execution/loops.py     — find `for` loops, fix header lag, index the current element
  render/{canvas,panels,composer}.py — draw each step into a frame
  visualizer.py          — the trace -> analyze -> compose -> encode pipeline
  runtime/serverless.py  — the HTTP/WSGI protocol + GIF encoding

See CLAUDE.md for the architecture overview. The pandas endpoint
(generate_pandas.py) is the same wiring with a different tracer/composer.
"""
try:  # package import in dev (imported as backend.generate)
    from .visualizer import PythonVisualizer
    from .runtime.serverless import make_app, encode_gif
    from .runtime.sandbox import STDLIB_IMPORTS
except ImportError:  # top-level module on the serverless runtime
    from visualizer import PythonVisualizer
    from runtime.serverless import make_app, encode_gif
    from runtime.sandbox import STDLIB_IMPORTS

# This endpoint's import allowlist: the shared stdlib set, nothing added — no
# pandas/numpy here.
ALLOWED_IMPORTS = set(STDLIB_IMPORTS)

# Quality presets: map a user-facing label to (code font size, final downscale).
# The Lanczos downscale keeps the canvas proportional so file size scales with
# quality.
QUALITY_PRESETS = {
    "low":    {"code_size": 22, "scale": 0.6},
    "medium": {"code_size": 34, "scale": 1.0},
    "high":   {"code_size": 46, "scale": 1.4},
}

_visualizer = PythonVisualizer(ALLOWED_IMPORTS)

# Re-exported for tests and the golden-image tooling.
build_frames = _visualizer.build_frames

app = make_app("/api/generate", _visualizer, QUALITY_PRESETS)
