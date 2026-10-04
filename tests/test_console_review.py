"""Console review fixes: parallel registry writes, the map's signer, symlinked evidence folders,
the owner on the LAN address, IPv6 hosts and action results a page can translate."""

import http.client
import os
import re
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import Project, serve
from test_console import ServerBase

from egd import console, dashboard

SRC = Path(__file__).resolve().parents[1] / "src" / "egd"


class ParallelAdds(ServerBase):
    def test_eight_repositories_added_at_once_are_all_kept(self):
        tree = tempfile.TemporaryDirectory()
        self.addCleanup(tree.cleanup)
        base = Path(tree.name).resolve()
        paths = []
        for i in range(8):
            (base / f"r{i}" / ".egd").mkdir(parents=True)
            paths.append(base / f"r{i}")
        real = console._save_registry

        def slow_save(reg):  # widen the window between reading console.toml and writing it
            time.sleep(0.03)
            return real(reg)
        start, results = threading.Barrier(8), []

        def add(p):
            start.wait()
            results.append(self.req("POST", "/api/repos", {"op": "add", "path": str(p)}, {"X-EGD-CSRF": self.csrf})[0])
        with mock.patch.dict(os.environ, {"EGD_BROWSE_ROOTS": str(base)}), \
                mock.patch.object(console, "_save_registry", slow_save):
            threads = [threading.Thread(target=add, args=(p,)) for p in paths]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(results, [200] * 8)
        kept = set(console.load_registry()["repos"])
        self.assertTrue({str(p) for p in paths} <= kept, sorted(kept))
        self.assertEqual(len(kept), 10)  # and the two it had


class MapSignerInDetail(ServerBase):
    def test_an_empty_reviewed_by_followed_by_a_comment_is_unsigned(self):
        m = self.a.root / ".egd" / "map.md"
        m.write_text(m.read_text().replace("reviewed_by: loc", "reviewed_by:\n<!-- write your name above -->", 1))
        rid = self.rid(self.a)
        code, d = self.req("GET", f"/api/repo?repo={rid}")
        self.assertEqual((code, d["signer"]), (200, ""))
        m.write_text(m.read_text().replace("reviewed_by:\n", "reviewed_by:  Linh \n", 1))
        self.assertEqual(self.req("GET", f"/api/repo?repo={rid}")[1]["signer"], "Linh")

    def test_the_page_shows_the_servers_signer(self):
        html = (SRC / "console.html").read_text(encoding="utf-8")
        self.assertIn('"signer" in d ? d.signer', html)
        self.assertNotIn(r"):\s*(.*)$/mi", html)  # \s* would read the next line as the name


class SymlinkedRuns(ServerBase):
    def setUp(self):
        super().setUp()
        self.outside = tempfile.TemporaryDirectory()
        self.addCleanup(self.outside.cleanup)
        Path(self.outside.name, "secret.md").write_text("not evidence")

    def test_files_never_leave_the_repository(self):
        rid, slug = self.rid(self.a), self.a.feature.name
        (self.a.feature / "runs").symlink_to(self.outside.name, target_is_directory=True)
        self.assertEqual(self.req("GET", f"/files/{rid}/{slug}/runs/secret.md")[0], 404)

    def test_a_runs_link_inside_the_feature_folder_still_serves(self):
        rid, slug = self.rid(self.a), self.a.feature.name
        (self.a.feature / "evidence").mkdir()
        (self.a.feature / "evidence" / "t.md").write_text("ok")
        (self.a.feature / "runs").symlink_to("evidence", target_is_directory=True)
        self.assertEqual(self.req("GET", f"/files/{rid}/{slug}/runs/t.md"), (200, "ok"))

    def test_a_feature_folder_outside_the_repository_is_refused(self):
        self.assertIsNone(dashboard.safe_runs(Path(self.outside.name), self.a.root))
        (self.a.feature / "runs").symlink_to(self.outside.name, target_is_directory=True)
        self.assertIsNone(dashboard.safe_runs(self.a.feature, self.a.root))

    def test_export_skips_it_instead_of_crashing(self):
        (self.a.feature / "runs").symlink_to(self.outside.name, target_is_directory=True)
        slug = self.a.feature.name
        data = {"repos": [{"id": "a", "features": [{"slug": next(
            f.slug for f in dashboard.all_features(self.a.root)), "proofs": [
            {"runs": [{"transcript": "runs/secret.md", "shots": []}]}]}]}]}
        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(dashboard, "portfolio", return_value=data), \
                mock.patch.object(dashboard, "render", return_value="<html></html>"):
            _, copied = dashboard.export_site(self.a.root, {}, Path(out))
            self.assertEqual(copied, 0)
            self.assertFalse((Path(out) / "files").exists(), slug)


