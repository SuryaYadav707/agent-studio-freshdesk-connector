"""One-shot localhost HTTP listener for `connector auth <oauth integration>`.

Binds 127.0.0.1 on the UI port, waits for exactly one GET on the configured
redirect path, returns its query parameters, then shuts down. The demo UI has
the same route; this exists so OAuth works from the CLI without the UI running.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from api.integrations.models.errors import ConfigError

_HTML_OK = b"<html><body style='font-family:sans-serif'><h3>Connected. You can close this tab.</h3></body></html>"
_HTML_ERR = b"<html><body style='font-family:sans-serif'><h3>Authorization failed. Check the terminal.</h3></body></html>"


def wait_for_callback(path: str, port: int, timeout: float = 300.0) -> dict[str, str]:
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != path:
                self.send_response(404)
                self.end_headers()
                return
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            result.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_HTML_ERR if "error" in query else _HTML_OK)
            done.set()

        def log_message(self, *_: object) -> None:  # silence default stderr logging
            return

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise ConfigError(f"cannot listen on 127.0.0.1:{port} for the OAuth callback: {exc}") from exc

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout):
            raise ConfigError("timed out waiting for the OAuth callback")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return result
