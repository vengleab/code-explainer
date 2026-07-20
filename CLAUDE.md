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
- Backend tests live in `backend/tests/` (stdlib `unittest`, discoverable by pytest too); run `make test` or `python -m unittest discover -s backend/tests`. They focus on `generate.py`'s trace/loop-index logic and the AST sandbox. No linter is configured, and the frontend has no tests.

## Architecture

### Request flow

```
frontend (React/Vite SPA)
  POST /api/generate         → backend/generate.py         (plain Python tracing)
  POST /api/generate-pandas  → backend/generate_pandas.py  (adds pandas/numpy; DataFrames drawn as diff-highlighted tables)
```

Routing lives in `vercel.json` (production) and is duplicated in `dev_server.py` (local). Each backend file is a self-contained WSGI app (`app(environ, start_response)`) deployed as its own Vercel Python function — there is no web framework.

### Backend pipeline (both generate files)

Three stages, top to bottom in each file:

1. **Trace** — `check_safe()` (AST pre-check) then `exec()` the user's code under `sys.settrace`, snapshotting locals/stdout per line into `steps`. `fix_loop_headers()` corrects a subtle one-iteration lag: the tracer's "line" event fires *before* a `for` header's FOR_ITER runs, so header steps would otherwise show the previous iteration's loop variable.
2. **Render** — one PIL `Image` per step (`render()`), drawing code, execution-order pills, variables, loop-list progress, and console panels.
3. **Encode** — animated GIF. With `format: "json"` in the POST body the response is `{gif, frames, durations}` (all base64) so the frontend can drive an interactive frame stepper; `frames` is dropped (null) past ~2.5MB and the UI falls back to the plain GIF. A `GET ?c=<base64url(code)>&ms=N&pal=dark|light` variant returns the GIF directly, giving each snippet a shareable URL (e.g. for Google Slides image-by-URL).

### Sandboxing (critical — don't weaken)

The backend executes user-submitted Python in-process. Defense-in-depth, duplicated in both generate files: AST denylist (dunder access, `eval`/`exec`/`open`/`getattr`/..., imports outside `ALLOWED_IMPORTS`), reduced `__builtins__` (`SAFE_BUILTIN_NAMES`), a guarded `__import__`, `MAX_CODE_LEN` (4000 chars), `MAX_STEPS` (200), and a 5s wall-clock check inside the trace callback (deliberately not `signal.alarm` — it only works on the main thread, which the serverless runtime doesn't guarantee). This is best-effort, not a real isolation boundary; `maxDuration` in `vercel.json` is the backstop. Any change to execution or imports must preserve all of these layers.

### Dual import convention (backend)

Shared modules are imported with a `try/except ImportError` fallback:

```python
try:                # dev: imported as a package (backend.generate)
    from .theme import get_palette
except ImportError: # Vercel: each function is bundled as a top-level module
    from theme import get_palette
```

Keep this pattern when adding shared backend modules.

### Syntax highlighting must stay in sync across four files

Token classification and colors are deliberately mirrored so the on-screen editor and the exported GIF look identical:

- `backend/pysyntax.py` — `iter_tokens()`, the tokenizer used by both GIF renderers
- `frontend/src/components/CodeEditor.jsx` — the same regex/classification in JS (`highlightPython`)
- `backend/theme.py` — `PALETTES["dark"|"light"]` RGB values (Monokai / Jupyter default), keyed by token category
- `frontend/src/theme/code-theme.css` — the matching `.tok-*` CSS classes

The category names (`com s num const kw storage builtin func dec op code`) are the contract: a token category or color changed in one place must be changed in all four. Theme rule from `theme.py`: the coral/pink brand accent never appears inside code or line highlights — line states use the slate/blue/green/red set only.

### Frontend

React 18 + Vite, no router, no state library. `App.jsx` owns mode/code/ms/theme state; `constants.js` defines `MODES` (per-mode endpoint, default snippet, frame duration) — adding a new mode means adding an entry there plus a backend service and routes. `ResultPanel.jsx` does the fetch (`format: "json"`), the frame-stepper playback, and the Copy GIF / Download / shareable-URL actions. Theme (`dark`/`light`) is passed as `palette` to the backend so the GIF matches the UI.

## Notes

- The README mentions `codegif.py`/`pandasgif.py` standalone CLIs at the repo root; those files are no longer present — the backend services are the only implementation.
- `backend/pyproject.toml` + `uv.lock` are what Vercel's Python builder resolves; `backend/requirements.txt` is a convenience mirror for local `pip install` — keep the two dependency lists in sync.
- Fonts are bundled in `backend/fonts/` (Roboto Mono, OFL) and included in the function bundles via `includeFiles` in `vercel.json`; rendering must not depend on system fonts.
