"""The read-only ai-dlc adapter, on a synthetic repository."""

import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts src/ on sys.path)
from egd.console import _act, _item, is_repo, perform
from egd.sources import aidlc
from egd.util import Refused


def build(root: Path):
    f = root / ".ai" / "features" / "2026090101-checkout"
    u = f / "04-units-of-work" / "UOW-01-pending-order"
    (u / "tickets").mkdir(parents=True)
    (root / ".ai" / "architecture.md").write_text("# Map\n\nverified_by:\n")
    (f / ".aidlc-state.yaml").write_text("feature: checkout\ncurrent_gate: G3\n")
    (f / "00-intent.md").write_text("# Checkout totals\n\n## Problem\nWrong totals.\n")
    (f / "01-assumptions.md").write_text(
        "| ID | Assumption | Confidence | Blocking | Blast radius if wrong | Status | Resolution |\n"
        "|----|----|----|----|----|----|----|\n"
        "| A-01 | Prices are VND | high | yes | all | pending | — |\n"
        "| A-02 | CSV only | low | no | small | confirmed | PO said so |\n")
    (f / "02-requirements.md").write_text("**AC-01** — Total is 300,000\n**AC-02** — Empty cart refused\n")
    (u / "uow.md").write_text("---\nid: UOW-01\ntitle: Pending order\nverifies: [AC-01, AC-02]\n---\n\n"
                              "## Demo script\n1. Check out\n")
    for tid, status, deps in (("T-01-01", "done", "[]"), ("T-01-02", "review", "[T-01-01]"),
                              ("T-01-03", "todo", "[T-01-02]"), ("T-01-04", "todo", "[]")):
        (u / "tickets" / f"{tid}.md").write_text(
            f"---\nid: {tid}\nuow: UOW-01\ntitle: Task {tid}\nestimate: 2h\nstatus: {status}\n"
            f"depends_on: {deps}\ntouches:\n  - src/a.ts   # new\n---\n")
    events = [{"action": "passed", "gate": "G0", "at": "2026-09-01T10:00:00", "by": "Lan"},
              {"action": "ticket in_progress", "ticket": "T-01-02", "at": "2026-09-01T11:00:00", "by": "Minh"},
              {"action": "ticket review", "ticket": "T-01-02", "at": "2026-09-01T12:00:00", "by": "Minh"}]
    (f / "history.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")


class AidlcSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_plan_as_console_features(self):
        self.assertTrue(is_repo(self.root))
        snap = aidlc.snapshot("shop", self.root, _item, _act)
        self.assertTrue(snap["readonly"])
        f = snap["features"][0]
        self.assertEqual(f["title"], "Checkout totals")
        self.assertEqual(f["gate"], "G4 build")
        self.assertEqual([g["passed"] for g in f["gates"]], [True, True, True, True, False, False])
        cols = {t["id"]: t["col"] for t in f["tasks"]}
        self.assertEqual(cols, {"T-01-01": "done", "T-01-02": "review", "T-01-03": "todo", "T-01-04": "ready"})
        self.assertEqual(next(t for t in f["tasks"] if t["id"] == "T-01-02")["owner"], "Minh")
        self.assertEqual(f["progress"], 25)
        self.assertEqual([a["id"] for a in f["acs"]], ["AC-01", "AC-02"])
        self.assertEqual([(a["id"], a["status"]) for a in f["assumptions"]], [("A-01", "open"), ("A-02", "confirmed")])
        kinds = sorted(i["kind"] for i in snap["inbox"])
        self.assertEqual(kinds, ["assumption", "map", "review"])
        self.assertTrue(all(not i["actions"] for i in snap["inbox"]))

    def test_actions_are_refused(self):
        with self.assertRaises(Refused):
            perform(self.root, "loc", {"action": "accept", "target": "T-01-02"})


if __name__ == "__main__":
    unittest.main()
