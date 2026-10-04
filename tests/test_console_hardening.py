"""The console server under bad input, a broken registry, slow repositories and shared links:
it answers every request with a status and a message, never a dropped connection, and what it
exports carries no local paths."""

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import Project, request, serve
from test_console import ConsoleBase, ServerBase

from egd import console, dashboard, web

REAL = console.repo_snapshot


def raw(port, method, path, headers, body=b"", timeout=5):
    """One request with exactly these headers (no Content-Length added) → (status, JSON or text)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", f"127.0.0.1:{port}")
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders(body)
        r = conn.getresponse()
        text = r.read().decode()
        try:
            return r.status, json.loads(text)
        except json.JSONDecodeError:
            return r.status, text
    finally:
        conn.close()


class BadRequests(ServerBase):
    def post(self, path, body: bytes, length=None, extra=None):
        h = {"X-EGD-CSRF": self.csrf, "Content-Type": "application/json",
             "Content-Length": str(len(body) if length is None else length), **(extra or {})}
        return raw(self.port, "POST", path, h, body)

    def test_content_length_must_be_a_sane_number(self):
        t = time.monotonic()
        code, res = self.post("/api/action", b"", length=-1)
        self.assertEqual(code, 413)  # used to pass the size check and wait forever on read(-1)
        self.assertLess(time.monotonic() - t, 3)
        self.assertEqual(self.post("/api/action", b"", length="ten")[0], 400)
        self.assertEqual(self.post("/api/action", b"", length=console.MAX_BODY + 1)[0], 413)

    def test_body_must_be_a_json_object(self):
        for body in (b"[1]", b'"text"', b"3", b"null", b"\xff\xfe{"):
            code, res = self.post("/api/action", body)
            self.assertEqual(code, 400, body)
            self.assertIn("error", res)
        self.assertEqual(self.post("/api/repos", b"[1]")[0], 400)

    def test_fields_of_the_wrong_type_are_refused_with_a_message(self):
        rid = self.rid(self.a)
        for body in ({"repo": ["x"], "action": "pass"}, {"repo": {"a": 1}}):
            code, res = self.post("/api/action", json.dumps(body).encode())
            self.assertEqual(code, 404, res)
        self.a.pass_through("frame", "clarify", "design", "slice")
        self.a.ok("start", "T-1.1", "--by", "loc")
        for spent in ("abc", [1], "nan", -2):
            code, res = self.post("/api/action", json.dumps(
                {"repo": rid, "action": "submit", "target": "T-1.1", "spent": spent, "confirm": True}).encode())
            self.assertEqual(code, 409, (spent, res))
            self.assertIn("spent must be a number", res["error"])
        code, res = self.post("/api/repos", json.dumps({"op": "add", "path": 12}).encode())
        self.assertIn(code, (403, 409))
        code, res = self.post("/api/repos", json.dumps({"op": "remove", "path": "/tmp/a\x00b"}).encode())
        self.assertEqual(code, 400, res)
        self.assertIn("invalid request", res["error"])

    def test_a_bug_answers_500_instead_of_dropping_the_connection(self):
        with mock.patch.object(console, "repo_detail", side_effect=KeyError("boom")):
            code, res = self.req("GET", f"/api/repo?repo={self.rid(self.a)}")
        self.assertEqual(code, 500)
        self.assertIn("boom", res["error"])


class BrokenRegistry(ServerBase):
    def setUp(self):
        super().setUp()
        self.rid_a = self.rid(self.a)
        console.config_path().write_text("user = \n[[repo]\n")
        self.server.RequestHandlerClass.portfolio.invalidate()  # past the portfolio's few seconds of cache

    def test_every_route_answers_and_the_page_can_say_why(self):
        code, data = self.req("GET", "/api/portfolio")
        self.assertEqual(code, 200)
        self.assertTrue(data["error"].startswith("console.toml is invalid: "), data["error"])
        self.assertEqual(data["repos"], [])
        self.assertEqual(self.get("/")[0], 200)
        self.assertEqual(self.req("GET", "/api/boot")[0], 200)
        for path in (f"/api/repo?repo={self.rid_a}", f"/api/feature?repo={self.rid_a}&slug=x",
                     f"/files/{self.rid_a}/x/runs/a.md"):
            code, res = self.req("GET", path)
            self.assertEqual(code, 409, path)
            self.assertIn("console.toml is invalid", res["error"])
        h = {"X-EGD-CSRF": self.csrf}
        code, res = self.req("POST", "/api/action", {"repo": self.rid_a, "action": "pass", "target": "frame"}, h)
        self.assertEqual(code, 409)
        self.assertIn("console.toml is invalid", res["error"])
        code, res = self.req("POST", "/api/repos", {"op": "remove", "path": str(self.b.root)}, h)
        self.assertEqual(code, 409)
        # fixed, it shows again
        console.save_registry({"user": "loc", "roots": [], "repos": [str(self.a.root)], "signers": {}})
        self.server.RequestHandlerClass.portfolio.invalidate()
        code, data = self.req("GET", "/api/portfolio")
        self.assertNotIn("error", data)
        self.assertEqual(len(data["repos"]), 1)


class AtomicWrites(ConsoleBase):
    def test_registry_and_map_are_never_half_written(self):
        path = console.config_path()
        before = path.read_text()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                console.save_registry({"user": "someone", "roots": [], "repos": []})
        self.assertEqual(path.read_text(), before)
        self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])  # no temp file left
        m = self.a.root / ".egd" / "map.md"
        m.write_text(m.read_text().replace("reviewed_by: loc", "reviewed_by:", 1))
        before = m.read_text()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                console.perform(self.a.root, "loc", {"action": "sign_map"})
        self.assertEqual(m.read_text(), before)


class SlowRepository(ConsoleBase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(console, "READ_WAIT", 0.3))
        self.portfolio = console.Portfolio(ttl=0)
        self.gate = threading.Event()
        self.addCleanup(self.portfolio.wait, 10)
        self.addCleanup(self.gate.set)

    def slow_for(self, project):
        slow = project.root.resolve()

        def read(rid, root):
            if root == slow:
                assert self.gate.wait(10)
            return REAL(rid, root)
        return mock.patch.object(console, "repo_snapshot", read)

    def repo(self, data, project):
        return next(r for r in data["repos"] if r["path"] == str(project.root.resolve()))

    def test_a_slow_repository_shows_its_last_snapshot_marked_stale(self):
        self.portfolio.tagged()
        (self.a.root / ".egd" / "note.md").write_text("draft\n")  # a's fingerprint changes: read again
        with self.slow_for(self.a):
            t = time.monotonic()
            data, _ = self.portfolio.tagged()
            self.assertLess(time.monotonic() - t, 3)
            self.assertTrue(self.repo(data, self.a)["stale"])
            self.assertNotIn("stale", self.repo(data, self.b))
            self.gate.set()
            self.portfolio.wait(10)
        data, _ = self.portfolio.tagged()
        self.assertNotIn("stale", self.repo(data, self.a))
        self.assertEqual(self.repo(data, self.a)["uncommitted"], 1)

    def test_a_slow_repository_never_read_before_shows_an_error_and_the_rest_shows(self):
        with self.slow_for(self.a):
            t = time.monotonic()
            data, _ = self.portfolio.tagged()
            self.assertLess(time.monotonic() - t, 3)
        self.assertIn("taking longer", self.repo(data, self.a)["error"])
        self.assertTrue(self.repo(data, self.b)["features"])
        self.gate.set()
        self.portfolio.wait(10)
        self.assertNotIn("error", self.repo(self.portfolio.tagged()[0], self.a))


class Export(unittest.TestCase):
    def test_an_exported_site_carries_no_local_path(self):
        p = Project()
        self.addCleanup(p.close)
        root = p.root.resolve()
        (root / ".egd" / "note.md").write_text("draft\n")  # uncommitted: its hint names the folder
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"HOME": str(root.parent),
                                             console.CONFIG_ENV: str(root.parent / "console.toml")}):
            data = dashboard.portfolio(root)
            repo = data["repos"][0]
            self.assertEqual((repo["path"], data["config"], data["roots"]), ("", "", []))
            hint = next(i["hint"] for i in repo["inbox"] if i["kind"] == "uncommitted")
            self.assertTrue(hint.startswith("git -C . add .egd"), hint)
            index, _ = dashboard.export_site(root, {}, Path(tmp))
            for f in (index, Path(tmp) / "data.json"):
                text = f.read_text()
                self.assertNotIn(str(root), text)
                self.assertNotIn(str(root.parent), text)  # the home folder
                self.assertNotIn("console.toml", text)

    def test_the_live_console_keeps_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Project()
            self.addCleanup(p.close)
            with mock.patch.dict(os.environ, {console.CONFIG_ENV: str(Path(tmp) / "console.toml")}):
                console.save_registry({"user": "loc", "roots": [], "repos": [str(p.root)]})
                data = console.Portfolio(ttl=0).get()
            self.assertEqual(data["repos"][0]["path"], str(p.root.resolve()))
            self.assertTrue(data["config"].endswith("console.toml"))

    def test_scrub_replaces_whole_paths_only(self):
        self.assertEqual(console._scrub({"a": ["/r/repo/x", "/r/repository", "/r/repo"]}, "/r/repo", "/h"),
                         {"a": ["./x", "/r/repository", "."]})
        self.assertEqual(console._scrub("/", "/r", "/"), "/")  # a home of "/" is never replaced


class TokenLink(ConsoleBase):
    def setUp(self):
        super().setUp()
        self.server = self.enterContext(serve(console.make_server("127.0.0.1", 0, token="s3cr3t", warm=False)))
        self.port = self.server.server_address[1]

    def test_the_link_sets_a_per_port_cookie_and_drops_the_token_from_the_address(self):
        code, h, _ = request(self.port, "GET", "/?t=s3cr3t&lang=vi")
        self.assertEqual(code, 303)
        self.assertEqual(h["Location"], "/?lang=vi")
        cookie = h["Set-Cookie"].split(";")[0]
        self.assertEqual(cookie, f"egd_c_{self.port}=s3cr3t")
        self.assertIn("Max-Age=34560000", h["Set-Cookie"])  # survives a browser restart
        self.assertEqual(request(self.port, "GET", "/?t=s3cr3t")[1]["Location"], "/")
        self.assertEqual(request(self.port, "GET", "/", headers={"Cookie": cookie})[0], 200)
        # another server's cookie on the same host (cookies ignore ports) does not open this one
        self.assertEqual(request(self.port, "GET", "/", headers={"Cookie": "egd_c=s3cr3t"})[0], 403)
        self.assertEqual(request(self.port, "GET", "/", headers={"Cookie": "egd_c_1=s3cr3t"})[0], 403)

    def test_without_the_link_a_browser_is_told_how_to_get_in(self):
        code, h, body = request(self.port, "GET", "/")
        self.assertEqual((code, h["Content-Type"].split(";")[0]), (403, "text/html"))
        self.assertIn(b"egd console", body)
        self.assertNotIn(b"s3cr3t", body)
        code, h, body = request(self.port, "GET", "/api/boot")
        self.assertEqual((code, h["Content-Type"].split(";")[0]), (403, "application/json"))
        self.assertEqual(request(self.port, "GET", "/?t=wrong")[0], 403)
        # an API call with the token is answered, not redirected
        self.assertEqual(request(self.port, "GET", "/api/boot?t=s3cr3t")[0], 200)


class Hosts(ServerBase):
    def test_host_header_parsing(self):
        cases = {"127.0.0.1:8780": "127.0.0.1", "localhost": "localhost", "[::1]:8780": "[::1]",
                 "[::1]": "[::1]", "LOCALHOST:1": "localhost", "[::1]evil": None, "[::1]:80x": None,
                 "localhost:80:90": None, "[::1": None, "": None, ":80": None, "localhost:": "localhost"}
        for header, name in cases.items():
            self.assertEqual(console.host_name(header), name, header)

    def test_only_local_hosts_without_a_token(self):
        for host, code in (("[::1]:1", 200), ("[::1]evil", 403), ("[::1]evil.example:1", 403),
                           ("localhost.evil.example", 403), ("127.0.0.1:1:2", 403)):
            self.assertEqual(self.get("/api/boot", host=host)[0], code, host)


class RegistryCache(ConsoleBase):
    def setUp(self):
        super().setUp()
        self.tree = tempfile.TemporaryDirectory()
        self.addCleanup(self.tree.cleanup)
        self.base = Path(self.tree.name).resolve()
        (self.base / "one" / ".egd").mkdir(parents=True)
        console.save_registry({"user": "loc", "roots": [str(self.base)], "repos": [str(self.a.root)]})
        self.portfolio = console.Portfolio(ttl=0)

    def scans(self):
        return mock.patch.object(console, "repo_paths", wraps=console.repo_paths)

    def test_the_repositories_are_looked_for_once_until_something_changes(self):
        with self.scans() as scan:
            ids = self.portfolio.repos()
            self.assertEqual(self.portfolio.repos(), ids)
            self.assertEqual(scan.call_count, 1)
            (self.base / "two" / ".egd").mkdir(parents=True)  # a new folder in a root: the root changed
            self.assertIn("two", self.portfolio.repos())
            self.assertEqual(scan.call_count, 2)
            console.save_registry({"user": "loc", "roots": [str(self.base)], "repos": []})
            os.utime(console.config_path(), ns=(1, 1))  # even within one mtime tick
            self.assertNotIn(self.a.root.name, {p.name for p in self.portfolio.repos().values()})
            self.assertEqual(scan.call_count, 3)
            self.portfolio.invalidate()  # what /api/repos does
            self.portfolio.repos()
            self.assertEqual(scan.call_count, 4)
            self.portfolio.invalidate(self.a.root)  # an action: the registry did not change
            self.portfolio.repos()
            self.assertEqual(scan.call_count, 4)

    def test_a_repository_nested_deeper_shows_after_the_ttl(self):
        self.portfolio.repos()
        (self.base / "one" / "deep").mkdir()  # does not change the root folder itself
        (self.base / "group").mkdir()
        self.portfolio.repos()
        (self.base / "group" / "three" / ".egd").mkdir(parents=True)
        self.assertNotIn("three", self.portfolio.repos())
        with mock.patch.object(console, "REGISTRY_TTL", 0):
            self.assertIn("three", self.portfolio.repos())


class RepoList(ServerBase):
    def post(self, body):
        return self.req("POST", "/api/repos", body, {"X-EGD-CSRF": self.csrf})

    def test_remove_expands_the_home_folder_and_says_when_nothing_matched(self):
        home = self.b.root.resolve().parent
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            code, res = self.post({"op": "remove", "path": "~/" + self.b.root.resolve().name})
        self.assertEqual(code, 200, res)
        self.assertNotIn(str(self.b.root.resolve()), console.load_registry()["repos"])
        code, res = self.post({"op": "remove", "path": str(self.b.root)})
        self.assertEqual(code, 404)
        self.assertIn("is not in the list", res["error"])

    def test_add_is_confined_like_browsing(self):
        twin = self.a.copy()
        self.addCleanup(twin.close)
        code, res = self.post({"op": "add", "path": str(twin.root)})
        self.assertEqual(code, 403, res)
        with mock.patch.dict(os.environ, {"EGD_BROWSE_ROOTS": str(twin.root.parent)}):
            self.assertEqual(self.post({"op": "add", "path": str(twin.root)})[0], 200)
        self.assertEqual(len(self.req("GET", "/api/portfolio")[1]["repos"]), 3)


class Evidence(ServerBase):
    def test_files_are_found_without_reading_any_plan(self):
        rid, slug = self.rid(self.a), self.a.feature.name
        (self.a.feature / "runs" / "r1").mkdir(parents=True)
        (self.a.feature / "runs" / "r1" / "t.md").write_text("ok")
        with mock.patch.object(console, "all_features", side_effect=AssertionError("parsed the plans")):
            self.assertEqual(self.req("GET", f"/files/{rid}/{slug}/runs/r1/t.md"), (200, "ok"))
            for bad in (f"/files/{rid}/../{slug}/runs/r1/t.md", f"/files/{rid}/%2e%2e/runs/r1/t.md",
                        f"/files/{rid}/.x/runs/r1/t.md", f"/files/{rid}/{slug}%2f..%2f{slug}/runs/r1/t.md",
                        f"/files/{rid}/nope/runs/r1/t.md", f"/files/{rid}/{slug}/runs/../plan.toml"):
                self.assertEqual(self.req("GET", bad)[0], 404, bad)


class MapSigner(ConsoleBase):
    def test_the_first_reviewed_by_line_decides(self):
        self.assertEqual(console.map_signer("# Map\nreviewed_by: Linh\n"), "Linh")
        self.assertEqual(console.map_signer("# Map\nreviewed_by:\n\nReviewed_by: Bao\n"), "")
        self.assertEqual(console.map_signer("# Map\n"), "")
        m = self.a.root / ".egd" / "map.md"
        m.write_text(m.read_text().replace("reviewed_by: loc", "reviewed_by:", 1) + "\nreviewed_by: someone\n")
        snap = console.repo_snapshot("a", self.a.root.resolve())
        self.assertFalse(snap["map_signed"])  # as the frame gate reads it
        self.assertEqual(console.perform(self.a.root, "loc", {"action": "sign_map"}), "map signed by loc")
        lines = m.read_text().splitlines()
        self.assertEqual([ln for ln in lines if ln.startswith("reviewed_by:")],
                         ["reviewed_by: loc", "reviewed_by: someone"])


class LivePageAssets(ServerBase):
    def test_design_system_is_linked_in_order_and_cached_for_good(self):
        _, _, body = self.get("/")
        html = body.decode()
        self.assertLess(html.index('src="ui/ui-core.js?v='), html.index("const BOOT"))
        self.assertLess(html.index('href="ui/ui.css?v='), html.index("</head>"))
        self.assertNotIn("/*EGD_UI_CSS*/", html)
        for name in web.EAGER:
            body, ctype, version = web.asset(name)
            self.assertIn(f"ui/{name}?v={version}", html)
            self.assertNotIn(body.decode()[:200], html)  # linked, not inlined
            code, h, got = self.get(f"/ui/{name}?v={version}")
            self.assertEqual((code, h["Content-Type"], got), (200, ctype, body))
            self.assertIn("immutable", h["Cache-Control"])
        # one script, ui.js then i18n.js, as a snapshot inlines them: ui.js's top level calls N_()
        core = web.asset("ui-core.js")[0].decode()
        ui, i18n = (web.HERE / "ui" / "ui.js").read_text(), (web.HERE / "ui" / "i18n.js").read_text()
        self.assertEqual(core, ui + "\n" + i18n)
        for name in ("ui.js", "i18n.js"):  # not served on their own
            self.assertEqual(self.get(f"/ui/{name}")[0], 404)

    def test_a_snapshot_still_inlines_everything(self):
        html = dashboard.render({"generated": "x", "repos": [], "readonly": True})
        self.assertNotIn("ui/ui-core.js", html)
        self.assertIn(web.asset("ui-core.js")[0].decode()[:200], html)


class OncePerFeature(ConsoleBase):
    def test_each_feature_is_replayed_once(self):
        from egd import events
        calls = []

        def counting(f):
            calls.append(f.slug)
            return events.replay(f)
        with mock.patch.object(console, "replay", counting), mock.patch.object(dashboard, "replay", counting):
            snap = console.repo_snapshot("a", self.a.root.resolve())
        self.assertEqual(sorted(calls), sorted(f["slug"] for f in snap["features"]))

    def test_next_problems_match_the_gate_check(self):
        from egd import gates
        from egd.events import replay
        from egd.model import all_features
        from egd.util import load_config
        root = self.a.root.resolve()
        cfg = load_config(root)
        snap = console.repo_snapshot("a", root)
        for fd, f in zip(snap["features"], all_features(root)):
            st = replay(f)
            self.assertEqual(fd["next_problems"], gates.check(f, st, cfg, gates.next_gate(f, st))[:12])


if __name__ == "__main__":
    unittest.main()


class LanVisitors(unittest.TestCase):
    """--lan: other machines may look; actions and folder browsing stay with this machine."""

    def handler(self, ip, remote_readonly=True, readonly=False):
        from egd import console
        server = console.make_server("127.0.0.1", 0, "tok", readonly, warm=False, remote_readonly=remote_readonly)
        self.addCleanup(server.server_close)
        h = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        h.client_address = (ip, 50000)
        return h

    def test_another_machine_only_looks(self):
        self.assertTrue(self.handler("192.168.1.42").readonly)
        self.assertTrue(self.handler("::ffff:10.0.0.7").readonly)

    def test_this_machine_still_acts(self):
        for ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.assertFalse(self.handler(ip).readonly, ip)

    def test_lan_write_lets_everyone_act_and_readonly_wins(self):
        self.assertFalse(self.handler("192.168.1.42", remote_readonly=False).readonly)
        self.assertTrue(self.handler("127.0.0.1", readonly=True).readonly)
