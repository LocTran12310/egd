"""A request never waits for a refresh it does not need: old snapshots are served while one background
read replaces them; a real change or an action is read at once. Payloads carry only what views read."""

import gzip
import json
import threading
import time
import unittest
from unittest import mock

from test_console import ConsoleBase, ServerBase

from egd import console
from egd.core import core_info
from egd.dashboard import portfolio as snapshot_data, render
from egd.util import DEFAULT_CONFIG

REAL = console.repo_snapshot


class Slow:
    """Stands in for `repo_snapshot`: counts reads per repository, and holds the ones `hold` picks
    until `release()`. `tweak` changes what a read returns (content the fingerprint cannot see)."""

    def __init__(self, hold=lambda root, n: False, tweak=None):
        self.hold, self.tweak = hold, tweak
        self.calls: list = []
        self.started = threading.Event()
        self.gate = threading.Event()
        self.lock = threading.Lock()

    def __call__(self, rid, root):
        with self.lock:
            self.calls.append(root)
            n = self.calls.count(root)
        if self.hold(root, n):
            self.started.set()
            assert self.gate.wait(10), "never released"
        snap = REAL(rid, root)
        return self.tweak(root, snap) if self.tweak else snap

    def release(self):
        self.gate.set()

    def count(self, root):
        with self.lock:
            return self.calls.count(root.resolve())


def repo_of(data, project):
    return next(r for r in data["repos"] if r["path"] == str(project.root.resolve()))


class StaleWhileRevalidate(ConsoleBase):
    def setUp(self):
        super().setUp()
        self.portfolio = console.Portfolio(ttl=0)
        self.addCleanup(self.portfolio.wait, 10)

    def test_an_old_snapshot_is_served_at_once_while_one_background_read_runs(self):
        first, tag = self.portfolio.tagged()
        self.portfolio.max_age = 0  # every snapshot is now old
        slow = Slow(hold=lambda root, n: True, tweak=lambda root, s: {**s, "branch": "refreshed"})
        self.addCleanup(slow.release)
        with mock.patch.object(console, "repo_snapshot", slow):
            t = time.monotonic()
            data, tag2 = self.portfolio.tagged()
            self.assertLess(time.monotonic() - t, 2)  # did not wait for the held reads
            self.assertTrue(slow.started.wait(5))
            self.assertEqual((tag2, data["repos"]), (tag, first["repos"]))
            for _ in range(3):  # polls meanwhile: still served at once, and no second read is started
                self.assertEqual(self.portfolio.tagged()[1], tag)
            self.assertEqual((slow.count(self.a.root), slow.count(self.b.root)), (1, 1))
            slow.release()
            self.portfolio.wait(10)
        data, tag3 = self.portfolio.tagged()
        self.assertNotEqual(tag3, tag)  # what the refresh changed shows, under a new tag
        self.assertEqual(repo_of(data, self.a)["branch"], "refreshed")

    def test_a_refresh_that_changes_nothing_keeps_the_tag(self):
        _, tag = self.portfolio.tagged()
        self.portfolio.max_age = 0
        with mock.patch.object(console, "repo_snapshot", wraps=REAL) as built:
            self.portfolio.tagged()
            self.portfolio.wait(10)
            self.assertEqual(built.call_count, 2)
        self.portfolio.max_age = 60
        self.assertEqual(self.portfolio.tagged()[1], tag)

    def test_a_changed_fingerprint_is_read_before_answering(self):
        self.portfolio.tagged()
        (self.a.root / ".egd" / "note.md").write_text("draft\n")
        with mock.patch.object(console, "repo_snapshot", wraps=REAL) as built:
            data, _ = self.portfolio.tagged()
            self.assertEqual([c.args[1] for c in built.call_args_list], [self.a.root.resolve()])
        self.assertEqual(repo_of(data, self.a)["uncommitted"], 1)

    def test_an_action_is_read_at_once_and_an_older_background_read_is_dropped(self):
        self.portfolio.tagged()
        self.portfolio.max_age = 0
        a = self.a.root.resolve()
        # the background refresh of a is held; it would bring back a's state from before the action
        slow = Slow(hold=lambda root, n: root == a and n == 1,
                    tweak=lambda root, s: {**s, "branch": "from-before"} if root == a and slow.count(root) == 1 else s)
        self.addCleanup(slow.release)
        with mock.patch.object(console, "repo_snapshot", slow):
            self.portfolio.tagged()
            self.assertTrue(slow.started.wait(5))
            self.portfolio.max_age = 60
            self.a.pass_through("frame")
            self.portfolio.invalidate(self.a.root)  # what /api/action does
            data, tag = self.portfolio.tagged()  # read now, not the held refresh
            self.assertEqual(slow.count(self.a.root), 2)
            self.assertNotEqual(repo_of(data, self.a)["features"][0]["gate"], "frame")
            slow.release()
            self.portfolio.wait(10)
        data, tag2 = self.portfolio.tagged()
        self.assertNotEqual(repo_of(data, self.a)["branch"], "from-before")
        self.assertEqual(tag2, tag)

    def test_a_failed_refresh_is_shown_by_the_next_request(self):
        self.portfolio.tagged()
        self.portfolio.max_age = 0

        def broken(rid, root):
            raise OSError("disk went away")
        with mock.patch.object(console, "repo_snapshot", broken):
            self.portfolio.tagged()  # still the old snapshots
            self.portfolio.wait(10)
            data, _ = self.portfolio.tagged()
        self.assertIn("disk went away", repo_of(data, self.a)["error"])

    def test_warm_start_reads_every_repository_once(self):
        slow = Slow(hold=lambda root, n: True)
        self.addCleanup(slow.release)
        with mock.patch.object(console, "repo_snapshot", slow):
            self.portfolio.warm()
            self.assertTrue(slow.started.wait(5))
            asked = threading.Thread(target=self.portfolio.tagged)  # the first page load, meanwhile
            asked.start()
            time.sleep(0.2)
            slow.release()
            asked.join(10)
            self.portfolio.wait(10)
            self.assertEqual((slow.count(self.a.root), slow.count(self.b.root)), (1, 1))  # shared, not doubled
            self.portfolio.tagged()
            self.assertEqual(len(slow.calls), 2)


