"""Parallel features: a teammate's merged work in files a feature never touches leaves its evidence
standing — only its footprint and the shared files `watch` names make it stale — and
`egd proof run --stale-only` re-proves only what no longer counts. A proof's `inputs` narrows
that further, to the files it reads. Evidence survives a squash or a rebase by its content, and
the runs that are left go quickest first, several at once, stopping at the first failure if asked."""

import subprocess
import unittest

from helpers import Project

from egd.events import record
from egd.model import load_feature
from egd.proof import definition_hash

PROOFS = ('[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"\n'
          '[[proof]]\nid = "P-2"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"\n')


class FootprintFreshness(unittest.TestCase):
    def setUp(self):
        self.p = Project()  # every task touches src/**
        (self.p.feature / "proof.toml").write_text(PROOFS)
        self.p.commit("proof")

    def tearDown(self):
        self.p.close()

    def _proven(self):
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")
        self.assertNotIn("stale", self.p.ok("proof", "list"))

    def _commit(self, path, text="x\n"):
        f = self.p.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        self.p.commit(f"change {path}")

    def _config(self, extra):
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text() + "\n" + extra)
        self.p.commit("config")

    def test_a_commit_outside_the_footprint_leaves_evidence_fresh(self):
        self._proven()
        self._commit("billing/invoice.txt")
        self.assertNotIn("stale", self.p.ok("proof", "list"))

    def test_a_commit_inside_the_footprint_makes_it_stale(self):
        self._proven()
        self._commit("billing/invoice.txt")
        self._commit("src/app.txt", "v2\n")
        self.assertIn("stale", self.p.ok("proof", "list"))

    def test_a_watched_shared_file_makes_it_stale(self):
        text = (self.p.root / ".egd" / "config.toml").read_text()
        (self.p.root / ".egd" / "config.toml").write_text(
            text.replace("timeout = 600", 'timeout = 600\nwatch = ["shared/**"]', 1))
        self.p.commit("watch")
        self._proven()
        self._commit("docs/notes.md")
        self.assertNotIn("stale", self.p.ok("proof", "list"))
        self._commit("shared/money.txt")
        self.assertIn("stale", self.p.ok("proof", "list"))

    def test_a_profiles_watch_counts_once_the_profile_is_named(self):
        self._config('[stack]\nprofiles = ["node"]\n')
        self._proven()
        self._commit("package.json", '{"dependencies": {"left-pad": "1.3.0"}}\n')
        self.assertIn("stale", self.p.ok("proof", "list"))

    def test_watching_everything_is_the_strict_rule(self):
        text = (self.p.root / ".egd" / "config.toml").read_text()
        (self.p.root / ".egd" / "config.toml").write_text(
            text.replace("timeout = 600", 'timeout = 600\nwatch = ["**"]', 1))
        self.p.commit("strict")
        self._proven()
        self._commit("docs/notes.md")
        self.assertIn("stale", self.p.ok("proof", "list"))

    def test_without_a_footprint_any_commit_makes_it_stale(self):
        plan = self.p.feature / "plan.toml"
        plan.write_text(plan.read_text().replace('touches = ["src/**"]\ntests = ["tests/ok.sh"]\n'
                                                 'done_when = ["shows total"]', 'done_when = ["shows total"]'))
        self.p.commit("a task with no touches")
        self._proven()
        self._commit("docs/notes.md")
        self.assertIn("stale", self.p.ok("proof", "list"))

    def test_merging_a_parallel_feature_keeps_the_branchs_evidence(self):
        self.p.git("checkout", "-q", "-b", "feature-a")
        self._commit("src/app.txt", "v2\n")
        self._proven()
        self.p.git("checkout", "-q", "main")
        self._commit("billing/invoice.txt")  # feature B, merged to main meanwhile
        self.p.git("checkout", "-q", "feature-a")
        self.p.git("merge", "-q", "--no-edit", "main")
        self.assertNotIn("stale", self.p.ok("proof", "list"))


