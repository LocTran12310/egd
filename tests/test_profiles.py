"""Stack profiles: found in the repository, then yours, then built in; detected at setup; asked of
tasks by the files they touch."""

import json
import tomllib
import unittest
from pathlib import Path

from helpers import Project

from egd import profiles


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class BuiltIn(unittest.TestCase):
    def test_every_built_in_profile_reads_and_says_what_it_asks(self):
        for name in ("python", "node", "react", "go"):
            p = profiles.load(Path("/nonexistent"), name)
            self.assertEqual(p["source"], "built in")
            self.assertTrue(p["title"] and p["detect"] and p["conventions"].strip() and p["review"] and p["test"], name)

    def test_a_task_covers_the_files_a_rule_names(self):
        self.assertTrue(profiles.covers("src/**", "**/*.tsx"))
        self.assertTrue(profiles.covers("src/migrations/**", "**/migrations/**"))
        self.assertFalse(profiles.covers("src/auth/**", "**/migrations/**"))
        self.assertFalse(profiles.covers("src/utils/a.ts", "**/*.tsx"))
        self.assertTrue(profiles.covers("src/App.tsx", "**/*.tsx"))


class InARepository(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_detected_in_a_monorepo_but_not_from_node_modules(self):
        write(self.p.root / "node_modules" / "x" / "package.json", '{"dependencies": {"react": "18"}}')
        self.assertNotIn("react", profiles.detect(self.p.root))
        write(self.p.root / "apps" / "web" / "package.json", '{"dependencies": {"react": "18"}, "devDependencies": {"vitest": "1"}}')
        self.assertIn("react", profiles.detect(self.p.root))
        self.assertEqual(profiles.test_command(self.p.root, profiles.load(self.p.root, "react")), "npx vitest run {tests}")

    def test_the_repositorys_own_profile_wins_and_can_be_new(self):
        write(self.p.root / ".egd" / "profiles" / "react.toml", 'title = "Our React"\ndetect = []\n')
        self.assertEqual(profiles.load(self.p.root, "react")["title"], "Our React")
        text = self.p.ok("profile", "--new", "shop", "--from", "react")
        self.assertIn("wrote .egd/profiles/shop.toml", text)
        self.assertEqual(profiles.available(self.p.root)["shop"][0], "repository")

    def test_use_names_the_stack_and_a_task_hears_what_it_asks(self):
        cfg_path = self.p.root / ".egd" / "config.toml"
        before = cfg_path.read_text()
        self.p.ok("profile", "--use", "react")
        after = cfg_path.read_text()
        self.assertEqual(tomllib.loads(after)["stack"]["profiles"], ["react"])
        self.assertIn('test_command = "sh {tests}"', after)   # one already set is kept
        self.assertTrue(after.startswith(before.rstrip("\n")))   # nothing else moves
        self.assertIn("did you mean 'react'?", self.p.egd("profile", "--use", "reactt")[1])
        self.assertIn("review for:", self.p.ok("profile"))
        text = self.p.ok("add", "task", "--slice", "S-1", "--title", "Page", "--estimate", "2", "--touches", "src/**",
                         "--done", "renders the total", "--by", "loc")
        self.assertIn("loading, empty and error states are shown", text)
        line = next(ln for ln in text.splitlines() if "egd set" in ln)
        value = line.split("'done_when=", 1)[1].rstrip("'")
        self.p.ok("set", "T-1.3", "done_when=" + value, "--by", "loc")
        plan = tomllib.loads((self.p.feature / "plan.toml").read_text())
        done = next(t for t in plan["task"] if t["id"] == "T-1.3")["done_when"]
        self.assertEqual(done, json.loads(value))   # items holding commas stay whole

    def test_setup_detects_the_stack_of_a_new_repository(self):
        import shutil
        shutil.rmtree(self.p.root / ".egd")
        write(self.p.root / "package.json", '{"dependencies": {"express": "4"}, "devDependencies": {"jest": "29"}}')
        self.assertIn("stack: node (detected)", self.p.ok("setup"))
        cfg = tomllib.loads((self.p.root / ".egd" / "config.toml").read_text())
        self.assertEqual((cfg["stack"]["profiles"], cfg["proof"]["test_command"]), (["node"], "npx jest {tests}"))


if __name__ == "__main__":
    unittest.main()
