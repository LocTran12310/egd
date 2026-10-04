"""Credentials never reach evidence: the UI runner's redaction and sensitive HTTP headers."""

import json
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from helpers import Project, serve

from egd.proof import ui_runner

API_KEY = "k3y-value-123"
COOKIE = "sid=c00kie-literal"
SET_COOKIE = "sid=srv-s3cret-cookie; HttpOnly"


class Me(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"user": "an", "echo": self.headers.get("X-Api-Key"),
                           "session": "srv-sess-xyz"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", SET_COOKIE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class UiRunner(unittest.TestCase):
    def test_redact_masks_every_value_longest_first(self):
        self.assertEqual(ui_runner.redact("token abc, abcdef and abc", ["abc", "abcdef"]),
                         "token ***, *** and ***")
        self.assertEqual(ui_runner.redact("nothing here", []), "nothing here")


class HttpTranscript(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)
        server = self.enterContext(serve(HTTPServer(("127.0.0.1", 0), Me)))
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text() + f'\n[env.local]\nbase_url = "http://127.0.0.1:{server.server_port}"\n')
        (self.p.root / ".egd" / "secrets.env").write_text(f"API_KEY={API_KEY}\n")
        (self.p.feature / "proof.toml").write_text(f'''
[[proof]]
id = "P-1"
kind = "http"
verifies = ["AC-1.1"]

[[proof.step]]
name = "who am I"
path = "/me"
headers = {{ Cookie = "{COOKIE}", X-Api-Key = "${{API_KEY}}" }}
capture = {{ session_cookie = "header:Set-Cookie" }}
expect.json = {{ "$.user" = "an" }}
expect.headers = {{ Set-Cookie = "sid=" }}

[[proof.step]]
name = "again"
path = "/me"
expect.headers = {{ Set-Cookie = "nothing-like-this" }}
''')
        self.p.commit("proof")

    def test_cookies_api_keys_and_session_values_are_masked(self):
        self.p.egd("proof", "run", "--by", "loc")
        text = "".join(t.read_text() for t in (self.p.feature / "runs").rglob("transcript.md"))
        events = "".join(e.read_text() for e in (self.p.feature / "events").glob("*proof_run.json"))
        self.assertIn("## 2. again — FAIL", text)  # the run happened and was written down
        self.assertIn("Cookie: ***", text)
        self.assertIn("X-Api-Key: ***", text)
        self.assertIn("got ***", text)  # a failed Set-Cookie check does not print the cookie
        for leaked in (API_KEY, "c00kie-literal", "srv-s3cret-cookie", "srv-sess-xyz"):
            self.assertNotIn(leaked, text, leaked)
            self.assertNotIn(leaked, events, leaked)


if __name__ == "__main__":
    unittest.main()
