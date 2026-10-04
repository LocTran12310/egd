import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import Project, request, serve

from egd import console


class ConsoleBase(unittest.TestCase):
    """Two EGD repositories in a throwaway console registry, signed as "loc". The repositories are
    built once per class (`prepare` adds to them) and each test works on its own copy."""

    @classmethod
    def setUpClass(cls):
        cls._a, cls._b = Project(), Project()
        cls.addClassCleanup(cls._a.close)
        cls.addClassCleanup(cls._b.close)
        cls.prepare()

    @classmethod
    def prepare(cls):
        """Shared starting state for every test of the class, on `cls._a` / `cls._b`."""

    def setUp(self):
        self.a, self.b = self._a.copy(), self._b.copy()
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)
        self.cfg_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.cfg_dir.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {console.CONFIG_ENV: str(Path(self.cfg_dir.name) / "console.toml")}))
        console.save_registry({"user": "loc", "roots": [], "repos": [str(self.a.root), str(self.b.root)]})


class ServerBase(ConsoleBase):
    """ConsoleBase with a console server running; `ttl` set → the portfolio cache is that short."""
    ttl = None

    def setUp(self):
        super().setUp()
        # no start-up read: each test decides what is on disk before the console first looks
        self.server = self.enterContext(serve(console.make_server("127.0.0.1", 0, warm=False)))
        if self.ttl is not None:
            self.server.RequestHandlerClass.portfolio.ttl = self.ttl
        self.port = self.server.server_address[1]
        self.csrf = self.server.RequestHandlerClass.csrf

    def get(self, path, headers=None, host=None):
        """→ (status, headers, raw body)"""
        return request(self.port, "GET", path, headers=headers, host=host)

    def req(self, method, path, body=None, headers=None, host=None):
        """→ (status, body as JSON, or as text when it is not JSON)"""
        code, _, raw = request(self.port, method, path, body, headers, host)
        try:
            return code, json.loads(raw)
        except json.JSONDecodeError:
            return code, raw.decode()

    def act(self, project, action, target=None, **fields):
        """One console action in `project`'s repository → (status, JSON body)."""
        body = {"repo": self.rid(project), "action": action, "target": target, **fields}
        return self.req("POST", "/api/action", body, {"X-EGD-CSRF": self.csrf})

    def rid(self, project):
        data = self.req("GET", "/api/portfolio")[1]
        return next(r["id"] for r in data["repos"] if r["path"] == str(project.root.resolve()))


class Scan(unittest.TestCase):
    def test_scan_finds_repositories_and_ids_are_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("one", "nested/two"):
                (root / name / ".egd").mkdir(parents=True)
            (root / "node_modules" / "x" / ".egd").mkdir(parents=True)
            found = console.scan(root)
            self.assertEqual(sorted(p.name for p in found), ["one", "two"])
            ids = console.repo_ids([root / "x" / "app", root / "y" / "app"])
            self.assertEqual(len(ids), 2)


class Registry(ConsoleBase):
    def test_portfolio_has_both_repos_and_inbox(self):
        self.a.pass_through("frame", "clarify", "design", "slice")
        self.a.ok("start", "T-1.1", "--by", "an")
        self.a.ok("submit", "T-1.1", "--by", "an", "--confirm")
        data = console.Portfolio(ttl=0).get()
        self.assertEqual(len(data["repos"]), 2)
        kinds = {i["kind"] for r in data["repos"] for i in r["inbox"]}
        self.assertIn("review", kinds)   # repo a: task waiting for review
        self.assertIn("gate", kinds)     # repo b: frame is ready to pass


