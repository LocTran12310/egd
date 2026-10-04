"""Trail integrity, proof envs, dropped work, redaction, separation of duties, git fan-out."""

import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from helpers import Project

from egd import events, gates, util
from egd.events import record, replay
from egd.graph import validate
from egd.model import all_features, load_feature
from egd.proof import proof_envs
from egd.report import ac_proof_status, status_text
from egd.util import load_config


def feature(p):
    return load_feature(p.root, p.feature)


def build_problems(p):
    f = feature(p)
    return gates.check(f, replay(f), load_config(p.root), "build")


class IdCollisions(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_two_branches_opening_the_same_bug_keep_both(self):
        p = self.p
        p.git("checkout", "-q", "-b", "other")
        p.ok("bug", "open", "--by", "an", "--title", "Other bug", "--severity", "minor")
        p.ok("bug", "fix", "BUG-001", "--by", "an")
        p.commit("other")
        p.git("checkout", "-q", "main")
        p.ok("bug", "open", "--by", "an", "--title", "Main bug", "--severity", "critical")
        p.commit("main")
        p.git("merge", "-q", "--no-edit", "other")
        st = replay(feature(p))
        self.assertEqual(set(st.defects), {"BUG-001", "BUG-001b"})
        by_title = {d["title"]: d for d in st.defects.values()}
        # the fix written on `other` follows its own defect, whichever id it now has
        self.assertEqual(by_title["Other bug"]["status"], "fixed")
        self.assertEqual(by_title["Main bug"]["status"], "open")  # the critical one did not vanish
        self.assertIn("opened twice", " ".join(st.warnings))
        self.assertIn("opened twice", p.ok("status"))
        renamed = next(k for k, d in st.defects.items() if k == "BUG-001b")
        p.ok("bug", "close", renamed, "--by", "an")
        self.assertEqual(replay(feature(p)).defects[renamed]["status"], "closed")
        self.assertIn("BUG-002", p.ok("bug", "open", "--by", "an", "--title", "next"))

    def test_cr_follow_ups_find_a_renamed_cr_by_uid(self):
        f = feature(self.p)
        a = record(f, "cr_opened", "loc", cr="CR-001", title="A", reason="r", hours=1, affects=[])
        b = record(f, "cr_opened", "loc", cr="CR-001", title="B", reason="r", hours=1, affects=[])
        record(f, "cr_rejected", "client", cr="CR-001", uid=b["uid"], note="")
        st = replay(f)
        self.assertEqual(st.crs["CR-001"]["title"], "A")
        self.assertEqual(st.crs["CR-001"]["status"], "pending")
        self.assertEqual((st.crs["CR-001b"]["title"], st.crs["CR-001b"]["status"]), ("B", "rejected"))
        self.assertNotEqual(a["uid"], b["uid"])

    def test_old_trails_without_uid_still_resolve_by_id(self):
        f = feature(self.p)
        for title in ("first", "second"):
            record(f, "defect_opened", "an", defect="BUG-001", title=title, severity="major", uid=None)
        record(f, "defect_fixed", "an", defect="BUG-001")
        st = replay(f)
        self.assertEqual(st.defects["BUG-001"]["status"], "fixed")
        self.assertEqual(st.defects["BUG-001b"]["status"], "open")


class ProofEnvs(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text() + '\n[env.staging]\nbase_url = "http://127.0.0.1:9"\n')

    def tearDown(self):
        self.p.close()

    def proof(self, envs):
        (self.p.feature / "proof.toml").write_text(
            f'[[proof]]\nid = "P-1"\nkind = "http"\nverifies = ["AC-1.1"]\nenvs = {envs}\n'
            '[[proof.step]]\npath = "/"\n')
        return feature(self.p)

    def test_bad_envs_are_plan_errors(self):
        cfg = load_config(self.p.root)
        for envs, msg in (('"staging"', "non-empty list"), ("[]", "non-empty list"),
                          ('["stagnig"]', "env 'stagnig' is not configured")):
            errors, _ = validate(self.proof(envs), cfg)
            self.assertTrue(any(msg in e for e in errors), (envs, errors))
        errors, _ = validate(self.proof('["staging", "repo"]'), cfg)
        self.assertFalse([e for e in errors if "env" in e])

    def test_unknown_env_stays_required(self):
        cfg = load_config(self.p.root)
        self.assertEqual(proof_envs(self.proof('"staging"').proofs[0], cfg), [("staging", True)])
        self.assertEqual(proof_envs(self.proof('["stagnig"]').proofs[0], cfg), [("stagnig", True)])
        self.assertIn("P-1@stagnig: never run — `egd proof run`", build_problems(self.p))


class DroppedWork(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.plan = self.p.feature / "plan.toml"

    def tearDown(self):
        self.p.close()

    def drop(self, task):
        text = self.plan.read_text()
        start = text.index(f'[[task]]\nid = "{task}"')
        end = text.find("[[task]]", start + 1)
        self.plan.write_text(text[:start] + (text[end:] if end != -1 else ""))

    def test_an_approved_cr_moves_the_baseline_but_does_not_disable_the_check(self):
        self.drop("T-1.2")
        self.assertTrue(any(x.startswith("T-1.2: planned at `slice`") for x in build_problems(self.p)))
        self.p.ok("cr", "open", "--by", "loc", "--title", "Drop T-1.2", "--reason", "client")
        self.p.ok("cr", "approve", "CR-001", "--by", "client")
        self.assertFalse([x for x in build_problems(self.p) if "planned" in x])
        self.drop("T-1.1")
        self.assertIn("T-1.1: planned when CR-001 was approved but removed — dropping planned work "
                      "needs an approved CR", build_problems(self.p))

    def test_trail_order_not_clock_order(self):
        # an imported approval with a local offset sorts before `slice` as a string, after it by seq
        record(feature(self.p), "cr_approved", "client", cr="CR-009", at="2000-01-01T09:00:00+07:00",
               planned_tasks=["T-1.1"], planned_proofs=[])
        self.drop("T-1.2")
        self.assertFalse([x for x in build_problems(self.p) if "planned" in x])

    def test_legacy_approval_without_a_snapshot_still_waives(self):
        record(feature(self.p), "cr_approved", "client", cr="CR-001")
        self.drop("T-1.2")
        self.assertFalse([x for x in build_problems(self.p) if "planned" in x])


class TrailWrites(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.events = self.p.feature / "events"

    def tearDown(self):
        self.p.close()

    def test_valid_json_that_is_not_an_event_is_reported_not_fatal(self):
        bad = {"000090-x-aaaaaa-task_started.json": {"type": "task_started", "seq": 90, "at": "", "by": "x"},
               "000091-x-bbbbbb-proof_run.json": {"type": "proof_run", "seq": 91, "at": "", "by": "x"},
               "000092-x-cccccc-gate_passed.json": {"type": "gate_passed", "seq": "92"}}
        for name, doc in bad.items():
            (self.events / name).write_text(json.dumps(doc))
        st = replay(feature(self.p))
        self.assertEqual(sorted(st.corrupt), sorted(bad))
        self.assertIn("unreadable event files", self.p.ok("status"))

    def test_seq_comes_from_file_names(self):
        hand = {"type": "note", "seq": 41, "at": "", "by": "x"}
        (self.events / "hand-written.json").write_text(json.dumps(hand))
        self.assertEqual(record(feature(self.p), "note", "loc")["seq"], 42)
        (self.events / "000070-x-dddddd-note.json").write_text("{}")  # the name is enough
        self.assertEqual(record(feature(self.p), "note", "loc")["seq"], 71)

    def test_writes_are_atomic(self):
        f = feature(self.p)
        before = sorted(os.listdir(self.events))
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                record(f, "note", "loc")
        self.assertEqual(sorted(os.listdir(self.events)), before)  # nothing half-written, no temp left
        record(f, "note", "loc")
        self.assertFalse([n for n in os.listdir(self.events) if not n.endswith(".json")])


class Tasks(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.pass_through("frame", "clarify", "design", "slice")

    def tearDown(self):
        self.p.close()

    def test_owner_cannot_accept_work_someone_else_submitted(self):
        self.p.ok("start", "T-1.1", "--by", "an")
        self.p.ok("submit", "T-1.1", "--by", "loc", "--confirm")
        out = self.p.refused("accept", "T-1.1", "--by", "An")
        self.assertIn("someone else", out)
        self.assertIn("egd solo", out)
        self.p.ok("accept", "T-1.1", "--by", "bo")

    def test_test_output_on_the_trail_is_redacted(self):
        (self.p.root / ".egd" / "secrets.env").write_text("API_TOKEN=supersecret-123\n")
        self.p.ok("start", "T-1.1", "--by", "an")
        (self.p.root / "tests" / "ok.sh").write_text("echo token=supersecret-123 home=$HOME; exit 0\n")
        self.p.commit("T-1.1 tests")
        self.p.ok("solo", "T-1.1", "--by", "an", "--confirm", "--reason", "alone")
        trail = "".join(p.read_text() for p in self.events())
        self.assertNotIn("supersecret-123", trail)
        self.assertIn("token=***", trail)
        self.assertIn(f"home={os.environ['HOME']}", trail)  # the environment is not a secret

    def events(self):
        return sorted((self.p.feature / "events").glob("*.json"))

    def test_files_for_task_reads_one_log(self):
        since = self.p.git("rev-parse", "HEAD").strip()
        (self.p.root / "src" / "a b.txt").write_text("x")
        self.p.commit("T-1.1 add a file\n\nwith a body")
        (self.p.root / "src" / "other.txt").write_text("x")
        self.p.commit("unrelated")
        (self.p.root / "src" / "ten.txt").write_text("x")
        self.p.commit("T-1.10 is another task")
        with mock.patch("subprocess.run", wraps=subprocess.run) as run:
            files, found, _ = util.files_for_task(self.p.root, "T-1.1", since)
        self.assertEqual((files, found), ({"src/a b.txt"}, True))
        self.assertEqual(sum(1 for c in run.call_args_list if "log" in c.args[0]), 1)
        self.assertFalse([c for c in run.call_args_list if "show" in c.args[0]])


class Freshness(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        (self.p.feature / "proof.toml").write_text("".join(
            f'[[proof]]\nid = "P-{i}"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"\n'
            for i in range(5)))
        self.p.commit("proofs")
        self.p.ok("proof", "run", "--by", "loc")
        self.p.commit("trail")

    def tearDown(self):
        self.p.close()

    def test_one_view_asks_git_once_and_the_next_sees_code_move(self):
        f, cfg = feature(self.p), load_config(self.p.root)
        with mock.patch("subprocess.run", wraps=subprocess.run) as run:
            status_text(f, cfg)
        # head, the commit exists, commits since it, then the uncommitted tree (tracked + new)
        self.assertLessEqual(sum(1 for c in run.call_args_list if c.args[0][0] == "git"), 5)
        self.assertEqual(ac_proof_status(f, replay(f), cfg), {"AC-1.1": "pass"})
        (self.p.root / "src" / "app.txt").write_text("moved\n")  # same process, no commit
        self.assertEqual(ac_proof_status(f, replay(f), cfg), {"AC-1.1": "stale"})


class Startup(unittest.TestCase):
    def test_core_modules_do_not_import_subprocess_or_secrets(self):
        src = Path(__file__).resolve().parent.parent / "src"
        code = ("import sys; sys.path.insert(0, %r); import egd.gates, egd.tasks, egd.report, egd.verify, "
                "egd.templates; print(sorted(m for m in ('subprocess', 'secrets', 'urllib.parse') "
                "if m in sys.modules))" % str(src))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "[]")


class Templates(unittest.TestCase):
    def test_a_title_with_backslashes_and_quotes_keeps_plan_toml_valid(self):
        p = Project()
        try:
            p.ok("new", "second", "--title", 'C:\\new "draft"', "--by", "loc")
            f = next(f for f in all_features(p.root) if f.slug.endswith("second"))
            plan = tomllib.loads((f.path / "plan.toml").read_text())
            self.assertEqual(plan["feature"]["title"], "C:\\new 'draft'")
            self.assertIsNone(f.error)
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
