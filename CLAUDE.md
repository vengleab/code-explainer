# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Turns a small Python (or pandas) snippet into an execution GIF: code with the current line highlighted, line-by-line execution order, live variables, list progress for simple `for` loops, and captured stdout. Deployed on Vercel as a static React frontend plus two Python serverless functions.

## Commands

```bash
make install    # npm install in frontend/
make dev        # backend (port 3000) + frontend Vite dev server (port 5173) together
make backend    # only the Python API server (dev_server.py)
make frontend   # only the Vite dev server
make build      # production build of the frontend (frontend/dist)
make test       # backend unit tests (stdlib unittest, no extra deps)
```

- `make dev`/`make backend` expect a `.venv` at the repo root (`python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt` — Pillow and pandas). Python >= 3.12.
- During local dev, Vite proxies `/api/*` to `http://localhost:3000` (see `frontend/vite.config.js`); `dev_server.py` serves the API by bridging HTTP to the WSGI apps and mimics the Vercel routing in `vercel.json`.
- Alternative: `vercel dev` runs both services with production-identical routing.
- Backend tests live in `backend/tests/` (stdlib `unittest`, discoverable by pytest too); run `make test` or `python -m unittest discover -s backend/tests`. They focus on `generate.py`'s trace/loop-index logic, the AST sandbox, and `numpy_model.py`'s slice geometry / error copy (`test_visualize_numpy.py`, which also drives the WSGI app directly). No linter is configured, and the frontend has no tests.

## Architecture

### Request flow

```
frontend (React/Vite SPA)
  POST /api/generate         → backend/generate.py         (plain Python tracing)
  POST /api/generate-pandas  → backend/generate_pandas.py  (adds pandas/numpy; DataFrames drawn as diff-highlighted tables)
  POST /api/visualize-numpy  → backend/visualize_numpy.py  (returns JSON, no image — see "NumPy page" below)
```

Routing lives in `vercel.json` (production) and is duplicated in `dev_server.py` (local). Each backend file is a self-contained WSGI app (`app(environ, start_response)`) deployed as its own Vercel Python function — there is no web framework.

### Backend layout

```
backend/
  generate.py  generate_pandas.py  visualize_numpy.py   # WSGI entrypoints (vercel.json)
  visualizer.py                                         # GIF pipeline both generate* drive
  numpy_model.py                                        # model visualize_numpy returns
  runtime/     sandbox.py  serverless.py                # untrusted-exec safety + HTTP/WSGI
  execution/   models.py  tracer.py  loops.py           # run the code, produce facts
  render/      canvas.py  pysyntax.py  theme.py         # turn facts into pixels
               panels.py  composer.py  fonts/
```

The **entrypoints and the dependency manifests must stay at `backend/`**: `vercel.json` names the three entrypoint paths literally, and `@vercel/python` resolves `pyproject.toml`/`requirements.txt` from the entrypoint's own directory.

**Subpackage names are constrained, not stylistic.** `backend/` becomes a `sys.path` root on the serverless runtime, so a subpackage name shadows that top-level name process-wide — `trace/` would shadow the stdlib `trace` module and `numpy/` would shadow NumPy. Check `importlib.util.find_spec(name)` before adding or renaming one. Each subpackage's `__init__.py` is docstring-only on purpose: a `render/__init__.py` that imported `composer` would cycle with `composer`'s own `from . import panels`, and any import there runs on every cold start.

`render/fonts/` must stay beside `render/canvas.py` — `FONT_DIR` is `dirname(__file__)/fonts`, and `load_font` swallows failures (a broken path degrades silently to a bitmap font with a 200 status).

### Backend pipeline (both generate files)

Three stages, top to bottom in each file:

1. **Trace** — `check_safe()` (AST pre-check) then `exec()` the user's code under `sys.settrace`, snapshotting locals/stdout per line into `steps`. `fix_loop_headers()` corrects a subtle one-iteration lag: the tracer's "line" event fires *before* a `for` header's FOR_ITER runs, so header steps would otherwise show the previous iteration's loop variable.
2. **Render** — one PIL `Image` per step (`render()`), drawing code, execution-order pills, variables, loop-list progress, and console panels.
3. **Encode** — animated GIF. With `format: "json"` in the POST body the response is `{gif, frames, durations}` (all base64) so the frontend can drive an interactive frame stepper; `frames` is dropped (null) past ~2.5MB and the UI falls back to the plain GIF. A `GET ?c=<base64url(code)>&ms=N&pal=dark|light` variant returns the GIF directly, giving each snippet a shareable URL (e.g. for Google Slides image-by-URL).

