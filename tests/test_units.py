import unittest

from helpers import Project

from egd.graph import find_cycle, longest_path, waves
from egd.proof.expect import compare, json_path, present
from egd.util import matches_any


class Graph(unittest.TestCase):
    T = [{"id": "a", "estimate_h": 2}, {"id": "b", "estimate_h": 3, "depends_on": ["a"]},
         {"id": "c", "estimate_h": 1, "depends_on": ["a"]}, {"id": "d", "estimate_h": 1, "depends_on": ["b", "c"]}]

    def test_waves_and_critical_path(self):
        self.assertEqual(waves(self.T), [["a"], ["b", "c"], ["d"]])
        self.assertEqual(longest_path(self.T), (6.0, ["a", "b", "d"]))

    def test_cycle(self):
        self.assertIsNone(find_cycle(self.T))
        cyc = find_cycle([{"id": "x", "depends_on": ["y"]}, {"id": "y", "depends_on": ["x"]}])
        self.assertEqual(cyc[0], cyc[-1])


class Expect(unittest.TestCase):
    DOC = {"items": [{"price": 5}, {"price": 7}], "status": "ok", "odd key": True}

    def test_paths(self):
        self.assertEqual(json_path(self.DOC, "$.items[1].price"), 7)
        self.assertEqual(json_path(self.DOC, "$.items[-1].price"), 7)
        self.assertEqual(json_path(self.DOC, "$['odd key']"), True)
        self.assertFalse(present(json_path(self.DOC, "$.nope")))

    def test_operators(self):
        self.assertTrue(compare(7, 7.0)[0])
        self.assertTrue(compare(7, {"gt": 5, "lte": 7})[0])
        self.assertFalse(compare(True, {"type": "number"})[0])
        self.assertTrue(compare([1, 2], {"len": 2})[0])
        self.assertTrue(compare("abc-123", {"matches": r"\d+"})[0])
        self.assertTrue(compare(json_path(self.DOC, "$.x"), {"exists": False})[0])
        self.assertFalse(compare(json_path(self.DOC, "$.x"), 1)[0])


class Globs(unittest.TestCase):
    def test_matches(self):
        self.assertTrue(matches_any("src/orders/a.ts", ["src/orders/**"]))
        self.assertTrue(matches_any("src/orders/a.ts", ["src/orders"]))
        self.assertTrue(matches_any("src/orders/a.ts", ["./src/orders/a.ts"]))
        self.assertFalse(matches_any("src/ordersx/a.ts", ["src/orders"]))


class Tiers(unittest.TestCase):
    def test_lite_skips_design_and_accept_and_map(self):
        p = Project(tier="lite")
        try:
            m = p.root / ".egd" / "map.md"
            m.write_text(m.read_text().replace("reviewed_by: loc", "reviewed_by:"))
            p.pass_through("frame", "clarify", "slice")
            self.assertIn("not a gate", p.refused("pass", "design", "--by", "loc"))
        finally:
            p.close()

    def test_full_requires_proof_per_ac(self):
        p = Project(tier="full")
        try:
            p.pass_through("frame", "clarify", "design")
            self.assertIn("no proof declared", p.refused("pass", "slice", "--by", "loc"))
        finally:
            p.close()


class GitHubDryRun(unittest.TestCase):
    def test_sync_plans_issue_creation(self):
        p = Project()
        try:
            cfg = p.root / ".egd" / "config.toml"
            cfg.write_text(cfg.read_text() + '\n[github]\nrepo = "acme/shop"\n')
            p.pass_through("frame", "clarify", "design", "slice")
            out = p.ok("sync", "github", "--dry-run")
            self.assertIn("gh issue create --repo acme/shop", out)
            self.assertIn("egd:todo", out)
            self.assertIn("2 gh calls", out)
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
