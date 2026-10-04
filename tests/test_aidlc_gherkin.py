import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import main  # noqa: F401  (puts src on the path)
from egd.model import load_feature
from egd.sources.aidlc_import import acceptance_criteria, import_repo, refresh_repo

FENCED = """## US-01 — A filter bar
### Acceptance criteria

**AC-01** — Sort is gone from the Model Report

```gherkin
Given I open the Model Report
When the filter bar renders
Then there is no ascending/descending toggle and no "Group by" select
And the Showing, Deal Type and Period controls are all still present
```
"""


class Gherkin(unittest.TestCase):
    def test_fenced_steps_keep_their_conjunctions_and_drop_the_fence(self):
        ac = acceptance_criteria(FENCED)[0]
        self.assertEqual(ac["title"], "Sort is gone from the Model Report")
        self.assertEqual(ac["then"], 'there is no ascending/descending toggle and no "Group by" select, '
                                     "and the Showing, Deal Type and Period controls are all still present")
        self.assertNotIn("```", ac["then"])

    def test_refresh_rewrites_imported_acs_and_their_fingerprints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            fdir = root / ".ai" / "features" / "f1"
            fdir.mkdir(parents=True)
            (fdir / "00-intent.md").write_text("# Intent — Weekly view\n\n## Problem\nx\n")
            (fdir / "02-requirements.md").write_text(FENCED)
            (fdir / ".aidlc-state.yaml").write_text("current_gate: G2\npassed: [G0, G1]\n")
            import_repo(root, "loc")
            target = root / ".egd" / "features" / "f1"
            # simulate the old importer's misreading
            plan = target / "plan.toml"
            plan.write_text(plan.read_text().replace(", and the Showing", " the Showing"))
            before = load_feature(root, target).scope_hash()
            for ev in (target / "events").glob("*.json"):
                e = json.loads(ev.read_text())
                if "scope" in e:
                    e["scope"] = before
                    ev.write_text(json.dumps(e))
            [r] = refresh_repo(root)
            self.assertEqual(r["changed"], 1)
            f = load_feature(root, target)
            self.assertIn(", and the Showing", f.acs[0]["then"])
            scopes = [json.loads(ev.read_text()).get("scope") for ev in (target / "events").glob("*.json")]
            self.assertIn(f.scope_hash(), scopes)
            self.assertNotIn(before, scopes)