### NumPy page (`/numpy`) — data in, canvas out

The odd one out: `pages/NumpyVisualizer.jsx` animates on an HTML canvas in the browser (arrows sweeping source → result, cells filling, speed slider), so its endpoint returns **no image**. `POST /api/visualize-numpy` answers `{arrays, oneD, target}` and the browser draws it.

`backend/numpy_model.py` builds that model from two sources on purpose:

- **real NumPy, for values** — the snippet is exec'd, so seeding, dtypes, broadcasting and the result of the expression are genuinely NumPy's, never reimplemented.
- **the AST, for geometry** — the drawing needs the *rectangle* a slice selects (`r0:r1, c0:c1`), which the resulting array no longer knows. Bounds come off the syntax and are resolved against the real shape via `slice().indices()`.

The last "animatable" statement wins (an assignment or bare expression that is a subscript, a boolean mask, or arithmetic on arrays); statements before it run first, so `B = A + 1` then `C = B[0:2]` works. Statements *after* it are deliberately not executed, so a later rebinding of `A` cannot change the frame. There is intentionally **no** parser in the frontend — one source of truth for what a snippet means, and it is the one that actually runs NumPy. `MAX_DIM`/`MAX_1D` keep arrays small enough that cells stay readable; a 1-D *result* is allowed to be longer than a 1-D source because it wraps in the strip (`drawFilter`).

### Sandboxing (critical — don't weaken)

