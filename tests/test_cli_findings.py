"""CLI findings: plan editing (add, rm, add proof), proof filters, lint before slice, bare `egd`,
fix hints, output polish — each answered with a message and the next command."""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from helpers import Project

from egd import planfile
from egd.cli import main

ROOT = Path(__file__).resolve().parent.parent
_outside = mock.patch.dict(os.environ)


def setUpModule():
    # these tests play a person at a terminal; inside Claude Code a default signer is refused
    _outside.start()
    os.environ.pop("CLAUDECODE", None)


def tearDownModule():
    _outside.stop()


class _Tty(io.StringIO):
    def isatty(self):
        return True


def egd(p, *argv, tty=False):
    """p.egd, with argparse's exits as return codes, optionally on a terminal."""
    buf, cwd = (_Tty() if tty else io.StringIO()), os.getcwd()
    os.chdir(p.root)
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = main(list(argv))
    except SystemExit as exc:
        code = exc.code
    finally:
        os.chdir(cwd)
    return code, buf.getvalue()


def plan(p):
    return tomllib.loads((p.feature / "plan.toml").read_text())


def proofs(p):
    return tomllib.loads((p.feature / "proof.toml").read_text()).get("proof", [])


class AddEntries(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_ac_kind_is_its_own_field(self):
        self.p.ok("add", "ac", "--kind", "nfr", "--given", "g", "--when", "w", "--then", "t", "--by", "loc")
        self.p.ok("add", "ac", "--kind", "functional", "--given", "g", "--when", "w", "--then", "t", "--by", "loc")
        acs = {a["id"]: a for a in plan(self.p)["ac"]}
        self.assertEqual((acs["AC-1.2"]["kind"], acs["AC-1.3"]["kind"]), ("nfr", "functional"))
        self.assertNotIn("kind", acs["AC-1.1"])

    def test_first_ac_needs_no_group_and_later_ones_follow_the_last(self):
        (self.p.feature / "plan.toml").write_text('[feature]\ntitle = "x"\n')
        self.assertIn("added AC-1.1", self.p.ok("add", "ac", "--given", "g", "--when", "w", "--then", "t",
                                                "--by", "loc"))
        self.p.ok("add", "ac", "--group", "3", "--given", "g", "--when", "w", "--then", "t", "--by", "loc")
        self.assertIn("added AC-3.2", self.p.ok("add", "ac", "--given", "g", "--when", "w", "--then", "t",
                                                "--by", "loc"))

    def test_missing_fields_name_real_flags(self):
        code, text = egd(self.p, "add", "task", "--slice", "S-1", "--title", "x", "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("needs --estimate", text)
        self.assertNotIn("--estimate-h", text)

    def test_a_flag_of_another_kind_is_refused(self):
        code, text = egd(self.p, "add", "ac", "--title", "x", "--given", "g", "--when", "w", "--then", "t",
                         "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("egd add ac does not take --title", text)

    def test_references_are_checked_at_add_time(self):
        text = self.p.refused("add", "slice", "--title", "x", "--covers", "AC-1.3", "--by", "loc")
        self.assertIn("unknown acceptance criterion AC-1.3", text)
        text = self.p.refused("add", "task", "--slice", "S-9", "--title", "x", "--estimate", "1", "--done", "d",
                              "--by", "loc")
        self.assertIn("unknown slice S-9 in 2026-", text)
        self.assertIn("(it has: S-1)", text)
        text = self.p.refused("add", "task", "--slice", "S-1", "--title", "x", "--estimate", "1", "--done", "d",
                              "--depends-on", "T-1.9", "--by", "loc")
        self.assertIn("unknown task T-1.9", text)
        self.assertIn("did you mean T-1.", text)

    def test_help_for_one_kind_lists_only_its_options(self):
        code, text = egd(self.p, "add", "task", "-h")
        self.assertEqual(code, 0)
        self.assertIn("usage: egd add task", text)
        self.assertIn("--estimate HOURS", text)
        self.assertIn("example:\n  egd add task", text)
        self.assertNotIn("--given", text)
        code, text = egd(self.p, "add", "-h")
        self.assertEqual(code, 0)
        self.assertIn("--given", text)  # without a kind: every option

    def test_plan_writes_are_atomic(self):
        with mock.patch("egd.planfile.write_atomic", wraps=planfile.write_atomic) as write:
            self.p.ok("add", "assumption", "--text", "x", "--by", "loc")
        self.assertEqual(write.call_args.args[0].resolve(), (self.p.feature / "plan.toml").resolve())


class AddProof(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_a_test_proof_added_by_command_runs_green(self):
        text = self.p.ok("add", "proof", "--kind", "test", "--verifies", "AC-1.1", "--title", "totals",
                         "--tests", "tests/ok.sh", "--by", "loc")
        self.assertIn("added P-1", text)
        self.assertEqual(proofs(self.p), [{"id": "P-1", "kind": "test", "title": "totals", "verifies": ["AC-1.1"],
                                           "tests": ["tests/ok.sh"]}])
        self.p.commit("proof")
        text = self.p.ok("proof", "run", "--by", "loc")
        self.assertIn("P-1@repo: pass", text)
        self.assertIn("added P-2", self.p.ok("add", "proof", "--kind", "test", "--verifies", "AC-1.1",
                                             "--title", "again", "--by", "loc"))

    def test_cli_and_http_proofs_are_valid(self):
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text() + '\n[env.staging]\nbase_url = "http://127.0.0.1:9"\n')
        self.p.ok("add", "proof", "--kind", "cli", "--verifies", "AC-1.1", "--title", "says hi",
                  "--run", "echo hi", "--expect-output", "hi", "--by", "loc")
        text = self.p.ok("add", "proof", "--kind", "http", "--verifies", "AC-1.1", "--title", "orders",
                         "--method", "post", "--path", "/orders", "--expect-status", "201",
                         "--envs", "staging", "--by", "loc")
        self.assertIn("egd-proof skill", text)
        cli, http = proofs(self.p)
        self.assertEqual(cli["step"], [{"name": "says hi", "run": "echo hi",
                                        "expect": {"exit": 0, "contains": "hi"}}])
        self.assertEqual((http["envs"], http["step"][0]["method"], http["step"][0]["expect"]),
                         (["staging"], "POST", {"status": 201}))
        self.assertIn("plan is valid", self.p.ok("graph"))

    def test_a_ui_proof_opens_a_page_and_asserts_what_is_on_it(self):
        with mock.patch("egd.proof.ui.playwright_ready", return_value=False):
            text = self.p.ok("add", "proof", "--kind", "ui", "--verifies", "AC-1.1", "--title", "total shows",
                             "--path", "orders/latest", "--expect-visible", "text=300,000",
                             "--expect-visible", "#total", "--viewports", "desktop,mobile", "--by", "loc")
        self.assertIn("screenshots need Playwright", text)
        (ui,) = proofs(self.p)
        self.assertEqual((ui["kind"], ui["viewports"]), ("ui", ["desktop", "mobile"]))
        self.assertEqual(ui["step"], [{"name": "total shows", "goto": "/orders/latest",
                                       "expect": {"visible": ["text=300,000", "#total"]}}])
        code, text = egd(self.p, "add", "proof", "--kind", "ui", "--verifies", "AC-1.1", "--title", "x",
                         "--path", "/", "--viewports", "mobil", "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("did you mean 'mobile'?", text)
        code, text = egd(self.p, "add", "proof", "--kind", "ui", "--verifies", "AC-1.1", "--title", "x",
                         "--by", "loc")
        self.assertIn("needs --path", text)

    def test_bad_input_is_refused_with_the_likely_fix(self):
        code, text = egd(self.p, "add", "proof", "--kind", "http", "--verifies", "AC-1.1", "--title", "x",
                         "--path", "/", "--envs", "stagin", "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("no env 'stagin'", text)
        text = self.p.refused("add", "proof", "--kind", "test", "--verifies", "AC-1.2", "--title", "x",
                              "--by", "loc")
        self.assertIn("did you mean AC-1.1?", text)
        code, text = egd(self.p, "add", "proof", "--kind", "cli", "--verifies", "AC-1.1", "--title", "x",
                         "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("needs --run", text)
        code, text = egd(self.p, "add", "proof", "--kind", "test", "--verifies", "AC-1.1", "--title", "x",
                         "--method", "GET", "--by", "loc")
        self.assertIn("does not take --method", text)
        self.assertFalse(proofs(self.p))


class ProofFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project()
        (cls.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "test"\ntitle = "t"\nverifies = ["AC-1.1"]\ntests = ["tests/ok.sh"]\n')
        cls.p.commit("proof")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def test_unknown_env_or_proof_is_an_error(self):
        for argv, expect in ((("--env", "stagin"), "no env 'stagin'"), (("--only", "P-9"), "no proof P-9"),
                             (("--only", "P-11"), "did you mean P-1?")):
            code, text = egd(self.p, "proof", "run", *argv, "--by", "loc")
            self.assertEqual(code, 2, argv)
            self.assertIn(expect, text)

    def test_ids_match_in_any_case(self):
        self.assertIn("P-1@repo: pass", self.p.ok("proof", "run", "--only", "p-1", "--by", "loc"))

    def test_nothing_matched_is_an_error(self):
        cfg = self.p.root / ".egd" / "config.toml"
        old = cfg.read_text()
        cfg.write_text(old + '\n[env.staging]\nbase_url = "http://127.0.0.1:9"\n')
        try:
            code, text = egd(self.p, "proof", "run", "--only", "P-1", "--env", "staging", "--by", "loc")
        finally:
            cfg.write_text(old)
        self.assertEqual(code, 2)
        self.assertIn("0 proofs matched --only P-1 --env staging", text)


class Remove(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_before_slice_anything_unreferenced_goes(self):
        text = self.p.refused("rm", "T-1.1", "--by", "loc")
        self.assertIn("T-1.2 depends on it", text)
        text = self.p.refused("rm", "AC-1.1", "--by", "loc")
        self.assertIn("S-1 covers it", text)
        self.assertIn("removed T-1.2 (task)", self.p.ok("rm", "T-1.2", "--by", "loc"))
        self.assertIn("removed A-1", self.p.ok("remove", "A-1", "--by", "loc"))
        doc = plan(self.p)
        self.assertEqual([t["id"] for t in doc["task"]], ["T-1.1"])
        self.assertNotIn("assumption", doc)
        self.assertIn("plan is valid", self.p.ok("graph"))

    def test_a_proof_goes_with_its_steps(self):
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n\n[[proof.step]]\nrun = "true"\n'
            '[proof.step.expect]\nexit = 0\n\n# the second one\n[[proof]]\nid = "P-2"\nkind = "test"\n'
            'verifies = ["AC-1.1"]\n')
        self.p.ok("rm", "P-1", "--by", "loc")
        text = (self.p.feature / "proof.toml").read_text()
        self.assertEqual([p["id"] for p in tomllib.loads(text)["proof"]], ["P-2"])
        self.assertIn("# the second one", text)
        self.assertNotIn("proof.step", text)

    def test_after_slice_planned_work_needs_a_change_request(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        text = self.p.refused("rm", "T-1.2", "--by", "loc")
        self.assertIn("after slice this needs a change request: egd cr open", text)
        self.assertIn("egd set A-1 status=rejected", self.p.refused("rm", "A-1", "--by", "loc"))
        text = self.p.ok("rm", "T-1.2", "--cr", "--by", "loc")
        self.assertIn("next: egd cr open", text)
        self.assertIn("T-1.2: planned at `slice` but removed", self.p.egd("check", "build")[1])

    def test_an_unknown_id_suggests_a_near_one(self):
        code, text = egd(self.p, "rm", "T-1.3", "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("did you mean T-1.2?", text)

    def test_far_commands_are_not_suggested(self):
        code, text = egd(self.p, "revert")
        self.assertEqual(code, 2)
        self.assertNotIn("did you mean", text)


class ScopeWarning(unittest.TestCase):
    def test_changing_an_ac_after_clarify_says_how_to_record_it(self):
        p = Project()
        try:
            self.assertNotIn("scope changed", p.ok("set", "AC-1.1", "then=x", "--by", "loc"))
            p.pass_through("frame", "clarify")
            self.assertNotIn("scope changed", p.ok("set", "AC-1.1", "levels=unit", "--by", "loc"))
            text = p.ok("set", "AC-1.1", "then=the total is 310000", "--by", "loc")
            self.assertIn("warning: scope changed since clarify — record it: egd cr open", text)
            self.assertIn("--affects AC-1.1", text)
            text = p.ok("add", "ac", "--given", "g", "--when", "w", "--then", "t", "--by", "loc")
            self.assertIn("--affects AC-1.2", text)
        finally:
            p.close()


class Lint(unittest.TestCase):
    def test_slice_rules_warn_until_slice_passes(self):
        p = Project()
        try:
            p.ok("set", "T-1.2", "estimate_h=9", "--by", "loc")  # over the 4h limit
            p.pass_through("frame", "clarify")
            code, text = egd(p, "lint")
            self.assertEqual(code, 0, text)
            self.assertIn("ok · 1 plan warning —", text)
            self.assertIn("· T-1.2: 9h > 4h", text)
            p.ok("set", "T-1.2", "estimate_h=2", "--by", "loc")
            p.pass_through("design", "slice")
            p.ok("set", "T-1.2", "estimate_h=9", "--by", "loc")
            code, text = egd(p, "lint")
            self.assertEqual(code, 1)
            self.assertIn("  - T-1.2: 9h > 4h", text)
        finally:
            p.close()


class Home(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project()
        cls.p.pass_through("frame", "clarify", "design", "slice")
        cls.p.ok("start", "T-1.1", "--by", "loc")
        cls.p.ok("submit", "T-1.1", "--by", "loc", "--confirm")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def home(self, **env):
        with mock.patch.dict(os.environ, env):
            return self.p.ok()

    def test_no_accept_suggested_to_the_owner(self):
        self.assertNotIn("egd accept T-1.1", self.home(EGD_USER="loc"))
        self.assertIn("Next: egd accept T-1.1", self.home(EGD_USER="an"))

    def test_no_accept_suggested_to_nobody(self):
        with mock.patch("egd.cli.whoami", return_value=("", "git user.name")):
            self.assertNotIn("egd accept", self.home())

    def test_inside_claude_code_the_name_says_pass_by(self):
        self.assertIn("you: an (inside Claude Code pass --by)", self.home(EGD_USER="an", CLAUDECODE="1"))
        self.assertNotIn("inside Claude Code", self.home(EGD_USER="an"))

    def test_feature_flag_alone_is_status(self):
        text = self.p.ok("-f", "checkout")
        self.assertIn(self.p.feature.name, text)
        self.assertEqual(text, self.p.ok("status", "checkout"))
        self.assertIn("plan is valid", self.p.ok("-f", "checkout", "graph"))


class FixHints(unittest.TestCase):
    def test_check_names_the_command(self):
        p = Project()
        try:
            map_md = p.root / ".egd" / "map.md"
            map_md.write_text(map_md.read_text().replace("reviewed_by: loc", "reviewed_by:"))
            text = p.egd("check", "frame")[1]
            self.assertIn("Fix: a person who read it writes their name after reviewed_by: in .egd/map.md", text)
            map_md.write_text(map_md.read_text().replace("reviewed_by:", "reviewed_by: loc"))
            p.ok("set", "D-1", "status=proposed", "--by", "loc")
            p.ok("set", "AC-1.1", "levels=", "--by", "loc")
            (p.feature / "design.md").write_text("# Design\n## Approach\nServer.\n")
            p.pass_through("frame", "clarify")
            text = p.egd("check", "design")[1]
            self.assertIn("Fix: egd set D-1 status=accepted", text)
            self.assertEqual(text.count(f"Fix: edit .egd/features/{p.feature.name}/design.md"), 1)
            self.assertIn("Fix: egd set AC-1.1 levels=unit", p.egd("check", "slice")[1])
        finally:
            p.close()


class Polish(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_a_default_signature_is_shown_once(self):
        with mock.patch.dict(os.environ, {"EGD_USER": "loc"}):
            text = self.p.ok("pass", "frame")
        self.assertIn("signed as loc (from EGD_USER)", text)
        self.assertEqual(text.count("signed"), 1)

    def test_board_and_report_print_on_a_terminal(self):
        code, text = egd(self.p, "board", tty=True)
        self.assertIn("# Delivery board", text)
        self.assertIn("wrote .egd/BOARD.md", text)
        self.assertNotIn("# Delivery board", egd(self.p, "board", "--quiet", tty=True)[1])
        self.assertNotIn("# Delivery board", egd(self.p, "board")[1])
        self.assertTrue((self.p.root / ".egd" / "BOARD.md").exists())
        code, text = egd(self.p, "report", tty=True)
        self.assertIn("wrote ", text)
        self.assertGreater(len(text.splitlines()), 3)
        self.assertEqual(len(egd(self.p, "report", "--quiet", tty=True)[1].splitlines()), 1)

    def test_setup_outside_git_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["--root", tmp, "setup"]), 0)
        self.assertIn("not a git repository — drift checks and evidence freshness need git", buf.getvalue())
        self.assertNotIn("not a git repository", self.p.ok("setup"))


OLD = next((p for p in ("/usr/bin/python3", shutil.which("python3.9") or "", shutil.which("python3.10") or "")
            if p and subprocess.run([p, "-c", "import sys; sys.exit(sys.version_info >= (3, 11))"]).returncode == 0),
           None)


@unittest.skipUnless(OLD, "no Python older than 3.11 here")
class OldPython(unittest.TestCase):
    def run_bin(self, path_dir):
        return subprocess.run([OLD, str(ROOT / "bin" / "egd"), "--version"], capture_output=True, text=True,
                              env={**os.environ, "PATH": path_dir})

    def test_says_what_to_install(self):
        with tempfile.TemporaryDirectory() as empty:
            r = self.run_bin(f"{empty}{os.pathsep}/usr/bin{os.pathsep}/bin")
        if shutil.which("python3.11", path="/usr/bin:/bin"):
            self.skipTest("a newer Python sits in /usr/bin")
        self.assertEqual(r.returncode, 2)
        self.assertIn("EGD needs Python 3.11 or newer (found 3.", r.stderr)
        self.assertIn("install.sh", r.stderr)

    def test_hands_over_to_a_newer_python(self):
        with tempfile.TemporaryDirectory() as bin_dir:
            os.symlink(sys.executable, Path(bin_dir) / "python3.13")
            r = self.run_bin(f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("egd 0.1.0", r.stdout)


if __name__ == "__main__":
    unittest.main()
