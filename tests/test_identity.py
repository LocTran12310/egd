import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from helpers import Project

from egd.cli import GROUPS, build_parser, main

TEAM = ('[[member]]\nname = "loc"\ngithub = "loc-gh"\naliases = ["T"]\nroles = ["pm","dev"]\n'
        '[[member]]\nname = "an"\nroles = ["dev"]\n')


def no_env():
    """os.environ without EGD_USER, so git user.name ("T" in Project) is the default."""
    env = {k: v for k, v in os.environ.items() if k != "EGD_USER"}
    return mock.patch.dict(os.environ, env, clear=True)


_outside = mock.patch.dict(os.environ)


def setUpModule():
    # these tests play a person at a terminal; inside Claude Code a default signer is refused
    _outside.start()
    os.environ.pop("CLAUDECODE", None)


def tearDownModule():
    _outside.stop()


class DefaultSigner(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def last_by(self):
        return self.p.ok("trail", "--last", "1").split()[1]

    def test_by_defaults_to_egd_user(self):
        with mock.patch.dict(os.environ, {"EGD_USER": "loc"}):
            text = self.p.ok("pass", "frame")
        self.assertIn("signed as loc (from EGD_USER)", text)
        self.assertEqual(self.last_by(), "loc")

    def test_by_defaults_to_git_user_name(self):
        with no_env():
            text = self.p.ok("pass", "frame")
        self.assertIn("signed as T (from git user.name)", text)
        self.assertEqual(self.last_by(), "T")

    def test_an_agent_never_signs_by_default(self):
        with mock.patch.dict(os.environ, {"EGD_USER": "loc", "CLAUDECODE": "1"}):
            code, text = self.p.egd("pass", "frame")
            self.assertEqual(code, 2)
            self.assertIn("--by", text)
            self.p.ok("pass", "frame", "--by", "loc")  # naming the signer still works

    def test_explicit_by_wins_and_is_not_announced(self):
        with mock.patch.dict(os.environ, {"EGD_USER": "an"}):
            text = self.p.ok("pass", "frame", "--by", "loc")
        self.assertNotIn("signed as", text)
        self.assertEqual(self.last_by(), "loc")

    def test_alias_and_handle_map_to_the_canonical_name(self):
        (self.p.root / ".egd" / "team.toml").write_text(TEAM)
        with no_env():
            self.assertIn("signed as loc (from git user.name)", self.p.ok("pass", "frame"))
        self.assertEqual(self.last_by(), "loc")
        self.p.ok("pass", "clarify", "--by", "loc-gh")
        self.assertEqual(self.last_by(), "loc")

    def test_default_identity_outside_the_team_is_refused(self):
        (self.p.root / ".egd" / "team.toml").write_text(TEAM)
        with mock.patch.dict(os.environ, {"EGD_USER": "mallory"}):
            text = self.p.refused("pass", "frame")
        self.assertIn("'mallory' (from EGD_USER) is not in .egd/team.toml", text)
        self.assertIn("team: loc (@loc-gh), an", text)
        self.assertIn("export EGD_USER", text)
        self.assertNotIn("signed as", text)

    def test_separation_of_duties_holds_with_the_default(self):
        (self.p.root / ".egd" / "team.toml").write_text(TEAM)
        self.p.pass_through("frame", "clarify", "design", "slice")
        with no_env():  # T is loc's alias
            self.p.ok("start", "T-1.1")
            self.p.ok("submit", "T-1.1", "--confirm")
            self.assertIn("someone else", self.p.refused("accept", "T-1.1"))
        self.p.ok("accept", "T-1.1", "--by", "an")

    def test_no_name_anywhere_is_a_usage_error(self):
        self.p.git("config", "--unset", "user.name")
        with no_env(), mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull,
                                                    "GIT_CONFIG_NOSYSTEM": "1"}):
            code, text = self.p.egd("pass", "frame")
        self.assertEqual(code, 2)
        self.assertIn("who is signing?", text)


class Orientation(unittest.TestCase):
    def test_bare_egd_in_a_repo_names_the_next_step(self):
        p = Project()
        try:
            with mock.patch.dict(os.environ, {"EGD_USER": "loc"}):
                text = p.ok()
                self.assertIn("checkout", text)
                self.assertIn("next gate: frame", text)
                self.assertIn("Next: egd pass frame", text)  # its checks already hold
                p.pass_through("frame", "clarify", "design", "slice")
                self.assertIn("Next: egd start T-1.1", p.ok())
                p.ok("start", "T-1.1")
                text = p.ok()
            self.assertIn("you hold T-1.1 (doing)", text)
            self.assertIn("Next: egd submit T-1.1 --confirm", text)
            self.assertLessEqual(len(text.splitlines()), 10)
        finally:
            p.close()

    def test_bare_egd_outside_a_repo_explains_and_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(["--root", tmp])
        self.assertEqual(code, 0)
        self.assertIn("egd setup", text)
        self.assertIn("egd -h", text)


class Typos(unittest.TestCase):
    def test_unknown_command_suggests_the_close_one(self):
        code, text = run(["stauts"])
        self.assertEqual(code, 2)
        self.assertIn("did you mean 'status'?", text)
        self.assertNotIn("Traceback", text)

    def test_unknown_gate_suggests_the_close_one(self):
        code, text = run(["pass", "buidl", "--by", "loc"])
        self.assertEqual(code, 2)
        self.assertIn("did you mean 'build'?", text)
        p = Project()
        try:
            code, text = p.egd("check", "fram")
            self.assertEqual(code, 2)
            self.assertIn("did you mean 'frame'?", text)
        finally:
            p.close()

    def test_help_groups_every_command_once(self):
        grouped = [n for _, names in GROUPS for n in names]
        self.assertEqual(len(grouped), len(set(grouped)))
        sub = next(a for a in build_parser()._actions if a.dest == "cmd")
        self.assertEqual(set(grouped), set(sub.choices))
        code, text = run(["-h"])
        self.assertEqual(code, 0)
        self.assertIn("Build:", text)
        self.assertNotIn("Other:", text)


def run(argv):
    """main() with output captured; argparse's exits become return codes."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue()


if __name__ == "__main__":
    unittest.main()
