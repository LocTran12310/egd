"""`egd verify` beyond the happy path: a dirty tree, an agent without --by, the lite tier, --gate."""

import json
import os
import unittest
from unittest import mock

from helpers import Project


class VerifyCases(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)

    def events(self):
        return sorted(p.name for p in (self.p.feature / "events").glob("*.json"))

    def test_uncommitted_code_is_reported_but_the_plan_folder_is_not(self):
        (self.p.root / "src" / "app.txt").write_text("v2\n")
        (self.p.root / ".egd" / "note.md").write_text("draft\n")
        text = self.p.ok("verify")
        self.assertIn("1 uncommitted file (src/app.txt)", text)
        self.assertIn("⚠️ working tree", text)  # a warning, not a blocker: the gate is still ready
        self.p.commit("v2")
        self.assertNotIn("uncommitted", self.p.ok("verify"))

    def test_run_inside_claude_code_needs_a_named_signer(self):
        (self.p.feature / "proof.toml").write_text(
            '[[proof]]\nid = "P-1"\nkind = "cli"\nverifies = ["AC-1.1"]\n[[proof.step]]\nrun = "true"\n')
        self.p.commit("proof")
        before = self.events()
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "EGD_USER": "loc"}):
            code, text = self.p.egd("verify", "--run")
            self.assertEqual(code, 2, text)
            self.assertIn("--by", text)
            self.assertEqual(self.events(), before)  # nothing ran, nothing recorded
            self.p.ok("verify")  # reading needs no signature
            self.assertIn("1/1 run passed", self.p.ok("verify", "--run", "--by", "loc"))

    def test_gate_asks_about_every_gate_up_to_it(self):
        self.assertEqual(self.p.egd("verify")[0], 0)  # frame could be passed now
        code, text = self.p.egd("verify", "--gate", "release")
        self.assertEqual(code, 1)
        self.assertIn("not ready for `release`", text)
        names = [n for n, _, _ in json.loads(self.p.egd("verify", "--gate", "release", "--json")[1])["sections"]]
        self.assertEqual([n for n in names if n.startswith("gate ")],
                         ["gate frame", "gate clarify", "gate design", "gate slice", "gate build",
                          "gate accept", "gate release"])


class VerifyLite(unittest.TestCase):
    def setUp(self):
        self.p = Project(tier="lite")
        self.addCleanup(self.p.close)

    def test_lite_skips_design_and_accept(self):
        self.assertIn("ready: a person may now run `egd pass frame`", self.p.ok("verify"))
        code, text = self.p.egd("verify", "--gate", "design")
        self.assertEqual(code, 2)
        self.assertIn("not a gate for tier lite", text)
        result = json.loads(self.p.egd("verify", "--gate", "release", "--json")[1])
        self.assertEqual([n for n, _, _ in result["sections"] if n.startswith("gate ")],
                         ["gate frame", "gate clarify", "gate slice", "gate build", "gate release"])
        self.assertFalse(result["ready"])


if __name__ == "__main__":
    unittest.main()
