import json
import unittest

from helpers import Project

from egd import mapdrift
from egd.model import load_feature

MAP = """# Repository map

reviewed_by: loc

## Stack
Plain text files and shell tests in `src/` and `tests/`.

## Layout
- `src/app.txt`, `app.txt`, `tests/*.sh`, `ok.sh` — here.
- `src/gone/` and `docs/old.md` — moved away. `tests/*.py` — never written.
- Ignored: `https://example.com/a/b`, `{feature}/plan.toml`, `<slug>/notes.md`, `/etc/hosts`, `~/x/y.md`,
  `../other/x.md`, `@scope/pkg`, `Date.now`, `next/navigation`, `src/generated/api.ts`, `secret.local.json`,
  `rm -rf src/nothing/`, `{a,b}`.
- Lists: `src/{app.txt,lost.txt}`.

## Run, test, deploy
`pnpm build`, `pnpm db:seed`, `npm run lint`, `pnpm install`, `pnpm vitest run x`, `pnpm exec tsc`,
`npm test`, `yarn run start`, `pnpm --filter web dev`.

```sh
pnpm fenced-away
```
"""

PACKAGE = {"scripts": {"build": "tsc", "db:migrate": "x"}, "devDependencies": {"vitest": "1"}}


class MapDrift(unittest.TestCase):
    """Where .egd/map.md no longer matches the repository: said in verify, never a closed gate."""

    def setUp(self):
        self.p = Project()
        (self.p.root / ".egd" / "map.md").write_text(MAP)
        (self.p.root / ".gitignore").write_text("secret.local.json\n")
        (self.p.root / "package.json").write_text(json.dumps(PACKAGE))
        self.p.commit("map")

    def tearDown(self):
        self.p.close()

    def notes(self):
        return mapdrift.notes(load_feature(self.p.root, self.p.feature))

    def test_gone_paths_and_empty_globs_are_named_and_the_rest_is_left_alone(self):
        paths = [n for n in self.notes() if "`" in n and "script" not in n]
        self.assertEqual(paths, ["map.md: `src/gone` no longer exists", "map.md: `docs/old.md` no longer exists",
                                 "map.md: `tests/*.py` matches no file", "map.md: `src/lost.txt` no longer exists"])

    def test_missing_scripts_are_named_and_built_ins_binaries_and_flags_are_not(self):
        scripts = [n for n in self.notes() if "script" in n]
        self.assertEqual(scripts, ['map.md: `pnpm db:seed` — package.json has no "db:seed" script',
                                   'map.md: `npm run lint` — package.json has no "lint" script',
                                   'map.md: `yarn run start` — package.json has no "start" script'])
        (self.p.root / "package.json").unlink()
        self.assertFalse([n for n in self.notes() if "script" in n])  # no package.json: not asked

    def test_a_workspace_package_script_counts(self):
        (self.p.root / "web").mkdir()
        (self.p.root / "web" / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}))
        self.assertNotIn("lint", " ".join(self.notes()))

    def test_a_task_touching_a_folder_the_map_never_names(self):
        plan = self.p.feature / "plan.toml"
        plan.write_text(plan.read_text().replace('touches = ["src/**"]\ntests = ["tests/ok.sh"]\ndone_when = ["returns 201"]',
                                                 'touches = ["src/**", "apps/web/**", ".github/x.yml", "**/y", "README.md"]\n'
                                                 'tests = ["tests/ok.sh"]\ndone_when = ["returns 201"]'))
        self.assertIn("map.md never mentions apps/ — touched by T-1.1", self.notes())
        self.assertEqual(sum("never mentions" in n for n in self.notes()), 1)

    def test_long_lists_are_cut(self):
        (self.p.root / ".egd" / "map.md").write_text("## Layout\n`src/`: " + " ".join(f"`gone/f{i}.md`" for i in range(13)))
        notes = self.notes()
        self.assertEqual(len(notes), 11)
        self.assertEqual(notes[-1], "… 3 more")

    def test_no_map_says_nothing_more(self):
        (self.p.root / ".egd" / "map.md").unlink()
        self.assertEqual(self.notes(), [])
        self.assertNotIn("⚠️ map", self.p.egd("verify")[1])

    def test_verify_says_it_once_and_no_gate_closes(self):
        text = self.p.ok("verify")
        self.assertIn("⚠️ map", text)
        self.assertEqual(text.count("`src/gone` no longer exists"), 1)  # not again under gate frame
        self.assertIn("ready: a person may now run `egd pass frame`", text)
        check = self.p.ok("check", "frame")
        self.assertIn("frame: open", check)
        self.assertIn("warning: map.md: `src/gone` no longer exists", check)
        self.assertIn("Fix: refresh .egd/map.md", check)
        self.p.pass_through("frame", "clarify", "design", "slice")
        self.assertIn("⚠️ map", self.p.egd("verify")[1])  # still said once frame has passed


if __name__ == "__main__":
    unittest.main()