class Server(ServerBase):
    def test_page_and_data(self):
        code, html = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(self.csrf, html)
        self.assertEqual(self.req("GET", "/api/portfolio")[1]["user"], "loc")

    def test_foreign_host_refused(self):
        self.assertEqual(self.req("GET", "/api/portfolio", host="evil.example")[0], 403)

    def test_action_needs_csrf_and_same_origin(self):
        body = {"repo": self.rid(self.a), "action": "pass", "target": "frame"}
        self.assertEqual(self.req("POST", "/api/action", body)[0], 403)
        bad_origin = {"X-EGD-CSRF": self.csrf, "Origin": "http://evil.example"}
        self.assertEqual(self.req("POST", "/api/action", body, bad_origin)[0], 403)

    def test_actions_are_real_transitions_signed_by_the_console_user(self):
        h = {"X-EGD-CSRF": self.csrf}
        rid = self.rid(self.a)
        code, res = self.req("POST", "/api/action", {"repo": rid, "action": "pass", "target": "frame"}, h)
        self.assertEqual(code, 200, res)
        last = self.a.ok("trail").strip().splitlines()[-1]
        self.assertRegex(last, r"loc\s+gate_passed\s+frame")
        # a refused transition comes back as 409 with the reasons
        code, res = self.req("POST", "/api/action", {"repo": rid, "action": "pass", "target": "build"}, h)
        self.assertEqual(code, 409)
        self.assertTrue(res["problems"])

    def test_resolve_assumption_writes_the_plan(self):
        plan = self.a.feature / "plan.toml"
        plan.write_text(plan.read_text().replace('status = "confirmed"', 'status = "open"'))
        h = {"X-EGD-CSRF": self.csrf}
        rid = self.rid(self.a)
        code, _ = self.req("POST", "/api/action", {"repo": rid, "feature": self.a.feature.name,
                                                   "action": "resolve", "target": "A-1"}, h)
        self.assertEqual(code, 409)  # a resolution note is mandatory
        code, res = self.req("POST", "/api/action", {"repo": rid, "feature": self.a.feature.name,
                                                     "action": "resolve", "target": "A-1",
                                                     "resolution": "PO said yes"}, h)
        self.assertEqual(code, 200, res)
        self.assertIn('status = "confirmed"', plan.read_text())
        self.assertIn("PO said yes (loc)", plan.read_text())

    def test_blank_map_cannot_be_signed(self):
        m = self.a.root / ".egd" / "map.md"
        from egd.templates import MAP
        m.write_text(MAP)
        h = {"X-EGD-CSRF": self.csrf}
        code, res = self.req("POST", "/api/action", {"repo": self.rid(self.a), "action": "sign_map"}, h)
        self.assertEqual(code, 409)
        self.assertIn("blank template", res["error"])

    def test_each_repo_can_sign_under_its_own_name(self):
        h = {"X-EGD-CSRF": self.csrf}
        rid_a, rid_b = self.rid(self.a), self.rid(self.b)
        code, res = self.req("POST", "/api/repos", {"op": "signer", "path": str(self.a.root), "name": "  Minh   Le "}, h)
        self.assertEqual((code, res["name"]), (200, "Minh Le"))
        repos = {r["id"]: r for r in self.req("GET", "/api/portfolio")[1]["repos"]}
        self.assertEqual((repos[rid_a]["signer"], repos[rid_a]["signer_own"]), ("Minh Le", True))
        self.assertEqual((repos[rid_b]["signer"], repos[rid_b]["signer_own"]), ("loc", False))
        # actions in repo a are signed as Minh Le, in repo b with the default name
        self.req("POST", "/api/action", {"repo": rid_a, "action": "pass", "target": "frame"}, h)
        self.req("POST", "/api/action", {"repo": rid_b, "action": "pass", "target": "frame"}, h)
        self.assertRegex(self.a.ok("trail").strip().splitlines()[-1], r"Minh Le\s+gate_passed")
        self.assertRegex(self.b.ok("trail").strip().splitlines()[-1], r"loc\s+gate_passed")
        # survives other registry writes; empty name goes back to the default
        self.req("POST", "/api/repos", {"op": "remove", "path": str(self.b.root)}, h)
        self.assertEqual(console.load_registry()["signers"][str(self.a.root.resolve())], "Minh Le")
        self.req("POST", "/api/repos", {"op": "signer", "path": str(self.a.root), "name": ""}, h)
        self.assertEqual(console.signer_for(console.load_registry(), self.a.root), "loc")

    def test_signer_outside_team_is_flagged(self):
        (self.a.root / ".egd" / "team.toml").write_text('[[member]]\nname = "loc"\nroles = ["pm"]\n')
        h = {"X-EGD-CSRF": self.csrf}
        self.req("POST", "/api/repos", {"op": "signer", "path": str(self.a.root), "name": "Mallory"}, h)
        repo = next(r for r in self.req("GET", "/api/portfolio")[1]["repos"] if r["path"] == str(self.a.root.resolve()))
        self.assertFalse(repo["signer_in_team"])

    def test_console_signs_with_the_name_team_toml_gives(self):
        (self.a.root / ".egd" / "team.toml").write_text(
            '[[member]]\nname = "Linh"\naliases = ["linh-dev"]\nroles = ["pm"]\n')
        h = {"X-EGD-CSRF": self.csrf}
        self.req("POST", "/api/repos", {"op": "signer", "path": str(self.a.root), "name": "linh-dev"}, h)
        code, res = self.act(self.a, "pass", "frame")
        self.assertEqual(code, 200, res)
        self.assertRegex(self.a.ok("trail").strip().splitlines()[-1], r"Linh\s+gate_passed\s+frame")
        m = self.a.root / ".egd" / "map.md"
        m.write_text(m.read_text().replace("reviewed_by: loc", "reviewed_by:", 1))
        self.assertEqual(self.act(self.a, "sign_map")[0], 200)
        self.assertIn("reviewed_by: Linh\n", m.read_text())
        # a name the team does not know is refused, with the way out
        self.req("POST", "/api/repos", {"op": "signer", "path": str(self.a.root), "name": "Mallory"}, h)
        code, res = self.act(self.a, "pass", "clarify")
        self.assertEqual(code, 409)
        self.assertIn("'Mallory' is not in .egd/team.toml", res["error"])
        self.assertTrue(any("Signs as" in p for p in res["problems"]), res["problems"])

    def test_repos_can_be_added_and_removed(self):
        h = {"X-EGD-CSRF": self.csrf}
        code, _ = self.req("POST", "/api/repos", {"op": "add", "path": self.cfg_dir.name}, h)
        self.assertEqual(code, 403)  # outside the folders the console may browse (the home folder)
        with mock.patch.dict(os.environ, {"EGD_BROWSE_ROOTS": self.cfg_dir.name}):
            code, _ = self.req("POST", "/api/repos", {"op": "add", "path": self.cfg_dir.name}, h)
        self.assertEqual(code, 409)  # no .egd there
        self.assertEqual(self.req("POST", "/api/repos", {"op": "remove", "path": str(self.b.root)}, h)[0], 200)
        self.assertEqual(len(self.req("GET", "/api/portfolio")[1]["repos"]), 1)


