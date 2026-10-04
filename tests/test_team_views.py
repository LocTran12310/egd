import json
import tomllib
import unittest

from helpers import Project, request, serve

from egd.dashboard import make_server
from egd.events import read
from egd.model import resolve_feature
from egd.util import load_config


class PlanEditing(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def plan(self):
        return tomllib.loads((self.p.feature / "plan.toml").read_text())

    def test_add_entries_with_generated_ids(self):
        self.p.ok("add", "assumption", "--by", "loc", "--text", "Ship by Q4", "--blocking")
        self.p.ok("add", "ac", "--by", "loc", "--group", "1", "--given", "g", "--when", "w",
                  "--then", 'says "hi"\\o/', "--levels", "unit,e2e")
        self.p.ok("add", "task", "--by", "loc", "--slice", "S-1", "--title", "More", "--estimate", "1.5",
                  "--touches", "src/**", "--done", "first", "--done", "second")
        plan = self.plan()
        a = plan["assumption"][-1]
        self.assertEqual((a["id"], a["blocking"], a["status"]), ("A-2", True, "open"))
        ac = plan["ac"][-1]
        self.assertEqual((ac["id"], ac["then"], ac["levels"]), ("AC-1.2", 'says "hi"\\o/', ["unit", "e2e"]))
        t = plan["task"][-1]
        self.assertEqual((t["id"], t["estimate_h"], t["done_when"]), ("T-1.3", 1.5, ["first", "second"]))

    def test_add_refuses_incomplete_and_duplicates(self):
        self.assertIn("--given", self.p.egd("add", "ac", "--by", "loc", "--group", "1")[1])
        self.assertIn("already exists", self.p.egd("add", "slice", "--by", "loc", "--id", "S-1",
                                                    "--title", "x")[1])

    def test_set_updates_in_place_and_keeps_comments(self):
        path = self.p.feature / "plan.toml"
        path.write_text("# keep me\n" + path.read_text())
        self.p.ok("set", "A-1", "status=open", "resolution=", "--by", "loc")
        self.p.ok("set", "T-1.2", "done_when=a,b,c", "estimate_h=1", "--by", "loc")
        self.p.ok("set", "S-1", "demo=line one\nline two", "--by", "loc")
        text = path.read_text()
        self.assertTrue(text.startswith("# keep me"))
        plan = self.plan()
        self.assertEqual(plan["assumption"][0]["status"], "open")
        task = next(t for t in plan["task"] if t["id"] == "T-1.2")
        self.assertEqual((task["done_when"], task["estimate_h"]), (["a", "b", "c"], 1))
        self.assertEqual(plan["slice"][0]["demo"], "line one\nline two")
        self.p.ok("set", "S-1", "demo=short", "--by", "loc")
        self.assertEqual(self.plan()["slice"][0]["demo"], "short")
        self.assertIn("no entry", self.p.egd("set", "X-9", "a=b", "--by", "loc")[1])


class Views(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.p.ok("start", "T-1.1", "--by", "an")

    def tearDown(self):
        self.p.close()

    def test_events_are_ordered_by_sequence(self):
        f = resolve_feature(self.p.root, None)
        seqs = [e["seq"] for e in read(f)]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

    def test_standup_groups_by_person(self):
        out = self.p.ok("standup")
        self.assertIn("loc\n  · passed gate frame", out)
        self.assertIn("now: T-1.1 doing", out)

    def test_vietnamese_client_report(self):
        out = self.p.ok("report", "--lang", "vi", "--print")
        self.assertIn("báo cáo tiến độ", out)
        self.assertIn("Đang thực hiện", out)
        self.assertIn("đang làm", out)
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text().replace('lang = "en"', 'lang = "vi"', 1))
        self.assertIn("Phương án hoàn tác", self.p.ok("release-note", "--print"))

    def test_static_site(self):
        out_dir = self.p.root / "site"
        self.p.ok("site", "--out", str(out_dir))
        html = (out_dir / "index.html").read_text()
        self.assertNotIn("/*EGD_DATA*/null", html)
        data = json.loads((out_dir / "data.json").read_text())
        self.assertEqual(data["repos"][0]["features"][0]["tasks"][0]["owner"], "an")


class Serve(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.cfg = load_config(self.p.root)

    def tearDown(self):
        self.p.close()

    def _start(self, token=None):
        server = self.enterContext(serve(make_server(self.p.root, self.cfg, "127.0.0.1", 0, token)))
        self.port = server.server_address[1]

    def get(self, path):
        code, _, body = request(self.port, "GET", path)
        return code, body.decode() if code == 200 else ""

    def test_pages_and_data(self):
        self._start()
        code, html = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("EGD dashboard", html)
        code, body = self.get("/api/portfolio")
        data = json.loads(body)
        self.assertTrue(data["repos"][0]["readonly"])
        self.assertEqual(data["repos"][0]["features"][0]["title"], "Checkout")

    def test_files_are_confined_to_runs(self):
        self._start()
        slug = f"{self.p.root.name}/{self.p.feature.name}"
        (self.p.feature / "runs" / "r1").mkdir(parents=True)
        (self.p.feature / "runs" / "r1" / "t.md").write_text("ok")
        self.assertEqual(self.get(f"/files/{slug}/runs/r1/t.md"), (200, "ok"))
        self.assertEqual(self.get(f"/files/{slug}/plan.toml")[0], 404)
        self.assertEqual(self.get(f"/files/{slug}/runs/../plan.toml")[0], 404)
        self.assertEqual(self.get(f"/files/{slug}/runs/%2e%2e/plan.toml")[0], 404)
        self.assertEqual(self.get(f"/files/{slug}/runs/..%2f..%2fconfig.toml")[0], 404)

    def test_token_required_when_set(self):
        self._start(token="s3cr3t")
        self.assertEqual(self.get("/api/portfolio")[0], 403)
        self.assertEqual(self.get("/api/portfolio?t=wrong")[0], 403)
        self.assertEqual(self.get("/api/portfolio?t=s3cr3t")[0], 200)


if __name__ == "__main__":
    unittest.main()
