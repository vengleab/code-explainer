"""
backend/serverless.py — the HTTP layer (WSGI), shared by both endpoints.

Each generate*.py is deployed as its own Vercel Python function but they speak
the identical protocol, so the request handling lives here once:

  POST {code, ms, palette, quality, format} -> image/gif  (or JSON when
      format == "json": {gif, frames, durations}, all base64)
  GET  ?c=<base64url(code)>&ms=N&pal=dark|light            -> image/gif
      (a shareable URL each snippet gets, e.g. Google Slides "image by URL")

`make_app(route_path, visualizer, quality_presets)` wires a
Visualizer (visualizer.py) to a route and returns the WSGI `app` callable each
function exposes. There is no web framework — `app(environ, start_response)` is
the raw WSGI contract: `environ` is the request, `start_response` is called once
with the status line + headers, and the return value is the response body.
"""
import base64
import json
import io
from urllib.parse import parse_qs

try:  # package import in dev (imported as backend.serverless)
    from .sandbox import MAX_CODE_LEN, UnsafeCodeError, ExecutionTimeout
except ImportError:  # top-level module on the serverless runtime
    from sandbox import MAX_CODE_LEN, UnsafeCodeError, ExecutionTimeout


def encode_gif(frames, durations):
    """Encode rendered frames into an animated GIF (bytes)."""
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, disposal=2, optimize=True)
    return buffer.getvalue()


# Serverless responses are size-capped (~4.5MB on Vercel), but local and standard
# API payloads can support full multi-step interactive trace scrubbers.
FRAMES_BYTES_LIMIT = 10_000_000


def build_json_payload(frames, durations):
    """Package frames for the interactive stepper, dropping them if oversized."""
    gif_bytes = encode_gif(frames, durations)
    frames_b64, total_bytes = [], 0
    for frame in frames:
        frame_buffer = io.BytesIO()
        frame.save(frame_buffer, format="PNG", optimize=True)
        frame_bytes = frame_buffer.getvalue()
        total_bytes += len(frame_bytes)
        if total_bytes > FRAMES_BYTES_LIMIT:
            frames_b64 = None
            break
        frames_b64.append("data:image/png;base64," + base64.b64encode(frame_bytes).decode())
    return {"gif": base64.b64encode(gif_bytes).decode(), "frames": frames_b64, "durations": durations}


# --------------------------------------------------------------------------
# WSGI response helpers
#
# Public because visualize_numpy.py answers the same JSON protocol without using
# make_app (it returns a model, not a GIF), and one copy of the status table is
# enough.
# --------------------------------------------------------------------------
STATUS_REASONS = {
    200: "OK", 400: "Bad Request", 404: "Not Found",
    405: "Method Not Allowed", 500: "Internal Server Error",
}


def status_line(code):
    return f"{code} {STATUS_REASONS.get(code, 'Error')}"


def json_response(start_response, status, payload):
    """Serialize `payload` as the whole JSON response body.

    `allow_nan=False` on purpose: NaN/Infinity are not valid JSON and would
    reach the browser as an unparseable token, so this raises instead.
    """
    body = json.dumps(payload, allow_nan=False).encode("utf-8")
    start_response(status_line(status), [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ])
    return [body]


def _gif_response(start_response, gif_bytes):
    start_response(status_line(200), [
        ("Content-Type", "image/gif"),
        ("Content-Length", str(len(gif_bytes))),
        # Let external fetchers (e.g. Google Slides' image proxy) cache the
        # result instead of re-running the trace on every request.
        ("Cache-Control", "public, max-age=86400"),
    ])
    return [gif_bytes]


def make_app(route_path, visualizer, quality_presets):
    """Return the WSGI `app` callable for one endpoint.

    `route_path` is the only path this function answers (e.g. "/api/generate");
    `visualizer` is the mode-specific pipeline; `quality_presets` maps a quality
    label to {code_size, scale}. The frame delay used when the request doesn't
    specify one comes from the visualizer itself (`default_ms`), so the value
    lives in exactly one place.
    """
    default_ms = visualizer.default_ms

    def _render(code, ms, output_format, palette, quality):
        preset = quality_presets.get(quality, quality_presets["medium"])
        frames, durations = visualizer.build_frames(
            code, ms=int(ms), code_size=preset["code_size"], scale=preset["scale"], palette=palette)
        if output_format == "json":
            return build_json_payload(frames, durations)
        return encode_gif(frames, durations)

    def _generate_or_error(start_response, code, ms, output_format="gif", palette="dark", quality="medium"):
        if not isinstance(code, str) or not code.strip():
            return json_response(start_response, 400, {"error": "'code' must be a non-empty string"})
        if len(code) > MAX_CODE_LEN:
            return json_response(start_response, 400, {"error": f"code too long (max {MAX_CODE_LEN} characters)"})
        if not isinstance(ms, (int, float)):
            ms = default_ms
        try:
            result = _render(code, ms, output_format, palette, quality)
        except (UnsafeCodeError, ExecutionTimeout) as e:
            return json_response(start_response, 400, {"error": str(e)})
        except Exception as e:
            return json_response(start_response, 500, {"error": f"{type(e).__name__}: {e}"})
        if output_format == "json":
            return json_response(start_response, 200, result)
        return _gif_response(start_response, result)

    def app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = (environ.get("PATH_INFO") or "/").split("?")[0]

        if path != route_path:
            return json_response(start_response, 404, {"error": "not found"})

        if method == "GET":
            query_params = parse_qs(environ.get("QUERY_STRING") or "")
            if "c" in query_params:
                try:
                    code_b64 = query_params["c"][0]
                    code = base64.urlsafe_b64decode(code_b64 + "=" * (-len(code_b64) % 4)).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    return json_response(start_response, 400,
                                         {"error": "invalid 'c' parameter (expected base64url-encoded UTF-8 code)"})
                try:
                    ms = int(query_params.get("ms", [str(default_ms)])[0])
                except ValueError:
                    ms = default_ms
                palette = query_params.get("pal", ["dark"])[0]
                return _generate_or_error(start_response, code, ms, palette=palette)
            return json_response(start_response, 200, {
                "ok": True,
                "usage": "POST {code, ms, palette} -> image/gif, or GET ?c=<base64url(code)>&ms=N&pal=dark|light -> image/gif",
            })

        if method != "POST":
            return json_response(start_response, 405, {"error": "method not allowed"})

        try:
            content_length = int(environ.get("CONTENT_LENGTH") or 0)
            raw_body = environ["wsgi.input"].read(content_length) if content_length else b"{}"
            payload = json.loads(raw_body or b"{}")
        except (ValueError, json.JSONDecodeError):
            return json_response(start_response, 400, {"error": "invalid JSON body"})

        output_format = "json" if payload.get("format") == "json" else "gif"
        return _generate_or_error(start_response, payload.get("code", ""), payload.get("ms", default_ms),
                                  output_format=output_format, palette=payload.get("palette", "dark"),
                                  quality=payload.get("quality", "medium"))

    return app
