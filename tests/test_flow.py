import json
import unittest

from helpers import Project, api_server


class Flow(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_gates_open_in_order(self):
        text = self.p.refused("pass", "clarify", "--by", "loc")
        self.assertIn("frame", text)
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.assertIn("next: build", self.p.ok("status"))

    def test_frame_needs_reviewed_map(self):
        m = self.p.root / ".egd" / "map.md"
        m.write_text(m.read_text().replace("reviewed_by: loc", "reviewed_by:"))
        self.assertIn("reviewed_by", self.p.refused("pass", "frame", "--by", "loc"))

    def test_blocking_assumption_closes_clarify(self):
        plan = self.p.feature / "plan.toml"
        plan.write_text(plan.read_text().replace('status = "confirmed"', 'status = "open"'))
        self.p.pass_through("frame")
        self.assertIn("blocking and still open", self.p.refused("pass", "clarify", "--by", "loc"))

    def test_task_lifecycle_and_separation_of_duties(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.assertIn("waits on T-1.1", self.p.refused("start", "T-1.2", "--by", "an"))
        self.p.ok("start", "T-1.1", "--by", "an")
        (self.p.root / "src" / "app.txt").write_text("v2\n")
        self.p.commit("T-1.1 orders endpoint")
        self.assertIn("--confirm", self.p.refused("submit", "T-1.1", "--by", "an"))
        self.p.ok("submit", "T-1.1", "--by", "an", "--confirm", "--spent", "2.5")
        self.assertIn("someone else", self.p.refused("accept", "T-1.1", "--by", "An "))
        self.p.ok("accept", "T-1.1", "--by", "loc")
        self.p.ok("start", "T-1.2", "--by", "an")
        self.p.ok("solo", "T-1.2", "--by", "an", "--confirm", "--reason", "solo week")
        self.p.pass_through("build")
        m = self.p.ok("metrics", "--json")
        data = json.loads(m)
        self.assertEqual(data["solo_bypasses"], 1)
        self.assertEqual(data["estimate_ratio"], round(2.5 / 3, 2))

    def test_scope_drift_refused_then_overridden(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")
        (self.p.root / "docs.md").write_text("x")
        self.p.commit("T-1.1 also docs")
        text = self.p.refused("submit", "T-1.1", "--by", "an", "--confirm")
        self.assertIn("docs.md", text)
        self.p.refused("submit", "T-1.1", "--by", "an", "--confirm", "--allow-drift")
        self.p.ok("submit", "T-1.1", "--by", "an", "--confirm", "--allow-drift", "--reason", "readme")

    def test_console_answers_drift_and_finishes_solo(self):
        from egd.console import perform
        from egd.util import Refused
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")
        (self.p.root / "notes.local").write_text("x")
        body = {"feature": "checkout", "action": "submit", "target": "T-1.1", "confirm": True}
        with self.assertRaises(Refused) as cm:
            perform(self.p.root, "an", body)
        self.assertEqual((cm.exception.code, cm.exception.files), ("drift", ["notes.local"]))
        perform(self.p.root, "an", {**body, "drift_reason": "local scratch file"})
        # the submitter cannot accept; the console finishes it solo, with a reason
        self.assertIn("done", perform(self.p.root, "an", {**body, "action": "solo", "note": "alone this week",
                                                          "drift_reason": "local scratch file"}))
        self.assertIn("done", self.p.ok("status"))

    def test_teammate_commits_are_not_drift(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")
        (self.p.root / "other.md").write_text("x")
        self.p.commit("T-9.9 someone else's work")
        (self.p.root / "src" / "app.txt").write_text("v2\n")
        self.p.commit("T-1.1 orders")
        self.p.ok("submit", "T-1.1", "--by", "an", "--confirm")

    def test_failing_tests_refuse_submit(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")
        (self.p.root / "tests" / "ok.sh").write_text("echo boom; exit 3\n")
        self.p.commit("T-1.1 break")
        self.assertIn("tests failed", self.p.refused("submit", "T-1.1", "--by", "an", "--confirm"))

    def test_scope_change_needs_approved_cr(self):
        self.p.pass_through("frame", "clarify", "design")
        plan = self.p.feature / "plan.toml"
        plan.write_text(plan.read_text().replace("300000 and", "330000 incl. VAT and"))
        self.assertIn("egd cr new", self.p.refused("pass", "slice", "--by", "loc").replace("cr open", "cr new"))
        out = self.p.ok("cr", "open", "--by", "loc", "--title", "Add VAT", "--reason", "law", "--hours", "2")
        self.assertIn("CR-001", out)
        self.p.refused("pass", "slice", "--by", "loc")
        self.p.ok("cr", "approve", "CR-001", "--by", "client")
        self.p.ok("pass", "slice", "--by", "loc")

    def test_team_roles(self):
        (self.p.root / ".egd" / "team.toml").write_text(
            '[[member]]\nname = "loc"\nroles = ["pm","dev"]\n'
            '[[member]]\nname = "an"\nroles = ["dev"]\n'
            '[[member]]\nname = "acme"\nroles = ["client"]\n')
        self.assertIn("lacks a role", self.p.refused("pass", "frame", "--by", "an"))
        self.assertIn("not in", self.p.refused("pass", "frame", "--by", "mallory"))
        self.p.pass_through("frame")

    def test_uat_and_defects_gate_accept_and_release(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        for t in ("T-1.1", "T-1.2"):
            self.p.ok("start", t, "--by", "an")
            self.p.ok("solo", t, "--by", "an", "--confirm", "--reason", "x")
        self.p.pass_through("build")
        self.p.ok("bug", "open", "--by", "acme", "--title", "Total wrong", "--severity", "major",
                  "--found-in", "uat", "--ac", "AC-1.1")
        self.p.ok("uat", "S-1", "--by", "acme", "--pass")
        self.assertIn("BUG-001", self.p.refused("pass", "accept", "--by", "acme"))
        self.p.ok("bug", "fix", "BUG-001", "--by", "an")
        self.p.ok("pass", "accept", "--by", "acme")
        self.p.ok("pass", "release", "--by", "loc")
        self.assertIn("released", self.p.ok("status"))
        self.assertIn("100%", self.p.ok("metrics").replace("leakage    : 100%", "100%"))

    def test_block_restores_previous_state(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")
        self.p.ok("block", "T-1.1", "--by", "an", "--reason", "waiting on API keys")
        self.assertIn("doing", self.p.ok("unblock", "T-1.1", "--by", "an"))

    def test_reports_render(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")
        board = self.p.ok("board", "--html", "--print")
        self.assertIn("Who is on what", board)
        self.assertIn("**an**", board)
        html = (self.p.root / ".egd" / "board.html").read_text()
        self.assertIn('"T-1.1"', html)
        self.assertNotIn("/*EGD_DATA*/null", html)
        self.assertIn("Status report".lower(), self.p.ok("report", "--print").lower())
        self.assertIn("AC-1.1", self.p.ok("pr"))
        self.assertIn("Rollback", self.p.ok("release-note", "--print"))
        self.p.ok("lint")
        self.assertIn("task_started", self.p.ok("trail"))


class Proofs(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def _write(self, base_url):
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text() + f'\n[env.local]\nbase_url = "{base_url}"\n'
                       '\n[env.staging]\nbase_url = "${STAGING_URL}"\nrequired = false\n')
        (self.p.root / ".egd" / "secrets.env").write_text("QA_PASSWORD=s3cret-pass\n")
        (self.p.feature / "proof.toml").write_text('''
[[proof]]
id = "P-1"
kind = "http"
title = "pending order"
verifies = ["AC-1.1"]

[[proof.step]]
name = "login"
method = "POST"
path = "/login"
json = { email = "qa@x", password = "${QA_PASSWORD}" }
capture = { token = "$.accessToken" }

[[proof.step]]
name = "create"
method = "POST"
path = "/orders"
headers = { Authorization = "Bearer {token}" }
json = { items = [{ sku = "A1", qty = 2 }] }
expect.status = 201
expect.json = { "$.total" = 300000, "$.status" = "pending", "$.id" = { exists = true } }

[[proof]]
id = "P-2"
kind = "cli"
verifies = ["AC-1.1"]
[[proof.step]]
run = "echo migrated ok"
expect = { contains = ["migrated"] }
''')
        self.p.commit("proofs")

    def test_http_and_cli_proofs_gate_build(self):
        with api_server() as url:
            self._write(url)
            self.p.pass_through("frame", "clarify", "design", "slice")
            for t in ("T-1.1", "T-1.2"):
                self.p.ok("start", t, "--by", "an")
                self.p.ok("solo", t, "--by", "an", "--confirm", "--reason", "x")
            self.p.commit("trail")
            self.assertIn("never run", self.p.refused("pass", "build", "--by", "loc"))
            out = self.p.ok("proof", "run", "--by", "loc")
            self.assertIn("P-1@local: pass", out)
            self.assertIn("P-2@repo: pass", out)
            self.assertIn("staging", out)  # optional env reports unavailable without failing
            run_dir = next((self.p.feature / "runs").iterdir())
            transcript = (run_dir / "P-1@local" / "transcript.md").read_text()
            self.assertNotIn("s3cret-pass", transcript)
            self.assertNotIn("tok-abc-123", transcript)
            self.assertIn("300000", transcript)
            evidence = (self.p.feature / "reports" / "evidence.md").read_text()
            self.assertIn("✅ pass", evidence)
            self.p.commit("evidence")
            self.p.ok("pass", "build", "--by", "loc")

    def test_wrong_value_fails_and_source_change_makes_stale(self):
        with api_server() as url:
            self._write(url)
            proof = self.p.feature / "proof.toml"
            proof.write_text(proof.read_text().replace('"$.total" = 300000', '"$.total" = 999'))
            self.p.commit("wrong")
            out = self.p.egd("proof", "run", "--by", "loc", "--only", "P-1")[1]
            self.assertIn("$.total", out)
            proof.write_text(proof.read_text().replace('"$.total" = 999', '"$.total" = 300000'))
            self.p.commit("right")
            self.p.ok("proof", "run", "--by", "loc")
            self.p.commit("trail")
            (self.p.root / "src" / "app.txt").write_text("changed\n")
            self.p.commit("code moved on")
            self.assertIn("stale", self.p.ok("proof", "list"))

    def test_readonly_env_refuses_writes(self):
        with api_server() as url:
            self._write(url)
            cfg = self.p.root / ".egd" / "config.toml"
            cfg.write_text(cfg.read_text().replace(f'base_url = "{url}"\n', f'base_url = "{url}"\nreadonly = true\n', 1))
            out = self.p.egd("proof", "run", "--by", "loc", "--only", "P-1", "--env", "local")[1]
            self.assertIn("read-only", out)


if __name__ == "__main__":
    unittest.main()