class StaleOnly(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        (self.p.feature / "proof.toml").write_text(PROOFS)
        self.p.commit("proof")

    def tearDown(self):
        self.p.close()

    def test_runs_only_what_no_longer_counts(self):
        first = self.p.ok("proof", "run", "--by", "loc", "--stale-only")  # never run: both
        self.assertIn("P-1@repo: pass", first)
        self.assertIn("P-2@repo: pass", first)
        self.p.commit("trail")
        proof = self.p.feature / "proof.toml"
        proof.write_text(PROOFS.replace('id = "P-2"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"',
                                        'id = "P-2"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "test 1"'))
        self.p.commit("P-2 asserts something else")
        again = self.p.ok("proof", "run", "--by", "loc", "--stale-only")
        self.assertIn("P-2@repo: pass", again)
        self.assertNotIn("P-1@repo", again)
        self.p.commit("trail")
        self.assertNotIn("stale", self.p.ok("proof", "list"))

    def test_nothing_to_run_is_not_a_failure(self):
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")
        self.assertIn("nothing to run", self.p.ok("proof", "run", "--by", "loc", "--stale-only"))

    def test_narrowed_by_only(self):
        self.p.ok("proof", "run", "--by", "loc", "--only", "P-1")
        self.p.commit("trail")
        self.assertIn("nothing to run", self.p.ok("proof", "run", "--by", "loc", "--stale-only", "--only", "P-1"))
        self.assertIn("P-2@repo: pass", self.p.ok("proof", "run", "--by", "loc", "--stale-only", "--only", "P-2"))


class ProofInputs(unittest.TestCase):
    """`inputs` on a proof: inside the feature, a change goes stale only for the proofs that read it."""

    def setUp(self):
        self.p = Project()  # footprint src/**

    def tearDown(self):
        self.p.close()

    def _proofs(self, inputs='inputs = ["src/exam/**"]\n'):
        (self.p.feature / "proof.toml").write_text(PROOFS.replace('kind = "cli"\n', 'kind = "cli"\n' + inputs, 1))
        self.p.commit("proofs")

    def _state(self):
        lines = self.p.ok("proof", "list").splitlines()
        return {pid: "stale" in next(l for l in lines if l.startswith(pid)) for pid in ("P-1", "P-2")}

    def _commit(self, path, text="x\n"):
        f = self.p.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        self.p.commit(f"change {path}")

    def test_a_change_outside_a_proofs_inputs_leaves_it_fresh(self):
        self._proofs()
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")
        self._commit("src/api.txt")  # the feature's, but not what P-1 reads
        self.assertEqual(self._state(), {"P-1": False, "P-2": True})
        self._commit("src/exam/exam.css")
        self.assertEqual(self._state(), {"P-1": True, "P-2": True})

    def test_a_watched_shared_file_still_counts(self):
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text() + '\n[stack]\nprofiles = ["node"]\n')
        self._proofs()
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")
        self._commit("pnpm-lock.yaml")
        self.assertEqual(self._state(), {"P-1": True, "P-2": True})

    def test_inputs_may_reach_beyond_the_footprint(self):
        self._proofs('inputs = ["src/exam/**", "packages/ui/**"]\n')
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")
        self._commit("packages/ui/button.tsx")
        self.assertEqual(self._state(), {"P-1": True, "P-2": False})

    def test_adding_inputs_to_a_proven_proof_re_proves_nothing(self):
        self._proofs("")
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")
        self._proofs()
        self.assertEqual(self._state(), {"P-1": False, "P-2": False})

    def test_an_empty_inputs_list_is_an_error(self):
        self._proofs("inputs = []\n")
        self.assertIn("P-1: inputs must be a non-empty list", self.p.egd("lint")[1])


def runs(p):
    """How many proof runs the trail holds."""
    return len(list((p.feature / "events").glob("*-proof_run.json")))


def cli_proof(pid, run, extra=""):
    return f'[[proof]]\nid = "{pid}"\nkind = "cli"\nverifies = ["AC-1.1"]\n{extra}[[proof.step]]\nrun = \'{run}\'\n'


