"""The app's page (default 127.0.0.1:8385): Syncthing's own interface plus an Nsync panel.

The Syncthing core listens on an internal port; this server forwards everything to it
and adds a button that opens /nsync/ (identity, QR, peers, relays) on top. Without a
managed core, / simply goes to the Nsync panel.
"""

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

import requests

log = logging.getLogger(__name__)

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "content-length", "content-encoding"}
INJECT = b'<script src="/nsync/inject.js"></script></body>'


def _static(name: str) -> bytes:
    return (resources.files("nsync") / "static" / name).read_bytes()


def make_handler(daemon, loop: asyncio.AbstractEventLoop, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    upstream = f"http://127.0.0.1:{daemon.config.gui_port}" if daemon.config.managed else None
    # No session: a jar would remember the core's cookies and send them back, so the core would
    # never issue its CSRF cookie again and the browser would never get one.

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # the daemon's own log is enough
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _host_ok(self) -> bool:
            # A page on another origin can reach 127.0.0.1 through DNS rebinding;
            # it cannot make the browser send a Host we recognise.
            return self.headers.get("Host") in allowed_hosts

        def _forward(self) -> None:
            if upstream is None:
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in HOP_BY_HOP | {"host", "accept-encoding"}}
            # Identity encoding so the page can be edited; the Host the core expects is its own.
            headers.update({"Host": upstream.split("//", 1)[1], "Accept-Encoding": "identity"})
            try:
                r = requests.request(self.command, upstream + self.path, headers=headers, data=body,
                                     stream=True, allow_redirects=False, timeout=(5, 120))
            except requests.RequestException as exc:
                return self._json(502, {"error": f"Syncthing core is not answering yet: {exc}"})
            with r:
                page = r.headers.get("Content-Type", "").startswith("text/html")
                payload = None
                if page:
                    # The window title is bound in Syncthing's template; "Nsync" instead of its name.
                    payload = r.content.replace(b"' | Syncthing'", b"' | Nsync'").replace(b"</body>", INJECT, 1)
                self.send_response(r.status_code)
                for k, v in r.raw.headers.items():
                    if k.lower() in HOP_BY_HOP:
                        continue
                    if k.lower() == "location":
                        v = v.replace(upstream, f"http://127.0.0.1:{port}")
                    self.send_header(k, v)
                if payload is not None:
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if r.headers.get("Content-Length"):
                    self.send_header("Content-Length", r.headers["Content-Length"])
                self.end_headers()  # no length otherwise: the response ends when the connection closes
                try:
                    for chunk in r.iter_content(8192):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            if self.path in ("/nsync/", "/nsync"):
                self._send(200, _static("index.html"), "text/html; charset=utf-8")
            elif self.path == "/nsync/logo.svg":
                self._send(200, _static("logo.svg"), "image/svg+xml")
            elif upstream and self.path.split("?")[0] == "/assets/img/logo-horizontal.svg":
                self._send(200, _static("brand.svg"), "image/svg+xml")  # Syncthing's own mark, replaced by ours
            elif upstream and self.path.startswith("/assets/img/favicon-") and self.path.split("?")[0].endswith(".png"):
                self._send(200, _static("favicon.png"), "image/png")
            elif self.path == "/nsync/inject.js":
                self._send(200, _static("inject.js"), "application/javascript")
            elif self.path == "/nsync/api/summary":
                self._json(200, daemon.summary())
            elif self.path == "/nsync/api/status":
                try:
                    self._json(200, daemon.status())
                except Exception as exc:
                    self._json(502, {"error": f"Syncthing unreachable: {exc}"})
            elif self.path.startswith("/nsync/"):
                self._json(404, {"error": "not found"})
            elif upstream is None and self.path in ("/", "/index.html"):
                self.send_response(302)
                self.send_header("Location", "/nsync/")
                self.end_headers()
            else:
                self._forward()

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self._host_ok() or (origin and origin.split("//", 1)[-1] not in allowed_hosts):
                return self._json(403, {"error": "forbidden"})
            if not self.path.startswith("/nsync/"):
                return self._forward()  # Syncthing guards its own writes with its CSRF token
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self._json(415, {"error": "json only"})  # a plain <form> post cannot send this
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/nsync/api/pair":
                    coro = daemon.pair(body["pairing"], body.get("name", ""))
                elif self.path == "/nsync/api/unpair":
                    coro = daemon.unpair(body["npub"])
                elif self.path == "/nsync/api/refresh":
                    coro = daemon.refresh_now()
                elif self.path == "/nsync/api/relays":
                    if body.get("reset"):
                        coro = daemon.reset_relays()
                    elif body.get("add"):
                        coro = daemon.add_relay(str(body["add"]))
                    else:
                        coro = daemon.remove_relay(str(body["remove"]))
                elif self.path == "/nsync/api/settings":
                    coro = daemon.set_settings(body.get("heartbeat_minutes"), body.get("announce_public"))
                else:
                    return self._json(404, {"error": "not found"})
                asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=15)
                self._json(200, {"ok": True})
            except (ValueError, KeyError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = lambda self: (
            self._forward() if self._host_ok() else self._json(403, {"error": "forbidden"}))

    return Handler


def start_web(daemon, port: int) -> ThreadingHTTPServer | None:
    loop = asyncio.get_running_loop()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(daemon, loop, port))
    except OSError as exc:
        log.warning("web page disabled, cannot bind 127.0.0.1:%s (%s)", port, exc)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("web page on http://127.0.0.1:%s", port)
    return server
