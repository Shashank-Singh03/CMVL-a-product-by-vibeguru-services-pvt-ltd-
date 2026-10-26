"""Loopback dashboard service with scoped artifacts and explicit local controls.

Only generated public outputs are served. Source snapshots, state, and model
worksheets are deliberately outside this HTTP surface. No target application
program runs here; retry uses the same supervised jobs as the CLI.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import automatic, history, tasks

MAX_BODY_BYTES = 8192
MAX_HTML_BYTES = 16_000_000
ARTIFACT = re.compile(r"tasks/([0-9a-f]{32})/output/(codemap\.(?:html|md|diff|mp4|webm))\Z")
TASK_ACTION = re.compile(r"/api/tasks/([0-9a-f]{32})/(retry|cancel)\Z")
RETRY_STATES = {stage + "_failed": stage for stage in automatic.JOBS}
CANCEL_STATES = (set(automatic.ACTIVE) | set(RETRY_STATES)
                 | {"frozen", "awaiting_review", "awaiting_labels", "map_reviewed"})
BOOT = '<script>window.__CODEMAP_SERVER__={version:1,apiBase:"/api"};</script>'


class DashboardServer(ThreadingHTTPServer):
    """A foreground, one-project HTTP service. Close with server_close()."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root, address):
        self.root = automatic.project_root(root)
        self.base = self.root / ".codemap"
        self.action_lock = threading.RLock()
        self.control_lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self._check_config_paths()
        with tasks.locked(self.root):
            automatic.pipeline._ensure_out(self.base)
            history.write_history(self.root)
        super().__init__(address, DashboardHandler)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_port}"

    def _check_config_paths(self):
        for filename in ("config.json", ".gitignore"):
            tasks._inside_path(self.root, self.base / filename, allow_missing=True)

    def settings(self):
        self._check_config_paths()
        return automatic.config(self.root)

    def _records(self):
        records, problem = history._read_states(self.base)
        records, invalid = history._records(records)
        raw = {}
        # _read_states bounds total input bytes and turns unavailable entries
        # generic. Only re-read records it accepted, for identity and controls.
        for record in records:
            status = record.get("status")
            if not isinstance(status, str) or status not in history.STATUSES or status == "state_unavailable":
                problem |= not isinstance(status, str) or status not in history.STATUSES
                continue
            try:
                raw[record["task_id"]] = tasks._load(self.root, record["task_id"])
            except tasks.TaskError:
                problem = True
        return records, raw, problem or invalid

    def snapshot(self, reconcile=True):
        with self.action_lock:
            if reconcile:
                with tasks.locked(self.root):
                    _, previous, _ = self._records()
                for ident, state in previous.items():
                    # refresh() can advance map_reviewed into video. Restrict
                    # read reconciliation to already queued/running jobs.
                    if state.get("status") in automatic.ACTIVE:
                        try:
                            automatic.refresh(self.root, ident)
                        except (OSError, ValueError, tasks.TaskError, automatic.AutomaticError):
                            pass
            with tasks.locked(self.root):
                records, raw, problem = self._records()
                data = history._dashboard_data(self.root, self.base, records, problem)
            for entry in data["tasks"]:
                state = raw.get(entry["id"], {})
                status = state.get("status")
                entry["controls"] = {
                    "cancel": status in CANCEL_STATES,
                    "retry": status in RETRY_STATES,
                    "stage": RETRY_STATES.get(status),
                    "revision": state.get("revision") if type(state.get("revision")) is int else None,
                }
            return data

    def index(self):
        with self.action_lock:
            self.snapshot()
            with tasks.locked(self.root):
                history.write_history(self.root)
            with self.open_public("index.html") as stream:
                content = stream.read(MAX_HTML_BYTES + 1)
            if len(content) > MAX_HTML_BYTES:
                raise ValueError("history too large")
            html = content.decode("utf-8")
            head, separator, body = html.partition("</head>")
            # Chromium can retain a refresh timer even after React removes the
            # meta element. API mode polls; leave reload only in saved fallback.
            head = re.sub(r'<meta\b[^>]*\bhttp-equiv\s*=\s*["\']refresh["\'][^>]*>', "", head, flags=re.I)
            return (head + BOOT + separator + body).encode("utf-8")

    def open_public(self, relative):
        """Open a canonical public artifact, refusing links and path swaps."""
        if relative != "index.html" and not ARTIFACT.fullmatch(relative):
            raise FileNotFoundError
        path = self.base / relative
        if not history._safe(path, self.base):
            raise FileNotFoundError
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise FileNotFoundError
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if os.name == "nt":
            descriptor = os.open(path, flags)
        else:
            directory = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                parts = relative.split("/")
                for part in parts[:-1]:
                    next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = next_directory
                descriptor = os.open(parts[-1], flags, dir_fd=directory)
            finally:
                os.close(directory)
        stream = os.fdopen(descriptor, "rb")
        try:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise FileNotFoundError
            if os.name == "nt":
                import ctypes
                import msvcrt
                from ctypes import wintypes
                function = ctypes.windll.kernel32.GetFinalPathNameByHandleW
                function.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
                function.restype = wintypes.DWORD
                buffer = ctypes.create_unicode_buffer(32_768)
                length = function(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
                if not length or length >= len(buffer):
                    raise FileNotFoundError
                final = buffer.value
                final = "\\\\" + final[8:] if final.startswith("\\\\?\\UNC\\") else final.removeprefix("\\\\?\\")
                if os.path.normcase(os.path.abspath(final)) != os.path.normcase(os.path.abspath(path)):
                    raise FileNotFoundError
            return stream
        except BaseException:
            stream.close()
            raise

    def handle_error(self, request, client_address):
        # Do not publish raw paths, task metadata or request bodies in traces.
        pass


class DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CodemapLocal/1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_args):
        pass

    def _headers(self, status, content_type, length, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Only public generated maps may be embedded in this project's shell.
        # All API and dashboard responses retain the existing framing denial.
        embedded_map = status == 200 and bool(re.fullmatch(r"/tasks/[0-9a-f]{32}/output/codemap\.html", urlsplit(self.path).path))
        ancestors = "'self'" if embedded_map else "'none'"
        self.send_header("X-Frame-Options", "SAMEORIGIN" if embedded_map else "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data:; media-src 'self' data: blob:; connect-src 'self'; frame-ancestors " + ancestors + "; object-src 'none'; base-uri 'none'")
        self.send_header("Connection", "close")
        self.close_connection = True
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _json(self, status, value):
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(encoded))
        if self.command != "HEAD":
            self.wfile.write(encoded)

    def _error(self, status, code, message):
        self._discard_body()
        self._json(status, {"error": message, "code": code})

    def _discard_body(self):
        # Closing a Windows TCP socket with unread request bytes can reset the
        # connection before the browser receives our error. Drain only a small,
        # declared body; never wait indefinitely for an unauthenticated sender.
        if self.command != "POST" or getattr(self, "_body_consumed", False) or self.headers.get("Transfer-Encoding"):
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 6:
            return
        length = int(lengths[0])
        if length > 65_536:
            return
        self._body_consumed = True
        self.connection.settimeout(1)
        try:
            self.rfile.read(length)
        except OSError:
            pass
        finally:
            self.connection.settimeout(15)

    def send_error(self, code, message=None, explain=None):
        self._error(code, "invalid_request", "This local dashboard request was not accepted.")

    def _guard(self, write=False):
        hosts = self.headers.get_all("Host", [])
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            self._error(403, "invalid_host", "Use this server's localhost address.")
            return False
        origins = self.headers.get_all("Origin", [])
        expected = "http://" + hosts[0].lower()
        if (write and len(origins) != 1) or len(origins) > 1 or (origins and origins[0] != expected):
            self._error(403, "invalid_origin", "Open the controls in this local dashboard.")
            return False
        site = self.headers.get("Sec-Fetch-Site")
        target = self._path()
        if target and target.startswith("/api/") and site not in (None, "same-origin", "none"):
            self._error(403, "cross_origin", "Cross-origin control requests are not accepted.")
            return False
        if write:
            supplied_headers = self.headers.get_all("X-Codemap-Token", [])
            supplied = supplied_headers[0] if len(supplied_headers) == 1 else ""
            if len(supplied) > 100 or not supplied.isascii() or not secrets.compare_digest(supplied, self.server.token):
                self._error(403, "invalid_token", "Reconnect to the local dashboard before changing this project.")
                return False
        return True

    def _path(self):
        try:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path, errors="strict")
        except (ValueError, UnicodeError):
            return None
        if (parsed.scheme or parsed.netloc or parsed.fragment or len(path) > 2048 or "\\" in path
                or any(ord(char) < 32 for char in path) or any(part in (".", "..") for part in path.split("/"))):
            return None
        return path

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._guard():
            return
        path = self._path()
        if path is None:
            self._error(400, "invalid_path", "The requested output path is not valid.")
            return
        try:
            if path in ("/", "/index.html"):
                encoded = self.server.index()
                self._headers(200, "text/html; charset=utf-8", len(encoded))
                if self.command != "HEAD":
                    self.wfile.write(encoded)
            elif path in ("/api/session", "/api/history"):
                value = {"history": self.server.snapshot(), "settings": self.server.settings()}
                if path == "/api/session":
                    value.update(version=1, project=value["history"]["project"], token=self.server.token)
                self._json(200, value)
            elif path == "/api/settings":
                self._json(200, {"settings": self.server.settings()})
            elif path == "/favicon.ico":
                self._headers(204, "image/x-icon", 0)
            elif ARTIFACT.fullmatch(path.lstrip("/")):
                self._file(path.lstrip("/"))
            else:
                self._error(404, "not_found", "That output is not available through the local dashboard.")
        except (FileNotFoundError, PermissionError):
            self._error(404, "not_found", "That output is not available through the local dashboard.")
        except (OSError, ValueError, tasks.TaskError, automatic.AutomaticError, RecursionError):
            self._error(503, "unavailable", "The saved dashboard could not be read safely. Use the agent or CLI to inspect this project.")

    def _file(self, relative):
        with self.server.open_public(relative) as stream:
            size = os.fstat(stream.fileno()).st_size
            start, end, status = 0, size - 1, 200
            requested = self.headers.get("Range")
            if requested:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested) if len(requested) <= 128 else None
                valid = bool(match and size and any(match.groups()))
                if valid:
                    left, right = match.groups()
                    if left:
                        start = int(left)
                        end = min(int(right), size - 1) if right else size - 1
                    else:
                        suffix = int(right)
                        valid = suffix > 0
                        start, end = max(0, size - suffix), size - 1
                    valid = valid and start < size and start <= end
                if not valid:
                    self._headers(416, "text/plain; charset=utf-8", 0, {"Content-Range": f"bytes */{size}"})
                    return
                status = 206
            length = max(0, end - start + 1)
            suffix = Path(relative).suffix
            content_type = {".html": "text/html; charset=utf-8", ".md": "text/plain; charset=utf-8",
                            ".diff": "text/plain; charset=utf-8", ".mp4": "video/mp4", ".webm": "video/webm"}.get(suffix)
            extra = {"Accept-Ranges": "bytes"}
            if status == 206:
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._headers(status, content_type or mimetypes.guess_type(relative)[0] or "application/octet-stream", length, extra)
            if self.command == "HEAD":
                return
            stream.seek(start)
            while length:
                chunk = stream.read(min(65_536, length))
                if not chunk:
                    break
                self.wfile.write(chunk)
                length -= len(chunk)

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise ValueError
        length = int(lengths[0])
        if length > MAX_BODY_BYTES or self.headers.get_content_type() != "application/json":
            raise ValueError
        self._body_consumed = True
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError
        return value

    def do_POST(self):
        if not self._guard(write=True):
            return
        path = self._path()
        match = TASK_ACTION.fullmatch(path or "")
        if path != "/api/settings" and not match:
            self._error(404, "not_found", "That local control is not available.")
            return
        try:
            body = self._body()
        except (OSError, ValueError, UnicodeError, RecursionError):
            self._error(400, "invalid_body", "Send a small JSON object containing supported settings or task controls.")
            return
        if not self.server.control_lock.acquire(blocking=False):
            self._error(409, "busy", "Another local control is running. Refresh and retry shortly.")
            return
        try:
            if path == "/api/settings":
                if set(body) != {"settings"} or not isinstance(body["settings"], dict):
                    self._error(400, "invalid_settings", "Choose supported project settings.")
                    return
                self.server._check_config_paths()
                candidate = dict(self.server.settings(), **body["settings"])
                try:
                    automatic._validate_settings(candidate)
                except automatic.AutomaticError:
                    self._error(400, "invalid_settings", "One or more settings are unsupported or outside their allowed range.")
                    return
                automatic.configure(self.server.root, "status", **body["settings"])
                message = "Project settings saved for future tasks. Existing tasks keep their captured settings."
                ident = None
            else:
                ident, action = match.groups()
                allowed_keys = {"revision"} | ({"stage"} if action == "retry" else set())
                if set(body) - allowed_keys or ("revision" in body and (type(body["revision"]) is not int or body["revision"] < 1)):
                    self._error(400, "invalid_action", "Refresh and choose an available task action.")
                    return
                try:
                    state = tasks.load(self.server.root, ident)
                except tasks.TaskError:
                    self._error(404, "not_found", "That captured task is not available.")
                    return
                if "revision" in body and body["revision"] != state["revision"]:
                    self._error(409, "stale_task", "This task changed. Refresh before trying that action again.")
                    return
                if action == "cancel":
                    if state["status"] not in CANCEL_STATES:
                        self._error(409, "action_unavailable", "This task cannot be cancelled in its current state.")
                        return
                    automatic.cancel(self.server.root, ident)
                    message = "Cancellation requested. Captured sources and completed outputs are retained."
                else:
                    stage = RETRY_STATES.get(state["status"])
                    if stage is None or body.get("stage", stage) != stage:
                        self._error(409, "action_unavailable", "Refresh and retry only the failed stage shown for this task.")
                        return
                    if stage == "review":
                        automatic.review_task(self.server.root, ident, wait=0)
                    elif stage == "render":
                        automatic.finish(self.server.root, ident, wait=0)
                    else:
                        automatic.retry(self.server.root, ident, stage)
                    message = "The failed stage was queued for retry. Source review and narration still use your agent."
            data = self.server.snapshot(reconcile=False)
            result = {"history": data, "settings": self.server.settings(), "message": message}
            if ident:
                result["task"] = next((entry for entry in data["tasks"] if entry["id"] == ident), None)
            self._json(200, result)
        except (OSError, ValueError, tasks.TaskError, automatic.AutomaticError, RecursionError):
            self._error(409, "control_failed", "The control did not complete. Check the task's saved review or labels with your agent, then refresh and retry.")
        finally:
            self.server.control_lock.release()

    def do_OPTIONS(self):
        self._error(405, "method_not_allowed", "Use this dashboard's same-origin controls.")


def create_server(root, host="127.0.0.1", port=0):
    """Create a loopback-only server; callers own serve_forever and close."""
    if host != "127.0.0.1":
        raise ValueError("The dashboard server binds only to 127.0.0.1.")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Choose a TCP port from 0 to 65535.")
    return DashboardServer(root, (host, port))


def serve(root, port=8765, open_browser=False):
    """Run until interrupted. Opening a browser is an explicit CLI option."""
    server = create_server(root, port=port)
    try:
        print(f"Codemap local dashboard: {server.origin}/", flush=True)
        print("Press Ctrl+C to stop. Project controls are available only while this command runs.", flush=True)
        if open_browser:
            import webbrowser
            webbrowser.open(server.origin + "/")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
