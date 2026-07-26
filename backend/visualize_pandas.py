"""
backend/visualize_pandas.py — Vercel Python service (WSGI entrypoint: visualize_pandas:app).

POST /api/visualize-pandas  {code} -> {dfs, target}

The data endpoint behind the Pandas Visualizer page. Returns structured JSON
describing the DataFrames and target operation for canvas visualization.
"""
import json

try:
    from .pandas_model import analyze, ModelError, MAX_ROWS, MAX_COLS
    from .runtime.sandbox import UnsafeCodeError, ExecutionTimeout
    from .runtime.serverless import json_response
except ImportError:
    from pandas_model import analyze, ModelError, MAX_ROWS, MAX_COLS
    from runtime.sandbox import UnsafeCodeError, ExecutionTimeout
    from runtime.serverless import json_response

ROUTE_PATH = "/api/visualize-pandas"


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = (environ.get("PATH_INFO") or "/").split("?")[0]

    if path != ROUTE_PATH:
        return json_response(start_response, 404, {"error": "not found"})

    if method == "GET":
        return json_response(start_response, 200, {
            "ok": True,
            "usage": "POST {code} -> {dfs, target}",
            "limits": {"max_rows": MAX_ROWS, "max_cols": MAX_COLS},
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
        return json_response(start_response, 400, {"error": str(e)})
    except Exception as e:
        return json_response(start_response, 500, {"error": f"{type(e).__name__}: {e}"})

    return json_response(start_response, 200, model)
