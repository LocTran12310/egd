"""The last full review: evidence that cannot be gamed by a typo or a reverted local edit, secrets
that stay out of the trail, and the CLI saying what it means."""

import json
import unittest
from unittest import mock

from helpers import Project

from egd import tasks
from egd.events import record, replay
from egd.model import load_feature
from egd.proof.http import sensitive_key
from egd.util import matches_any, md_cell


def feature(p):
    return load_feature(p.root, p.feature)


def proof(p, body):
    (p.feature / "proof.toml").write_text(body)
    p.commit("proof")


CLI = '[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nname = "{name}"\nrun = "{run}"\n'


class Globs(unittest.TestCase):
    def test_double_star_may_be_no_folder_at_all(self):
        self.assertTrue(matches_any("src/a.py", ["src/**/*.py"]))
        self.assertTrue(matches_any("src/x/y/a.py", ["src/**/*.py"]))
        self.assertTrue(matches_any("a.py", ["**/*.py"]))
        self.assertFalse(matches_any("lib/a.py", ["src/**/*.py"]))

    def test_table_cells_and_secret_keys(self):
        self.assertEqual(md_cell("total 300 | status\npending"), "total 300 \\| status pending")
        for key in ("password", "access_token", "apiKey", "x-api-key", "session_id", "client_secret"):
            self.assertTrue(sensitive_key(key), key)
        for key in ("author", "footprint", "passenger", "tokens_used", "sessionCount", "spin", "total"):
            self.assertFalse(sensitive_key(key), key)


