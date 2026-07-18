"""
Local dev server — serves the frontend static files and proxies /api/generate
to the backend WSGI app, mimicking the Vercel routing in vercel.json.

Usage:
    python dev_server.py          # starts on http://localhost:3000
    python dev_server.py --port 8080
"""
import os
import sys
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ensure the project root is on sys.path so `backend.generate` resolves.
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from backend.generate import app as wsgi_app  # noqa: E402
from backend.generate_pandas import app as wsgi_pandas_app  # noqa: E402

FRONTEND_DIR = os.path.join(ROOT, "frontend")


class DevHandler(BaseHTTPRequestHandler):
    """Route /api/* to the WSGI backend, everything else to frontend/."""

    # ── API (WSGI bridge) ─────────────────────────────────────────────
    def _handle_api(self):
        # Build a minimal WSGI environ from the HTTP request.
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""

        from io import BytesIO
        environ = {
            "REQUEST_METHOD": self.command,
            "PATH_INFO": self.path.split("?")[0],
            "QUERY_STRING": self.path.split("?", 1)[1] if "?" in self.path else "",
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(content_length),
            "wsgi.input": BytesIO(body),
            "wsgi.errors": sys.stderr,
            "SERVER_NAME": "localhost",
            "SERVER_PORT": str(self.server.server_address[1]),
        }

        response_started = []

        def start_response(status, headers):
            response_started.append((status, headers))

        result = self._pick_wsgi_app()(environ, start_response)

        status, headers = response_started[0]
        code = int(status.split(" ", 1)[0])
        self.send_response(code)
        for key, val in headers:
            self.send_header(key, val)
        # Allow frontend on same origin to call the API.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        for chunk in result:
            self.wfile.write(chunk)

    # ── Static frontend ──────────────────────────────────────────────
    def _serve_static(self):
        path = self.path.split("?")[0].lstrip("/")
        if path == "" or path.endswith("/"):
            path += "index.html"
        filepath = os.path.join(FRONTEND_DIR, path)

        if not os.path.isfile(filepath):
            self.send_error(404, "Not found")
            return

        ctype, _ = mimetypes.guess_type(filepath)
        with open(filepath, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Dispatcher ────────────────────────────────────────────────────
    def _pick_wsgi_app(self):
        """Choose the right WSGI app based on the request path."""
        path = self.path.split("?")[0]
        if path.startswith("/api/generate-pandas"):
            return wsgi_pandas_app
        return wsgi_app

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._handle_api()
        else:
            self._serve_static()

    def do_POST(self):
        self._handle_api()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Code-Explainer local dev server")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), DevHandler)
    print(f"🚀  Dev server running at  http://localhost:{args.port}")
    print(f"   Frontend: {FRONTEND_DIR}")
    print(f"   API:      http://localhost:{args.port}/api/generate")
    print("   Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