The backend executes user-submitted Python in-process. Defense-in-depth, owned by `runtime/sandbox.py` and applied by every endpoint: AST denylist (dunder access, `eval`/`exec`/`open`/`getattr`/..., imports outside `ALLOWED_IMPORTS`), reduced `__builtins__` (`SAFE_BUILTIN_NAMES`), a guarded `__import__`, `MAX_CODE_LEN` (4000 chars), `MAX_STEPS` (200), and a 5s wall-clock check inside the trace callback (deliberately not `signal.alarm` — it only works on the main thread, which the serverless runtime doesn't guarantee). This is best-effort, not a real isolation boundary; `maxDuration` in `vercel.json` is the backstop. Any change to execution or imports must preserve all of these layers.

`numpy_model.py` runs the same layers via `runtime/sandbox.py` (its `ALLOWED_IMPORTS` is numpy-only — no pandas), including the settrace wall-clock guard, even though it does no tracing.

Each endpoint still declares its own `ALLOWED_IMPORTS`, but the two GIF endpoints compose it from `runtime.sandbox.STDLIB_IMPORTS` (a `frozenset`) instead of repeating the same twelve names: `generate.py` uses it as-is, `generate_pandas.py` adds `pandas`/`numpy`/`pd`/`np`. Editing `STDLIB_IMPORTS` therefore widens both at once — treat it as a security change, not housekeeping. `numpy_model.py` keeps its own shorter list on purpose.

### Dual import convention (backend)

The same file is imported under two different module names: `backend.render.theme` in dev (`dev_server.py` puts the repo root on `sys.path`) and `render.theme` on Vercel (the entrypoint's directory is the `sys.path` root, so `backend` isn't in the name). Which form to write depends **only** on whether the import crosses a subpackage boundary:

| Import | Form | Shim? |
|---|---|---|
| Same subpackage | `from .panels import ...` | **No** — resolves as `render.panels` on Vercel and `backend.render.panels` in dev. Both work. |
| Cross subpackage | `from ..execution.loops import ...` / `from execution.loops import ...` | **Yes** — on Vercel `render` *is* top-level, so `..` raises `ImportError: attempted relative import beyond top-level package`. |
| Root ↔ root | `from .visualizer import ...` / `from visualizer import ...` | **Yes** — root modules have `__package__ == ''`, so any relative import raises `ImportError`. |

```python
try:                # dev: backend.render.composer, so `..` is backend
    from ..execution.loops import active_loop
except ImportError: # Vercel: render.composer, so `..` is beyond the top level
    from execution.loops import active_loop
```

**Never put a same-subpackage import inside a shim's `try`.** If `from .canvas import ...` shares a block with `from ..execution.loops import ...`, then on Vercel the `try` dies on the cross-package line and the `except` dies on `from canvas import ...` — reporting `No module named 'canvas'`, which names the wrong module entirely. Keep plain relative imports above the `try`.

More generally, `except ImportError` swallows *transitive* failures, so a genuine error deep in an imported module surfaces as a confusing name. When an import breaks, test each branch in its own process:

```bash
cd backend && ../.venv/bin/python -c "import generate, generate_pandas, visualize_numpy"   # Vercel branch
.venv/bin/python -c "import backend.generate, backend.generate_pandas, backend.visualize_numpy"  # dev branch
```

Separate processes matter: importing both conventions at once loads two copies of every module under different `sys.modules` keys, giving `UnsafeCodeError` two identities — which silently turns a 400 into a 500 at `runtime/serverless.py`'s `except (UnsafeCodeError, ExecutionTimeout)`. (`make test` does exactly this today; see Notes.)

### Syntax highlighting must stay in sync across four files

Token classification and colors are deliberately mirrored so the on-screen editor and the exported GIF look identical:

- `backend/render/pysyntax.py` — `iter_tokens()`, the tokenizer used by both GIF renderers
- `frontend/src/components/CodeEditor.jsx` — the same regex/classification in JS (`highlightPython`)
- `backend/render/theme.py` — `PALETTES["dark"|"light"]` RGB values (Monokai / Jupyter default), keyed by token category
- `frontend/src/styles/code-theme.css` — the matching `.tok-*` CSS classes

The category names (`com s num const kw storage builtin func dec op code`) are the contract: a token category or color changed in one place must be changed in all four. Theme rule from `render/theme.py`: the coral/pink brand accent never appears inside code or line highlights — line states use the slate/blue/green/red set only.

### Frontend

React 18 + Vite, no router, no state library. `App.jsx` owns mode/code/ms/theme state plus a hand-rolled `route` (`explainer` | `dataflow` | `numpy`, from `location.pathname`/hash and `history.pushState`) — each non-explainer route needs a matching rewrite to `/index.html` in `vercel.json` or a shared link 404s. `constants.js` defines `MODES` (per-mode endpoint, default snippet, frame duration) — adding a new mode means adding an entry there plus a backend service and routes. `ResultPanel.jsx` does the fetch (`format: "json"`), the frame-stepper playback, and the Copy GIF / Download / shareable-URL actions. Theme (`dark`/`light`) is passed as `palette` to the backend so the GIF matches the UI.

The `dataflow` and `numpy` routes are self-contained canvas pages that bypass `MODES` and the GIF pipeline entirely: `pages/DataFlow.jsx` is fully client-side (it even encodes its own GIF), and `pages/NumpyVisualizer.jsx` posts to `/api/visualize-numpy` and animates the model it gets back.

## Notes

- The README mentions `codegif.py`/`pandasgif.py` standalone CLIs at the repo root; those files are no longer present — the backend services are the only implementation.
- `backend/pyproject.toml` + `uv.lock` are what Vercel's Python builder resolves; `backend/requirements.txt` is a convenience mirror for local `pip install` — keep the two dependency lists in sync.
- Fonts are bundled in `backend/render/fonts/` (Roboto Mono, OFL) and included in the function bundles via `includeFiles` in `vercel.json`; rendering must not depend on system fonts. `includeFiles` also declares `backend/{runtime,execution,render}/**` explicitly rather than trusting `@vercel/python` to recurse below the entrypoint's directory — if it didn't, every endpoint would 500 with `ModuleNotFoundError` while all local checks stayed green.
- Known wart: `make test` loads `sandbox.py` twice — as `runtime.sandbox` (the two flat test files, which are the only coverage of the branch that runs in production) and as `backend.runtime.sandbox` (`test_visualize_numpy.py`). `UnsafeCodeError` therefore has two identities in one run. Each test file's `assertRaises` matches the convention its subject uses, so this is safe — do not "tidy" one file into the other's style without removing the shim entirely.