class WarmServer(ConsoleBase):
    def test_the_console_reads_its_repositories_as_it_starts(self):
        with mock.patch.object(console, "repo_snapshot", wraps=REAL) as built:
            server = console.make_server("127.0.0.1", 0)
            try:
                portfolio = server.RequestHandlerClass.portfolio
                portfolio.wait(10)
                self.assertEqual(built.call_count, 2)
                portfolio.tagged()
                self.assertEqual(built.call_count, 2)  # the first page load found them ready
            finally:
                server.server_close()


class Payload(ServerBase):
    ttl = 0

    def test_refresh_that_changed_content_changes_the_etag(self):
        code, h, _ = self.get("/api/portfolio")
        tag = h["ETag"]
        portfolio = self.server.RequestHandlerClass.portfolio
        portfolio.max_age = 0
        tweak = lambda root, s: {**s, "uncommitted": 7}  # noqa: E731 — a change the fingerprint misses
        with mock.patch.object(console, "repo_snapshot", Slow(tweak=tweak)):
            self.assertEqual(self.get("/api/portfolio", {"If-None-Match": tag})[0], 304)  # served stale
            portfolio.wait(10)
        portfolio.max_age = 60
        code, h, body = self.get("/api/portfolio", {"If-None-Match": tag})
        self.assertEqual(code, 200)
        self.assertNotEqual(h["ETag"], tag)
        self.assertEqual(repo_of(json.loads(body), self.a)["uncommitted"], 7)

    def test_portfolio_leaves_out_the_guide_rules_and_unread_fields(self):
        data = json.loads(self.get("/api/portfolio")[2])
        self.assertFalse({"core", "core_vi"} & set(data))
        f = repo_of(data, self.a)["features"][0]
        self.assertNotIn("why", f)
        self.assertIn("health", f)
        detail = json.loads(self.get(f"/api/feature?repo={repo_of(data, self.a)['id']}&slug={f['slug']}")[2])
        self.assertNotIn("brief", detail)

    def test_core_endpoint_in_both_languages_with_etag_and_guard(self):
        roles = DEFAULT_CONFIG["roles"]
        tags = {}
        for lang, want in (("en", core_info(roles)), ("vi", core_info(roles, "vi")), ("fr", core_info(roles))):
            code, h, body = self.get(f"/api/core?lang={lang}", {"Accept-Encoding": "gzip"})
            self.assertEqual((code, h["Content-Encoding"], h["Cache-Control"]), (200, "gzip", "private, no-cache"))
            self.assertEqual(json.loads(gzip.decompress(body)), want)
            tags[lang] = h["ETag"]
            code, h, body = self.get(f"/api/core?lang={lang}", {"If-None-Match": tags[lang]})
            self.assertEqual((code, body), (304, b""))
        self.assertNotEqual(tags["en"], tags["vi"])
        self.assertEqual(tags["fr"], tags["en"])  # any other language is English
        self.assertEqual(json.loads(self.get("/api/core")[2]), core_info(roles))
        self.assertEqual(self.get("/api/core?lang=vi", host="evil.example")[0], 403)

    def test_snapshot_still_carries_the_rules(self):
        data = snapshot_data(self.a.root)
        self.assertEqual(data["core"], core_info(DEFAULT_CONFIG["roles"]))
        self.assertEqual(data["core_vi"], core_info(DEFAULT_CONFIG["roles"], "vi"))
        self.assertIn('"core_vi"', render(data))