class HonestEvidence(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_a_typo_in_an_assertion_is_an_error_not_a_pass(self):
        proof(self.p, CLI.format(name="x", run="true") + 'expect = { exti = 0 }\n')
        self.assertIn("unknown key 'exti' in step 1's expect — did you mean 'exit'?", self.p.egd("lint")[1])
        self.assertIn("P-1@repo: error", self.p.egd("proof", "run", "--by", "loc")[1])

    def test_a_reverted_local_edit_makes_the_run_stale(self):
        proof(self.p, CLI.format(name="flag", run="grep -q fixed flags.cfg"))
        (self.p.root / "flags.cfg").write_text("fixed\n")  # outside touches (src/**), never committed
        self.assertIn("P-1@repo: pass", self.p.ok("proof", "run", "--by", "loc"))
        self.assertNotIn("stale", self.p.ok("proof", "list"))
        (self.p.root / "flags.cfg").write_text("broken\n")
        self.assertIn("stale", self.p.ok("proof", "list"))

    def test_step_names_never_carry_a_secret_onto_the_trail(self):
        (self.p.root / ".egd" / "secrets.env").write_text("API_TOKEN=supersecret123\n")
        proof(self.p, CLI.format(name="call with ${API_TOKEN}", run="true"))
        self.p.ok("proof", "run", "--by", "loc")
        trail = "".join(f.read_text() for f in (self.p.feature / "events").glob("*proof_run.json"))
        self.assertNotIn("supersecret123", trail)
        self.assertNotIn("supersecret123", self.p.ok("pr"))

    def test_output_that_is_not_utf8_is_still_evidence(self):
        proof(self.p, CLI.format(name="bytes", run="printf 'ok \\\\377'"))
        self.assertIn("P-1@repo: pass", self.p.ok("proof", "run", "--by", "loc"))

    def test_health_counts_the_proofs_the_plan_still_has(self):
        proof(self.p, CLI.format(name="x", run="false"))
        self.p.egd("proof", "run", "--by", "loc")
        self.assertIn("1 failing proof", self.p.ok("status"))
        proof(self.p, "")
        self.assertNotIn("failing proof", self.p.ok("status"))

    def test_a_cli_proofs_own_screenshots_are_its_evidence(self):
        proof(self.p, CLI.format(name="shoot", run="printf x > $EGD_OUT/01-page.png"))
        self.p.ok("proof", "run", "--by", "loc")
        [run] = [json.loads(e.read_text()) for e in (self.p.feature / "events").glob("*proof_run.json")]
        self.assertTrue(any(a.endswith("01-page.png") for a in run["artifacts"]))
        transcript = next(self.p.feature.glob("runs/*/P-1@repo/transcript.md")).read_text()
        self.assertIn("![01-page](01-page.png)", transcript)
        from egd.dashboard import feature_data
        from egd.util import load_config
        [shown] = feature_data(feature(self.p), load_config(self.p.root))["proofs"]
        self.assertTrue(shown["runs"][0]["shots"][0].endswith("01-page.png"))

    def test_acs_without_a_proof_are_named(self):
        self.assertIn("no proof yet: AC-1.1", self.p.ok("proof", "list"))
        self.assertIn("Not verified: AC-1.1 — no proof declared", self.p.ok("pr"))


class Rollback(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        (self.p.feature / "release.md").write_text("# R\n## Rollback\n<!-- how to undo -->\n\n## Checks after deploy\nlogs\n")
        self.p.commit("release template")

    def tearDown(self):
        self.p.close()

    def test_asked_for_once_the_work_is_done_and_written_from_the_console(self):
        from egd import console
        self.assertNotIn("rollback: not written", self.p.ok("status"))
        self.p.pass_through("frame", "clarify", "design", "slice")
        for t in ("T-1.1", "T-1.2"):
            self.p.ok("start", t, "--by", "an")
            self.p.ok("solo", t, "--by", "an", "--confirm", "--reason", "solo week")
        self.assertIn("rollback: not written yet", self.p.ok("status"))
        self.assertIn("Not written yet: how to undo this", self.p.ok("pr"))
        snap = console.repo_snapshot("r", self.p.root)
        self.assertTrue(any(i["kind"] == "rollback" for i in snap["inbox"]))
        console.perform(self.p.root, "loc", {"action": "rollback", "feature": "checkout",
                                             "rollback": "Revert the merge commit; no migration."})
        text = (self.p.feature / "release.md").read_text()
        self.assertIn("## Rollback\nRevert the merge commit; no migration.\n\n## Checks after deploy\nlogs", text)
        self.assertNotIn("how to undo", text)
        self.assertNotIn("rollback: not written", self.p.ok("status"))
        self.assertIn("Revert the merge commit", self.p.ok("pr"))


class Close(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")

    def tearDown(self):
        self.p.close()

    def test_a_dropped_feature_stops_without_passing_anything_and_can_come_back(self):
        self.assertIn("needs --reason", self.p.refused("close", "--by", "loc"))
        text = self.p.ok("close", "--by", "loc", "--reason", "client paused the export work")
        self.assertIn("closed as dropped", text)
        self.assertIn("left undone: T-1.1, T-1.2", text)
        self.assertIn("closed as dropped by loc", self.p.ok("status"))
        self.assertIn("closed", self.p.ok("list"))
        self.assertIn("is closed", self.p.refused("pass", "build", "--by", "loc"))
        self.assertIn("is closed", self.p.refused("start", "T-1.2", "--by", "an"))
        from egd import console
        self.assertEqual([i for i in console.repo_snapshot("r", self.p.root)["inbox"] if i.get("slug")], [])
        from egd.dashboard import SIGNATURES
        self.assertTrue({"feature_closed", "feature_reopened"} <= SIGNATURES)  # the approval trail shows them
        self.assertIn("nothing ready", self.p.ok("ready"))
        board = self.p.ok("board", "--print")
        self.assertNotIn("T-1.2", board)
        self.assertNotIn("T-1.1", board)
        standup = self.p.ok("standup")
        self.assertIn("closed as dropped", standup)
        self.assertNotIn("T-1.1 doing", standup)
        self.p.ok("close", "--undo", "--by", "loc", "--reason", "client is back")
        self.assertIn("T-1.2", self.p.ok("board", "--print"))
        self.assertIn("next: build", self.p.ok("status"))

    def test_a_spike_closes_as_research_with_what_it_found(self):
        self.p.ok("close", "--as", "research", "--by", "loc", "--reason", "Postgres COPY is 9x faster; see docs/spike.md")
        self.assertIn("closed as research", self.p.ok("status"))
        self.assertIn("already closed (research)", self.p.refused("close", "--by", "loc", "--reason", "again"))


class AgentsByTask(unittest.TestCase):
    def test_each_task_names_its_agent_and_model_and_the_plan_can_choose(self):
        from egd.graph import assignment
        self.assertEqual(assignment({"id": "T-1", "title": "Rename a helper", "estimate_h": 1, "touches": ["src/**"]}),
                         ("builder", "haiku", "small"))
        self.assertEqual(assignment({"id": "T-2", "title": "Refresh session tokens", "estimate_h": 3,
                                     "touches": ["src/**"]})[:2], ("builder", "opus"))
        self.assertEqual(assignment({"id": "T-3", "title": "Cover totals", "estimate_h": 2,
                                     "touches": ["tests/**", "src/a.test.ts"]})[:2], ("tester", "sonnet"))
        self.assertEqual(assignment({"id": "T-4", "title": "x", "estimate_h": 2, "touches": ["src/**"],
                                     "agent": "person", "model": "fable"}), ("person", "fable", "set in the plan"))
        p = Project()
        self.addCleanup(p.close)
        p.ok("set", "T-1.2", "model=opus", "--by", "loc")
        self.assertIn("T-1.2 (builder·opus)", p.ok("graph"))
        self.assertEqual(p.egd("set", "T-1.2", "model=gpt", "--by", "loc")[0], 2)
        p.pass_through("frame", "clarify", "design", "slice")
        self.assertIn("→ builder · sonnet (usual)", p.ok("ready"))
        p.ok("start", "T-1.1", "--by", "claude", "--model", "sonnet")
        [e] = [json.loads(x.read_text()) for x in (p.feature / "events").glob("*task_started.json")]
        self.assertEqual(e["model"], "sonnet")


class Commands(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_the_feature_word_is_exact_and_follows_root(self):
        with self.assertRaises(SystemExit):
            self.p.egd("eckou")  # part of `checkout`: a typo, not a feature
        code, text = self.p.egd("--root", str(self.p.root), "checkout")
        self.assertEqual(code, 0, text)
        self.assertIn("tier standard", text)

    def test_a_committed_secret_is_named_not_mistaken_for_a_local_trail(self):
        (self.p.root / ".egd" / "secrets.env").write_text("X=1\n")
        self.p.git("add", "-f", ".egd/secrets.env")
        self.p.git("commit", "-q", "-m", "oops")
        text = self.p.ok()
        self.assertIn("git rm --cached .egd/secrets.env", text)
        self.assertNotIn("meant to stay on this machine", text)

    def test_a_string_touches_is_one_glob(self):
        plan = self.p.feature / "plan.toml"
        plan.write_text(plan.read_text().replace('touches = ["src/**"]', 'touches = "src/**"', 1))
        self.assertEqual(feature(self.p).task("T-1.1")["touches"], ["src/**"])

    def test_your_own_commit_without_the_task_id_is_mentioned(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "loc")
        (self.p.root / "src" / "app.txt").write_text("v2\n")
        self.p.git("add", "src")
        self.p.git("commit", "-q", "-m", "T-1.1 total")
        (self.p.root / "hack.py").write_text("x\n")
        self.p.git("add", "hack.py")
        self.p.git("commit", "-q", "-m", "wip")
        text = self.p.ok("submit", "T-1.1", "--by", "loc", "--confirm")
        self.assertIn("commits that do not name T-1.1 changed files outside its touches: hack.py", text)

    def test_a_block_that_lands_while_tests_run_is_not_overwritten(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "loc")
        real = tasks._verify

        def slow_tests(f, cfg, task):  # meanwhile, someone blocks the task
            record(f, "task_blocked", "an", task="T-1.1", reason="waiting on API keys")
            return real(f, cfg, task)
        with mock.patch.object(tasks, "_verify", side_effect=slow_tests):
            text = self.p.refused("submit", "T-1.1", "--by", "loc", "--confirm")
        self.assertIn("changed while its tests ran", text)
        self.assertEqual(replay(feature(self.p)).task("T-1.1").status, "blocked")


if __name__ == "__main__":
    unittest.main()
