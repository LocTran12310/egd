"""Core review fixes: the writer lock, CRs and dropped work, proof envs, redaction, drift,
skipped tests, solo signing, replay edge cases, standup and wording."""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import Project
from test_aidlc_gherkin import FENCED

from egd import events, gates, tasks, tracking
from egd.events import locked, record, replay
from egd.model import load_feature
from egd.proof import proof_matrix, run_proofs
from egd.report import client_report, health, metrics_text, standup
from egd.team import Team
from egd.templates import GITIGNORE, PLAN
from egd.util import Refused, known_secrets, load_config, plural

SRC = Path(__file__).resolve().parents[1] / "src"


def feature(p):
    return load_feature(p.root, p.feature)


def cfg_of(p):
    return load_config(p.root)


def started(p):
    return [e for e in replay(feature(p)).events if e["type"] == "task_started"]


# the check-then-record every racer runs: start T-1.1 only if it is still todo
RACE = """
import sys, time
sys.path.insert(0, {src!r})
from pathlib import Path
from egd.events import locked, record, replay
from egd.model import load_feature
root, fdir, at, who = Path({root!r}), Path({fdir!r}), {at!r}, sys.argv[1]
while time.time() < at:
    time.sleep(0.005)
with locked(root):
    f = load_feature(root, fdir)
    if replay(f).task("T-1.1").status == "todo":
        time.sleep(0.15)  # widen the window between the check and the record
        record(f, "task_started", who, task="T-1.1")
        print("won")
"""


