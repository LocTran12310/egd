import json
import unittest

from helpers import Project


class Verify(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_reads_only_and_names_what_keeps_the_next_gate_closed(self):
        before = sorted(p.name for p in (self.p.feature / "events").glob("*.json")) \
            if (self.p.feature / "events").exists() else []
        self.assertIn("ready: a person may now run `egd pass frame`", self.p.ok("verify"))
        m = self.p.root / ".egd" / "map.md"
        m.write_text(m.read_text().replace("reviewed_by: loc", "reviewed_by:"))
        code, text = self.p.egd("verify")
        self.assertEqual(code, 1)
        self.assertIn("not ready for `frame`", text)
        self.assertIn("reviewed_by", text)
        after = sorted(p.name for p in (self.p.feature / "events").glob("*.json")) \
            if (self.p.feature / "events").exists() else []
        self.assertEqual(before, after)

    def test_final_pass_runs_tests_and_proofs_then_hands_build_to_a_person(self):
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "test"\ntitle = "orders"\nverifies = ["AC-1.1"]\ntests = ["tests/ok.sh"]\n')
        self.p.commit("proof")
        self.p.pass_through("frame", "clarify", "design", "slice")
        for t in ("T-1.1", "T-1.2"):
            self.p.ok("start", t, "--by", "an")
            self.p.ok("solo", t, "--by", "an", "--confirm", "--reason", "solo week")
        self.p.commit("work")
        self.assertIn("not ready for `build`", self.p.refused("verify"))  # P-1 never ran
        text = self.p.ok("verify", "--run", "--by", "an")
        self.assertIn("ready: a person may now run `egd pass build`", text)
        self.assertIn("passed in", text)  # task tests ran
        self.assertNotIn("passed build", self.p.ok("trail"))  # verify never signs
        far = json.loads(self.p.egd("verify", "--gate", "release", "--json")[1])
        self.assertFalse(far["ready"])
        self.assertIn("S-1: no UAT verdict", dict((n, p) for n, p, _ in far["sections"])["gate accept"])
        pr = self.p.ok("pr")
        self.assertIn("### Verification", pr)
        self.assertIn("P-1 `test`", pr)