class OwnerOnTheLanAddress(unittest.TestCase):
    def handler(self, client, server_ip):
        server = console.make_server("127.0.0.1", 0, "tok", warm=False, remote_readonly=True)
        self.addCleanup(server.server_close)
        h = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        h.client_address = (client, 50000)
        h.connection = mock.Mock(getsockname=mock.Mock(return_value=(server_ip, 8780)))
        return h

    def test_the_owner_opening_the_lan_link_on_this_machine_may_act(self):
        self.assertFalse(self.handler("192.168.1.5", "192.168.1.5").readonly)
        self.assertFalse(self.handler("fe80::1", "fe80::1").readonly)

    def test_another_machine_still_only_looks(self):
        self.assertTrue(self.handler("192.168.1.42", "192.168.1.5").readonly)
        h = self.handler("192.168.1.42", "")
        h.connection.getsockname.side_effect = OSError("closed")
        self.assertTrue(h.readonly)


class Ipv6Host(unittest.TestCase):
    def test_the_console_listens_on_an_ipv6_address(self):
        try:
            server = console.make_server("::1", 0, warm=False)
        except OSError as exc:
            self.skipTest(f"no IPv6 loopback here: {exc}")
        self.assertEqual(server.address_family, socket.AF_INET6)
        with serve(server):
            port = server.server_address[1]
            conn = http.client.HTTPConnection("::1", port, timeout=5)
            try:
                conn.request("GET", "/api/boot", headers={"Host": f"[::1]:{port}"})
                r = conn.getresponse()
                self.assertEqual(r.status, 200)
                self.assertIn(b'"readonly": false', r.read())
            finally:
                conn.close()

    def test_a_bracketed_host_works_too(self):
        try:
            server = console.make_server("[::1]", 0, warm=False)
        except OSError as exc:
            self.skipTest(f"no IPv6 loopback here: {exc}")
        server.server_close()


class ActionResults(ServerBase):
    def test_a_result_says_what_was_done_for_the_page_to_translate(self):
        m = self.a.root / ".egd" / "map.md"
        m.write_text(m.read_text().replace("reviewed_by: loc", "reviewed_by:", 1))
        code, res = self.act(self.a, "sign_map")
        self.assertEqual(code, 200, res)
        self.assertEqual((res["action"], res["target"], res["by"], res["message"]),
                         ("sign_map", None, "loc", "map signed by loc"))

    def test_every_action_has_a_translatable_result_matching_the_english(self):
        html = (SRC / "console.html").read_text(encoding="utf-8")
        table = html[html.index("const ACT_DONE = {"):]
        table = table[:table.index("};")]
        done = dict(re.findall(r'(\w+): N_\("([^"]*)"\)', table))
        src = (SRC / "console.py").read_text(encoding="utf-8")
        body = src[src.index("def _perform("):src.index("# ------------------------------------------------------------------ server")]
        actions = set(re.findall(r'action == "(\w+)"', body))
        for group in re.findall(r"action in \(([^)]*)\)", body):
            actions |= set(re.findall(r'"(\w+)"', group))
        self.assertEqual(set(done), actions)
        self.assertEqual(done["sign_map"].format(by="loc"), "map signed by loc")
        self.assertEqual(done["start"].format(target="T-1", by="loc"), "T-1 → doing (@loc)")
        vi = (SRC / "ui" / "i18n-vi.js").read_text(encoding="utf-8")
        for english in done.values():
            self.assertIn(f'"{english}":', vi)


class InlineMarkdown(unittest.TestCase):
    def test_raw_marker_characters_are_dropped_before_parsing(self):
        js = (SRC / "ui" / "ui.js").read_text(encoding="utf-8")
        start = js.index("const inlineMd")
        self.assertIn(r'.replace(/[\u0000\u0001]/g, "")', js[start:start + 400])


if __name__ == "__main__":
    unittest.main()
