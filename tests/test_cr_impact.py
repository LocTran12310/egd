import json
import unittest

from helpers import Project

from egd import console, dashboard, report
from egd.events import record, replay
from egd.model import load_feature
from egd.util import load_config

AC_2 = """
[[ac]]
id = "AC-2"
given = "a cart"
when = "the buyer prints it"
then = "the receipt shows 2 lines"
levels = ["unit"]
"""

PROOF = '[[proof]]\nid = "P-1"\nkind = "test"\ntitle = "orders"\nverifies = ["AC-1.1"]\ntests = ["tests/ok.sh"]\n'


class CrImpact(unittest.TestCase):
    """What a change to some ACs reaches — previewed, printed on open, kept on the event, shown back."""

    def setUp(self):
        self.p = Project()
        (self.p.feature / "proof.toml").write_text(PROOF)
        self.p.commit("plan")

    def tearDown(self):
        self.p.close()

    def events(self):
        return sorted(p.name for p in (self.p.feature / "events").glob("*.json"))

    def feature(self):
        f = load_feature(self.p.root, self.p.feature)
        return f, replay(f), load_config(self.p.root)

    def deliver_and_change(self):
        """S-1 built, proven and accepted by the client; then T-1.2 reopened and the code changed."""
        self.p.pass_through("frame", "clarify", "design", "slice")
        for t in ("T-1.1", "T-1.2"):
            self.p.ok("start", t, "--by", "an")
            self.p.ok("solo", t, "--by", "an", "--confirm", "--reason", "solo week")
        self.p.commit("work")
        self.p.ok("proof", "run", "--by", "an")
        self.p.pass_through("build")
        self.p.ok("uat", "S-1", "--pass", "--by", "client")
        self.p.ok("reopen", "T-1.2", "--by", "loc", "--reason", "VAT")
        (self.p.root / "src" / "app.txt").write_text("v2\n")
        self.p.commit("T-1.2 VAT")  # P-1 ran on the old code: stale now

    def test_an_ac_no_slice_task_or_proof_reaches(self):
        plan = self.p.feature / "plan.toml"
        plan.write_text(plan.read_text() + AC_2)
        text = self.p.ok("cr", "impact", "--affects", "AC-2")
        self.assertIn("slices   none cover them", text)
        self.assertIn("tasks    none", text)
        self.assertIn("proofs   none verify them", text)
        self.assertIn("hours    ≤ 0h", text)

    def test_preview_names_what_must_be_redone_and_records_nothing(self):
        self.deliver_and_change()
        before = self.events()
        text = self.p.ok("cr", "impact", "--affects", "AC-1.1")
        self.assertIn("S-1 (UAT passed — walk it again)", text)
        self.assertIn("done, to reopen or add to: T-1.1", text)
        self.assertIn("in progress: T-1.2 (doing)", text)
        self.assertIn("P-1 (stale)", text)
        self.assertIn("≤ 5h", text)
        self.assertIn("rough upper bound", text)
        self.assertEqual(self.events(), before)
        self.assertIn("--affects", self.p.egd("cr", "impact")[1])

    def test_open_prints_and_keeps_the_impact_and_reports_show_it(self):
        self.deliver_and_change()
        text = self.p.ok("cr", "open", "--by", "loc", "--title", "Add VAT", "--reason", "law",
                         "--hours", "1", "--affects", "AC-1.1")
        self.assertLess(text.index("impact of changing AC-1.1"), text.index("waiting for the client"))
        self.assertIn("--hours 1 is well under the 5h", text)  # said, not refused
        opened = json.loads(next((self.p.feature / "events").glob("*-cr_opened.json")).read_text())
        imp = opened["impact"]
        self.assertEqual(imp["hours"], 5)
        self.assertEqual([s["uat"] for s in imp["slices"]], ["passed"])
        self.assertEqual({t["id"]: t["group"] for t in imp["tasks"]}, {"T-1.1": "done", "T-1.2": "active"})
        self.assertEqual([(p["id"], p["state"]) for p in imp["proofs"]], [("P-1", "stale")])

        f, st, cfg = self.feature()
        self.assertIn("Add VAT — impact: UAT again for S-1; 1 finished task(s) to reopen; 1 proof(s) to run again; "
                      "up to 5h", report.client_report(f, cfg))
        self.assertIn("tác động: UAT lại S-1", report.client_report(f, cfg, lang="vi"))
        cr = dashboard.feature_data(f, cfg)["crs"][0]
        self.assertEqual(cr["impact"], {"uat": ["S-1"], "done": 1, "proofs": 1, "hours": 5})
        inbox = console.feature_inbox("r", f, st, cfg, None, [])
        self.assertIn("impact: UAT again for S-1", next(i for i in inbox if i["kind"] == "cr")["detail"])

    def test_hours_close_to_the_estimate_and_a_cr_with_no_affects_say_nothing_more(self):
        self.p.pass_through("frame", "clarify")
        text = self.p.ok("cr", "open", "--by", "loc", "--title", "t", "--reason", "r", "--hours", "4",
                         "--affects", "AC-1.1")
        self.assertIn("impact of changing AC-1.1", text)
        self.assertNotIn("well under", text)
        text = self.p.ok("cr", "open", "--by", "loc", "--title", "t2", "--reason", "r")
        self.assertNotIn("impact", text)
        self.assertNotIn("impact", json.loads(sorted((self.p.feature / "events").glob("*-cr_opened.json"))[-1]
                                              .read_text()))

    def test_a_cr_opened_before_snapshots_renders_as_before(self):
        f, _, cfg = self.feature()
        record(f, "cr_opened", "loc", cr="CR-001", title="Old change", reason="r", hours=2, affects=["AC-1.1"])
        record(f, "cr_opened", "loc", cr="CR-002", title="Odd", reason="r", hours=1, impact={"tasks": "x", "hours": "?"})
        f, st, cfg = self.feature()
        text = report.client_report(f, cfg)
        self.assertIn("| CR-001 | Old change | 2 | ", text)
        self.assertIn("| CR-002 | Odd | 1 | ", text)
        self.assertEqual([c["impact"] for c in dashboard.feature_data(f, cfg)["crs"]], [None, None])
        details = [i["detail"] for i in console.feature_inbox("r", f, st, cfg, None, []) if i["kind"] == "cr"]
        self.assertEqual(len(details), 2)
        self.assertFalse(any("impact" in d for d in details))


if __name__ == "__main__":
    unittest.main()
