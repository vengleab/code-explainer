"""
backend/visualize_numpy.py — Vercel Python service (WSGI entrypoint: visualize_numpy:app).

POST /api/visualize-numpy  {code} -> {arrays, oneD, target}

The data endpoint behind the NumPy page. Unlike the two generate* services it
returns no image: the browser animates the model on a canvas, so this function's
whole job is to run the snippet under the sandbox and describe what to draw
(see numpy_model.py for why the analysis splits between real NumPy and the AST).

It therefore does not use runtime.serverless.make_app — that wires a route to the
GIF pipeline (build_frames -> encode), which has no counterpart here. The
request handling below is this endpoint's own, but the JSON response helper is
the shared one from runtime/serverless.py.
"""
import json

try:  # package import in dev (imported as backend.visualize_numpy)
    from .numpy_model import analyze, ModelError, MAX_DIM, MAX_1D
    from .runtime.sandbox import UnsafeCodeError, ExecutionTimeout
    from .runtime.serverless import json_response
except ImportError:  # top-level module on the serverless runtime
    from numpy_model import analyze, ModelError, MAX_DIM, MAX_1D
    from runtime.sandbox import UnsafeCodeError, ExecutionTimeout
    from runtime.serverless import json_response

ROUTE_PATH = "/api/visualize-numpy"


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = (environ.get("PATH_INFO") or "/").split("?")[0]

    if path != ROUTE_PATH:
        return json_response(start_response, 404, {"error": "not found"})

    if method == "GET":
        return json_response(start_response, 200, {
            "ok": True,
            "usage": "POST {code} -> {arrays, oneD, target}",
            "limits": {"max_dim": MAX_DIM, "max_1d": MAX_1D},
        })

    if method != "POST":
        return json_response(start_response, 405, {"error": "method not allowed"})

    try:
        content_length = int(environ.get("CONTENT_LENGTH") or 0)
        raw_body = environ["wsgi.input"].read(content_length) if content_length else b"{}"
        payload = json.loads(raw_body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return json_response(start_response, 400, {"error": "invalid JSON body"})

    try:
        model = analyze(payload.get("code", ""))
    except (ModelError, UnsafeCodeError, ExecutionTimeout) as e:
        # All three are the user's to fix, so they read the same way in the UI.
        return json_response(start_response, 400, {"error": str(e)})
    except Exception as e:
        return json_response(start_response, 500, {"error": f"{type(e).__name__}: {e}"})

    return json_response(start_response, 200, model)
