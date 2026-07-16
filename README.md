# code-explainer

Turns a small Python snippet into an execution GIF: code with the current
line highlighted, the line-by-line execution order, live variables, list
progress for simple `for` loops, and captured stdout.

## Layout

Deployed as two Vercel [Services](https://vercel.com/docs/services) in one
project — a static frontend and a Python backend — wired together in
`vercel.json`.

- `codegif.py` — the original standalone CLI (unchanged): `python codegif.py myscript.py -o out.gif`
- `frontend/index.html` — the `frontend` service: single-page UI, textarea in,
  GIF out
- `backend/generate.py` — the `backend` service: a WSGI app (`generate:app`)
  exposing the same trace/render pipeline as `codegif.py` over HTTP —
  `POST /api/generate {"code": "...", "ms": 900}` -> `image/gif` bytes
- `backend/fonts/` — bundled Roboto Mono (OFL-licensed) so rendering doesn't
  depend on fonts being present on the deploy machine
- `backend/pyproject.toml` / `backend/uv.lock` — Vercel's Python builder
  resolves dependencies with `uv`; `backend/requirements.txt` is kept only
  as a convenience for a plain `pip install -r requirements.txt` locally
- `vercel.json` — declares both services (with the backend's `entrypoint`
  and `functions` config for bundling fonts) and the top-level rewrites that
  route `/api/*` to the backend and everything else to the frontend

## Local development

```bash
npm i -g vercel   # once
vercel dev         # serves both services locally, same routing as prod
```

## Deploying

**Git-connected (recommended, deploys on every push):**
1. Push this repo to GitHub/GitLab/Bitbucket.
2. [vercel.com/new](https://vercel.com/new) → import the repo → Deploy.

**CLI, one-off:**
```bash
vercel        # preview deploy
vercel --prod # production deploy
```

## Security note

`backend/generate.py` executes user-submitted Python server-side to trace it.
It is *not* a full sandbox — there's no seccomp/gVisor/VM isolation, just
defense-in-depth inside the same process:

- an AST pre-check rejects dunder access, `eval`/`exec`/`open`/`__import__`,
  and imports outside a small stdlib allowlist (`math`, `random`, `string`,
  `itertools`, `functools`, `collections`, `datetime`, `re`, `json`,
  `statistics`, `decimal`, `fractions`)
- `exec()` runs against a reduced `__builtins__` dict
- a 5-second wall-clock check (`time.monotonic`, checked from inside the
  per-line trace callback — no `signal.alarm`, since that only works on the
  main thread and isn't guaranteed here) and a 200-step trace cap bound
  runaway/infinite loops; Vercel's own `maxDuration` is the backstop for
  anything neither catches (e.g. one pathologically slow single line)
- submitted code is capped at 4000 characters

If you expose this publicly, put auth and/or rate limiting in front of it —
don't rely on the above as a complete security boundary.