class VerifyReusesEvidence(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        (self.p.feature / "proof.toml").write_text(PROOFS)
        self.p.commit("proof")
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")

    def tearDown(self):
        self.p.close()

    def test_run_leaves_passing_evidence_alone(self):
        before = runs(self.p)
        text = self.p.egd("verify", "--run", "--by", "loc")[1]
        self.assertIn("none re-run", text)
        self.assertEqual(runs(self.p), before)

    def test_run_re_runs_only_what_went_stale(self):
        proof = self.p.feature / "proof.toml"
        proof.write_text(PROOFS.replace('id = "P-2"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"',
                                        'id = "P-2"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "test 1"'))
        self.p.commit("P-2 changed")
        before = runs(self.p)
        text = self.p.egd("verify", "--run", "--by", "loc")[1]
        self.assertIn("1/1 run passed; the rest still pass on this code", text)
        self.assertEqual(runs(self.p), before + 1)

    def test_all_re_runs_everything(self):
        before = runs(self.p)
        self.p.egd("verify", "--run", "--all", "--by", "loc")
        self.assertEqual(runs(self.p), before + 2)


class SquashAndRebase(unittest.TestCase):
    """A run's commit gone from the clone: its content fingerprint keeps the evidence."""

    def setUp(self):
        self.p = Project()
        (self.p.feature / "proof.toml").write_text(PROOFS)
        self.p.commit("proof")

    def tearDown(self):
        self.p.close()

    def _forget_branch(self, name):
        self.p.git("branch", "-D", name)
        self.p.git("reflog", "expire", "--expire=now", "--all")
        self.p.git("gc", "-q", "--prune=now")

    def test_a_squash_merge_keeps_the_evidence(self):
        self.p.git("checkout", "-q", "-b", "feature-a")
        (self.p.root / "src" / "app.txt").write_text("v2\n")
        self.p.commit("T-1.1 v2")
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")
        sha = self.p.git("rev-parse", "HEAD~1").strip()
        self.p.git("checkout", "-q", "main")
        (self.p.root / "billing.txt").write_text("feature B\n")
        self.p.commit("feature B")
        self.p.git("merge", "-q", "--squash", "feature-a")
        self.p.commit("feature A, squashed")
        self._forget_branch("feature-a")
        gone = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=self.p.root,
                              capture_output=True).returncode
        self.assertNotEqual(gone, 0, "the run's commit must be gone for this test to mean anything")
        self.assertNotIn("stale", self.p.ok("proof", "list"))
        (self.p.root / "src" / "app.txt").write_text("v3\n")
        self.p.commit("code moved on")
        self.assertIn("stale", self.p.ok("proof", "list"))

    def test_a_run_without_a_fingerprint_on_a_lost_commit_stays_stale(self):
        f = load_feature(self.p.root, self.p.feature)
        for proof in f.proofs:  # as recorded before fingerprints, on a commit this clone never had
            record(f, "proof_run", "loc", proof=proof["id"], env="repo", status="pass", sha="0" * 40,
                   **{"def": definition_hash(proof, {}, {})})
        self.assertIn("stale", self.p.ok("proof", "list"))


class Scheduling(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def _proofs(self, body):
        (self.p.feature / "proof.toml").write_text(body)
        self.p.commit("proofs")

    def test_jobs_run_proofs_at_once_and_serial_ones_alone(self):
        import time
        self._proofs("".join(cli_proof(f"P-{n}", f"touch busy.{n}; sleep 1; rm busy.{n}") for n in (1, 2, 3))
                     + cli_proof("P-4", 'test -z "$(ls busy.* 2>/dev/null)"', "serial = true\n"))
        started = time.monotonic()
        text = self.p.ok("proof", "run", "--by", "loc", "--jobs", "3")
        self.assertLess(time.monotonic() - started, 2.5)
        for n in (1, 2, 3, 4):
            self.assertIn(f"P-{n}@repo: pass", text)
        self.assertLess(text.index("P-1@repo"), text.index("P-4@repo"))  # printed in plan order

    def test_jobs_from_config(self):
        from egd.proof import proof_jobs
        self.assertEqual(proof_jobs({"proof": {"jobs": 4}}), 4)
        self.assertEqual(proof_jobs({"proof": {"jobs": 4}}, 2), 2)
        self.assertEqual(proof_jobs({"proof": {"jobs": "x"}}), 1)
        self.assertEqual(proof_jobs({}), 1)

    def test_fail_fast_runs_the_quick_failure_first_and_stops(self):
        self._proofs(cli_proof("P-1", "sleep 1") + cli_proof("P-2", "false"))
        self.assertEqual(self.p.egd("proof", "run", "--by", "loc")[0], 1)  # both timed now
        self.p.commit("trail")
        code, text = self.p.egd("proof", "run", "--by", "loc", "--fail-fast")
        self.assertEqual(code, 1)
        self.assertIn("P-2@repo: fail", text)
        self.assertNotIn("P-1@repo: pass", text)
        self.assertIn("did not start: P-1@repo", text)

    def test_quickest_first_by_last_duration_else_by_kind(self):
        from egd.proof import expected_ms
        self.assertEqual(expected_ms({"kind": "ui"}, {"ms": 1200}), 1200)
        self.assertEqual(expected_ms({"kind": "ui"}, {"steps": [{"ms": 5}, {"ms": 7}]}), 12)
        self.assertLess(expected_ms({"kind": "cli"}, None), expected_ms({"kind": "ui"}, None))

    def test_changing_serial_re_proves_nothing(self):
        self._proofs(cli_proof("P-1", "true"))
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")
        self._proofs(cli_proof("P-1", "true", "serial = true\n"))
        self.assertNotIn("stale", self.p.ok("proof", "list"))


if __name__ == "__main__":
    unittest.main()
