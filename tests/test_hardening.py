"""Regression tests for the review findings: redaction, serving, scope, robustness."""

import unittest

from helpers import Project, api_server, request, serve

from egd.dashboard import make_server, render
from egd.util import load_config


class Redaction(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_secrets_never_reach_transcripts_or_reasons(self):
        with api_server() as url:
            cfg = self.p.root / ".egd" / "config.toml"
            cfg.write_text(cfg.read_text() + f'\n[env.local]\nbase_url = "{url}/${{TENANT}}"\n')
            (self.p.root / ".egd" / "secrets.env").write_text(
                "TENANT=tenant-xyz\nPW=p@ss w0rd&1\n")
            (self.p.feature / "proof.toml").write_text('''
[[proof]]
id = "P-1"
kind = "http"
verifies = ["AC-1.1"]
[[proof.step]]
method = "POST"
path = "/login"
form = { password = "${PW}" }
expect = { status = 999 }

[[proof]]
id = "P-2"
kind = "cli"
verifies = ["AC-1.1"]
[[proof.step]]
run = "echo nothing"
expect = { contains = ["${PW}"] }
''')
            self.p.commit("proofs")
            self.p.egd("proof", "run", "--by", "loc")
            text = ""
            for t in (self.p.feature / "runs").rglob("transcript.md"):
                text += t.read_text()
            for e in (self.p.feature / "events").glob("*proof_run.json"):
                text += e.read_text()
            for leaked in ("tenant-xyz", "p@ss w0rd&1", "p%40ss+w0rd%261", "p%40ss%20w0rd%261"):
                self.assertNotIn(leaked, text)


class Serving(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        server = self.enterContext(serve(make_server(self.p.root, load_config(self.p.root), "127.0.0.1", 0, None)))
        self.port = server.server_address[1]

    def tearDown(self):
        self.p.close()

    def get(self, path, host=None):
        code, _, body = request(self.port, "GET", path, host=host)
        return code, body

    def test_foreign_host_header_refused_without_token(self):
        self.assertEqual(self.get("/api/portfolio", host="attacker.example")[0], 403)
        self.assertEqual(self.get("/api/portfolio", host=f"localhost:{self.port}")[0], 200)

    def test_only_inert_evidence_is_served(self):
        run = self.p.feature / "runs" / "r1"
        run.mkdir(parents=True)
        (run / "evil.svg").write_text("<svg onload=alert(1)>")
        (run / "page.html").write_text("<script>alert(1)</script>")
        (run / "shot.png").write_bytes(b"\x89PNG")
        slug = f"{self.p.root.name}/{self.p.feature.name}"
        self.assertEqual(self.get(f"/files/{slug}/runs/r1/evil.svg")[0], 404)
        self.assertEqual(self.get(f"/files/{slug}/runs/r1/page.html")[0], 404)
        self.assertEqual(self.get(f"/files/{slug}/runs/r1/shot.png")[0], 200)

    def test_payload_cannot_break_out_of_the_script(self):
        html = render({"features": [{"title": "<!--<script></script>"}]})
        start = html.index("const EMBEDDED = ")
        self.assertNotIn("<", html[start:html.index(";", start)])


class Scope(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def edit_ac(self, old, new):
        plan = self.p.feature / "plan.toml"
        plan.write_text(plan.read_text().replace(old, new))

    def test_cr_cannot_approve_edits_made_after_it_was_opened(self):
        self.p.pass_through("frame", "clarify", "design")
        self.edit_ac("300000 and", "330000 and")
        self.p.ok("cr", "open", "--by", "loc", "--title", "VAT", "--reason", "law")
        self.edit_ac("330000 and", "999999 and")
        self.assertIn("changed after CR-001", self.p.refused("cr", "approve", "CR-001", "--by", "client"))

    def test_dropping_planned_work_needs_a_cr(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        for t in ("T-1.1",):
            self.p.ok("start", t, "--by", "an")
            self.p.ok("solo", t, "--by", "an", "--confirm", "--reason", "x")
        plan = self.p.feature / "plan.toml"
        text = plan.read_text()
        plan.write_text(text[:text.index('[[task]]\nid = "T-1.2"')])
        self.assertIn("T-1.2: planned at `slice` but removed", self.p.refused("pass", "build", "--by", "loc"))

    def test_alias_cannot_accept_own_work(self):
        (self.p.root / ".egd" / "team.toml").write_text(
            '[[member]]\nname = "loc"\nroles = ["pm"]\n'
            '[[member]]\nname = "Alice"\ngithub = "alice-gh"\nroles = ["dev"]\n')
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "Alice")
        self.p.ok("submit", "T-1.1", "--by", "Alice", "--confirm")
        self.assertIn("someone else", self.p.refused("accept", "T-1.1", "--by", "alice-gh"))


class Freshness(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n'
            '[[proof.step]]\nrun = "true"\n')
        self.p.commit("proof")

    def tearDown(self):
        self.p.close()

    def test_editing_a_proof_makes_its_runs_stale(self):
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")
        self.assertNotIn("stale", self.p.ok("proof", "list"))
        proof = self.p.feature / "proof.toml"
        proof.write_text(proof.read_text().replace('run = "true"', 'run = "false"'))
        self.assertIn("stale", self.p.ok("proof", "list"))

    def test_unknown_commit_is_stale(self):
        from egd.proof import freshness
        self.assertFalse(freshness(self.p.root, {"sha": "deadbeef" * 5}))


class Robustness(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_nan_estimate_refused(self):
        self.assertIn("finite", self.p.egd("set", "T-1.1", "estimate_h=nan", "--by", "loc")[1])

    def test_entries_without_id_are_reported_not_crashing(self):
        plan = self.p.feature / "plan.toml"
        plan.write_text(plan.read_text() + '\n[[task]]\ntitle = "orphan"\n')
        self.assertIn("has no id", self.p.egd("graph")[1])
        self.p.ok("status")
        self.p.ok("board")

    def test_one_broken_feature_does_not_break_the_rest(self):
        self.p.ok("new", "other", "--by", "loc")
        broken = next(p for p in (self.p.root / ".egd" / "features").iterdir() if p.name.endswith("other"))
        (broken / "plan.toml").write_text("this is = = not toml")
        self.p.ok("list")
        self.assertIn("invalid TOML", self.p.egd("check", broken.name)[1])

    def test_non_object_event_is_corrupt(self):
        (self.p.feature / "events" / "000999-x.json").write_text("null")
        self.assertIn("unreadable event", self.p.egd("check")[1])

    def test_task_id_with_suffix_still_attributes(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")
        (self.p.root / "src" / "app.txt").write_text("v2\n")
        self.p.commit("T-1.1-fix2 adjust totals")
        out = self.p.ok("submit", "T-1.1", "--by", "an", "--confirm")
        self.assertNotIn("no commit mentions", out)


if __name__ == "__main__":
    unittest.main()
