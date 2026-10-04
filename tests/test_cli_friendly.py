"""The CLI answers mistakes with a message and a next step — never a traceback."""

import contextlib
import io
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from helpers import Project

from egd import github
from egd.cli import build_parser, main
from egd.events import read
from egd.model import feature_owning, resolve_feature
from egd.util import UsageError


def egd(p, *argv):
    """p.egd, with argparse's exits as return codes."""
    buf, cwd = io.StringIO(), os.getcwd()
    os.chdir(p.root)
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = main(list(argv))
    except SystemExit as exc:
        code = exc.code
    finally:
        os.chdir(cwd)
    return code, buf.getvalue()


class NoTraceback(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text() + '\n[github]\nrepo = "o/r"\n')

    def tearDown(self):
        self.p.close()

    def test_sync_without_gh_says_how_to_get_it(self):
        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(os.environ, {"PATH": empty}):
            code, text = self.p.egd("sync", "github", "--include-unplanned", "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("`gh` is not installed", text)
        self.assertIn("--dry-run", text)
        self.assertNotIn("Traceback", text)

    def test_sync_gives_up_on_a_hung_gh(self):
        with tempfile.TemporaryDirectory() as bin_dir:
            gh = Path(bin_dir) / "gh"
            gh.write_text("#!/bin/sh\n/bin/sleep 5\n")
            gh.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": bin_dir}), mock.patch.object(github, "GH_TIMEOUT", 0.2):
                code, text = self.p.egd("sync", "github", "--include-unplanned", "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("did not answer within", text)

    def test_import_without_ai_folder(self):
        for argv in (("import", "aidlc", "--by", "loc"), ("import", "aidlc", "--refresh")):
            code, text = self.p.egd(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("has no .ai/features/", text)
            self.assertIn("--root", text)
            self.assertNotIn("Traceback", text)

    def test_stray_os_error_is_a_message(self):
        boom = FileNotFoundError(2, "No such file or directory", "plan.toml")
        with mock.patch("egd.cli.cmd_list", side_effect=boom):
            code, text = self.p.egd("list")
        self.assertEqual((code, text.strip()), (2, "egd: no such file or directory: plan.toml"))


class FeatureArgument(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.ok("new", "refunds", "--by", "loc")

    def tearDown(self):
        self.p.close()

    def test_dash_f_and_positional_both_work(self):
        for argv in (("status", "-f", "checkout"), ("status", "checkout"), ("status", "--feature", "checkout")):
            self.assertIn("[", self.p.ok(*argv).splitlines()[0])
            self.assertIn("checkout", self.p.ok(*argv).splitlines()[0])
        for cmd in ("graph", "ready", "pr", "metrics", "trail", "verify"):
            self.p.egd(cmd, "-f", "checkout")  # parses; the result depends on the plan
            code, text = self.p.egd(cmd, "-f", "nope")
            self.assertEqual(code, 2, cmd)
            self.assertIn("no feature matches 'nope'", text)
        self.assertIn("frame: closed", self.p.egd("check", "-f", "refunds")[1])
        self.assertIn("frame: open", self.p.ok("check", "frame", "-f", "checkout"))
        self.p.ok("pass", "frame", "-f", "checkout", "--by", "loc")
        self.p.ok("proof", "list", "-f", "checkout")

    def test_two_different_features_are_refused(self):
        code, text = self.p.egd("status", "checkout", "-f", "refunds")
        self.assertEqual(code, 2)
        self.assertIn("give one", text)
        self.p.ok("status", "checkout", "-f", "checkout")

    def test_task_and_record_commands_take_dash_f(self):
        for gate in ("frame", "clarify", "design", "slice"):
            self.p.ok("pass", gate, "-f", "checkout", "--by", "loc")
        self.p.ok("start", "T-1.1", "-f", "checkout", "--by", "an")
        self.p.ok("cr", "list", "-f", "checkout")
        self.p.ok("bug", "list", "-f", "checkout")
        self.p.ok("add", "slice", "-f", "checkout", "--by", "loc", "--title", "More")
        self.p.ok("set", "S-2", "demo=x", "-f", "checkout", "--by", "loc")
        self.assertIn("[github] repo", self.p.egd("sync", "github", "-f", "checkout", "--dry-run")[1])

    def test_site_and_import_keep_repeatable_feature(self):
        args = build_parser().parse_args(["site", "-f", "checkout", "--feature", "refunds"])
        self.assertEqual(args.feature, ["checkout", "refunds"])
        args = build_parser().parse_args(["import", "aidlc", "-f", "a", "-f", "b"])
        self.assertEqual(args.feature, ["a", "b"])

    def test_unknown_feature_suggests_or_lists(self):
        code, text = self.p.egd("status", "chekout")
        self.assertEqual(code, 2)
        self.assertIn("did you mean 'checkout'?", text)
        code, text = self.p.egd("pr", "zzz")
        self.assertIn("features: checkout, refunds", text)
        code, text = self.p.egd("pr")
        self.assertIn("name one with -f", text)

    def test_exact_short_name_wins_over_a_longer_one(self):
        self.p.ok("new", "checkout-v2", "--by", "loc")
        self.assertIn(self.p.feature.name, self.p.ok("status", "checkout").splitlines()[0])


class Flags(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def _drift(self):
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")
        (self.p.root / "docs.md").write_text("x")
        self.p.commit("T-1.1 also docs")

    def test_submit_takes_drift_reason(self):
        self._drift()
        self.assertIn("--drift-reason", self.p.refused("submit", "T-1.1", "--by", "an", "--confirm",
                                                       "--allow-drift"))
        self.p.ok("submit", "T-1.1", "--by", "an", "--confirm", "--allow-drift", "--drift-reason", "readme")
        submitted = [e for e in read(resolve_feature(self.p.root, None)) if e["type"] == "task_submitted"]
        self.assertEqual(submitted[-1]["drift_reason"], "readme")

    def test_solo_asks_for_drift_reason_not_reason(self):
        self._drift()
        text = self.p.refused("solo", "T-1.1", "--by", "an", "--confirm", "--reason", "alone", "--allow-drift")
        self.assertIn("--drift-reason", text)
        self.p.ok("solo", "T-1.1", "--by", "an", "--confirm", "--reason", "alone", "--allow-drift",
                  "--drift-reason", "readme")

    def test_help_explains_flags_and_hides_the_old_alias(self):
        text = build_parser()._subparsers._group_actions[0].choices["submit"].format_help()
        self.assertIn("--drift-reason", text)
        self.assertIn("actual hours spent", text)
        self.assertNotIn("--reason", text)
        bug = build_parser()._subparsers._group_actions[0].choices["bug"].format_help()
        self.assertIn("critical, major, minor, trivial", bug)


class Records(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_bug_open_needs_a_title(self):
        self.assertIn("--title", self.p.refused("bug", "open", "--by", "loc"))
        self.assertFalse(any(e["type"] == "defect_opened" for e in read(resolve_feature(self.p.root, None))))

    def test_bug_choices_suggest_on_typo(self):
        code, text = egd(self.p, "bug", "open", "--by", "loc", "--title", "x", "--severity", "majr")
        self.assertEqual(code, 2)
        self.assertIn("did you mean 'major'?", text)
        code, text = egd(self.p, "bug", "open", "--by", "loc", "--title", "x", "--found-in", "uta")
        self.assertEqual(code, 2)
        self.assertIn("did you mean 'uat'?", text)
        self.assertIn("BUG-001", self.p.ok("bug", "open", "--by", "loc", "--title", "x", "--severity", "minor"))

    def test_cr_affects_must_name_real_acs(self):
        text = self.p.refused("cr", "open", "--by", "loc", "--title", "t", "--reason", "r",
                              "--affects", "AC-1.1,AC-1.2")
        self.assertIn("AC-1.2", text)
        self.assertIn("did you mean AC-1.1?", text)
        self.assertIn("CR-001", self.p.ok("cr", "open", "--by", "loc", "--title", "t", "--reason", "r",
                                          "--affects", "AC-1.1"))


class PlanEdits(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def plan(self):
        return tomllib.loads((self.p.feature / "plan.toml").read_text())

    def test_set_refuses_fields_the_kind_does_not_hold(self):
        code, text = self.p.egd("set", "AC-1.1", "blocking=maybe", "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("an ac has no field 'blocking'", text)
        self.assertIn("did you mean 'status'?", self.p.egd("set", "A-1", "statsu=open", "--by", "loc")[1])
        self.assertNotIn("blocking", self.plan()["ac"][0])

    def test_set_keeps_fields_an_entry_already_has(self):
        path = self.p.feature / "plan.toml"
        path.write_text(path.read_text().replace('id = "AC-1.1"', 'id = "AC-1.1"\ntitle = "Total"'))
        self.p.ok("set", "AC-1.1", "title=Order total", "--by", "loc")
        self.assertEqual(self.plan()["ac"][0]["title"], "Order total")

    def test_set_booleans_and_enums(self):
        self.assertIn("true or false", self.p.egd("set", "A-1", "blocking=maybe", "--by", "loc")[1])
        self.p.ok("set", "A-1", "blocking=no", "--by", "loc")
        self.assertIs(self.plan()["assumption"][0]["blocking"], False)
        code, text = self.p.egd("set", "D-1", "status=done", "--by", "loc")
        self.assertEqual(code, 2)
        self.assertIn("proposed, accepted, superseded", text)
        self.assertIn("did you mean 'confirmed'?", self.p.egd("set", "A-1", "status=confimed", "--by", "loc")[1])
        self.assertIn("'smoke'", self.p.egd("set", "AC-1.1", "levels=unit,smoke", "--by", "loc")[1])
        self.p.ok("set", "D-1", "status=superseded", "--by", "loc")
        self.assertEqual(self.plan()["decision"][0]["status"], "superseded")

    def test_add_validates_like_set(self):
        self.assertIn("proposed, accepted, superseded",
                      self.p.egd("add", "decision", "--by", "loc", "--title", "x", "--status", "maybe")[1])
        text = self.p.ok("add", "ac", "--by", "loc", "--given", "g", "--when", "w", "--then", "t")
        self.assertIn("added AC-1.2", text)  # no --group: the group of the last AC

    def test_unknown_id_suggests_a_near_one(self):
        self.assertIn("did you mean T-1.2?", self.p.egd("set", "T-1.3", "title=x", "--by", "loc")[1])
        self.assertNotIn("did you mean", self.p.egd("set", "X-9", "title=x", "--by", "loc")[1])


class Owning(unittest.TestCase):
    def test_feature_owning_finds_by_kind_or_any(self):
        p = Project()
        try:
            p.ok("new", "refunds", "--by", "loc")
            self.assertEqual(feature_owning(p.root, "task", "T-1.1", None).path, p.feature)
            self.assertEqual(feature_owning(p.root, None, "D-1", None).path, p.feature)
            with self.assertRaises(UsageError) as cm:
                feature_owning(p.root, "slice", "S-9", None)
            self.assertIn("no feature has slice S-9", str(cm.exception))
        finally:
            p.close()


class StatusOnce(unittest.TestCase):
    def test_status_notes_older_rules_without_rereading_the_trail(self):
        p = Project()
        try:
            created = next((p.feature / "events").glob("*-feature_created.json"))
            event = json.loads(created.read_text())
            created.write_text(json.dumps({**event, "rules": 0}))
            self.assertIn("planned under rules v0", p.ok("status"))
            with mock.patch("egd.cli.read", side_effect=AssertionError("trail read twice")):
                p.ok("status")
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
