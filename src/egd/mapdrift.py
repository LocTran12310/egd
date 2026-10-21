"""Has .egd/map.md drifted from the repository it describes?

A person signed the map at `frame`; the repository kept moving. This says where the map no longer
matches — never closing or reopening a gate, only asking someone to refresh it. Three questions:

- **Paths.** Inline `code` in the map that names a path — it holds a `/`, or ends in a file name
  with a known extension (`eslint.config.js`) — must still exist. A glob (`*`, `**`) must match
  something. Maps abbreviate, so the match is loose: a path counts as present at the repository
  root, under any folder of it (`repos/*` in a sentence about apps/api is apps/api/src/repos), and
  with folders between its parts (`input/core` is packages/input/src/core), and without its
  extension (`lib/common/http` is lib/common/http.ts). `{a,b}` lists are expanded.
  A path with no extension is only asked about when its first folder exists somewhere: otherwise
  it is as likely a package (`next/navigation`) or an action (`pnpm/action-setup`) as a folder.
  A missing path git ignores, or one under a `generated` folder, is something the repository makes
  when it runs (codegen output, `.env.local`, `.egd/.auth/…`), not drift.
- **Scripts.** `npm run x`, `pnpm run x`, `bun run x`, `yarn run x` — and `pnpm x` / `yarn x` when
  x is no built-in, no installed binary and no dependency — need a script x in the root
  package.json or in any workspace package's (a map often quotes a command run inside one).
  No package.json at the root: nothing is asked.
- **Touches.** A task of the feature touching `apps/…` while the map never says `apps`: the map
  does not cover where the work happens. Dot folders (.egd, .github) and `**/…` are left out.

Skipped on purpose, since a false alarm costs more than a missed one: URLs and anything with a
`:`, placeholders (`{feature}`, `<slug>`, `…`, `$VAR`), tokens with a space (commands), absolute,
`~/` and `../` paths, npm scopes (`@scope/name`), dotted names that are no file (`Date.now`), and
fenced code blocks. Folders that are build output or installs (node_modules, dist…) are not
searched, nor are other checkouts inside the repository (worktrees, submodules), but a path at the
root that exists still counts.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .model import Feature
from .util import egd_dir, git, strip_comments

SHOWN = 10  # lines per kind, then "… N more"
PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".turbo",
         ".wrangler", ".cache", "coverage", ".pytest_cache", ".mypy_cache", "target", "vendor"}
LIMIT = 200_000  # entries indexed; a repository bigger than this is only checked at its root
EXTENSIONS = {
    "js", "mjs", "cjs", "ts", "mts", "cts", "jsx", "tsx", "json", "jsonc", "toml", "yaml", "yml", "md", "mdx",
    "py", "go", "rs", "java", "kt", "rb", "php", "cs", "swift", "sql", "sh", "bash", "zsh", "ps1", "css", "scss",
    "sass", "less", "html", "astro", "vue", "svelte", "lock", "txt", "ini", "cfg", "conf", "xml", "gradle",
    "properties", "proto", "graphql", "gql", "prisma", "tf", "csv", "env", "example", "dockerfile",
}
PNPM_BUILTIN = {
    "install", "i", "add", "remove", "rm", "un", "uninstall", "update", "up", "upgrade", "link", "ln", "unlink",
    "import", "rebuild", "rb", "prune", "fetch", "exec", "dlx", "create", "init", "outdated", "list", "ls", "ll",
    "la", "why", "audit", "publish", "pack", "store", "env", "config", "c", "setup", "root", "bin", "licenses",
    "patch", "patch-commit", "patch-remove", "server", "deploy", "doctor", "recursive", "multi", "m", "help",
    "self-update", "approve-builds", "cat-file", "cat-index", "find-hash", "ignored-builds", "run", "x",
}
YARN_BUILTIN = {
    "install", "add", "remove", "upgrade", "up", "info", "init", "link", "unlink", "why", "workspace",
    "workspaces", "dlx", "exec", "config", "cache", "global", "set", "plugin", "npm", "pack", "publish",
    "outdated", "audit", "list", "bin", "node", "run", "version", "constraints", "dedupe", "explain", "help",
}
_CODE = re.compile(r"`([^`\n]+)`")
_FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
_RUNS = re.compile(r"^(npm|pnpm|yarn|bun)\s+(?:run\s+)?([^\s-][^\s]*)")


def _text(root: Path) -> str | None:
    path = egd_dir(root) / "map.md"
    try:
        return strip_comments(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _codes(text: str) -> list[str]:
    """Inline `code` outside fenced blocks, in order, once each."""
    return list(dict.fromkeys(m.group(1).strip() for m in _CODE.finditer(_FENCE.sub("", text))))


def _expand(token: str) -> list[str]:
    """`apps/{site,api}` → apps/site, apps/api — one level of braces, any number of groups."""
    m = re.search(r"\{([^{}]*,[^{}]*)\}", token)
    if not m:
        return [token]
    return [x for part in m.group(1).split(",") for x in _expand(token[:m.start()] + part.strip() + token[m.end():])]


def _pathlike(token: str) -> bool:
    if any(c in token for c in " \t:<>…$\"'|;()[]=,!?#&") or "..." in token:
        return False  # commands, URLs, placeholders, prose
    if re.search(r"\{[^{}]*\}", token):  # braces left after expansion are a placeholder: {feature}
        return False
    if token.startswith(("/", "~", "@", "-")) or ".." in token.split("/"):
        return False  # absolute, home, npm scope, a flag, outside the repository
    if "/" in token.rstrip("/"):
        return bool(re.fullmatch(r"[\w.*+@-]+(/[\w.*+@-]+)*/?", token))
    name, dot, ext = token.rpartition(".")
    return bool(dot and name and re.fullmatch(r"[\w*+-][\w.*+-]*", name) and ext.lower() in EXTENSIONS)


def _index(root: Path) -> list[str] | None:
    """Every file and folder (posix, relative); None if huge. Installs, build output and other
    checkouts inside this one (a worktree or submodule: a folder with its own .git) are left out."""
    out: list[str] = []
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in PRUNE and not os.path.exists(os.path.join(here, d, ".git"))]
        rel = Path(here).relative_to(root).as_posix()
        base = "" if rel == "." else rel + "/"
        out += [base + n for n in (*dirs, *files)]
        if len(out) > LIMIT:
            return None
    return out


def _part(part: str) -> str:
    if part == "**":
        return ".*"
    return "".join("[^/]*" if c == "*" else re.escape(c) for c in part)


def _loose(path: str, ext: bool) -> re.Pattern:
    """`a/b*/c` as found anywhere, with any folders between its parts — and, for a path with no
    extension, with one (`http` → http.ts): loose on purpose."""
    parts = [_part(p) for p in path.split("/") if p]
    return re.compile(r"(?:^|.*/)" + r"/(?:.*/)?".join(parts) + ("" if ext else r"(?:\.[\w]+)?") + "$")


def _has_ext(path: str) -> bool:
    name, dot, ext = path.rsplit("/", 1)[-1].rpartition(".")
    return bool(dot and name and ext.lower() in EXTENSIONS)


def _ignored(root: Path, paths: list[str]) -> set[str]:
    """Which of `paths` git ignores — asked once, for all of them."""
    if not paths:
        return set()
    import subprocess  # as util.git does: only when there is something to ask
    try:
        out = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=root, input="\n".join(paths),
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return set(out.stdout.splitlines())


GENERATED = {"generated", "__generated__", "gen"}


def missing_paths(root: Path, text: str, index: list[str] | None) -> list[str]:
    """Paths the map names that are gone. `index` is `_index(root)`; None (too big): nothing said."""
    if not index:
        return []
    folders = {part for p in index for part in p.split("/")[:-1]} | {
        p for p in index if "/" not in p and (root / p).is_dir()}
    gone: list[str] = []
    for token in _codes(text):
        for path in _expand(token):
            if not _pathlike(path):
                continue
            path = path.rstrip("/")
            if path in gone or ("*" not in path and (root / path).exists()) or GENERATED & set(path.split("/")):
                continue
            ext = _has_ext(path)
            if not ext and "/" in path and path.split("/", 1)[0] not in folders:
                continue  # more likely a package, an action or a URL path than a folder
            rx = _loose(path, ext)
            if not any(rx.match(p) for p in index):
                gone.append(path)
    ignored = _ignored(root, [p for p in gone if "*" not in p])
    return [f"`{p}` " + ("matches no file" if "*" in p else "no longer exists") for p in gone if p not in ignored]


def _package(path: Path) -> dict:
    try:
        pkg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return pkg if isinstance(pkg, dict) else {}


def missing_scripts(root: Path, text: str, index: list[str] | None) -> list[str]:
    """`pnpm x`-style commands the map names whose script no package.json has."""
    if not (root / "package.json").is_file():
        return []
    scripts: set[str] = set()
    deps: set[str] = set()
    for rel in ["package.json", *[p for p in index or [] if p.endswith("/package.json")]]:
        pkg = _package(root / rel)
        scripts |= set(pkg["scripts"]) if isinstance(pkg.get("scripts"), dict) else set()
        deps |= {k for key in ("dependencies", "devDependencies") if isinstance(pkg.get(key), dict) for k in pkg[key]}
    out = []
    for token in _codes(text):
        m = _RUNS.match(token)
        if not m:
            continue
        tool, name = m.group(1), m.group(2)
        explicit = re.match(r"^\S+\s+run\s", token) is not None
        if tool in ("npm", "bun") and not explicit:
            continue  # `npm test`, `bun x`: built-ins or binaries
        if not explicit and (name in (PNPM_BUILTIN if tool == "pnpm" else YARN_BUILTIN) or name in deps
                             or (root / "node_modules" / ".bin" / name).exists()):
            continue
        if name not in scripts:
            out.append(f"`{tool} {'run ' if explicit else ''}{name}` — package.json has no \"{name}\" script")
    return list(dict.fromkeys(out))


def unmapped_touches(f: Feature, text: str) -> list[str]:
    where: dict[str, list[str]] = {}
    for t in f.tasks:
        for pattern in t.get("touches", []):
            pattern = str(pattern).strip()
            pattern = pattern[2:] if pattern.startswith("./") else pattern
            if not pattern or pattern.startswith("*") or "/" not in pattern:
                continue  # anywhere (**/…), or a file at the root
            top = pattern.split("/", 1)[0]
            if "*" in top or "{" in top or top.startswith("."):
                continue  # a glob, or a dot folder: .egd, .github, .claude
            where.setdefault(top, [])
            if t["id"] not in where[top]:
                where[top].append(t["id"])
    return [f"map.md never mentions {top}/ — touched by {', '.join(ids)}"
            for top, ids in where.items()
            if not re.search(r"(?<![\w.-])" + re.escape(top) + r"(?![\w-])", text)]


def _capped(items: list[str]) -> list[str]:
    return items[:SHOWN] + ([f"… {len(items) - SHOWN} more"] if len(items) > SHOWN else [])


def notes(f: Feature) -> list[str]:
    """What `egd verify` and `egd check frame` say: paths, scripts, touches, each kind capped at
    SHOWN lines. [] when there is no map."""
    text = _text(f.root)
    if not text:
        return []
    index = _index(f.root) if _codes(text) else None  # one walk of the repository for both
    return (_capped([f"map.md: {p}" for p in missing_paths(f.root, text, index)])
            + _capped([f"map.md: {s}" for s in missing_scripts(f.root, text, index)])
            + _capped(unmapped_touches(f, text)))
