"""What trying EGD on real repositories asked for: the feature name as a command, a trail kept on
one machine, an honest verify, and screenshots that need one setup per machine, not per repo."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import Project

from egd import selfmanage as sm
from egd.proof import Context, ui


class FeatureWord(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_the_feature_name_alone_shows_its_status(self):
        self.assertIn("tier standard", self.p.ok("checkout"))
        self.assertIn("plan is valid", self.p.ok("checkout", "graph"))

    def test_a_word_that_is_neither_still_says_unknown_command(self):
        with self.assertRaises(SystemExit) as exit_:
            self.p.egd("nothing-like-it")  # argparse's own "unknown command" exit
        self.assertEqual(exit_.exception.code, 2)


class LocalTrail(unittest.TestCase):
    def setUp(self):
        self.p = Project()  # its .egd/ is committed

    def tearDown(self):
        self.p.close()

    def test_setup_local_excludes_egd_once_and_warns_while_git_still_tracks_it(self):
        self.assertNotIn("warning: .egd/", self.p.ok())
        self.assertIn("git already tracks", self.p.ok("setup", "--local"))  # this one's .egd/ is committed
        self.p.ok("setup", "--local")
        exclude = (self.p.root / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.count("/.egd/\n"), 1)  # anchored: a sibling project's .egd/ stays shared
        self.assertIn("git rm -r --cached .egd", self.p.ok())
        self.p.git("rm", "-r", "-q", "--cached", ".egd")
        self.assertNotIn("warning: .egd/", self.p.ok())
        self.assertTrue((self.p.root / ".egd" / "config.toml").exists())
        self.assertNotIn("??", self.p.git("status", "--porcelain", "--untracked-files=all", "--", ".egd"))
        self.p.git("commit", "-q", "-m", "untrack .egd")
        self.assertIn("nothing of EGD is committed", self.p.ok("setup", "--local"))


class HonestVerify(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "test"\ntitle = "orders"\nverifies = ["AC-1.1"]\ntests = ["tests/ok.sh"]\n')
        self.p.commit("proof")

    def tearDown(self):
        self.p.close()

    def test_tests_not_run_are_not_green_and_proofs_say_where_they_stand(self):
        text = self.p.egd("verify")[1]
        self.assertIn("○ tests", text)
        self.assertIn("⚠️ proofs", text)
        self.assertIn("0/1 pass on the current code", text)
        self.p.ok("proof", "run", "--by", "an")
        self.assertIn("✅ proofs\n    · 1/1 pass on the current code", self.p.egd("verify")[1])


class LocalSetupIsNotTheFeatures(unittest.TestCase):
    """Edits someone keeps uncommitted on their machine — a .gitignore line, an editor config."""

    def setUp(self):
        self.p = Project()
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"\n')
        self.p.commit("proof")
        self.p.pass_through("frame", "clarify", "design", "slice")
        (self.p.root / "README.md").write_text("local notes\n")  # there before the task starts

    def tearDown(self):
        self.p.close()

    def test_an_edit_older_than_the_task_is_not_drift_until_it_changes_again(self):
        self.p.ok("start", "T-1.1", "--by", "loc")
        (self.p.root / "src" / "app.txt").write_text("v2\n")
        self.p.git("add", "src")
        self.p.git("commit", "-q", "-m", "T-1.1 total")
        self.assertNotIn("drift", self.p.ok("submit", "T-1.1", "--by", "loc", "--confirm"))
        self.p.ok("accept", "T-1.1", "--by", "an")
        self.p.ok("start", "T-1.2", "--by", "loc")
        (self.p.root / "README.md").write_text("local notes, edited under T-1.2\n")
        self.assertIn("README.md", self.p.refused("submit", "T-1.2", "--by", "loc", "--confirm"))

    def test_proofs_stay_fresh_beside_local_edits_but_not_beside_the_features_own(self):
        text = self.p.ok("proof", "run", "--by", "loc")
        self.assertIn("do not count against its evidence: README.md", text)
        self.assertIn("P-1@repo: pass", text)
        self.assertNotIn("stale", self.p.ok("proof", "list"))
        (self.p.root / "src" / "app.txt").write_text("v2\n")
        self.assertIn("stale", self.p.ok("proof", "list"))


class NoTestRunner(unittest.TestCase):
    def test_saying_so_stops_the_reminders(self):
        p = Project()
        self.addCleanup(p.close)
        p.ok("set", "T-1.2", "tests=", "--by", "loc")
        self.assertIn("T-1.2: no tests listed", p.ok("lint") + p.egd("verify")[1])
        cfg = p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text().replace('test_command = "sh {tests}"', 'test_command = ""'))
        text = p.egd("verify")[1]
        self.assertNotIn("no tests listed", text)
        self.assertIn("this repository has no test runner", text)
        p.commit("no runner")
        p.pass_through("frame", "clarify", "design", "slice")
        p.ok("start", "T-1.1", "--by", "loc")
        self.assertNotIn("tests not run", p.ok("submit", "T-1.1", "--by", "loc", "--confirm"))


class SharedSignIn(unittest.TestCase):
    def _plan(self, proof, env_cfg, config):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen = {}

        def fake(cmd, input=None, **kw):
            seen.update(json.loads(input))
            return mock.Mock(stdout=json.dumps({"status": "pass", "steps": []}), stderr="", returncode=0)
        ctx = Context(root=Path(tmp.name), feature=None, env="local", env_cfg=env_cfg, base_url="http://app.test/",
                      secrets={"QA_USER": "ann"}, redact=set(), out_dir=Path(tmp.name), config=config)
        with mock.patch.object(ui.subprocess, "run", side_effect=fake):
            ui.run(proof, ctx)
        return seen

    def test_every_viewport_signs_in_first_and_the_envs_own_steps_win(self):
        login = [{"goto": "/login", "fill": {"#user": "${QA_USER}"}, "click": "button[type=submit]"}]
        plan = self._plan({"id": "P-1", "step": [{"goto": "/orders"}]},
                          {"base_url": "http://app.test/", "login": login}, {"ui": {"login": [{"goto": "/elsewhere"}]}})
        first, second = plan["steps"]
        self.assertEqual((first["name"], first["fill"]["#user"], first["safe"], first["screenshot"]),
                         ("sign in", "ann", True, False))
        self.assertEqual((second["goto"], plan["base_url"], plan["storage_state"]), ("/orders", "http://app.test", None))

    def test_a_proof_of_the_login_page_itself_opts_out(self):
        plan = self._plan({"id": "P-1", "login": False, "step": [{"goto": "/login"}]},
                          {"base_url": "http://app.test"}, {"ui": {"login": [{"goto": "/login"}]}})
        self.assertEqual(len(plan["steps"]), 1)


class PlaywrightOncePerMachine(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        for p in (mock.patch.object(Path, "home", return_value=Path(self.home.name)),
                  mock.patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("EGD_UI_PYTHON", None)
        self.ui = Path(self.home.name) / ".egd" / "ui"

    def _made(self):
        (self.ui / "bin").mkdir(parents=True)
        (self.ui / "bin" / "python").write_text("")
        (self.ui / "pyvenv.cfg").write_text("home = /usr/bin\n")

    def test_the_python_setup_made_is_used_unless_one_is_named(self):
        self.assertEqual(ui.ui_python({}), sys.executable)
        self._made()
        self.assertEqual(ui.ui_python({}), str(self.ui / "bin" / "python"))
        self.assertEqual(ui.ui_python({"ui": {"python": "~/venv/bin/python"}}),
                         os.path.expanduser("~/venv/bin/python"))
        with mock.patch.dict(os.environ, {"EGD_UI_PYTHON": "/opt/py"}):
            self.assertEqual(ui.ui_python({}), "/opt/py")

    def test_uninstall_removes_it_and_only_a_folder_setup_made(self):
        (self.ui / "bin").mkdir(parents=True)  # no pyvenv.cfg: not ours
        with mock.patch.object(sm, "_run", return_value=(1, "")), \
                mock.patch.object(sm.shutil, "which", return_value=None):
            self.assertFalse(any("Playwright" in w for w, _, _ in sm.plan()))
            (self.ui / "pyvenv.cfg").write_text("home = /usr/bin\n")
            self.assertTrue(any("Playwright" in w for w, _, _ in sm.plan()))
            lines = []
            sm.uninstall(True, False, False, lines.append)
        self.assertFalse((Path(self.home.name) / ".egd").exists())
        self.assertTrue(any("ms-playwright" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()
