"""Stack profiles: what a kind of repository needs — its test command, conventions, review
checklist, and extra done_when items for tasks that touch certain files.

A profile is a small TOML file, found in this order, so a repository can tune or add its own:

    .egd/profiles/<name>.toml   this repository's (commit it: the team shares it)
    ~/.egd/profiles/<name>.toml yours, for every repository on this machine
    built in                    python, node, react, go

`[stack] profiles = [...]` in .egd/config.toml says which ones a repository uses.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

BUILT_IN = Path(__file__).parent
SKIP = {"node_modules", ".git", ".venv", "venv", "dist", "build", ".next"}


def _dirs(root: Path) -> list[tuple[str, Path]]:
    return [("repository", root / ".egd" / "profiles"), ("yours", Path.home() / ".egd" / "profiles"),
            ("built in", BUILT_IN)]


def available(root: Path) -> dict[str, tuple[str, Path]]:
    """name → (where it comes from, file): the first place that has a name wins."""
    out: dict[str, tuple[str, Path]] = {}
    for where, folder in _dirs(root):
        for f in sorted(folder.glob("*.toml")) if folder.is_dir() else []:
            out.setdefault(f.stem, (where, f))
    return out


def load(root: Path, name: str) -> dict:
    found = available(root).get(name)
    if found is None:
        raise KeyError(name)
    try:
        doc = tomllib.loads(found[1].read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"profile {name} ({found[1]}): {exc}") from None
    return {**doc, "name": name, "source": found[0], "path": str(found[1])}


def _files(root: Path, name: str) -> list[Path]:
    """The file at the root, or one or two folders down (a monorepo's apps/web/package.json)."""
    out = [root / name] if (root / name).is_file() else []
    for pattern in (f"*/{name}", f"*/*/{name}"):
        out += [p for p in root.glob(pattern) if p.is_file() and not SKIP & set(p.relative_to(root).parts)]
    return out[:20]


def holds(root: Path, rule: str) -> bool:
    """`file` exists, or `file:text` — some such file contains the text."""
    name, _, text = rule.partition(":")
    for f in _files(root, name):
        if not text:
            return True
        try:
            if text in f.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def detect(root: Path) -> list[str]:
    """Profiles whose `detect` rules match this repository."""
    hits = []
    for name in available(root):
        try:
            rules = load(root, name).get("detect", [])
        except ValueError:
            continue
        if any(holds(root, r) for r in rules):
            hits.append(name)
    return hits


def active(root: Path, cfg: dict) -> list[dict]:
    """The profiles this repository names in `[stack] profiles` (unknown names are skipped)."""
    out = []
    for name in (cfg.get("stack", {}) or {}).get("profiles", []) or []:
        try:
            out.append(load(root, str(name)))
        except (KeyError, ValueError):
            continue
    return out


def test_command(root: Path, profile: dict) -> str | None:
    """The first `[[test]]` whose `when` rules hold here (no `when`: always)."""
    for t in profile.get("test", []):
        when = t.get("when", [])
        if not when or any(holds(root, r) for r in when):
            return t.get("command")
    return None


def covers(touch: str, glob: str) -> bool:
    """Whether a task touching `touch` (a path or glob) may change files that `glob` names."""
    from ..util import matches_any
    if matches_any(touch.replace("**", "x").replace("*", "x"), [glob]):   # src/migrations/** ~ **/migrations/**
        return True
    last = glob.rsplit("/", 1)[-1]
    if "*." in last:   # a kind of file (**/*.tsx): one could sit anywhere under the task's folder
        prefix = touch.split("*", 1)[0].rstrip("/")
        sample = (prefix + "/" if prefix and "*" in touch else "") + "x" + last[last.index("*.") + 1:]
        return "*" in touch and matches_any(sample, [touch]) and matches_any(sample, [glob])
    return False


def done_when_for(profiles: list[dict], touches: list[str]) -> list[str]:
    """Extra done_when items the profiles ask of a task touching these paths."""
    items: list[str] = []
    for p in profiles:
        for rule in p.get("done_when", []):
            globs = [str(g) for g in rule.get("touches", [])]
            if any(covers(t, g) for t in touches for g in globs):
                items += [i for i in rule.get("items", []) if i not in items]
    return items


TEMPLATE = '''title = "{title}"
# detect = ["package.json:react"]      # file, or file:text — when this profile fits a repository

conventions = """
- …
"""

review = [
  "…",
]

# [[test]]
# when = ["package.json:vitest"]
# command = "npx vitest run {{tests}}"

# [[done_when]]
# touches = ["**/*.tsx"]
# items = ["…"]
'''
