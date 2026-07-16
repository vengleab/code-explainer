# code-explainer

Turns a small Python snippet into an execution GIF: code with the current
line highlighted, the line-by-line execution order, live variables, list
progress for simple `for` loops, and captured stdout.

## Layout

- `codegif.py` — the original standalone CLI (unchanged): `python codegif.py myscript.py -o out.gif`
- `api/generate.py` — Vercel Python serverless function exposing the same
  pipeline over HTTP: `POST /api/generate {"code": "...", "ms": 900}` ->
  `image/gif` bytes
- `index.html` — single-page frontend (textarea in, GIF out) served as a
  static file from the project root
- `fonts/` — bundled Roboto Mono (OFL-licensed) so rendering doesn't depend
  on fonts being present on the deploy machine
- `vercel.json` — wires the bundled fonts into the function's filesystem and
  sets `maxDuration`

## Local development

```bash
npm i -g vercel   # once
vercel dev         # serves index.html + api/generate.py locally
```

## Deploying

**Git-connected (recommended, deploys on every push):**
1. Push this repo to GitHub/GitLab/Bitbucket.
2. [vercel.com/new](https://vercel.com/new) → import the repo → Deploy.
   No framework preset needed (leave it as "Other"); Vercel auto-detects
   `api/*.py` as serverless functions and everything else as static.

**CLI, one-off:**
```bash
vercel        # preview deploy
vercel --prod # production deploy
```

## Security note

`api/generate.py` executes user-submitted Python server-side to trace it.
It is *not* a full sandbox — there's no seccomp/gVisor/VM isolation, just
defense-in-depth inside the same process:

- an AST pre-check rejects dunder access, `eval`/`exec`/`open`/`__import__`,
  and imports outside a small stdlib allowlist (`math`, `random`, `string`,
  `itertools`, `functools`, `collections`, `datetime`, `re`, `json`,
  `statistics`, `decimal`, `fractions`)
- `exec()` runs against a reduced `__builtins__` dict
- a 5-second wall-clock timeout (`SIGALRM`) and an 800-step trace cap bound
  runaway/infinite loops
- submitted code is capped at 4000 characters

If you expose this publicly, put auth and/or rate limiting in front of it —
don't rely on the above as a complete security boundary.
