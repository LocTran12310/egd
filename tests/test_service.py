import os
import plistlib
import shlex
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts src on the path)
from egd import service


class LaunchAgent(unittest.TestCase):
    """`egd console --service` on macOS: a launchd agent EGD writes, starts and removes."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.calls = []
        for p in (mock.patch.object(service.Path, "home", return_value=Path(self.home.name)),
                  mock.patch.object(service.sys, "platform", "darwin"),
                  mock.patch.object(service.shutil, "which", return_value="/bin/launchctl"),
                  mock.patch.object(service, "_run", side_effect=lambda *c: self.calls.append(c) or (0, "")),
                  mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.home.name) / ".config")})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("EGD_TOKEN", None)  # restored by the patch.dict above

    def plist(self) -> dict:
        return plistlib.loads(service._plist().read_bytes())

    def test_install_writes_a_private_agent_that_runs_this_egd(self):
        args = service.serve_args(8780, lan=True, lan_write=False, readonly=False, token=None)
        token = args[args.index("--token") + 1]
        service.install(args)
        plist = service._plist()
        self.assertEqual(oct(plist.stat().st_mode & 0o777), "0o600")  # it carries the token
        doc = self.plist()
        self.assertEqual(doc["ProgramArguments"][1:], ["-m", "egd", "console", "serve", "--port", "8780", "--lan"])
        self.assertIn("PYTHONPATH", doc["EnvironmentVariables"])
        self.assertTrue(doc["KeepAlive"])
        self.assertTrue(any(c[:2] == ("launchctl", "bootstrap") for c in self.calls))
        self.assertEqual(service.installed(), plist)

    def test_the_token_is_in_the_environment_never_in_the_process_list(self):
        service.install(service.serve_args(8780, lan=True, lan_write=False, readonly=False, token="s3cr3t-tok"))
        doc = self.plist()
        self.assertEqual(doc["EnvironmentVariables"]["EGD_TOKEN"], "s3cr3t-tok")
        self.assertNotIn("s3cr3t-tok", doc["ProgramArguments"])
        self.assertNotIn("--token", doc["ProgramArguments"])

    def test_the_definition_and_the_log_are_private_from_the_first_byte(self):
        created = []
        real_open = os.open

        def spy(path, flags, mode=0o777, *a, **kw):
            if flags & os.O_CREAT:
                created.append((Path(path).name, mode))
            return real_open(path, flags, mode, *a, **kw)
        with mock.patch.object(service.os, "open", side_effect=spy):
            service.install(service.serve_args(8780, False, False, False, None))
        self.assertEqual({m for _, m in created}, {0o600})
        self.assertIn("console.log", [n for n, _ in created])
        log = service._log()
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(service._plist().stat().st_mode), 0o600)
        self.assertEqual([p.name for p in service._plist().parent.iterdir()], [service._plist().name])  # no temp left

    def test_an_old_world_readable_log_is_made_private(self):
        log = service._log()
        log.parent.mkdir(parents=True)
        log.write_text("old\n")
        log.chmod(0o644)
        service.install(service.serve_args(8780, False, False, False, None))
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(log.read_text(), "old\n")  # appended to, never truncated

    def test_it_always_runs_with_a_token_even_on_this_machine_only(self):
        args = service.serve_args(8780, False, False, False, None)
        self.assertEqual(args[:2], ["--port", "8780"])
        self.assertGreater(len(args[args.index("--token") + 1]), 16)
        self.assertNotIn("--lan", args)
        service.install(args)
        self.assertEqual(service.link(), f"http://127.0.0.1:8780/?t={args[args.index('--token') + 1]}")

    def test_a_fixed_token_is_chosen_once_and_kept_when_set_up_again(self):
        args = service.serve_args(8780, lan=True, lan_write=True, readonly=False, token=None)
        self.assertIn("--lan-write", args)
        token = args[args.index("--token") + 1]
        self.assertIsNone(service.current_token())
        service.install(args)
        self.assertEqual(service.current_token(), token)
        again = service.serve_args(8781, lan=True, lan_write=False, readonly=False, token=None)
        self.assertEqual(again[again.index("--token") + 1], token)  # the shared link keeps working
        given = service.serve_args(8781, lan=True, lan_write=False, readonly=False, token="mine")
        self.assertEqual(given[given.index("--token") + 1], "mine")

    def test_a_service_from_an_older_egd_keeps_its_token(self):
        plist = service._plist()
        plist.parent.mkdir(parents=True)
        plist.write_bytes(plistlib.dumps({"Label": service.LABEL, "ProgramArguments": [
            "/usr/bin/python3", "-m", "egd", "console", "serve", "--port", "8790", "--lan", "--token", "old-tok"]}))
        cur = service.current()
        self.assertEqual((cur["token"], cur["port"], cur["lan"]), ("old-tok", 8790, True))
        self.assertEqual(service.link(), "http://127.0.0.1:8790/?t=old-tok")

    def test_off_removes_it(self):
        service.install(["--port", "8780"])
        self.assertTrue(service.uninstall())
        self.assertIsNone(service.installed())
        self.assertFalse(service.uninstall())

    def test_uninstall_plan_includes_it(self):
        from egd import selfmanage
        service.install(["--port", "8780"])
        with mock.patch.object(selfmanage, "_plugins", return_value=[]), \
                mock.patch.object(selfmanage, "_has_market", return_value=False), \
                mock.patch.object(selfmanage, "_docker", return_value={}), \
                mock.patch.object(selfmanage, "installer", return_value=None), \
                mock.patch.dict(os.environ, {"EGD_CONSOLE_CONFIG": str(Path(self.home.name) / "none.toml")}):
            whats = [w for w, _, _ in selfmanage.plan()]
        self.assertTrue(any("background console" in w for w in whats))


class SystemdUnit(unittest.TestCase):
    """`egd console --service` on Linux: the unit is quoted so no value can add a directive."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        home = Path(self.home.name)
        for p in (mock.patch.object(service.Path, "home", return_value=home),
                  mock.patch.object(service.sys, "platform", "linux"),
                  mock.patch.object(service.shutil, "which", return_value="/bin/systemctl"),
                  mock.patch.object(service, "_run", return_value=(0, "")),
                  mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home / ".config")})):
            p.start()
            self.addCleanup(p.stop)
        for k in ("EGD_TOKEN", "EGD_USER", "EGD_CONSOLE_CONFIG", "EGD_CONSOLE_ROOTS", "EGD_BROWSE_ROOTS"):
            os.environ.pop(k, None)

    def test_every_argument_and_value_is_quoted_and_escaped(self):
        text = service.unit_text(["/opt/my python/py", "a\\b", 'say "hi"', "100%", "$HOME"],
                                 {"EGD_USER": 'Linh "L" $x 5%', "EGD_TOKEN": "t"})
        lines = text.splitlines()
        self.assertIn('ExecStart="/opt/my python/py" "a\\\\b" "say \\"hi\\"" "100%%" "$$HOME"', lines)
        self.assertIn('Environment="EGD_USER=Linh \\"L\\" $x 5%%"', lines)  # no $ expansion in Environment=
        self.assertIn('Environment="EGD_TOKEN=t"', lines)
        self.assertEqual(sum(ln.startswith("ExecStart=") for ln in lines), 1)

    def test_a_line_break_is_refused_rather_than_written(self):
        for bad in ("x\nExecStartPre=/bin/sh -c evil", "x\ry"):
            with self.assertRaises(RuntimeError):
                service.unit_text(["/usr/bin/python3", "-m", "egd"], {"EGD_USER": bad})
            with self.assertRaises(RuntimeError):
                service.unit_text(["/usr/bin/python3", bad], {})
        with mock.patch.dict(os.environ, {"EGD_USER": "a\n[Service]\nExecStart=/bin/evil"}), \
                self.assertRaises(RuntimeError):
            service.install(service.serve_args(8780, False, False, False, None))
        self.assertIsNone(service.installed())

    def test_install_writes_a_private_unit_and_reads_its_token_back(self):
        with mock.patch.dict(os.environ, {"EGD_USER": 'Linh "L" 100%'}):
            service.install(service.serve_args(8780, lan=True, lan_write=False, readonly=False, token="t$k%n"))
        unit = service._unit()
        self.assertEqual(stat.S_IMODE(unit.stat().st_mode), 0o600)
        text = unit.read_text()
        self.assertNotIn("--token", text)
        exec_line = next(ln for ln in text.splitlines() if ln.startswith("ExecStart="))
        words = shlex.split(exec_line[len("ExecStart="):])
        self.assertEqual(words[words.index("serve") + 1:], ["--port", "8780", "--lan"])
        cur = service.current()
        self.assertEqual(cur["token"], "t$k%n")
        self.assertEqual(cur["env"]["EGD_USER"], 'Linh "L" 100%')
        self.assertEqual(cur["port"], 8780)
        self.assertTrue(cur["lan"])
