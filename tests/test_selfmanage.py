import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts src on the path)
from egd import selfmanage as sm

PLUGINS = [{"id": "egd@egd", "scope": "user"},
           {"id": "egd@egd", "scope": "project", "projectPath": "/work/app"},
           {"id": "other@x", "scope": "user"}]


def fake_run(cmd, cwd=None):
    joined = " ".join(cmd)
    table = {
        "claude plugin list --json": (0, json.dumps(PLUGINS)),
        "claude plugin marketplace list": (0, "Configured marketplaces:\n\n  ❯ egd\n    Source: GitHub"),
        "docker ps -a --format {{.Names}}\t{{.Image}}": (0, "egd-console-1\tegd:0.1.0\nweb\tnginx:1"),
        "docker volume ls --format {{.Name}}": (0, "egd_console-data\nother"),
        "docker images egd --format {{.Repository}}:{{.Tag}}": (0, "egd:0.1.0"),
        "uv tool dir": (0, "/home/u/.local/share/uv/tools"),
    }
    return table.get(joined, (0, ""))


class Uninstall(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        (Path(self.home.name) / ".egd").mkdir()
        (Path(self.home.name) / ".egd" / "console.toml").write_text('user = "x"\n')
        patches = [mock.patch.object(sm, "_run", side_effect=fake_run),
                   mock.patch.object(sm.shutil, "which", side_effect=lambda n: f"/usr/bin/{n}" if n != "pipx" else None),
                   mock.patch.object(sm.Path, "home", return_value=Path(self.home.name)),
                   mock.patch.object(sys, "prefix", "/home/u/.local/share/uv/tools/egd-cli"),
                   mock.patch.dict(sm.os.environ, {}, clear=False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        sm.os.environ.pop("EGD_CONSOLE_CONFIG", None)
        self.addCleanup(self.home.cleanup)

    def test_plan_lists_every_piece_of_egd_and_the_cli_last(self):
        steps = [(what, cmd, cwd) for what, cmd, cwd in sm.plan()]
        whats = [w for w, _, _ in steps]
        plugin = [s for s in steps if "plugin egd@egd" in s[0]]
        self.assertEqual([s[1][-1] for s in plugin], ["user"])  # never a project's shared settings
        self.assertTrue(any("marketplace" in w for w in whats))
        self.assertTrue(any("container egd-console-1" in w for w in whats))
        self.assertFalse(any("web" in w or "nginx" in w for w in whats))  # only EGD's own containers
        self.assertTrue(any("console settings" in w for w in whats))
        self.assertEqual(steps[-1][1], ["uv", "tool", "uninstall", "egd-cli"])

    def test_project_scope_is_left_in_the_repository_and_named(self):
        lines = []
        sm.uninstall(True, False, True, lines.append)
        self.assertIn("Claude Code plugin egd@egd left in /work/app/.claude/settings.json "
                      "(shared with the team; remove it there if you want)", lines)

    def test_update_asks_uv_for_python_3_11_like_the_installer(self):
        with mock.patch("egd.service.installed", return_value=None):  # never restart a real console
            sm.update(lambda _: None)
        uv = next(c.args[0] for c in sm._run.call_args_list if c.args[0][:3] == ["uv", "tool", "install"])
        self.assertEqual(uv[uv.index("--python") + 1], ">=3.11")

    def test_keep_data_spares_the_repository_list(self):
        whats = [w for w, _, _ in sm.plan(keep_data=True)]
        self.assertFalse(any("volume" in w or "console settings" in w for w in whats))

    def test_without_a_terminal_it_asks_for_yes(self):
        lines = []
        with mock.patch.object(sm.sys.stdin, "isatty", return_value=False):
            self.assertEqual(sm.uninstall(False, False, False, lines.append), 2)
        self.assertIn("--yes", lines[-1])
        self.assertTrue((Path(self.home.name) / ".egd" / "console.toml").exists())  # nothing removed

    def test_dry_run_removes_nothing_and_says_repo_data_stays(self):
        lines = []
        self.assertEqual(sm.uninstall(True, False, True, lines.append), 0)
        self.assertTrue(any(".egd/ (plans, trail" in ln for ln in lines))
        self.assertTrue((Path(self.home.name) / ".egd" / "console.toml").exists())


class ConsoleFileOnly(unittest.TestCase):
    """Uninstall deletes the console's settings file — never a folder that holds anything else."""

    def test_a_custom_config_path_never_takes_its_folder_with_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "Documents"
            docs.mkdir()
            (docs / "thesis.docx").write_text("keep me")
            (docs / "console.toml").write_text("")
            self.assertTrue(sm._remove_console_file(docs / "console.toml"))
            self.assertTrue((docs / "thesis.docx").exists())
            self.assertTrue(docs.is_dir())

    def test_a_project_egd_at_home_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            egd = Path(tmp) / ".egd"
            (egd / "features").mkdir(parents=True)
            (egd / "config.toml").write_text("")
            (egd / "console.toml").write_text("")
            self.assertTrue(sm._remove_console_file(egd / "console.toml"))
            self.assertTrue((egd / "features").is_dir() and (egd / "config.toml").exists())

    def test_an_empty_console_folder_goes(self):
        with tempfile.TemporaryDirectory() as tmp:
            egd = Path(tmp) / ".egd"
            egd.mkdir()
            (egd / "console.toml").write_text("")
            self.assertTrue(sm._remove_console_file(egd / "console.toml"))
            self.assertFalse(egd.exists())
