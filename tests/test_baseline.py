"""Evidence has to bite: a test or cli proof that passed is run again with the feature's changes
taken out — its touches as they were when its first task started, its tests as they are now — and
must fail there. A proof that passes either way is toothless and closes `build` (rules v3); on the
`full` tier a proof never checked that way closes it too. The code always comes back."""

import json
import os
import unittest

from helpers import Project

from egd.model import load_feature
from egd.proof import baseline

BITES = '[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "grep -q v2 src/app.txt"\n'
TOOTHLESS = '[[proof]]\nid = "P-2"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"\n'


class Baseline(unittest.TestCase):
    def setUp(self, tier="standard", proofs=BITES + TOOTHLESS):
        self.p = Project(tier)
        (self.p.feature / "proof.toml").write_text(proofs)
        self.p.commit("plan")
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")
        self.p.commit("trail")
        self._write("src/app.txt", "v2\n")
        self.p.commit("T-1.1 total")

    def tearDown(self):
        self.p.close()

    def _write(self, path, text):
        f = self.p.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    def _app(self):
        return (self.p.root / "src" / "app.txt").read_text()

    def test_a_proof_that_needs_the_change_bites_and_one_that_does_not_is_toothless(self):
        out = self.p.egd("proof", "run", "--by", "an")[1]
        self.assertIn("P-1@repo: pass", out)
        self.assertIn("↳ bites: fail without this feature's changes", out)
        self.assertIn("↳ ⚠ toothless", out)
        self.assertEqual(self._app(), "v2\n")  # the code came back
        self.assertEqual(self.p.git("status", "--porcelain", "--", "src"), "")
        listed = self.p.ok("proof", "list")
        self.assertIn("pass · bites", listed)
        self.assertIn("pass · toothless", listed)
        check = self.p.refused("check", "build")
        self.assertIn("P-2@repo: passes without this feature's changes too", check)
        self.assertNotIn("P-1@repo", check)
        self.assertIn("Fix: make P-2 assert what the AC changes", check)
        pr = self.p.ok("pr")
        self.assertIn("P-1 `cli` on repo: pass · fails without the change", pr)
        self.assertIn("P-2 `cli` on repo: pass · ⚠ passes without the change too", pr)

    def test_the_trail_records_the_check_against_the_run_it_checked(self):
        self.p.ok("proof", "run", "--by", "an", "--only", "P-1")
        f = load_feature(self.p.root, self.p.feature)
        from egd.events import replay
        st = replay(f)
        run = st.latest_proofs()[("P-1", "repo")]
        check = st.baselines[("P-1", "repo", run["run"])]
        self.assertEqual((check["status"], check["outcome"], check["taken"]), ("bites", "fail", 1))
        self.assertEqual(check["base"], st.tasks["T-1.1"].started_sha)
        self.assertTrue((self.p.feature / check["artifacts"][0]).is_file())

    def test_tests_stay_as_they_are_now(self):
        # the proof's own test was rewritten by the task: taken back to `exit 0`, it would pass
        self._write("tests/check.sh", "grep -q v2 src/app.txt\n")
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-3"\nkind = "test"\nverifies = ["AC-1.1"]\ntests = ["tests/check.sh"]\n')
        self.p.commit("T-1.1 test")
        out = self.p.egd("proof", "run", "--by", "an")[1]
        self.assertIn("↳ bites", out)

    def test_added_and_removed_files_are_taken_out_and_put_back(self):
        self._write("src/new.txt", "fresh\n")
        script = self.p.root / "src" / "run.sh"
        script.write_text("echo hi\n")
        script.chmod(0o755)
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-4"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "test -e src/new.txt"\n')
        self.p.commit("T-1.1 new files")
        out = self.p.egd("proof", "run", "--by", "an")[1]
        self.assertIn("↳ bites", out)
        self.assertEqual((self.p.root / "src" / "new.txt").read_text(), "fresh\n")
        self.assertTrue(os.access(script, os.X_OK))
        self.assertEqual(self.p.git("status", "--porcelain", "--", "src"), "")

    def test_a_regression_proof_opts_out_and_the_gate_says_so(self):
        text = (self.p.feature / "proof.toml").read_text()
        (self.p.feature / "proof.toml").write_text(text.replace('id = "P-2"', 'id = "P-2"\nbaseline = false'))
        self.p.commit("P-2 is a regression proof")
        out = self.p.egd("proof", "run", "--by", "an")[1]
        self.assertNotIn("toothless", out)
        check = self.p.egd("check", "build")[1]
        self.assertNotIn("P-2@repo: passes without", check)
        self.assertIn("P-2@repo: baseline = false", check)

    def test_baseline_must_be_true_or_false(self):
        text = (self.p.feature / "proof.toml").read_text()
        (self.p.feature / "proof.toml").write_text(text.replace('id = "P-2"', 'id = "P-2"\nbaseline = "no"'))
        self.assertIn("P-2: baseline is true or false", self.p.egd("graph")[1])

    def test_uncommitted_changes_in_the_footprint_are_never_taken_out(self):
        self._write("src/app.txt", "v2\nmine\n")
        out = self.p.egd("proof", "run", "--by", "an")[1]
        self.assertIn("not checked without the feature's changes — uncommitted changes", out)
        self.assertEqual(self._app(), "v2\nmine\n")

    def test_a_file_edited_while_the_baseline_runs_stays_as_edited(self):
        f = load_feature(self.p.root, self.p.feature)
        head = self.p.git("rev-parse", "HEAD").strip()
        from egd.events import replay
        plan, _, _ = baseline.taken_out(f, baseline.base_sha(f, replay(f)), head)
        notes = baseline._before(self.p.root, plan, head, lambda: self._write("src/app.txt", "edited\n"))
        self.assertEqual(self._app(), "edited\n")
        self.assertIn("left as it is now: src/app.txt", notes[0])

    def test_a_stopped_run_is_put_back_by_the_next(self):
        f = load_feature(self.p.root, self.p.feature)
        head = self.p.git("rev-parse", "HEAD").strip()
        from egd.events import replay
        plan, _, _ = baseline.taken_out(f, baseline.base_sha(f, replay(f)), head)
        # killed between taking the code out and putting it back: what `_before` leaves behind
        now = baseline._tree(self.p.root, head)
        marker = baseline._marker(self.p.root)
        marker.write_text(json.dumps({"head": head, "back": {p: now.get(p) for p in plan},
                                      "taken": {p: e[1] if e else "" for p, e in plan.items()}}))
        for path, entry in plan.items():
            baseline._put(self.p.root, path, entry)
        self.assertEqual(self._app(), "v1\n")
        out =self.p.egd("proof", "run", "--by", "an", "--only", "P-1")[1]
        self.assertIn("put back 1 file a stopped baseline run had taken out", out)
        self.assertEqual(self._app(), "v2\n")
        self.assertFalse(marker.exists())

    def test_turned_off_for_the_repository(self):
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text().replace("timeout = 600", "timeout = 600\nbaseline = false", 1))
        self.p.commit("no baseline")
        out = self.p.egd("proof", "run", "--by", "an")[1]
        self.assertNotIn("↳", out)
        self.assertNotIn("toothless", self.p.egd("check", "build")[1])

    def test_an_older_plan_only_hears_a_warning(self):
        event = next((self.p.feature / "events").glob("*-feature_created.json"))
        event.write_text(json.dumps({**json.loads(event.read_text()), "rules": 2}))
        self.p.ok("proof", "run", "--by", "an")
        check = self.p.egd("check", "build")[1]
        self.assertIn("warning: P-2@repo: passes without this feature's changes too", check)


class FullTier(unittest.TestCase):
    def setUp(self):
        Baseline.setUp(self, "full", BITES)

    tearDown = Baseline.tearDown
    _write = Baseline._write

    def test_a_proof_never_checked_closes_build_and_stale_only_checks_it(self):
        self.p.ok("proof", "run", "--by", "an", "--no-baseline")
        self.assertIn("P-1@repo: not checked against the code before this feature",
                      self.p.egd("check", "build")[1])
        out = self.p.egd("proof", "run", "--by", "an", "--stale-only")[1]
        self.assertIn("↳ bites", out)
        self.assertNotIn("not checked against", self.p.egd("check", "build")[1])

    def test_verify_run_checks_it_too(self):
        self.p.ok("proof", "run", "--by", "an", "--no-baseline")
        out = self.p.egd("verify", "--run", "--by", "an")[1]
        self.assertIn("also failed without this feature's changes, as evidence should: P-1@repo", out)


if __name__ == "__main__":
    unittest.main()
