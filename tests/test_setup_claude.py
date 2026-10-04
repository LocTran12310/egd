import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401

from egd.templates import setup_claude


class SetupClaude(unittest.TestCase):
    def test_merges_into_existing_settings(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]},
                                                                       "enabledPlugins": {"other@x": True}}))
            setup_claude(root)
            data = json.loads((root / ".claude" / "settings.json").read_text())
            self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
            self.assertEqual(data["enabledPlugins"], {"other@x": True, "egd@egd": True})
            self.assertEqual(data["extraKnownMarketplaces"]["egd"]["source"],
                             {"source": "github", "repo": "LocTran12310/egd"})

    def test_refuses_broken_json(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.json").write_text("{nope")
            with self.assertRaises(ValueError):
                setup_claude(root)


if __name__ == "__main__":
    unittest.main()
