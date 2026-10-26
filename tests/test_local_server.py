"""Loopback HTTP controls exercise real fixtures, never source applications."""
from __future__ import annotations

import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/codemap/scripts"))
from codemap_lib import automatic, history, server, tasks


class LocalDashboardServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-local-server-")
        self.addCleanup(self.temp.cleanup)
        # Production resolves project roots; normalize Windows TEMP aliases too.
        self.root = Path(self.temp.name).resolve() / "fixture-project"
        self.root.mkdir()
        self.source = self.root / "app.py"
        self.source.write_text("raise RuntimeError('Never execute fixture source')\n\ndef value():\n    return 1\n", encoding="utf-8")
        started = tasks.start(self.root, "test-session", "test-prompt")
        self.ident = started["task_id"]
        self.source.write_text(self.source.read_text(encoding="utf-8").replace("return 1", "return 2"), encoding="utf-8")
        frozen = tasks.freeze(self.root, self.ident)
        self.out = Path(frozen["out_dir"])
        (self.out / "codemap.html").write_text("<!doctype html><p>Generated fixture map</p>", encoding="utf-8")
        (self.out / "codemap.mp4").write_bytes(b"0123456789")
        (self.out / "codemap.diff").write_text("-return 1\n+return 2\n", encoding="utf-8")
        self.state = tasks.update(self.root, self.ident, status="awaiting_review", map_ready=True,
                                  error="PRIVATE_ERROR api_key='PRIVATE_CREDENTIAL_123'", prompt="PRIVATE_PROMPT")
        self.httpd = server.create_server(self.root)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, path="/api/session", method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=8)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def api(self, path, value=None, method="POST", headers=None, auth=True):
        sent = {"Content-Type": "application/json", "Origin": self.httpd.origin}
        if auth:
            sent["X-Codemap-Token"] = self.httpd.token
        sent.update(headers or {})
        body = json.dumps(value if value is not None else {}).encode("utf-8")
        status, response_headers, raw = self.request(path, method, body, sent)
        return status, response_headers, json.loads(raw)

    def test_index_boot_is_memory_only_and_session_exposes_only_public_controls(self):
        self.assertIn("*\n", (self.root / ".codemap/.gitignore").read_text(encoding="utf-8"))
        status, headers, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"window.__CODEMAP_SERVER__", page)
        self.assertNotIn(self.httpd.token.encode(), page)
        disk = (self.root / ".codemap/index.html").read_text(encoding="utf-8")
        self.assertNotIn(server.BOOT, disk)
        self.assertNotIn(self.httpd.token, disk)
        self.assertIn('<meta http-equiv="refresh" content="5">', disk)
        self.assertIsNone(re.search(rb'<meta\b[^>]*http-equiv=["\']refresh["\']', page.split(b"</head>")[0], re.I))
        status, headers, raw = self.request()
        session = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(session["token"], self.httpd.token)
        self.assertEqual(session["settings"], automatic.DEFAULTS)
        item = session["history"]["tasks"][0]
        self.assertTrue(item["controls"]["cancel"])
        self.assertFalse(item["controls"]["retry"])
        self.assertEqual(item["controls"]["revision"], self.state["revision"])
        self.assertNotIn(str(self.root).encode(), raw)
        for secret in (b"PRIVATE_ERROR", b"PRIVATE_CREDENTIAL_123", b"PRIVATE_PROMPT", b"Never execute fixture source"):
            self.assertNotIn(secret, raw)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_only_generated_maps_allow_same_origin_embedding(self):
        status, headers, _ = self.request(f"/tasks/{self.ident}/output/codemap.html?embedded=1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", headers["Content-Security-Policy"])
        for target in ("/", "/api/session", f"/tasks/{self.ident}/output/codemap.mp4", "/tasks/" + "f" * 32 + "/output/codemap.html"):
            with self.subTest(target=target):
                _, headers, _ = self.request(target)
                self.assertEqual(headers["X-Frame-Options"], "DENY")
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_host_origin_fetch_site_and_missing_or_wrong_csrf_are_rejected(self):
        for headers in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                status, _, _ = self.request(headers=headers)
                self.assertEqual(status, 403)
        action = f"/api/tasks/{self.ident}/cancel"
        for headers, auth in (({}, False), ({"X-Codemap-Token": "wrong"}, True),
                              ({"X-Codemap-Token": "\xe9"}, True), ({"Origin": "https://attacker.invalid"}, True)):
            with self.subTest(headers=headers):
                status, _, _ = self.api(action, headers=headers, auth=auth)
                self.assertEqual(status, 403)
        status, _, _ = self.request(action, "POST", b"{}", {"Content-Type": "application/json", "X-Codemap-Token": self.httpd.token})
        self.assertEqual(status, 403)
        self.assertEqual(tasks.load(self.root, self.ident)["status"], "awaiting_review")

    def test_encoded_api_path_cannot_bypass_cross_site_check(self):
        status, _, _ = self.request("/%61pi/session", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_private_files_and_noncanonical_paths_are_never_served(self):
        relative = f"/tasks/{self.ident}"
        paths = [relative + "/state.json", relative + "/work/app.py", relative + "/output/ir.json",
                 relative + "/output/review.json", relative + "/output/labels.json", relative + "/output/baseline/app.py",
                 "/config.json", "/.gitignore", "/../app.py", "/%2e%2e/app.py", "/%252e%252e/app.py",
                 relative + "/output/%2e%2e/../work/app.py", "/api/../../app.py", "/tasks/../config.json"]
        for path in paths:
            with self.subTest(path=path):
                status, _, raw = self.request(path)
                self.assertIn(status, (400, 404))
                self.assertNotIn(b"Never execute fixture source", raw)

    def test_public_outputs_head_and_video_ranges_work(self):
        video = f"/tasks/{self.ident}/output/codemap.mp4"
        status, headers, body = self.request(video)
        self.assertEqual((status, body), (200, b"0123456789"))
        self.assertEqual(headers["Content-Type"], "video/mp4")
        for requested, expected in (("bytes=2-5", b"2345"), ("bytes=7-", b"789"), ("bytes=-3", b"789")):
            status, headers, body = self.request(video, headers={"Range": requested})
            self.assertEqual((status, body), (206, expected))
            self.assertIn("Content-Range", headers)
        status, headers, body = self.request(video, "HEAD", headers={"Range": "bytes=0-3"})
        self.assertEqual(status, 206)
        self.assertEqual(body, b"")
        self.assertEqual(headers["Content-Length"], "4")
        for invalid in ("bytes=10-", "bytes=5-2", "bytes=-0", "bytes=1-2,4-5", "bytes=" + "9" * 200 + "-"):
            status, headers, body = self.request(video, headers={"Range": invalid})
            self.assertEqual(status, 416)
            self.assertEqual(headers["Content-Range"], "bytes */10")

    def test_artifact_reparse_file_or_parent_is_not_served(self):
        artifact = self.out / "codemap.html"
        original = history._is_reparse
        for target in (artifact, self.out):
            with self.subTest(target=target.name):
                with patch.object(history, "_is_reparse", side_effect=lambda value: value == target or original(value)):
                    status, _, _ = self.request(f"/tasks/{self.ident}/output/codemap.html")
                    self.assertEqual(status, 404)

    def test_legitimate_cancel_keeps_sources_and_rejects_stale_revision(self):
        action = f"/api/tasks/{self.ident}/cancel"
        status, _, result = self.api(action, {"revision": self.state["revision"] - 1})
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "stale_task")
        before = self.source.read_bytes()
        status, _, result = self.api(action, {"revision": self.state["revision"]})
        self.assertEqual(status, 200)
        self.assertEqual(result["task"]["status"], "cancelled")
        self.assertFalse(result["task"]["controls"]["retry"])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(tasks.verify(self.root, self.ident))

    def test_cancel_controls_do_not_interrupt_ongoing_coding_capture(self):
        for status in ("capturing", "active", "freezing", "complete", "skipped", "interrupted", "capture_failed"):
            with self.subTest(status=status):
                tasks.update(self.root, self.ident, status=status)
                code, _, raw = self.request("/api/history")
                item = json.loads(raw)["history"]["tasks"][0]
                self.assertEqual(code, 200)
                self.assertFalse(item["controls"]["cancel"])
                code, _, _ = self.api(f"/api/tasks/{self.ident}/cancel")
                self.assertEqual(code, 409)

    def test_retry_dispatches_only_failed_stage_and_queues_once(self):
        state = tasks.update(self.root, self.ident, status="analysis_failed", stage="analysis")
        def retry(root, ident, stage):
            self.assertEqual((root, ident, stage), (self.root, self.ident, "analysis"))
            tasks.update(root, ident, status="analysis_queued")
        with patch.object(automatic, "retry", side_effect=retry) as called:
            status, _, result = self.api(f"/api/tasks/{self.ident}/retry", {"revision": state["revision"], "stage": "analysis"})
            self.assertEqual(status, 200)
            self.assertEqual(result["task"]["status"], "analysis_queued")
            status, _, _ = self.api(f"/api/tasks/{self.ident}/retry", {"stage": "analysis"})
            self.assertEqual(status, 409)
            called.assert_called_once()

    def test_a_history_read_does_not_reject_an_available_control_as_busy(self):
        dispatched = threading.Event()
        finished = []
        original = automatic.cancel
        def cancel(root, ident):
            dispatched.set()
            return original(root, ident)
        def request_cancel():
            finished.append(self.api(f"/api/tasks/{self.ident}/cancel"))
        with patch.object(automatic, "cancel", side_effect=cancel):
            with self.httpd.action_lock:
                request = threading.Thread(target=request_cancel, daemon=True)
                request.start()
                self.assertTrue(dispatched.wait(timeout=3), "An unrelated dashboard read must not make cancellation busy")
            request.join(timeout=8)
        self.assertFalse(request.is_alive())
        self.assertEqual(finished[0][0], 200)
        self.assertEqual(finished[0][2]["task"]["status"], "cancelled")

    def test_retry_review_and_render_use_validating_public_commands(self):
        for stage, function in (("review", "review_task"), ("render", "finish")):
            with self.subTest(stage=stage):
                tasks.update(self.root, self.ident, status=stage + "_failed", stage=stage)
                with patch.object(automatic, function) as called:
                    status, _, _ = self.api(f"/api/tasks/{self.ident}/retry", {"stage": stage})
                self.assertEqual(status, 200)
                called.assert_called_once_with(self.root, self.ident, wait=0)

    def test_failed_controls_never_echo_raw_exception_details(self):
        tasks.update(self.root, self.ident, status="video_failed", stage="video")
        with patch.object(automatic, "retry", side_effect=automatic.AutomaticError("PRIVATE_TRACE password='PRIVATE_FAILURE'")):
            status, _, result = self.api(f"/api/tasks/{self.ident}/retry", {"stage": "video"})
        self.assertEqual(status, 409)
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_valid_settings_save_for_future_tasks_only_and_invalid_patch_preserves_file(self):
        tasks.update(self.root, self.ident, settings=dict(automatic.DEFAULTS))
        code, _, result = self.api("/api/settings", {"settings": {"video": False, "minutes": .5, "res": 720}})
        self.assertEqual(code, 200)
        self.assertFalse(result["settings"]["video"])
        self.assertEqual(result["settings"]["minutes"], .5)
        self.assertTrue(tasks.load(self.root, self.ident)["settings"]["video"])
        path = self.root / ".codemap/config.json"
        before = path.read_bytes()
        for invalid in ({"unknown": 1}, {"video": "false"}, {"timeout": 0}, {"tts": "cmd:arbitrary"}, {"enabled": None}):
            with self.subTest(invalid=invalid):
                code, _, _ = self.api("/api/settings", {"settings": invalid})
                self.assertEqual(code, 400)
                self.assertEqual(path.read_bytes(), before)

    def test_oversized_nonjson_nonfinite_and_unknown_actions_are_rejected(self):
        headers = {"Content-Type": "application/json", "Origin": self.httpd.origin, "X-Codemap-Token": self.httpd.token}
        for body, content_type in ((b"x" * 8200, "application/json"), (b"{}", "text/plain"),
                                   (b"[]", "application/json"), (b'{"settings":{"minutes":NaN}}', "application/json")):
            headers["Content-Type"] = content_type
            status, _, _ = self.request("/api/settings", "POST", body, headers)
            self.assertEqual(status, 400)
        status, _, _ = self.api("/api/execute", {"command": "PRIVATE_COMMAND"})
        self.assertEqual(status, 404)

    def test_unknown_and_malformed_status_records_remain_generic(self):
        path = Path(self.state["task_dir"]) / "state.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["status"] = ["PRIVATE_STATUS"]
        path.write_text(json.dumps(value), encoding="utf-8")
        status, _, raw = self.request("/api/session")
        self.assertEqual(status, 200)
        item = json.loads(raw)["history"]["tasks"][0]
        self.assertEqual(item["status"], "state_unavailable")
        self.assertFalse(item["controls"]["cancel"])
        self.assertNotIn(b"PRIVATE_STATUS", raw)

    def test_server_never_binds_a_public_interface(self):
        for host in ("0.0.0.0", "::", "localhost", "192.168.1.5"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                server.create_server(self.root, host=host)


if __name__ == "__main__":
    unittest.main()