class People(ConsoleBase):
    @classmethod
    def prepare(cls):
        a = cls._a
        a.pass_through("frame", "clarify", "design", "slice")  # signed "loc" before the team was written
        a.ok("start", "T-1.1", "--by", "an")
        a.ok("submit", "T-1.1", "--by", "an", "--confirm")
        (a.root / ".egd" / "team.toml").write_text(
            '[[member]]\nname = "Linh"\ngithub = "linh-dev"\naliases = ["loc"]\nroles = ["pm", "lead", "dev"]\n\n'
            '[[member]]\nname = "An"\naliases = ["an-dev"]\nroles = ["dev"]\n')
        a.commit("team")

    def test_names_shown_as_team_toml_spells_them(self):
        repo = repo_of(console.Portfolio(ttl=0).get(), self.a)
        f = repo["features"][0]
        self.assertEqual({g["by"] for g in f["gates"] if g["passed"]}, {"Linh"})
        task = next(t for t in f["tasks"] if t["id"] == "T-1.1")
        self.assertEqual((task["owner"], task["submitted_by"]), ("An", "An"))  # "an" → its member
        self.assertEqual({e["by"] for e in f["trail"]}, {"Linh", "An"})
        review = next(i for i in repo["inbox"] if i["kind"] == "review")
        self.assertIn("submitted by An", review["detail"])
        summary = repo_of(console.Portfolio(ttl=0).tagged()[0], self.a)["features"][0]
        self.assertEqual({e["by"] for e in summary["trail"]}, {"Linh"})  # what was signed
        # what was signed is stored as it was signed
        stored = {json.loads(p.read_text())["by"] for p in (self.a.feature / "events").glob("*.json")}
        self.assertIn("loc", stored)
        self.assertNotIn("Linh", stored)

    def test_unknown_names_are_kept_as_signed(self):
        from egd.dashboard import namer
        from egd.team import Team
        who = namer(Team(self.a.root, {}))
        self.assertEqual([who(n) for n in ("LOC", " linh-dev ", "Claude (via loc)", "", None)],
                         ["Linh", "Linh", "Claude (via loc)", "", None])
        self.assertEqual(namer()("loc"), "loc")


if __name__ == "__main__":
    unittest.main()
