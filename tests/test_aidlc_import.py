"""`egd import aidlc` on a synthetic ai-dlc repository."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts src/ on sys.path)
from helpers import Project  # noqa: F401
from test_aidlc_source import build

from egd.events import replay
from egd.graph import validate
from egd.model import all_features
from egd.sources.aidlc_import import acceptance_criteria, import_repo
from egd.util import load_config


class Import(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        build(self.root)
        self.before = {p: p.read_bytes() for p in (self.root / ".ai").rglob("*") if p.is_file()}

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_keeps_ai_untouched_and_replays_history(self):
        report = import_repo(self.root, "Linh")
        self.assertEqual(len(report["features"]), 1)
        after = {p: p.read_bytes() for p in (self.root / ".ai").rglob("*") if p.is_file()}
        self.assertEqual(self.before, after)
        f = all_features(self.root)[0]
        self.assertEqual(f.title, "Checkout totals")
        self.assertEqual([a["id"] for a in f.acs], ["AC-01", "AC-02"])
        self.assertEqual([s["id"] for s in f.slices], ["UOW-01"])
        self.assertEqual(len(f.tasks), 4)
        st = replay(f)
        self.assertEqual(list(st.gates), ["frame", "clarify", "design", "slice"])   # ai-dlc G0..G3
        self.assertEqual(st.gates["frame"]["by"], "Lan")
        self.assertEqual({t: s.status for t, s in st.tasks.items()},
                         {"T-01-01": "done", "T-01-02": "review"})
        self.assertEqual(st.tasks["T-01-02"].submitted_by, "Minh")
        errors, _ = validate(f, load_config(self.root))
        self.assertEqual(errors, [])
        self.assertTrue(all(e.get("imported") == "ai-dlc" for e in st.events))
        self.assertIn("reviewed_by:", (self.root / ".egd" / "map.md").read_text())

    def test_import_is_idempotent(self):
        import_repo(self.root, "Linh")
        again = import_repo(self.root, "Linh")
        self.assertEqual(again["features"][0].get("skipped"), "already imported")

    def test_acceptance_criteria_formats(self):
        bold = "## US-01\n**AC-01** — totals\nGiven a cart\nWhen checking out\nThen it totals 300\n"
        table = "| ID | Given | When | Then |\n|---|---|---|---|\n| AC-02 | a cart | paying | it works |\n"
        listed = "- **AC-03** — Given a lead, when the page loads, then the button\n  is hidden.\n"
        plain = "| AC | Text |\n|---|---|\n| AC-04 | The report shows totals |\n"
        acs = {a["id"]: a for a in acceptance_criteria(bold + "\n" + table + "\n" + listed + "\n" + plain)}
        self.assertEqual(acs["AC-01"]["then"], "it totals 300")
        self.assertEqual((acs["AC-02"]["given"], acs["AC-02"]["then"]), ("a cart", "it works"))
        self.assertEqual(acs["AC-03"]["when"], "the page loads")
        self.assertEqual(acs["AC-03"]["then"], "the button is hidden")
        self.assertEqual(acs["AC-04"]["then"], "The report shows totals")


if __name__ == "__main__":
    unittest.main()