class Browse(ServerBase):
    def setUp(self):
        super().setUp()
        self.base = Path(self.cfg_dir.name).resolve()
        (self.base / "code" / "shop" / ".egd").mkdir(parents=True)
        (self.base / "code" / "legacy" / ".ai" / "features").mkdir(parents=True)
        (self.base / "code" / "plain" / ".git").mkdir(parents=True)
        self.enterContext(mock.patch.dict(os.environ, {"EGD_BROWSE_ROOTS": str(self.base / "code")}))

    def browse(self, path, csrf=True):
        return self.req("GET", path, headers={"X-EGD-CSRF": self.csrf} if csrf else None)

    def test_lists_folders_with_their_kind(self):
        code, d = self.browse("/api/browse?path=" + str(self.base / "code"))
        self.assertEqual(code, 200)
        self.assertEqual({e["name"]: e["kind"] for e in d["entries"]},
                         {"legacy": "ai-dlc", "plain": "git", "shop": "egd"})
        self.assertIsNone(d["parent"])

    def test_confined_to_roots_and_needs_csrf(self):
        self.assertEqual(self.browse("/api/browse?path=/")[0], 403)
        self.assertEqual(self.browse("/api/browse?path=" + str(self.base / "code" / ".." / ".."))[0], 403)
        self.assertEqual(self.browse("/api/browse?path=" + str(self.base / "code"), csrf=False)[0], 403)

    def test_scan_finds_repositories(self):
        code, d = self.browse("/api/browse/scan?path=" + str(self.base / "code"))
        self.assertEqual(code, 200)
        self.assertEqual(sorted(r["name"] for r in d["repos"]), ["legacy", "shop"])


class SetupFromConsole(Browse):
    def post(self, body):
        return self.req("POST", "/api/repos", body, {"X-EGD-CSRF": self.csrf})

    def test_setup_creates_egd_and_registers(self):
        target = self.base / "code" / "plain"
        code, res = self.post({"op": "add", "path": str(target)})
        self.assertEqual(code, 409)
        self.assertTrue(res["setup_possible"])
        code, res = self.post({"op": "setup", "path": str(target)})
        self.assertEqual(code, 200, res)
        self.assertTrue((target / ".egd" / "config.toml").exists())
        self.assertIn(str(target), console.load_registry()["repos"])

    def test_setup_refuses_non_git_and_outside_roots(self):
        (self.base / "code" / "notes").mkdir()
        self.assertEqual(self.post({"op": "setup", "path": str(self.base / "code" / "notes")})[0], 409)
        outside = self.base / "elsewhere"
        (outside / ".git").mkdir(parents=True)
        self.assertEqual(self.post({"op": "setup", "path": str(outside)})[0], 403)
        self.assertFalse((outside / ".egd").exists())


class ReadOnly(ConsoleBase):
    def test_readonly_refuses_actions(self):
        with serve(console.make_server("127.0.0.1", 0, readonly=True)) as server:
            code, _, _ = request(server.server_address[1], "POST", "/api/action", b"{}",
                                 {"X-EGD-CSRF": server.RequestHandlerClass.csrf})
            self.assertEqual(code, 403)


class RepoDetail(unittest.TestCase):
    def test_shows_what_config_expects_but_never_secret_values(self):
        from egd.console import repo_detail
        from egd.templates import setup_repo
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup_repo(root)
            cfg = root / ".egd" / "config.toml"
            cfg.write_text(cfg.read_text() + '\n[env.staging]\nbase_url = "${STAGING_URL}"\n')
            (root / ".egd" / "secrets.env").write_text("STAGING_URL=https://s3cret.example\n")
            d = repo_detail("demo", root)
            self.assertEqual(d["secrets"], [{"name": "STAGING_URL", "set": True}])  # template comments ignored
            self.assertEqual(d["env"][0]["base_url"], "${STAGING_URL}")
            self.assertNotIn("s3cret", json.dumps(d))


if __name__ == "__main__":
    unittest.main()
