"""Small shared helpers: paths, config, git, secrets, time."""

from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import os
import re
import tomllib
from _thread import get_ident
from pathlib import Path

EGD_DIR = ".egd"
FEATURES = "features"


class Refused(Exception):
    """A transition or gate the rules do not allow. The message says why."""

    def __init__(self, message: str, problems: list[str] | None = None,
                 code: str | None = None, files: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or []
        self.code = code      # machine-readable kind, e.g. "drift", so a UI can offer the way out
        self.files = files or []


class UsageError(Exception):
    pass


# --------------------------------------------------------------------- paths

def find_root(start: Path | None = None) -> Path:
    """Walk upward until a directory holding `.egd/` is found."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / EGD_DIR).is_dir():
            return candidate
    raise UsageError("no .egd/ directory found here or above — run `egd setup` first")


def egd_dir(root: Path) -> Path:
    return root / EGD_DIR


def features_dir(root: Path) -> Path:
    return root / EGD_DIR / FEATURES


# --------------------------------------------------------------------- toml

def load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise UsageError(f"{path}: invalid TOML — {exc}") from exc


DEFAULT_CONFIG = {
    "limits": {"task_hours": 4, "slice_hours": 16},
    "proof": {"timeout": 600, "output_limit": 6000},
    "roles": {
        "gate": ["pm", "lead"],
        "review": ["dev", "qa", "lead", "pm"],
        "uat": ["client", "po"],
        "cr_approve": ["client", "po"],
    },
    "env": {},
    "ui": {},
    "github": {},
}


def load_config(root: Path) -> dict:
    raw = load_toml(egd_dir(root) / "config.toml")
    merged: dict = {}
    for key, default in DEFAULT_CONFIG.items():
        value = raw.get(key, {})
        merged[key] = {**default, **value} if isinstance(default, dict) else value
    for key, value in raw.items():
        merged.setdefault(key, value)
    return merged


# --------------------------------------------------------------------- secrets

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _secrets_file(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    path = egd_dir(root) / "secrets.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def load_secrets(root: Path) -> dict[str, str]:
    """`.egd/secrets.env` (never committed) layered under the process env."""
    values = _secrets_file(root)
    values.update({k: v for k, v in os.environ.items() if k not in values or v})
    return values


def missing_vars(text: str, secrets: dict[str, str]) -> list[str]:
    return [name for name in _VAR.findall(text) if not secrets.get(name)]


def interpolate(value, secrets: dict[str, str]):
    """Replace ${VAR} recursively inside strings, lists and dicts."""
    if isinstance(value, str):
        return _VAR.sub(lambda m: secrets.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [interpolate(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: interpolate(v, secrets) for k, v in value.items()}
    return value


def secret_values(text_sources, secrets: dict[str, str]) -> set[str]:
    """Every value substituted from ${VAR} in the given sources — the redaction set."""
    names: set[str] = set()
    stack = [text_sources]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            names.update(_VAR.findall(item))
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return {secrets[n] for n in names if secrets.get(n) and len(secrets[n]) >= 3}


UNREFERENCED_MIN = 6  # shorter secrets.env values nobody references are words, not secrets


def known_secrets(root: Path, *sources) -> set[str]:
    """Values worth redacting from output: every ${VAR} the sources (config, proofs) reference,
    at any length from 3, plus the secrets.env values nothing references, from 6 characters —
    `APP=app` or `DEBUG=true` must not turn `happy` into `h***y`. Not the whole environment —
    PATH and HOME are not secrets, and hiding them would only garble the output."""
    secrets = load_secrets(root)
    named = {secrets[k] for k in _secrets_file(root) if len(secrets.get(k) or "") >= UNREFERENCED_MIN}
    return named | secret_values(list(sources), secrets)


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file and rename, so a crash never leaves half a file behind."""
    tmp = path.with_name(f".{path.name}.{os.urandom(3).hex()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------- time

def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(ts: _dt.datetime | None = None) -> str:
    return (ts or now()).isoformat(timespec="seconds")


def parse_iso(text: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(text)


def hours_between(a: str, b: str) -> float:
    return round((parse_iso(b) - parse_iso(a)).total_seconds() / 3600, 2)


# --------------------------------------------------------------------- git

def git(root: Path, *args: str, check: bool = False) -> str | None:
    import subprocess  # lazily: most commands never reach git, and it is the costliest import
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        if check:
            raise UsageError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
        return None
    return out.stdout


def tracked_but_ignored(root: Path) -> list[str]:
    """Files under .egd/ git tracks although its ignore rules say not to: all of them when .egd/
    itself is kept local (`egd setup --local`), or ones .egd/.gitignore keeps out (secrets.env)."""
    out = git(root, "-c", "core.quotePath=false", "ls-files", "--cached", "--ignored", "--exclude-standard",
              "--", EGD_DIR)
    return (out or "").splitlines()


def _memo(key: tuple, compute):
    """Inside `git_memo`, ask git once per key; outside it, every time."""
    slot = _MEMO.get(get_ident())
    if slot is None:
        return compute()
    if key not in slot[1]:
        slot[1][key] = compute()
    return slot[1][key]


def head_sha(root: Path) -> str | None:
    def ask():
        out = git(root, "rev-parse", "HEAD")
        return out.strip() if out else None
    return _memo(("head", str(root)), ask)


def local_store(root: Path) -> Path | None:
    """This clone's own notes, never committed: inside .git (per worktree)."""
    rel = (git(root, "rev-parse", "--git-path", "egd-local.json") or "").strip()
    return (root / rel) if rel else None


def local_record(root: Path, section: str, key: str, value: dict | None = None) -> dict | None:
    """Read (value None) or write one entry of the clone's notes: content hashes of uncommitted
    files, kept off the committed trail — a hash there would let anyone confirm a guess at a
    local file's content. At most 300 entries per section, oldest dropped."""
    path = local_store(root)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    part = data.setdefault(section, {})
    if value is None:
        return part.get(key)
    part[key] = value
    for old in list(part)[:-300]:
        del part[old]
    write_atomic(path, json.dumps(data, ensure_ascii=False))
    return value


class git_memo:
    """`with git_memo():` — inside, `source_changed_since` asks git once per (root, sha).

    Every proof×env of a report asks about the same one or two commits, so one view costs
    four git calls, not four per proof. Scoped (per thread, nestable) rather than process-wide:
    a long-running console must still see code that moved between two polls.
    Also a decorator: `@git_memo()`.
    """

    def __call__(self, fn):
        def wrapped(*args, **kwargs):
            with git_memo():
                return fn(*args, **kwargs)
        wrapped.__name__, wrapped.__doc__ = fn.__name__, fn.__doc__
        return wrapped

    def __enter__(self):
        _MEMO.setdefault(get_ident(), [0, {}])[0] += 1
        return self

    def __exit__(self, *exc):
        slot = _MEMO[get_ident()]
        slot[0] -= 1
        if not slot[0]:
            del _MEMO[get_ident()]


_MEMO: dict[int, list] = {}  # thread → [depth, {(root, sha): answer}]


def clear_git_cache() -> None:
    """Something egd ran (a proof, a test command) may have written files: ask git afresh."""
    slot = _MEMO.get(get_ident())
    if slot:
        slot[1].clear()


def source_changed_since(root: Path, sha: str | None, footprint: list[str] | None = None) -> bool | None:
    """True when code under the EGD root (outside .egd/) differs from `sha`: any commit since, or
    an uncommitted change inside the feature's `footprint` (every uncommitted change, without one).
    In a monorepo, changes outside the folder holding .egd/ are not watched.

    None when it cannot be told (no git, unknown sha). Plan and trail edits do not count:
    recording a proof must not make that same proof stale. Nor does a local edit far from the
    feature — a tweaked .gitignore, an editor config — that nobody will commit with it.
    """
    head = head_sha(root)
    if head is None:
        return None
    if not sha or not _memo(("commit?", str(root), sha),
                            lambda: git(root, "cat-file", "-e", f"{sha}^{{commit}}") is not None):
        return True  # a commit this clone does not have proves nothing about this code
    if sha != head:
        committed = _memo(("committed", str(root), sha), lambda: git(
            root, "diff", "--name-only", sha, "HEAD", "--", ".", f":(exclude){EGD_DIR}"))
        if committed is None:
            return None
        if committed.strip():
            return True
    return bool(split_local(dirty_paths(root), footprint)[0])


def split_local(paths: list[str], footprint: list[str] | None) -> tuple[list[str], list[str]]:
    """(changes that count, local changes outside the feature's footprint that do not)."""
    if not footprint:
        return paths, []
    return ([p for p in paths if matches_any(p, footprint)], [p for p in paths if not matches_any(p, footprint)])


def blob_hashes(root: Path, paths: list[str]) -> dict[str, str]:
    """Each path's content hash as it is on disk now ("" when it is gone)."""
    present = [p for p in paths if (root / p).is_file()]
    found = (git(root, "hash-object", "--", *present) or "").split() if present else []
    hashes = dict(zip(present, found)) if len(found) == len(present) else {}
    return {p: hashes.get(p, "") for p in paths}


# Never a change anyone made: caches and OS litter, even where no .gitignore says so.
JUNK_DIRS = {"__pycache__", ".pytest_cache", "node_modules"}
JUNK_FILES = {".DS_Store"}
JUNK_SUFFIXES = (".pyc",)


def is_junk(path: str) -> bool:
    *dirs, name = path.split("/")
    return name in JUNK_FILES or name.endswith(JUNK_SUFFIXES) or any(d in JUNK_DIRS for d in dirs)


def _names(root: Path, *args: str) -> list[str]:
    # -z: no quoting of unusual paths
    out = git(root, "-c", "core.quotePath=false", *args) or ""
    return [n.strip() for n in out.split("\0") if n.strip()]


def untracked(root: Path) -> list[str]:
    """New files outside .egd/ that count: not ignored by the repo's .gitignore, not junk."""
    return [n for n in _names(root, "ls-files", "-z", "--others", "--exclude-standard")
            if not n.startswith(EGD_DIR + "/") and not is_junk(n)]


def dirty_paths(root: Path) -> list[str]:
    """Uncommitted changes outside .egd/ that make evidence stale, sorted; [] without git."""
    def ask():
        if head_sha(root) is None:
            return []
        tracked = _names(root, "diff", "-z", "--name-only", "--relative", "HEAD")
        return sorted({n for n in tracked if not n.startswith(EGD_DIR + "/")} | set(untracked(root)))
    return list(_memo(("dirty", str(root)), ask))


def files_for_task(root: Path, task_id: str, since: str | None,
                   before: dict[str, str] | None = None) -> tuple[set[str], bool, set[str]]:
    """Files a task changed: commits whose message names it, plus the dirty tree — kept only
    where the tree differs from where the task started (`since`), so a file added and removed
    again under the task is no change at all.

    Attribution by commit message keeps teammates' merged work out of a task's footprint; the
    net diff keeps out what the task undid. Without a start commit it is attribution alone.
    Untracked files count only when not ignored and not junk (`__pycache__/`, `*.pyc`, …).
    `before` (path → content hash, recorded at `egd start`): uncommitted changes that were
    already there and are still exactly as they were are someone's local setup, not the task's.
    Returns (files, any_commit_found, unnamed): `unnamed` are files that your own commits not
    naming the task changed (merges aside) and that still differ — maybe this task's work.
    """
    files: set[str] = set()
    unnamed: set[str] = set()
    pattern = re.compile(rf"(?<![\w.-]){re.escape(task_id)}(?!\.?\d)")
    found = False
    if since:
        # one `git log` for every commit: \x1e message \x1f \0, then its files NUL-separated;
        # --relative: paths from the egd root, not the repo root
        me = (git(root, "config", "user.email") or "").strip().lower()
        log = git(root, "-c", "core.quotePath=false", "log", "-z", "--cc", "--name-only", "--relative",
                  "--format=%x1e%P%x1d%ae%x1d%B%x1f", f"{since}..HEAD") or ""
        for entry in log.split("\x1e"):
            head, sep, rest = entry.partition("\x1f")
            parents, _, head = head.partition("\x1d")
            author, _, message = head.partition("\x1d")
            changed = {n.lstrip("\n").strip() for n in rest.split("\0") if n.strip()}
            if sep and pattern.search(message):
                found = True
                files |= changed
            elif sep and len(parents.split()) < 2 and me and author.strip().lower() == me:
                unnamed |= changed  # your own commit that forgot the id; a teammate's is theirs
    dirty: set[str] = set()
    if head_sha(root):
        dirty.update(_names(root, "diff", "-z", "--name-only", "--relative", "HEAD"))
    new = untracked(root)
    dirty.update(new)
    if before:
        same = [p for p in dirty if p in before and p not in files]
        now = blob_hashes(root, same)
        dirty -= {p for p in same if now[p] == before[p]}
    files |= dirty
    if since and git(root, "cat-file", "-e", f"{since}^{{commit}}") is not None:
        net = set(_names(root, "diff", "-z", "--name-only", "--relative", since)) | set(new)
        files &= net
        unnamed &= net
    keep = lambda paths: {p for p in paths if not p.startswith(EGD_DIR + "/")}  # noqa: E731
    return keep(files), found, keep(unnamed - files)


def matches_any(path: str, patterns: list[str]) -> bool:
    """Globs as people write them: `*` and `**` cross folders, and `**/` may also be no folder
    at all (`src/**/*.py` covers `src/a.py`); a plain folder name covers what is inside it."""
    for pattern in patterns:
        pattern = str(pattern).strip().removeprefix("./")
        if not pattern:
            continue
        if fnmatch.fnmatch(path, pattern) or ("**/" in pattern and fnmatch.fnmatch(
                path, pattern.replace("/**/", "/").removeprefix("**/"))):
            return True
        if path == pattern or path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


# --------------------------------------------------------------------- text

def md_cell(text) -> str:
    """Text safe inside a Markdown table cell: a `|` or a line break would end the cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def plural(n: int, word: str, many: str | None = None) -> str:
    """`1 task`, `2 tasks`, `0 tasks`; `many` for irregular words (`1 proof run`, `entries`)."""
    return f"{n} {word if n == 1 else many or word + 's'}"


def norm_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug:
        raise UsageError("name must contain letters or digits")
    return slug


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def md_sections(text: str) -> dict[str, str]:
    """`## Heading` → body, comments removed, keys lower-cased."""
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in strip_comments(text).splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current, buf = m.group(1).strip().lower(), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def is_blank_template(text: str) -> bool:
    """True when every `##` section is empty once comments are removed."""
    sections = md_sections(text)
    return bool(sections) and not any(body.strip() for body in sections.values())


def has_todo(text: str) -> bool:
    return bool(re.search(r"\bTODO\b|\bTBD\b", strip_comments(text)))
