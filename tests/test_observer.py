import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from canopy_observer import (
    Config,
    RpcError,
    collect_report,
    evaluate_height_progress,
    request_json,
)


HEALTHY_RESPONSES = {
    "/v1/": "1.2.3",
    "/v1/query/height": 12345,
    "/v1/admin/peer-info": {
        "numPeers": 4,
        "numInbound": 1,
        "numOutbound": 3,
        "peers": [{"publicKey": "must-not-appear"}],
    },
    "/v1/admin/consensus-info": {
        "isSyncing": False,
        "status": "voting on proposal",
        "address": "must-not-appear",
        "publicKey": "must-not-appear",
        "view": {"height": 12344, "round": 0, "phase": "PROPOSE_VOTE"},
    },
    "/v1/admin/resource-usage": {
        "process": {"usedCPUPercent": 12.0, "usedMemoryPercent": 3.5},
        "system": {
            "usedCPUPercent": 20.0,
            "usedRAMPercent": 40.0,
            "usedDiskPercent": 50.0,
        },
    },
}


def fake_request(responses):
    def request(base_url, route, method, timeout, username, password):
        value = responses[route]
        if isinstance(value, Exception):
            raise value
        return value

    return request


class JsonHandler(BaseHTTPRequestHandler):
    def _reply(self, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply("test-version")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._reply(54321)

    def log_message(self, format, *args):
        return


class ObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), JsonHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_http_client_handles_get_and_post(self):
        version = request_json(self.base_url, "/v1/", "GET", 1)
        height = request_json(self.base_url, "/v1/query/height", "POST", 1)

        self.assertEqual(version, "test-version")
        self.assertEqual(height, 54321)

    def test_healthy_validator_is_ok(self):
        report = collect_report(Config(), fake_request(HEALTHY_RESPONSES))

        self.assertEqual(report["status"], "OK")
        self.assertEqual(report["node"]["height"], 12345)
        self.assertEqual(report["node"]["peers"]["total"], 4)

    def test_syncing_node_is_warning(self):
        responses = dict(HEALTHY_RESPONSES)
        responses["/v1/admin/consensus-info"] = {
            "isSyncing": True,
            "status": "syncing",
            "view": {},
        }

        report = collect_report(Config(), fake_request(responses))

        self.assertEqual(report["status"], "WARNING")

    def test_zero_peers_is_critical(self):
        responses = dict(HEALTHY_RESPONSES)
        responses["/v1/admin/peer-info"] = {
            "numPeers": 0,
            "numInbound": 0,
            "numOutbound": 0,
        }

        report = collect_report(Config(), fake_request(responses))

        self.assertEqual(report["status"], "CRITICAL")

    def test_public_rpc_failure_is_critical(self):
        responses = dict(HEALTHY_RESPONSES)
        responses["/v1/query/height"] = RpcError("height unavailable")

        report = collect_report(Config(), fake_request(responses))

        self.assertEqual(report["status"], "CRITICAL")

    def test_report_does_not_store_validator_or_peer_identity(self):
        report = collect_report(Config(), fake_request(HEALTHY_RESPONSES))
        serialized = str(report)

        self.assertNotIn("must-not-appear", serialized)

    def test_height_object_is_supported(self):
        responses = dict(HEALTHY_RESPONSES)
        responses["/v1/query/height"] = {"height": 999}

        report = collect_report(Config(), fake_request(responses))

        self.assertEqual(report["node"]["height"], 999)

    def test_height_progress_records_baseline(self):
        now = datetime(2026, 9, 2, tzinfo=timezone.utc)
        report = collect_report(Config(), fake_request(HEALTHY_RESPONSES))

        state = evaluate_height_progress(report, None, 600, now)

        self.assertEqual(report["status"], "OK")
        self.assertEqual(state["height"], 12345)

    def test_height_progress_detects_stalled_node(self):
        now = datetime(2026, 9, 2, tzinfo=timezone.utc)
        report = collect_report(Config(), fake_request(HEALTHY_RESPONSES))
        state = {
            "height": 12345,
            "changed_at": (now - timedelta(minutes=11)).isoformat(),
        }

        evaluate_height_progress(report, state, 600, now)

        self.assertEqual(report["status"], "CRITICAL")
        check = next(item for item in report["checks"] if item["name"] == "height_progress")
        self.assertIn("has not advanced", check["message"])

    def test_height_progress_resets_timer_after_advance(self):
        now = datetime(2026, 9, 2, tzinfo=timezone.utc)
        report = collect_report(Config(), fake_request(HEALTHY_RESPONSES))
        state = {
            "height": 12344,
            "changed_at": (now - timedelta(hours=1)).isoformat(),
        }

        new_state = evaluate_height_progress(report, state, 600, now)

        self.assertEqual(report["status"], "OK")
        self.assertEqual(new_state["height"], 12345)
        self.assertEqual(new_state["changed_at"], now.isoformat())


if __name__ == "__main__":
    unittest.main()