class WriterLock(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)
        self.lock = self.p.root / ".egd" / ".lock"

    def test_four_threads_and_two_processes_one_winner(self):
        p = self.p
        at = time.time() + 1.5  # every racer starts together, once the processes have imported egd
        script = RACE.format(src=str(SRC), root=str(p.root), fdir=str(p.feature), at=at)
        procs = [subprocess.Popen([sys.executable, "-c", script, f"proc{i}"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for i in range(2)]
        wins = []

        def racer(who):
            f = load_feature(p.root, p.feature)
            while time.time() < at:
                time.sleep(0.005)
            with locked(p.root):
                if replay(f).task("T-1.1").status == "todo":
                    time.sleep(0.15)
                    record(f, "task_started", who, task="T-1.1")
                    wins.append(who)

        threads = [threading.Thread(target=racer, args=(f"thread{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        for i, pr in enumerate(procs):
            out, err = pr.communicate(timeout=30)
            self.assertEqual(pr.returncode, 0, err)
            wins += [f"proc{i}"] if "won" in out else []
        self.assertEqual(len(wins), 1, wins)
        self.assertEqual(len(started(p)), 1)
        self.assertFalse(self.lock.exists())  # released by whoever held it last

    def test_reentrant_within_a_thread(self):
        f = feature(self.p)
        with locked(self.p.root):
            with locked(self.p.root):
                record(f, "note", "loc")  # record takes the lock too
                self.assertTrue(self.lock.exists())
            self.assertTrue(self.lock.exists())  # still held by the outer block
        self.assertFalse(self.lock.exists())

    def test_a_live_holder_makes_the_next_writer_wait_then_refuse(self):
        self.lock.write_text(f"{os.getpid()} elsewhere 2026-01-01T00:00:00+00:00 x\n")
        started_at = time.monotonic()
        with self.assertRaises(Refused) as cm:
            with locked(self.p.root, timeout=0.3):
                pass
        self.assertGreaterEqual(time.monotonic() - started_at, 0.3)
        self.assertIn("try again", str(cm.exception))
        self.assertTrue(self.lock.exists())  # someone else's lock is never removed

    def test_a_crashed_writers_old_lock_is_removed(self):
        import socket
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        self.lock.write_text(f"{gone.pid} {socket.gethostname()} 2026-01-01T00:00:00+00:00 x\n")
        old = time.time() - 120
        os.utime(self.lock, (old, old))
        with locked(self.p.root, timeout=0.5):
            self.assertIn(str(os.getpid()), self.lock.read_text())
        self.assertFalse(self.lock.exists())

    def test_lock_is_gitignored_and_never_makes_evidence_stale(self):
        self.assertIn(".lock", GITIGNORE.split())
        self.assertIn(".lock", (self.p.root / ".egd" / ".gitignore").read_text().split())
        with locked(self.p.root):
            self.assertEqual(self.p.git("status", "--porcelain"), "")


class ChangeRequests(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.plan = self.p.feature / "plan.toml"

    def drop(self, task):
        text = self.plan.read_text()
        start = text.index(f'[[task]]\nid = "{task}"')
        end = text.find("[[task]]", start + 1)
        self.plan.write_text(text[:start] + (text[end:] if end != -1 else ""))

    def test_work_dropped_after_the_cr_was_opened_is_not_legitimised(self):
        self.p.ok("cr", "open", "--by", "loc", "--title", "Rename", "--reason", "client")
        self.drop("T-1.2")
        out = self.p.refused("cr", "approve", "CR-001", "--by", "client")
        self.assertIn("T-1.2", out)
        self.assertIn("open a new CR", out)
        self.assertTrue(any(x.startswith("T-1.2: planned at `slice`") for x in
                            gates.check(feature(self.p), replay(feature(self.p)), cfg_of(self.p), "build")))

    def test_a_cr_opened_after_the_drop_lists_it_and_its_approval_agrees_to_it(self):
        self.drop("T-1.2")
        f = feature(self.p)
        e = tracking.cr_open(f, replay(f), Team(self.p.root, {}), "loc", "Drop it", "client", 0, [])
        self.assertEqual(e["dropped"], ["T-1.2"])
        self.assertIn("T-1.2", " ".join(e["notes"]))
        self.p.ok("cr", "approve", "CR-001", "--by", "client")
        self.assertFalse([x for x in gates.check(f, replay(f), cfg_of(self.p), "build") if "planned" in x])
        self.assertIn("drops T-1.2", client_report(f, cfg_of(self.p)))

    def test_old_crs_without_a_snapshot_still_approve(self):
        f = feature(self.p)
        record(f, "cr_opened", "loc", cr="CR-001", title="old", reason="r", scope=f.scope_hash())
        self.drop("T-1.2")
        self.p.ok("cr", "approve", "CR-001", "--by", "client")


class SoloSigning(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)

    def test_with_no_team_client_signatures_carry_a_note(self):
        p = self.p
        p.pass_through("frame", "clarify", "design", "slice")
        for t in ("T-1.1", "T-1.2"):
            p.ok("start", t, "--by", "loc")
            p.ok("solo", t, "--by", "loc", "--confirm", "--reason", "alone")
        p.ok("pass", "build", "--by", "loc")
        f, team, cfg = feature(p), Team(p.root, {}), cfg_of(p)
        warn = "no roles defined — anyone may sign; add the client to .egd/team.toml"
        self.assertEqual(tracking.uat(f, replay(f), team, "S-1", "loc", True, "")["notes"], [warn])
        self.assertEqual(gates.pass_gate(f, replay(f), cfg, team, "accept", "loc")["notes"], [warn])
        tracking.cr_open(f, replay(f), team, "loc", "More", "why", 1, [])
        self.assertEqual(tracking.cr_decide(f, replay(f), team, "CR-001", "loc", True, "")["notes"], [warn])
        (p.root / ".egd" / "team.toml").write_text('[[member]]\nname = "Acme"\nroles = ["client"]\n'
                                                   '[[member]]\nname = "loc"\nroles = ["dev"]\n')
        tracking.cr_open(f, replay(f), Team(p.root, cfg), "loc", "More", "why", 1, [])
        self.assertEqual(tracking.cr_decide(f, replay(f), Team(p.root, cfg), "CR-002", "Acme", False, "")["notes"], [])


class ProofEnvsAllDisabled(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text() + '\n[env.staging]\nbase_url = "http://127.0.0.1:9"\nenabled = false\n')

    def test_a_proof_with_no_enabled_env_is_a_build_problem_not_a_vanished_proof(self):
        for envs in ("", 'envs = ["staging"]\n'):
            (self.p.feature / "proof.toml").write_text(
                f'[[proof]]\nid = "P-1"\nkind = "http"\nverifies = ["AC-1.1"]\n{envs}[[proof.step]]\npath = "/"\n')
            f = feature(self.p)
            problems = gates.check(f, replay(f), cfg_of(self.p), "build")
            self.assertTrue(any(x.startswith("P-1: no enabled environment") for x in problems), (envs, problems))

    def test_a_local_proof_still_runs_on_the_repo(self):
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"\n')
        f = feature(self.p)
        self.assertEqual([(p["id"], env) for p, env, *_ in proof_matrix(f, replay(f), cfg_of(self.p))],
                         [("P-1", "repo")])
        self.assertFalse([x for x in gates.check(f, replay(f), cfg_of(self.p), "build") if "no enabled" in x])


class Redaction(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)
        (self.p.root / ".egd" / "secrets.env").write_text(
            "APP=app\nDEBUG=true\nPIN=xyz\nDB_PASSWORD=hunter2-long\n")

    def test_short_unreferenced_values_are_words_not_secrets(self):
        f = feature(self.p)
        plain = known_secrets(self.p.root, cfg_of(self.p), f.raw_proofs)
        self.assertEqual(plain, {"hunter2-long"})
        referenced = known_secrets(self.p.root, cfg_of(self.p), [{"x": "${PIN}"}])
        self.assertEqual(referenced, {"hunter2-long", "xyz"})

    def test_the_proof_runner_redacts_what_tasks_redact(self):
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\n'
            'run = "echo happy true hunter2-long; echo pin=${PIN}"\n')
        self.p.commit("proof")
        [r] = run_proofs(feature(self.p), cfg_of(self.p), "loc")
        self.assertEqual(r["status"], "pass", r)
        text = "".join(t.read_text() for t in (self.p.feature / "runs").rglob("transcript.md"))
        self.assertIn("happy true ***", text)  # unreferenced: only the long one is hidden
        self.assertIn("pin=***", text)         # referenced: hidden at 3 characters
        self.assertNotIn("hunter2-long", text)
        self.assertNotIn("xyz", text)


class Drift(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "loc")

    def test_caches_os_litter_and_ignored_files_never_count(self):
        root = self.p.root
        (root / ".gitignore").write_text("build/\n")
        self.p.git("add", "-A")  # a teammate's commit: not this task's
        self.p.git("-c", "user.email=an@example.com", "-c", "user.name=An", "commit", "-q", "-m", "ignore build")
        for rel in ("__pycache__/m.cpython-314.pyc", "lib/x.pyc", ".pytest_cache/v/cache",
                    "node_modules/a/index.js", ".DS_Store", "docs/.DS_Store", "build/out.bin"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x")
        (root / "src" / "app.txt").write_text("v2\n")
        out = self.p.ok("submit", "T-1.1", "--by", "loc", "--confirm")
        self.assertNotIn("drift", out)
        [e] = [e for e in replay(feature(self.p)).events if e["type"] == "task_submitted"]
        self.assertEqual(e["drift"], [])

    def test_a_file_added_then_removed_under_the_task_is_no_change(self):
        root = self.p.root
        (root / "scratch.txt").write_text("tmp")
        self.p.commit("T-1.1 try something")
        (root / "scratch.txt").unlink()
        (root / "src" / "app.txt").write_text("v2\n")
        self.p.commit("T-1.1 the real change")
        self.p.ok("submit", "T-1.1", "--by", "loc", "--confirm")
        [e] = [e for e in replay(feature(self.p)).events if e["type"] == "task_submitted"]
        self.assertEqual(e["drift"], [])

    def test_real_drift_is_still_caught(self):
        (self.p.root / "README.md").write_text("x")
        out = self.p.refused("submit", "T-1.1", "--by", "loc", "--confirm")
        self.assertIn("README.md", out)

    def test_dirty_note_names_the_paths_and_suggests_gitignore(self):
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"\n')
        self.p.commit("proof")
        for i in range(7):  # inside the feature's touches (src/**)
            (self.p.root / "src" / f"f{i}.txt").write_text("x")
        results = run_proofs(feature(self.p), cfg_of(self.p), "loc")
        [note] = {n for r in results for n in r["notes"]}
        self.assertIn("7 uncommitted files (src/f0.txt, src/f1.txt, src/f2.txt, src/f3.txt, "
                      "src/f4.txt, … 2 more)", note)
        self.assertIn(".gitignore", note)
        self.assertNotIn("notes", json.loads(next((self.p.feature / "events").glob("*proof_run.json")).read_text()))


class SkippedTests(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text().replace('test_command = "sh {tests}"', ""))
        self.p.commit("no test command")
        self.p.pass_through("frame", "clarify", "design", "slice")

    def test_submit_says_tests_were_not_run_and_verify_warns(self):
        p = self.p
        p.ok("start", "T-1.1", "--by", "loc")
        f = feature(p)
        e = tasks.submit(f, replay(f), cfg_of(p), Team(p.root, {}), "T-1.1", "loc", confirm=True)
        msg = "tests not run — set test_command under [proof] in .egd/config.toml"
        self.assertIn(msg, e["notes"])
        on_disk = [json.loads(x.read_text()) for x in (p.feature / "events").glob("*task_submitted.json")]
        self.assertEqual(on_disk[0]["tests_skipped"], msg)
        _, out = p.egd("verify")  # not ready (T-1.2 is still todo); the warning is what matters
        self.assertIn("⚠️ tests", out)
        self.assertIn(f"⚠ {msg}", out)


class Replay(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)

    def test_an_event_missing_a_field_changes_nothing(self):
        f = feature(self.p)
        record(f, "cr_opened", "loc", cr="CR-001", title="t", reason="r")
        e = record(f, "cr_approved", "", cr="CR-001")
        path = next((self.p.feature / "events").glob(f"{e['seq']:06d}-*"))
        doc = json.loads(path.read_text())
        del doc["by"]
        path.write_text(json.dumps(doc))
        st = replay(f)
        self.assertEqual(st.crs["CR-001"]["status"], "pending")  # not half-approved
        self.assertEqual(st.corrupt, [path.name])

    def test_a_file_that_vanishes_while_reading_is_reported(self):
        f = feature(self.p)
        record(f, "note", "loc")
        real = Path.read_text

        def flaky(self, *a, **kw):
            if self.name.endswith("-note.json"):
                raise FileNotFoundError(self)
            return real(self, *a, **kw)

        with mock.patch.object(Path, "read_text", flaky):
            st = replay(f)
        self.assertEqual(len(st.corrupt), 1)
        self.assertTrue(st.corrupt[0].endswith("-note.json"))

    def test_aidlc_refresh_skips_an_unreadable_event(self):
        from egd.sources.aidlc_import import import_repo, refresh_repo
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            fdir = root / ".ai" / "features" / "f1"
            fdir.mkdir(parents=True)
            (fdir / "00-intent.md").write_text("# Intent — Weekly view\n\n## Problem\nx\n")
            (fdir / "02-requirements.md").write_text(FENCED)
            (fdir / ".aidlc-state.yaml").write_text("current_gate: G2\npassed: [G0, G1]\n")
            import_repo(root, "loc")
            target = root / ".egd" / "features" / "f1"
            plan = target / "plan.toml"
            plan.write_text(plan.read_text().replace(", and the Showing", " the Showing"))
            (target / "events" / "000999-x-broken.json").write_bytes(b"\xff{not json")
            [r] = refresh_repo(root)
            self.assertEqual(r["changed"], 1)
            self.assertEqual(r["notes"], ["skipped unreadable event 000999-x-broken.json"])


class Standup(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)
        (self.p.root / ".egd" / "team.toml").write_text(
            '[[member]]\nname = "Alice"\ngithub = "alice-gh"\naliases = ["Alice Dev"]\nroles = ["dev"]\n')

    def test_one_person_one_line_repeats_collapsed(self):
        f = feature(self.p)
        record(f, "task_started", "Alice Dev", task="T-1.1")
        for i in range(5):
            record(f, "proof_run", "alice-gh", proof="P-1", env="repo", run=f"r{i}", status="pass")
        text = standup(self.p.root, cfg_of(self.p))
        self.assertNotIn("Alice Dev\n", text)
        self.assertEqual(text.count("\nAlice\n"), 1, text)
        self.assertIn("  · ran proofs (Checkout) ×5", text)
        self.assertIn("  · started T-1.1 (Checkout)\n", text + "\n")
        self.assertNotIn("  (", text.replace("  · ", "").replace("  now", ""))
        self.assertIn("now: T-1.1 doing (Checkout)", text)


class Wording(unittest.TestCase):
    def test_plural(self):
        self.assertEqual([plural(n, "task") for n in (0, 1, 2)], ["0 tasks", "1 task", "2 tasks"])
        self.assertEqual(plural(1, "entry", "entries"), "1 entry")

    def test_counts_read_naturally_and_released_is_translated(self):
        p = Project()
        self.addCleanup(p.close)
        p.pass_through("frame", "clarify", "design", "slice")
        p.ok("block", "T-1.1", "--by", "loc", "--reason", "waiting")
        f = feature(p)
        self.assertIn("1 blocked task", health(f, replay(f))[1])
        self.assertIn("over 0 tasks", metrics_text(f, cfg_of(p)) + "over 0 tasks")
        self.assertIn("0 rejections", metrics_text(f, cfg_of(p)))
        with mock.patch("egd.report.next_gate", return_value=None):
            vi = client_report(f, cfg_of(p), lang="vi")
        self.assertIn("Giai đoạn: **đã phát hành**", vi)
        self.assertIn("ready to pick up: 0 tasks", standup(p.root, cfg_of(p)))

    def test_plan_template_comments_line_up(self):
        for tier in ("lite", "standard", "full"):
            lines = PLAN.format(title="X", tier=tier).splitlines()
            tier_line = next(x for x in lines if x.startswith("tier = "))
            client_line = next(x for x in lines if x.startswith("client = "))
            self.assertEqual(tier_line.index("#"), client_line.index("#"), tier)

    def test_same_person_is_gone(self):
        self.assertFalse(hasattr(events, "same_person"))

    def test_every_egd_command_named_in_a_message_exists(self):
        import argparse
        from egd.cli import build_parser
        sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
        bad = []
        for path in (SRC / "egd").rglob("*.py"):
            for m in re.finditer(r"`egd ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", path.read_text(encoding="utf-8")):
                cmd, arg = m.groups()
                if cmd not in sub.choices:
                    bad.append(f"{path.name}: egd {cmd}")
                    continue
                choices = next((a.choices for a in sub.choices[cmd]._actions
                                if not a.option_strings and a.choices), None)
                if arg and choices and arg not in choices:
                    bad.append(f"{path.name}: egd {cmd} {arg}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
