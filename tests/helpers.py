import atexit
import contextlib
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Tests never touch this machine's own EGD — its background console (launchd/systemd), ~/.egd,
# Playwright in ~/.egd/ui: HOME is a throwaway folder, and the service manager is out of reach
# unless a test stands in for it.
_HOME = tempfile.mkdtemp(prefix="egd-test-home-")
os.environ["HOME"] = _HOME
atexit.register(shutil.rmtree, _HOME, True)
for _k in ("EGD_TOKEN", "EGD_CONSOLE_CONFIG", "EGD_UI_PYTHON", "XDG_CONFIG_HOME"):
    os.environ.pop(_k, None)

from egd import service as _service  # noqa: E402


def _no_service_manager(*cmd):
    raise AssertionError(f"a test reached the real service manager: {' '.join(cmd)}")


_service._run = _no_service_manager

from egd.cli import main  # noqa: E402

BRIEF = """# Checkout
## Problem
Buyers cannot see a total before paying.
## Outcome
Checkout shows the total and creates a pending order.
## Success signal
Support tickets about totals drop.
## Out of scope
Discount codes.
"""

DESIGN = """# Design
## Approach
Server computes totals.
## Alternatives considered
Client-side totals.
## Failure modes
Empty cart → 422.
"""

PLAN = """
[feature]
title = "Checkout"
tier = "{tier}"

[[assumption]]
id = "A-1"
text = "VND only"
blocking = true
status = "confirmed"
resolution = "Client confirmed on call"

[[ac]]
id = "AC-1.1"
given = "a cart with 2 x A1 at 150000"
when = "the buyer checks out"
then = "the total is 300000 and the order is pending"
levels = ["integration"]

[[decision]]
id = "D-1"
title = "Server-side totals"
status = "accepted"

[[slice]]
id = "S-1"
title = "Pending order"
covers = ["AC-1.1"]
demo = "check out, see 300000"

[[task]]
id = "T-1.1"
slice = "S-1"
title = "POST /orders"
estimate_h = 3
touches = ["src/**"]
tests = ["tests/ok.sh"]
done_when = ["returns 201"]

[[task]]
id = "T-1.2"
slice = "S-1"
title = "Confirmation"
estimate_h = 2
depends_on = ["T-1.1"]
touches = ["src/**"]
tests = ["tests/ok.sh"]
done_when = ["shows total"]
"""


class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, doc):
        body = json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        doc = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/login":
            if doc.get("password") == "s3cret-pass":
                return self._send(200, {"accessToken": "tok-abc-123"})
            return self._send(401, {"error": "bad"})
        if self.path == "/orders":
            if self.headers.get("Authorization") != "Bearer tok-abc-123":
                return self._send(401, {"error": "auth"})
            total = sum(150000 * i["qty"] for i in doc.get("items", []))
            return self._send(201, {"id": "o1", "total": total, "status": "pending"})
        self._send(404, {})


@contextlib.contextmanager
def serve(server):
    """Run `server` on a thread until the block ends. It polls for shutdown every 50 ms: with
    serve_forever's default 0.5 s, every test that starts a server spends half a second stopping it.
    In setUp: `self.enterContext(serve(server))`."""
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def request(port, method, path, body=None, headers=None, host=None):
    """One HTTP request to 127.0.0.1:`port` → (status, headers, raw body). `body` is sent as JSON
    (a dict) or as is (bytes); `host` overrides the Host header."""
    conn = http.client.HTTPConnection("127.0.0.1", port)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    try:
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        return r.status, r.headers, r.read()
    finally:
        conn.close()


@contextlib.contextmanager
def api_server():
    with serve(HTTPServer(("127.0.0.1", 0), Api)) as server:
        yield f"http://127.0.0.1:{server.server_port}"


class Project:
    """A throwaway git repo with EGD set up."""

    def __init__(self, tier="standard"):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.txt").write_text("v1\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "ok.sh").write_text("exit 0\n")
        self.egd("setup")
        self.egd("new", "checkout", "--tier", tier, "--by", "loc")
        self.feature = next((self.root / ".egd" / "features").iterdir())
        (self.feature / "brief.md").write_text(BRIEF)
        (self.feature / "design.md").write_text(DESIGN)
        (self.feature / "plan.toml").write_text(PLAN.format(tier=tier))
        (self.feature / "release.md").write_text("# R\n## Rollback\nRevert the deploy.\n")
        (self.feature / "proof.toml").write_text("")
        map_md = self.root / ".egd" / "map.md"
        map_md.write_text(map_md.read_text().replace("reviewed_by:", "reviewed_by: loc", 1)
                          .replace("## Stack\n", "## Stack\nPlain text files and shell tests.\n", 1))
        cfg = self.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text().replace('# test_command = "pnpm vitest run {tests}"',
                                               'test_command = "sh {tests}"'))
        self.commit("init")

    def copy(self):
        """An independent copy of this repository — cheaper than building another one."""
        twin = object.__new__(Project)
        twin._tmp = tempfile.TemporaryDirectory()
        twin.root = Path(twin._tmp.name)
        shutil.copytree(self.root, twin.root, symlinks=True, dirs_exist_ok=True)
        twin.feature = twin.root / self.feature.relative_to(self.root)
        return twin

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=True).stdout

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg, "--allow-empty")

    def egd(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(list(argv))
        finally:
            os.chdir(cwd)
        return code, out.getvalue() + err.getvalue()

    def ok(self, *argv):
        code, text = self.egd(*argv)
        assert code == 0, f"egd {' '.join(argv)} → {code}\n{text}"
        return text

    def refused(self, *argv):
        code, text = self.egd(*argv)
        assert code == 1, f"expected refusal: egd {' '.join(argv)} → {code}\n{text}"
        return text

    def pass_through(self, *gates):
        for g in gates:
            self.ok("pass", g, "--by", "loc")

    def close(self):
        self._tmp.cleanup()
