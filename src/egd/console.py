"""EGD Console — one web page over every repository you work in.

    egd console                      serve on http://127.0.0.1:8780
    egd console add ~/code/shop      register a repository (or scan a folder with --scan)
    egd console list | remove <name>

The console reads each repository's `.egd/` exactly as the CLI does and computes
an inbox of decisions only a person can make. Actions taken in the browser are the
same transitions the CLI performs, signed with the console user's name, and land
as ordinary event files in that repository (commit them as usual).
"""

from __future__ import annotations

import functools
import gzip
import hashlib
import http.server
from contextlib import nullcontext
import json
import os
import re
import secrets as _secrets
import socket
import tomllib
import threading
import time
import urllib.parse
from pathlib import Path

from . import gates, tasks, tracking
from .sources import aidlc
from .dashboard import LOCAL_HOSTS, SERVABLE, SIGNATURES, feature_data, feature_summary, namer, safe_runs
from .events import replay
from .graph import validate
from .model import all_features
from .planfile import set_fields, toml_value
from .team import Team
from .util import (EGD_DIR, git_memo, plural, Refused, UsageError, features_dir, git, is_blank_template, iso, load_config,
                   load_toml, missing_vars, write_atomic)

CONFIG_ENV = "EGD_CONSOLE_CONFIG"
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next",
             "vendor", "target", ".idea", ".cache"}


# ------------------------------------------------------------------ registry

def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV) or Path.home() / ".egd" / "console.toml").expanduser()


def _raw_registry() -> dict:
    """console.toml as written. A file that is not valid TOML is refused with a message a person
    can act on — the server shows it on the page instead of failing every request."""
    path = config_path()
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise UsageError(f"console.toml is invalid: {exc} — fix or delete {path}") from exc
    except OSError as exc:
        raise UsageError(f"console.toml cannot be read: {exc.strerror} ({path})") from exc


_GIT_NAME: list[str] = []


def _git_name() -> str:
    """Your global `git config user.name`, read once — the console signs as you like the CLI does."""
    if not _GIT_NAME:
        import subprocess
        try:
            out = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        _GIT_NAME.append(out)
    return _GIT_NAME[0]


def load_registry() -> dict:
    raw = _raw_registry()
    env_roots = [r for r in os.environ.get("EGD_CONSOLE_ROOTS", "").split(os.pathsep) if r]
    return {
        # who signs: $EGD_USER, else the name saved with --user, else git user.name
        "user": os.environ.get("EGD_USER") or raw.get("user", "") or _git_name(),
        "roots": list(dict.fromkeys([*raw.get("roots", []), *env_roots])),
        "repos": [r.get("path") for r in raw.get("repo", []) if isinstance(r, dict) and r.get("path")],
        "signers": _signers(raw),
    }


def _signers(raw: dict) -> dict[str, str]:
    """Per-repository signing names, by resolved path; repositories without one use `user`."""
    out = {}
    for r in raw.get("signer", []):
        if isinstance(r, dict) and r.get("path") and str(r.get("name", "")).strip():
            out[str(Path(r["path"]).expanduser().resolve())] = str(r["name"]).strip()
    return out


# Every write of console.toml, and every read-modify-write of it, holds this: the server answers
# requests on threads, and eight "add" requests at once must keep eight repositories, not two.
REGISTRY_LOCK = threading.RLock()


def save_registry(reg: dict) -> Path:
    with REGISTRY_LOCK:
        return _save_registry(reg)


def _save_registry(reg: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# EGD Console — repositories and identity. Edited by `egd console`.", ""]
    lines.append(f"user = {toml_value(reg.get('user', ''))}")
    lines.append(f"roots = {toml_value(reg.get('roots', []))}")
    for p in reg.get("repos", []):
        lines += ["", "[[repo]]", f"path = {toml_value(p)}"]
    signers = reg["signers"] if "signers" in reg else _signers(_raw_registry())
    for p, name in sorted(signers.items()):
        lines += ["", "[[signer]]", f"path = {toml_value(p)}", f"name = {toml_value(name)}"]
    write_atomic(path, "\n".join(lines) + "\n")  # a crash mid-write must not leave half a registry
    return path


def signer_for(reg: dict, root: Path) -> str:
    """Who console actions in this repository are signed as."""
    return reg.get("signers", {}).get(str(Path(root).resolve())) or reg.get("user", "")


def scan(root: Path, depth: int = 4) -> list[Path]:
    """Directories under `root` holding `.egd/`, without descending into them."""
    found: list[Path] = []
    root = root.expanduser()

    def walk(d: Path, level: int):
        if is_repo(d):
            found.append(d.resolve())
            return
        if level >= depth:
            return
        try:
            children = sorted(c for c in d.iterdir() if c.is_dir() and c.name not in SKIP_DIRS
                              and not c.name.startswith("."))
        except OSError:
            return
        for c in children:
            walk(c, level + 1)

    if root.is_dir():
        walk(root, 0)
    return found


def is_repo(d: Path) -> bool:
    """An EGD repository, or one planned with ai-dlc (shown read-only)."""
    return (d / EGD_DIR).is_dir() or aidlc.is_repo(d)


def repo_paths(reg: dict) -> list[Path]:
    paths = [Path(p).expanduser().resolve() for p in reg["repos"]]
    for r in reg["roots"]:
        paths += scan(Path(r))
    seen, out = set(), []
    for p in paths:
        if p not in seen and is_repo(p):
            seen.add(p)
            out.append(p)
    return out


def repo_kind(d: Path) -> str | None:
    if (d / EGD_DIR).is_dir():
        return "egd"
    if aidlc.is_repo(d):
        return "ai-dlc"
    if (d / ".git").exists():
        return "git"
    return None


def browse_roots() -> list[Path]:
    """Folders the console may list. EGD_BROWSE_ROOTS (path-separated) or the home folder."""
    raw = [r for r in os.environ.get("EGD_BROWSE_ROOTS", "").split(os.pathsep) if r]
    roots = [Path(r).expanduser().resolve() for r in raw] or [Path.home().resolve()]
    return [r for r in roots if r.is_dir()]


def browse(path: str, registered: set[str]) -> dict:
    """Sub-folders of `path` (never files), confined to the browse roots."""
    roots = browse_roots()
    if not path:
        return {"path": "", "parent": None, "kind": None, "roots": [str(r) for r in roots],
                "entries": [{"name": str(r), "path": str(r), "kind": repo_kind(r),
                             "registered": str(r) in registered} for r in roots]}
    target = Path(path).expanduser().resolve()
    root = next((r for r in roots if target == r or target.is_relative_to(r)), None)
    if root is None or not target.is_dir():
        raise Refused("that folder is outside the folders this console may browse")
    entries = []
    try:
        children = sorted((c for c in target.iterdir()
                           if c.is_dir() and not c.name.startswith(".") and c.name not in SKIP_DIRS),
                          key=lambda c: c.name.lower())
    except OSError as exc:
        raise Refused(f"cannot read {target}: {exc.strerror}") from exc
    for c in children[:500]:
        entries.append({"name": c.name, "path": str(c), "kind": repo_kind(c), "registered": str(c) in registered})
    return {"path": str(target), "parent": None if target == root else str(target.parent),
            "kind": repo_kind(target), "registered": str(target) in registered,
            "roots": [str(r) for r in roots], "entries": entries, "truncated": len(children) > 500}


def repo_ids(paths: list[Path]) -> dict[str, Path]:
    """Short, stable, unique ids: the folder name, prefixed by its parent on collision."""
    ids: dict[str, Path] = {}
    for p in paths:
        rid = p.name
        if rid in ids:
            rid = f"{p.parent.name}-{p.name}"
        n = 2
        while rid in ids:
            rid, n = f"{p.name}-{n}", n + 1
        ids[rid] = p
    return ids


# ------------------------------------------------------------------ portfolio

def _reviewed_line(lines: list[str]) -> int | None:
    """Index of the line that decides who signed the map: the first `reviewed_by:` line."""
    return next((i for i, ln in enumerate(lines) if ln.lower().startswith("reviewed_by:")), None)


def _field(text: str, key: str) -> str:
    """The value of the first `key:` line of `text` ("" when there is none or it is empty) — on
    that line only: an empty `reviewed_by:` followed by a comment line is not signed."""
    key = key.lower() + ":"
    line = next((ln for ln in text.splitlines() if ln.lower().startswith(key)), "")
    return line.split(":", 1)[1].strip() if line else ""


def map_signer(text: str) -> str:
    """Who signed .egd/map.md — "" when nobody has. The first `reviewed_by:` line decides, as the
    `frame` gate reads it: a filled line further down does not sign a map whose first one is empty."""
    lines = text.splitlines()
    idx = _reviewed_line(lines)
    return "" if idx is None else lines[idx].split(":", 1)[1].strip()


def _item(severity, kind, repo, f, title, detail="", hint="", actions=None, target=None):
    return {"severity": severity, "kind": kind, "repo": repo, "slug": f.slug if f else None,
            "feature": f.title if f else None, "title": title, "detail": detail, "hint": hint,
            "target": target, "actions": actions or []}


def _act(action, label, target=None, primary=False, needs=None):
    return {"action": action, "label": label, "target": target, "primary": primary, "needs": needs or []}


@git_memo()  # every feature of a repo asks git about the same commits
def repo_snapshot(rid: str, root: Path) -> dict:
    cfg = load_config(root)
    branch = (git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None
    # --no-optional-locks: a poll must not rewrite .git/index (that would change the fingerprint each time)
    status = git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all", "--", EGD_DIR) or ""
    pending = [ln for ln in status.splitlines()
               if ln.strip() and not any(x in ln for x in ("/runs/", "/reports/", "BOARD.md", "board.html"))]
    team = Team(root, cfg)
    map_text = (root / EGD_DIR / "map.md").read_text(encoding="utf-8") if (root / EGD_DIR / "map.md").exists() else ""
    map_signed = bool(map_signer(map_text))
    features, inbox, who = [], [], namer(team)
    for f in all_features(root):
        st = replay(f)  # once per feature: feature_data and the inbox read this same state
        data = feature_data(f, cfg, team, st)
        data["repo"] = rid
        data["last_activity"] = st.events[-1]["at"] if st.events else None
        nxt = gates.next_gate(f, st)
        problems = _next_problems(f, st, nxt, data["gate_checks"]) if nxt else []
        data["next_problems"] = problems[:12]
        features.append(data)
        inbox += feature_inbox(rid, f, st, cfg, nxt, problems, who)
    if any(f["gate"] == "frame" and f["tier"] != "lite" for f in features) and not map_signed:
        inbox.append(_item("decide", "map", rid, None, "Repository map is unsigned",
                           "A person has to read .egd/map.md and confirm it is true before `frame` opens.",
                           actions=[_act("sign_map", "Read & sign", primary=True)]))
    if pending:
        inbox.append(_item("hygiene", "uncommitted", rid, None,
                           f"{plural(len(pending), 'trail/plan change')} not committed",
                           "Teammates cannot see these until they are committed and pushed.",
                           hint=f"git -C {root} add .egd && git -C {root} commit -m 'egd: trail'"))
    return {"id": rid, "name": root.name, "path": str(root), "branch": branch,
            "uncommitted": len(pending), "map_signed": map_signed,
            "team": [{"name": m.get("name"), "roles": m.get("roles", []),
                      "aliases": [a for a in [m.get("github"), *m.get("aliases", [])] if a]} for m in team.members],
            "reviewers": [m.get("name") for m in team.members if _may(team, m.get("name"), "review")],
            # who may do what: the page offers an action only to someone it would not refuse
            "team_set": team.configured,
            "may": {act: [m.get("name") for m in team.members if _may(team, m.get("name"), act)]
                    for act in ("gate", "review", "uat", "cr_approve", "work")},
            "features": features, "inbox": inbox}


def _next_problems(f, st, nxt: str, checks: dict) -> list[str]:
    """What `gates.check(f, st, cfg, nxt)` says for the next gate, from the gate checks
    `feature_data` already ran — the next gate's earlier gates are passed by definition, so only
    the trail's and the plan's own problems come first."""
    problems = []
    if st.corrupt:
        problems.append("unreadable event files: " + ", ".join(st.corrupt))
    if f.error:
        problems.append(f.error)
    return problems + list(checks.get(nxt, []))


def _may(team: Team, name: str | None, action: str) -> bool:
    try:
        team.require(name or "", action)
        return True
    except Refused:
        return False


def repo_detail(rid: str, root: Path) -> dict:
    """What makes one repository different: its committed config, team and map, how Claude Code
    is wired in, and which secrets the config expects. Secret values and git remotes (which can
    carry tokens) are never read into the response."""
    from .util import load_secrets

    def text(*parts: str) -> str:
        p = root.joinpath(*parts)
        try:
            return p.read_text(encoding="utf-8") if p.is_file() else ""
        except (OSError, UnicodeDecodeError):
            return ""

    egd = (root / EGD_DIR).is_dir()
    cfg = load_config(root) if egd else {}
    config_text = text(EGD_DIR, "config.toml")
    # every ${VAR} in the parsed config's values (comments do not count), by util's own pattern
    parsed = load_toml(root / EGD_DIR / "config.toml") if egd else {}
    wanted = sorted(set(missing_vars(json.dumps(parsed, default=str), {})))
    known = load_secrets(root) if egd else {}
    settings = {}
    try:
        settings = json.loads(text(".claude", "settings.json") or "{}")
    except json.JSONDecodeError:
        pass
    skills_dir = root / ".claude" / "skills"
    ignored = git(root, "check-ignore", "-q", EGD_DIR) is not None if egd else None
    tracked = bool((git(root, "ls-files", "--", EGD_DIR) or "").strip()) if egd else None
    map_text = text(EGD_DIR, "map.md") if egd else text(".ai", "architecture.md")
    return {
        "id": rid, "path": str(root), "egd": egd,
        "files": {"config": config_text, "team": text(EGD_DIR, "team.toml"), "map": text(EGD_DIR, "map.md"),
                  "aidlc_config": text(".ai", "aidlc.yaml"), "aidlc_map": text(".ai", "architecture.md")},
        # who signed the map, read line by line as the `frame` gate reads it — the page shows this
        "signer": map_signer(map_text) if egd else _field(map_text, "verified_by"),
        "limits": cfg.get("limits", {}), "proof": {k: v for k, v in cfg.get("proof", {}).items()},
        "report": cfg.get("report", {}), "roles": cfg.get("roles", {}), "ui": cfg.get("ui", {}),
        "github": cfg.get("github", {}),
        "pr": cfg.get("pr", {}), "review": cfg.get("review", {}),
        "env": [{"name": k, "base_url": v.get("base_url", ""), "required": v.get("required", True),
                 "readonly": bool(v.get("readonly"))} for k, v in cfg.get("env", {}).items() if isinstance(v, dict)],
        "secrets": [{"name": n, "set": n in known} for n in wanted],
        "claude": {"plugin": bool(settings.get("enabledPlugins", {}).get("egd@egd")),
                   "skills": sorted(d.name for d in skills_dir.iterdir() if d.is_dir()) if skills_dir.is_dir() else []},
        "git": {"egd_ignored": ignored, "egd_tracked": tracked, "aidlc": (root / ".ai").is_dir()},
    }


def feature_inbox(rid, f, st, cfg, nxt, problems, who=lambda name: name) -> list[dict]:
    if st.closed:  # dropped, a finished spike, or replaced: nothing in it waits for anyone
        return []
    out = []
    if nxt and not problems:
        out.append(_item("decide", "gate", rid, f, f"Ready to pass `{nxt}`",
                         gates.NEXT[nxt], actions=[_act("pass", f"Pass {nxt}", nxt, primary=True)],
                         target=nxt))
    titles = {t["id"]: t.get("title", "") for t in f.tasks}
    for tid, ts in st.tasks.items():
        if ts.status == "review":
            out.append(_item("decide", "review", rid, f, f"{tid} waiting for review",
                             f"{titles.get(tid, '')} — submitted by {who(ts.submitted_by)}",
                             actions=[_act("accept", "Accept", tid, primary=True),
                                      _act("reject", "Reject", tid, needs=["reason"])], target=tid))
        elif ts.status == "blocked":
            out.append(_item("unblock", "blocked", rid, f, f"{tid} blocked",
                             f"{titles.get(tid, '')} — {ts.block_reason}",
                             actions=[_act("unblock", "Unblock", tid)], target=tid))
    for cid, cr in st.crs.items():
        if cr.get("status") == "pending":
            out.append(_item("decide", "cr", rid, f, f"{cid} awaiting the client",
                             f"{cr.get('title', '')} (+{cr.get('hours', 0):g}h) — {cr.get('reason', '')}",
                             actions=[_act("cr_approve", "Approve", cid, primary=True),
                                      _act("cr_reject", "Reject", cid, needs=["note"])], target=cid))
    for a in f.assumptions:
        if a.get("status", "open") == "open" and a.get("blocking"):
            out.append(_item("decide", "assumption", rid, f, f"{a['id']} is blocking and unanswered",
                             a.get("text", ""), hint="Being wrong here forces rework.",
                             actions=[_act("resolve", "Confirm", a["id"], primary=True, needs=["resolution"]),
                                      _act("reject_assumption", "Reject", a["id"], needs=["resolution"])],
                             target=a["id"]))
    if "build" in st.gates and "accept" in f.gates and "accept" not in st.gates:
        for s in f.slices:
            v = st.uat.get(s["id"])
            if v is None or not v.get("passed"):
                out.append(_item("decide", "uat", rid, f, f"{s['id']} needs a UAT verdict",
                                 s.get("title", ""), hint="The client walks the demo script.",
                                 actions=[_act("uat_pass", "UAT pass", s["id"], primary=True),
                                          _act("uat_fail", "UAT fail", s["id"], needs=["note"])],
                                 target=s["id"]))
    # the rollback is written while the change is fresh — once the work is done, not at the last gate
    work_done = f.tasks and all(st.task(t["id"]).status == "done" for t in f.tasks)
    if "release" in f.gates and "release" not in st.gates and (work_done or "build" in st.gates) \
            and not f.sections("release.md").get("rollback"):
        out.append(_item("decide", "rollback", rid, f, "How is this undone? Rollback not written",
                         "Write it now, while the change is fresh: revert what, migrations, flags, data.",
                         hint="release needs it — release.md → ## Rollback",
                         actions=[_act("rollback", "Write rollback", None, primary=True, needs=["rollback"])]))
    latest = st.latest_proofs()
    for (pid, env), run in latest.items():
        if run.get("status") == "fail" and run.get("required", True):
            out.append(_item("unblock", "proof", rid, f, f"{pid}@{env} failing",
                             run.get("reason", ""), hint=f"egd proof run {f.slug} --only {pid} --by <you>",
                             target=pid))
    for did, d in st.defects.items():
        if d.get("status") == "open" and d.get("severity") in gates.SERIOUS:
            out.append(_item("unblock", "defect", rid, f, f"{did} {d.get('severity')} defect open",
                             d.get("title", ""), target=did))
    errors, _ = validate(f, cfg) if (f.slices or "clarify" in st.gates) else ([], [])
    if f.error or st.corrupt or errors:
        out.append(_item("hygiene", "lint", rid, f, "Plan has problems",
                         "; ".join(([f.error] if f.error else []) + errors)[:400],
                         hint=f"egd graph {f.slug}"))
    return out


def fingerprint(root: Path) -> tuple:
    """A cheap stand-in for "did anything this repository's snapshot reads change": a stat walk of
    the plan folder (entries, newest change, total size) plus git's HEAD, index, reflog and branch
    ref, so a commit, a checkout or `git add` shows too. No file is read except .git/HEAD."""
    plan = root / EGD_DIR if (root / EGD_DIR).is_dir() else root / ".ai"
    try:
        st = plan.stat()
    except OSError:
        return (plan.name, None, _git_marks(root))
    # ctime as well as mtime: a rename keeps a file's mtime
    count, newest, size = 0, max(st.st_mtime_ns, st.st_ctime_ns), 0
    stack = [str(plan)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    st = e.stat(follow_symlinks=False)
                    count, size = count + 1, size + st.st_size
                    newest = max(newest, st.st_mtime_ns, st.st_ctime_ns)
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
        except OSError:
            pass
    return (plan.name, count, newest, size, _git_marks(root))


def _git_marks(root: Path) -> tuple:
    top = next((d for d in (root, *root.parents) if (d / ".git").exists()), None)
    if top is None:
        return ()
    gd = top / ".git"
    try:
        if gd.is_file():  # a worktree or submodule: ".git" names the real git folder
            gd = (top / gd.read_text(encoding="utf-8").split(":", 1)[1].strip()).resolve()
        head = (gd / "HEAD").read_text(encoding="utf-8").strip()
        common = gd / "commondir"
        common = (gd / common.read_text(encoding="utf-8").strip()).resolve() if common.is_file() else gd
    except (OSError, IndexError, UnicodeDecodeError):
        return ("?",)
    files = [gd / "HEAD", gd / "index", gd / "logs" / "HEAD", common / "packed-refs"]
    if head.startswith("ref:"):
        files.append(common / head[4:].strip())
    marks = []
    for f in files:
        try:
            st = f.stat()
            marks.append((st.st_mtime_ns, st.st_size))
        except OSError:
            marks.append(None)
    return (head, *marks)


def _rev(doc) -> str:
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def _revise(snap: dict) -> dict:
    """Give every feature `rev`, a hash of its full data: the summary carries it, so a page knows
    when a feature it holds in detail has changed, and it is that feature's ETag."""
    for f in snap.get("features", []):
        f.pop("rev", None)
        f["rev"] = _rev(f)
    return snap


READ_WAIT = 10.0  # seconds a request waits for one repository before showing its last snapshot instead
REGISTRY_TTL = 30.0  # the registered repositories are looked for again at least this often
READERS = 8  # repositories read at the same time


def _mark(p: Path):
    """(mtime, size) of `p`, or None — enough to tell that a file or folder changed."""
    try:
        st = p.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _scrub(doc, root: str, home: str):
    """`doc` with the repository's absolute path written "." and the home folder "~" in every
    string — what a snapshot meant for other people carries instead of where this machine keeps it."""
    if isinstance(doc, str):
        for path, short in ((root, "."), (home, "~")):
            if len(path) > 1 and path in doc:
                doc = re.sub(re.escape(path) + r"(?![\w.-])", short, doc)
        return doc
    if isinstance(doc, dict):
        return {k: _scrub(v, root, home) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_scrub(v, root, home) for v in doc]
    return doc


def public(snap: dict, root: Path) -> dict:
    """A repository's snapshot without local paths: no `path`, and none in hints or notes."""
    return {**_scrub(snap, str(root), str(Path.home())), "path": ""}


class _Job:
    """One repository being read. A request that needs the same read waits for it instead of
    starting another."""

    def __init__(self, rid: str, fp: tuple, gen: int):
        self.rid, self.fp, self.gen = rid, fp, gen
        self.started = time.monotonic()  # the snapshot is as of now: the fingerprint was taken just before
        self.done = threading.Event()
        self.result: tuple | Exception | None = None  # the snapshot entry, or why it could not be read
        self.abandoned = False  # a request stopped waiting: when it lands, the next poll rebuilds


class Portfolio:
    """Builds the portfolio, cached briefly so a busy page does not re-run git for every poll.

    Each repository's snapshot is kept with its `fingerprint` and read again only when that changes
    or after an action there (`invalidate`) — then the request waits, because what it shows must be
    what is on disk. It waits `READ_WAIT` seconds at most: a repository slower than that (a network
    drive, a huge history) shows its last snapshot marked `stale`, or an error, and the next poll
    picks the read up — one slow repository never holds the whole portfolio. A snapshot older than
    `max_age` (proofs go stale when code outside the plan folder moves, which the fingerprint does
    not see) is still served at once while one background thread reads the repository again; when
    that lands, the next poll gets it under a new ETag. `warm` reads every repository in the
    background so the first page load finds them ready.

    Which repositories there are (console.toml plus a walk of its `roots`) is kept while
    console.toml, the environment and each root folder are unchanged, and looked for again after
    `REGISTRY_TTL` seconds, since a repository nested deeper in a root does not change the root.

    `get` is everything, every feature in full — what a snapshot embeds. `tagged` is what the live
    page polls: each feature as its summary, with an ETag over the content for 304 answers.
    `feature` and `trails` answer from the same cache.

    With `roots`, it shows exactly those repositories, read-only — the single-repository
    dashboard (`egd serve`, `egd site`) is this same view of one repository. Such a view may be
    shared, so it carries no local paths (`public`).
    """

    def __init__(self, ttl: float = 3.0, roots: list[Path] | None = None, max_age: float = 60.0):
        self.ttl = ttl
        self.max_age = max_age
        self.fixed = [Path(r).resolve() for r in roots] if roots else None
        self._lock = threading.RLock()
        self._cache: tuple[float, dict, dict, str] | None = None  # (built at, full, summary, tag)
        self._snaps: dict[Path, tuple] = {}  # root → (id, fingerprint, read at, snapshot, summary)
        self._last: dict[Path, tuple] = {}  # root → its last good entry, shown (stale) while a read is slow
        self._gens: dict[Path, int] = {}  # root → actions there so far: a read from before one is dropped
        self._jobs: dict[Path, _Job] = {}  # root → the read of it in flight
        self._threads: set[threading.Thread] = set()
        self._readers = threading.BoundedSemaphore(READERS)
        self._generation = 0
        self._reg: tuple | None = None  # (key, at, root marks, registry, ids)
        self._reg_gen = 0

    def invalidate(self, root: Path | None = None):
        """Something changed: the next read rebuilds (and rescans `root` whatever its fingerprint).
        Without `root`, the registry changed: the repositories are looked for again too."""
        with self._lock:
            self._cache = None
            self._generation += 1
            if root is not None:
                root = Path(root).resolve()
                self._snaps.pop(root, None)
                self._gens[root] = self._gens.get(root, 0) + 1
            else:
                self._reg, self._reg_gen = None, self._reg_gen + 1

    def registry(self) -> tuple[dict, dict[str, Path]]:
        """(console.toml as `load_registry` reads it, {id: path} of every repository) — cached, see
        the class. Raises UsageError when console.toml is not valid."""
        if self.fixed is not None:
            return {"user": "", "roots": [], "repos": [], "signers": {}}, repo_ids(self.fixed)
        cfg = config_path()
        key = (str(cfg), _mark(cfg), os.environ.get("EGD_CONSOLE_ROOTS", ""), os.environ.get("EGD_USER", ""))
        with self._lock:
            cached, gen = self._reg, self._reg_gen
        if (cached and cached[0] == key and time.monotonic() - cached[1] < REGISTRY_TTL
                and all(_mark(r) == m for r, m in cached[2])):
            return cached[3], dict(cached[4])
        at = time.monotonic()
        reg = load_registry()
        marks = [(Path(r).expanduser(), _mark(Path(r).expanduser())) for r in reg["roots"]]  # before the walk
        ids = repo_ids(repo_paths(reg))
        with self._lock:
            if gen == self._reg_gen:  # not if the registry was changed while this one was read
                self._reg = (key, at, marks, reg, ids)
        return reg, dict(ids)

    def repos(self) -> dict[str, Path]:
        return self.registry()[1]

    def get(self) -> dict:
        """Every repository with every feature in full, and the rules in words (`core`, `core_vi`)
        — a snapshot page carries everything it shows. The live page fetches those from /api/core."""
        return {**self._build()[0], "core": core_doc("en")[0], "core_vi": core_doc("vi")[0]}

    def tagged(self) -> tuple[dict, str]:
        """The portfolio with each feature as its summary, and a hash of it all but the build time.
        Each summary's `rev` changes with its feature's detail, so the hash does too."""
        _, summary, tag = self._build()
        return summary, tag

    def feature(self, rid: str, slug: str) -> dict | None:
        """One feature in full, from the same cache as the portfolio; None if there is no such one.
        Past `ttl`, only its own repository is looked at again, not the whole portfolio."""
        with self._lock:
            cache = self._cache if self._cache and time.monotonic() - self._cache[0] < self.ttl else None
        if cache:
            repo = next((r for r in cache[1]["repos"] if r["id"] == rid), None)
        else:
            root = self.repos().get(rid)
            try:
                repo = self._entry(rid, root)[3] if root else None
            except Exception:  # the portfolio shows why
                repo = None
        return next((f for f in repo["features"] if f.get("slug") == slug), None) if repo else None

    def trails(self, everything: bool = False) -> tuple[dict, str]:
        """Every feature's signed events — or, with `everything`, its whole trail — for the approval
        trail page, tagged like the portfolio (any change to a feature changes its tag)."""
        full, _, tag = self._build()
        pick = (lambda e: True) if everything else (lambda e: e.get("type") in SIGNATURES)
        return {"repos": [{"id": r["id"], "features": [
            {"slug": f["slug"], "trail": [e for e in f.get("trail", []) if pick(e)]} for f in r["features"]]}
            for r in full["repos"]]}, f'{tag}-{"all" if everything else "signed"}'

    # ---------------------------------------------------------------- background reads
    def warm(self) -> None:
        """Read every repository in the background, so the first page load finds them ready."""
        def run():
            try:
                current = self.repos()
            except UsageError:  # an invalid console.toml: the page says so
                return
            for rid, root in current.items():
                try:
                    self._entry(rid, root)
                except Exception:  # shown, with its reason, by the request that reads it
                    pass
        self._spawn(run)

    def wait(self, timeout: float | None = None) -> None:
        """Until no background read is running (or `timeout` seconds) — for tests and shutdown."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                threads = list(self._threads)
            if not threads:
                return
            for t in threads:
                t.join(None if end is None else max(0.0, end - time.monotonic()))
            if end is not None and time.monotonic() >= end:
                return

    def _spawn(self, fn, *args) -> None:
        def run():
            try:
                fn(*args)
            finally:
                with self._lock:
                    self._threads.discard(threading.current_thread())
        t = threading.Thread(target=run, name="egd-portfolio", daemon=True)
        with self._lock:
            self._threads.add(t)
        t.start()

    # ---------------------------------------------------------------- reading repositories
    def _entry(self, rid: str, root: Path, wait: float = READ_WAIT) -> tuple:
        """This repository's snapshot entry: the cached one while its fingerprint holds (refreshed in
        the background once it is older than `max_age`), else read now — or, when a read with the
        same fingerprint is already running, that one — waiting `wait` seconds at most.
        Raises when the repository cannot be read."""
        got = self._begin(rid, root)
        return got if isinstance(got, tuple) else self._await(got, root, wait)

    def _begin(self, rid: str, root: Path) -> tuple | _Job:
        """The cached entry, or the read that will produce it (started here unless one is running)."""
        fp = fingerprint(root)  # taken before reading, so a write during the read shows next time
        with self._lock:
            prev, gen = self._snaps.get(root), self._gens.get(root, 0)
            if prev and prev[0] == rid and prev[1] == fp:
                if time.monotonic() - prev[2] >= self.max_age and root not in self._jobs:
                    job = self._jobs[root] = _Job(rid, fp, gen)
                    self._spawn(self._read, job, root, True)
                return prev
            job = self._jobs.get(root)
            if not (job and (job.rid, job.fp, job.gen) == (rid, fp, gen)):
                job = self._jobs[root] = _Job(rid, fp, gen)
                self._spawn(self._read, job, root)
        return job

    def _await(self, job: _Job, root: Path, wait: float) -> tuple:
        """`job`'s entry once it lands. Past `wait` seconds: the last snapshot of the repository,
        marked `stale`, or an error naming the slow repository."""
        if not job.done.wait(max(0.0, wait)):
            with self._lock:
                if not job.done.is_set():
                    job.abandoned = True
                    last = self._last.get(root)
                    if last and last[0] == job.rid:
                        return _stale(last)
                    raise TimeoutError(f"{root.name} is taking longer than {READ_WAIT:g}s to read "
                                       "(slow disk or git?) — it shows on a later refresh")
        if isinstance(job.result, Exception):
            raise job.result
        return job.result

    def _read(self, job: _Job, root: Path, background: bool = False) -> None:
        """Read one repository for `job` and keep the result — unless an action there, or a read
        that started later, got in first."""
        try:
            with self._readers:
                if (root / EGD_DIR).is_dir():
                    snap = {**repo_snapshot(job.rid, root), "source": "egd", "readonly": False}
                else:
                    snap = aidlc.snapshot(job.rid, root, _item, _act)
                    for f in snap.get("features", []):
                        f.pop("brief", None)  # no view reads it
            if self.fixed is not None:
                snap = public(snap, root)
            _revise(snap)
            summary = {**snap, "features": [feature_summary(f) for f in snap["features"]]}
            job.result = (job.rid, job.fp, job.started, snap, summary)
        except Exception as exc:  # one broken repo must not blank the console
            job.result = exc
        with self._lock:
            if self._jobs.get(root) is job:
                del self._jobs[root]
            if isinstance(job.result, tuple):
                last = self._last.get(root)
                if last is None or last[2] <= job.started:
                    self._last[root] = job.result
            cur = self._snaps.get(root)
            current = self._gens.get(root, 0) == job.gen and (cur is None or cur[2] <= job.started)
            if current and isinstance(job.result, tuple):
                self._snaps[root] = job.result
            elif current and background:  # could not refresh: the next request reads it and shows why
                self._snaps.pop(root, None)
            if (current and background) or job.abandoned:
                # the next poll is assembled again, under a new ETag if it changed
                self._cache = None
                self._generation += 1
            job.done.set()  # under the lock: a waiter that gives up sees either this or nothing

    def _build(self) -> tuple[dict, dict, str]:
        with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < self.ttl:
                return self._cache[1:]
            generation = self._generation
        try:
            reg, current = self.registry()
        except UsageError as exc:  # the page shows why, and every other route keeps working
            data = {"generated": iso(), "user": "", "config": str(config_path()), "roots": [], "repos": [],
                    "error": str(exc)}
            return data, data, _rev({k: v for k, v in data.items() if k != "generated"})
        explicit = {str(Path(p).expanduser().resolve()) for p in reg["repos"]}
        # every repository's read starts at once; together they wait READ_WAIT at most
        started = [(rid, root, self._begin(rid, root)) for rid, root in current.items()]
        deadline = time.monotonic() + READ_WAIT
        snaps, summaries = [], []
        for rid, root, got in started:
            try:
                _, _, _, snap, summary = got if isinstance(got, tuple) else \
                    self._await(got, root, deadline - time.monotonic())
            except Exception as exc:  # one broken repo must not blank the console
                snap = summary = {"id": rid, "name": root.name, "path": str(root), "error": str(exc),
                                  "features": [], "inbox": [], "team": [], "uncommitted": 0}
                if self.fixed is not None:
                    snap = summary = public(snap, root)
            snaps.append({**snap, "path": str(root)})
            summaries.append({**summary})
        signers = reg.get("signers", {})
        for s, sm in zip(snaps, summaries):
            s["pinned"] = s["path"] in explicit
            s["signer"] = signers.get(s["path"]) or reg["user"]
            s["signer_own"] = s["path"] in signers
            if s.get("source") == "egd" and s["signer"]:
                team = Team(Path(s["path"]), load_config(Path(s["path"])))
                s["signer_in_team"] = (not team.configured) or team.member(s["signer"]) is not None
            if self.fixed is not None:
                s["readonly"] = True
                s["path"] = ""  # a view of one repository may be shared: it says nothing of this machine
            sm.update({k: v for k, v in s.items() if k != "features"})
        if self.fixed is None:
            data = {"generated": iso(), "user": reg["user"], "config": str(config_path()),
                    "roots": reg["roots"], "repos": snaps}
        else:
            data = {"generated": iso(), "user": "", "config": "", "roots": [], "repos": snaps}
        summary = {**data, "repos": summaries}
        tag = _rev({k: v for k, v in summary.items() if k != "generated"})
        with self._lock:
            # a build that started before an action must not overwrite what the action changed
            if generation == self._generation:
                self._cache = (time.monotonic(), data, summary, tag)
                keep = set(current.values())
                self._snaps = {r: v for r, v in self._snaps.items() if r in keep}
                self._last = {r: v for r, v in self._last.items() if r in keep}
        return data, summary, tag


def _stale(entry: tuple) -> tuple:
    """`entry` (id, fingerprint, read at, snapshot, summary) marked as not the latest."""
    rid, fp, at, snap, summary = entry
    return rid, fp, at, {**snap, "stale": True}, {**summary, "stale": True}


@functools.cache
def core_doc(lang: str) -> tuple[dict, str]:
    """The rules in words for the guide (`core.core_info`), in `lang`, and their ETag."""
    from .core import core_info
    from .util import DEFAULT_CONFIG
    doc = core_info(DEFAULT_CONFIG["roles"], lang)
    return doc, _rev(doc)


# ------------------------------------------------------------------ actions

def _signs_as(team: Team, user: str) -> str:
    """The console user as team.toml spells them, exactly as the CLI records `--by` — so an alias
    ("linh-dev") signs as the member ("Linh"). A name matching nobody is refused, with the way out."""
    try:
        return team.canonical(user)
    except Refused as exc:
        exc.problems.append("or change who signs in this repository: Repositories → Signs as")
        raise


class Done(str):
    """`perform`'s one-line result, in English, and `by`: who it was signed as (team.toml's
    spelling) — so a page can say it in the reader's language from the action and its target."""
    by = ""


def perform(root: Path, user: str, body: dict) -> str:
    """Run one console action as `user`. Returns a one-line result (a `Done`); raises Refused."""
    msg, by = _perform(root, user, body)
    done = Done(msg)
    done.by = by
    return done


def _perform(root: Path, user: str, body: dict) -> tuple[str, str]:
    if not (root / EGD_DIR).is_dir():
        raise Refused(f"{root.name} is planned with another tool — shown read-only in the console")
    if not user:
        raise Refused("set your name first: egd console --user <name>")
    from .model import resolve_feature
    action = body.get("action")
    target = body.get("target")
    cfg = load_config(root)
    team = Team(root, cfg)
    user = _signs_as(team, user)
    if action == "sign_map":
        path = root / EGD_DIR / "map.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if map_signer(text):
            raise Refused("the map is already signed")
        lines = text.splitlines()
        idx = _reviewed_line(lines)  # the line map_signer reads: signing fills exactly that one
        if is_blank_template(text):
            raise Refused("the map is still the blank template — fill it in (the egd-scout agent can draft it), "
                          "then sign")
        team.require(user, "work")
        if idx is None:
            lines.insert(1 if lines else 0, f"reviewed_by: {user}")
        else:
            lines[idx] = f"reviewed_by: {user}"
        write_atomic(path, "\n".join(lines) + "\n")
        return f"map signed by {user}", user
    f = resolve_feature(root, body.get("feature"))
    st = replay(f)
    note = str(body.get("note") or body.get("reason") or body.get("resolution") or body.get("closing") or "").strip()
    if action == "pass":
        gates.pass_gate(f, st, cfg, team, target, user)
        return f"passed {target}", user
    if action == "start":
        tasks.start(f, st, cfg, team, target, user)
        return f"{target} → doing (@{user})", user
    if action == "accept":
        tasks.accept(f, st, cfg, team, target, user)
        return f"{target} accepted", user
    if action == "reject":
        tasks.reject(f, st, cfg, team, target, user, note)
        return f"{target} sent back", user
    if action == "block":
        tasks.block(f, st, cfg, team, target, user, note)
        return f"{target} blocked", user
    if action == "unblock":
        tasks.unblock(f, st, cfg, team, target, user)
        return f"{target} unblocked", user
    if action in ("submit", "solo"):
        spent = body.get("spent")
        try:
            spent = float(spent) if spent not in (None, "") else None
        except (TypeError, ValueError):
            raise Refused(f"spent must be a number of hours, not {str(spent)[:20]!r}") from None
        if spent is not None and not 0 <= spent < 10_000:  # nan and inf fail this too
            raise Refused("spent must be a number of hours between 0 and 10000")
        drift = str(body.get("drift_reason") or "").strip() or None
        common = dict(confirm=bool(body.get("confirm")), spent=spent, allow_drift=bool(drift))
        if action == "submit":
            tasks.submit(f, st, cfg, team, target, user, reason=drift, **common)
            return f"{target} → review", user
        tasks.solo(f, st, cfg, team, target, user, note, drift_reason=drift, **common)
        return f"{target} done (no review)", user
    if action in ("cr_approve", "cr_reject"):
        tracking.cr_decide(f, st, team, target, user, action == "cr_approve", note)
        return f"{target} {'approved' if action == 'cr_approve' else 'rejected'}", user
    if action in ("uat_pass", "uat_fail"):
        tracking.uat(f, st, team, target, user, action == "uat_pass", note)
        return f"UAT {target}: {'pass' if action == 'uat_pass' else 'fail'}", user
    if action == "close":
        gates.close(f, st, team, user, str(body.get("outcome") or "dropped"), note)
        return f"{f.slug} closed", user
    if action == "reopen_feature":
        gates.reopen(f, st, team, user, note)
        return f"{f.slug} reopened", user
    if action == "rollback":
        team.require(user, "work")
        text = str(body.get("rollback") or "").strip()
        if not text:
            raise Refused("write how this release is undone")
        from .planfile import set_section
        set_section(f, "release.md", "Rollback", text)
        return f"rollback written for {f.slug}", user
    if action in ("resolve", "reject_assumption"):
        team.require(user, "gate")
        if not note:
            raise Refused("a resolution note is required — who confirmed it, and how")
        status = "confirmed" if action == "resolve" else "rejected"
        set_fields(f, target, {"status": status, "resolution": f"{note} ({user})"})
        return f"{target} {status}", user
    raise Refused(f"unknown action '{action}'")


# ------------------------------------------------------------------ server

MAX_BODY = 64 * 1024
GZIP_MIN = 1024
COMPRESSIBLE = ("text/", "application/json")


def accepts_gzip(header: str | None) -> bool:
    """Whether an Accept-Encoding header allows gzip (`gzip;q=0` and `*;q=0` refuse it)."""
    quality = {}
    for part in (header or "").lower().split(","):
        name, _, params = part.strip().partition(";")
        q = 1.0
        for p in params.split(";"):
            k, _, v = p.strip().partition("=")
            if k == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        if name:
            quality[name.strip()] = q
    q = quality.get("gzip", quality.get("x-gzip", quality.get("*", 0.0)))
    return q > 0


# What a browser sees without the link's token: how to get in, not a bare refusal.
LOCKED_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>EGD Console</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
:root{color-scheme:light dark}body{font:16px/1.6 system-ui,sans-serif;max-width:34rem;margin:15vh auto;padding:0 16px}
code{background:#8882;padding:.1em .35em;border-radius:4px}h1{font-size:1.3rem}p{margin:.6em 0}
</style></head><body><h1>EGD Console needs its link</h1>
<p>This console opens with a link that carries a key (<code>?t=…</code>), so nobody else can use it.</p>
<p><b>On this machine:</b> run <code>egd console</code> in a terminal — it prints the link. Open it
once and this browser remembers it.</p>
<p><b>A teammate's console:</b> ask them for the link again.</p>
<hr><p lang="vi"><b>Console cần đúng link.</b> Trên máy này: chạy <code>egd console</code> trong terminal để
lấy link (có <code>?t=…</code>), mở một lần là trình duyệt nhớ. Console của người khác: xin lại link.</p>
</body></html>""".encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    portfolio: Portfolio
    token: str | None
    csrf: str
    _readonly: bool        # the whole console is view-only (--readonly, or a dashboard)
    remote_readonly: bool  # --lan: other machines may look, only this one may act
    mode: str

    @property
    def readonly(self) -> bool:
        """Read-only for this request. With --lan, a visitor from another machine only looks:
        every action is signed with the console user's name, which is not theirs."""
        return self._readonly or (self.remote_readonly and not self._from_this_machine())

    def _from_this_machine(self) -> bool:
        ip = (self.client_address or ("",))[0]
        if ip in ("127.0.0.1", "::1") or ip.startswith("127.") or ip.startswith("::ffff:127."):
            return True
        # the owner opening the LAN link on this machine: the request arrives on the very address it
        # comes from, which only a connection from this host can do
        try:
            return bool(ip) and self.connection.getsockname()[0] == ip
        except (AttributeError, OSError, IndexError):
            return False
    # HTTP/1.1: browsers ignore an ETag on an HTTP/1.0 response, and the page's requests share
    # one connection. Every response carries Content-Length; an idle connection closes after a minute.
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype: str, extra: dict | None = None):
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}
        if ctype.startswith(COMPRESSIBLE):  # text shrinks 3-5x; images are compressed already
            headers["Vary"] = "Accept-Encoding"
            if code != 304 and len(body) > GZIP_MIN and accepts_gzip(self.headers.get("Accept-Encoding")):
                body = gzip.compress(body, 6)
                headers["Content-Encoding"] = "gzip"
        if code != 304:  # a 304 has no body, so it describes none
            headers.update({"Content-Type": ctype, "Content-Length": str(len(body))})
        if self.close_connection:
            headers["Connection"] = "close"
        self._answering = True
        self.send_response(code)
        for k, v in {**headers, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if code != 304:
            self.wfile.write(body)

    def _failed(self, exc: Exception):
        """A bug still answers, rather than dropping the connection — unless the answer had begun."""
        if getattr(self, "_answering", False):
            raise exc
        self.close_connection = True
        return self._json(500, {"error": f"internal error: {type(exc).__name__}: {exc}"})

    def _json(self, code, doc, extra=None):
        self._send(code, json.dumps(doc, ensure_ascii=False).encode(), "application/json; charset=utf-8", extra)

    def _revalidated(self, tag: str, doc, extra: dict):
        """`doc()` with an ETag. "no-cache": the browser keeps the body and revalidates it; while the
        tag is the same it gets a 304 and no body, and the body is never even serialised."""
        cache = {"ETag": f'"{tag}"', "Cache-Control": "private, no-cache"}
        asked = [t.strip().removeprefix("W/") for t in (self.headers.get("If-None-Match") or "").split(",")]
        if cache["ETag"] in asked:
            return self._send(304, b"", "application/json; charset=utf-8", {**extra, **cache})
        return self._json(200, doc(), {**extra, **cache})

    @property
    def _cookie(self) -> str:
        """The token cookie's name. Cookies ignore ports: `egd serve` and `egd console` on one host
        would overwrite each other's under a shared name."""
        return f"egd_c_{self.server.server_address[1]}"

    def _gate(self) -> tuple[bool, dict]:
        if not self.token:
            return host_name(self.headers.get("Host")) in LOCAL_HOSTS, {}
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        given = query.get("t", [None])[0]
        if given and _secrets.compare_digest(given, self.token):
            # kept across browser restarts (400 days, the longest browsers allow): the link is opened once
            return True, {"Set-Cookie": f"{self._cookie}={self.token}; HttpOnly; SameSite=Strict; Path=/; "
                                        "Max-Age=34560000"}
        cookies = [c.strip() for c in (self.headers.get("Cookie") or "").split(";")]
        return f"{self._cookie}={self.token}" in cookies, {}

    def do_GET(self):
        self._answering = False  # one handler serves every request of a kept-alive connection
        try:
            return self._get()
        except UsageError as exc:  # an invalid console.toml: say so rather than drop the connection
            return self._json(409, {"error": str(exc)})
        except Exception as exc:
            return self._failed(exc)

    def _get(self):
        ok, extra = self._gate()
        if not ok:
            if urllib.parse.urlsplit(self.path).path.startswith("/api/"):
                return self._json(403, {"error": "this console needs its link (with ?t=…)"})
            return self._send(403, LOCKED_PAGE, "text/html; charset=utf-8")
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        if path in ("/", "/index.html"):
            if extra:  # the link's ?t=: the cookie carries it from now on, so the address bar drops it
                rest = [(k, v) for k, v in urllib.parse.parse_qsl(url.query, keep_blank_values=True) if k != "t"]
                where = url.path + (f"?{urllib.parse.urlencode(rest)}" if rest else "")
                return self._send(303, b"", "text/plain", {**extra, "Location": where})
            from .web import page
            html = page({"csrf": self.csrf, "readonly": self.readonly, "mode": self.mode})
            return self._send(200, html.encode(), "text/html; charset=utf-8", extra)
        if path == "/api/boot":  # same-origin only: other sites cannot read this response
            return self._json(200, {"csrf": self.csrf, "readonly": self.readonly, "mode": self.mode}, extra)
        if path == "/api/portfolio":  # every feature as its summary; the rest is /api/feature
            data, tag = self.portfolio.tagged()
            tag = f'{tag}{"-ro" if self.readonly else ""}'
            return self._revalidated(tag, lambda: {**data, "readonly": self.readonly}, extra)
        if path == "/api/feature":  # one feature in full, from the portfolio's cache
            query = urllib.parse.parse_qs(url.query)
            found = self.portfolio.feature(query.get("repo", [""])[0], query.get("slug", [""])[0])
            if found is None:
                return self._json(404, {"error": "unknown repository or feature"}, extra)
            return self._revalidated(found["rev"], lambda: found, extra)
        if path == "/api/core":  # the rules in words, for the guide page — fetched when it opens
            lang = urllib.parse.parse_qs(url.query).get("lang", [""])[0]
            doc, tag = core_doc("vi" if lang == "vi" else "en")
            return self._revalidated(tag, lambda: doc, extra)
        if path == "/api/trail":  # the approval trail: signed events, or every event with all=1
            query = urllib.parse.parse_qs(url.query)
            data, tag = self.portfolio.trails(everything=query.get("all", [""])[0] == "1")
            return self._revalidated(tag, lambda: data, extra)
        if path.startswith("/ui/"):  # only the files web.asset knows: a name lookup, never a path on disk
            from .web import asset
            found = asset(path[len("/ui/"):])
            if found is None:
                return self._send(404, b"not found", "text/plain")
            body, ctype, version = found
            given = urllib.parse.parse_qs(url.query).get("v", [""])[0]
            cache = "private, max-age=31536000, immutable" if given == version else "no-cache"
            return self._send(200, body, ctype, {**extra, "Cache-Control": cache})
        if path in ("/api/browse", "/api/browse/scan") and self.readonly:
            return self._json(403, {"error": "this console is read-only"})
        if path in ("/api/browse", "/api/browse/scan") and self.portfolio.fixed is None:
            # a custom header cannot be sent cross-site without CORS, which this server never allows
            if not _secrets.compare_digest(self.headers.get("X-EGD-CSRF", ""), self.csrf):
                return self._json(403, {"error": "missing CSRF header"})
            target = urllib.parse.parse_qs(url.query).get("path", [""])[0]
            registered = {str(p) for p in self.portfolio.repos().values()}
            try:
                if path == "/api/browse":
                    return self._json(200, browse(target, registered))
                listing = browse(target, registered)  # same confinement check
                found = scan(Path(listing["path"]), depth=3)
                return self._json(200, {"path": listing["path"], "repos": [
                    {"name": p.name, "path": str(p), "kind": repo_kind(p), "registered": str(p) in registered}
                    for p in found]})
            except (Refused, ValueError) as exc:  # ValueError: a path with a NUL byte
                return self._json(403, {"error": str(exc)})
        if path == "/api/repo":
            rid = urllib.parse.parse_qs(url.query).get("repo", [""])[0]
            root = self.portfolio.repos().get(rid)
            if not root:
                return self._json(404, {"error": "unknown repository"})
            detail = repo_detail(rid, root)
            if self.portfolio.fixed is not None:
                detail = public(detail, root)
            return self._json(200, detail)
        if path == "/api/map":
            rid = urllib.parse.parse_qs(url.query).get("repo", [""])[0]
            root = self.portfolio.repos().get(rid)
            if not root:
                return self._json(404, {"error": "unknown repository"})
            m = root / EGD_DIR / "map.md"
            return self._json(200, {"text": m.read_text(encoding="utf-8") if m.exists() else ""})
        if path.startswith("/files/"):
            return self._file(path[len("/files/"):])
        return self._send(404, b"not found", "text/plain")

    def _file(self, rest: str):
        """Evidence of one feature: a file under its runs/ folder, by a name lookup of the
        repository and the feature folder — no plan is read."""
        parts = rest.split("/", 2)
        if len(parts) < 3:
            return self._send(404, b"not found", "text/plain")
        rid, slug, rel = parts
        root = self.portfolio.repos().get(rid)
        if root is None or not rel.startswith("runs/") or slug in ("", ".", "..") or slug.startswith(".") \
                or "\\" in slug or "\0" in rest:
            return self._send(404, b"not found", "text/plain")
        base = features_dir(root).resolve()
        folder = (base / slug).resolve()
        if folder.parent != base or not (folder / "plan.toml").is_file():
            return self._send(404, b"not found", "text/plain")
        runs = safe_runs(folder, root)  # None when runs/ (or the folder) leads outside the repository
        target = (folder / rel).resolve()
        if (runs is None or not target.is_file() or not target.is_relative_to(runs)
                or target.suffix.lower() not in SERVABLE):
            return self._send(404, b"not found", "text/plain")
        return self._send(200, target.read_bytes(), SERVABLE[target.suffix.lower()],
                          {"Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'"})

    def do_POST(self):
        self._answering = False
        self.close_connection = True  # a refused request leaves its body unread: never reuse the connection
        ok, _ = self._gate()
        if not ok:
            return self._json(403, {"error": "not allowed"})
        if not _secrets.compare_digest(self.headers.get("X-EGD-CSRF", ""), self.csrf):
            return self._json(403, {"error": "missing or wrong CSRF header — reload the page"})
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc.lower() != (self.headers.get("Host") or "").lower():
            return self._json(403, {"error": "cross-origin request refused"})
        if self.readonly:
            return self._json(403, {"error": "this console is read-only"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json(400, {"error": "Content-Length must be a number"})
        if not 0 <= length <= MAX_BODY:  # a negative length would make the read below wait forever
            return self._json(413, {"error": "request too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:  # not JSON, or not UTF-8
            return self._json(400, {"error": "invalid JSON"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "the request body must be a JSON object"})
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api/action":
                return self._action(body)
            if path == "/api/repos" and self.portfolio.fixed is None:
                return self._repos(body)
        except UsageError as exc:  # an invalid console.toml
            return self._json(409, {"error": str(exc)})
        except (ValueError, TypeError) as exc:  # a field of the wrong type: refused, never a dropped connection
            return self._json(400, {"error": f"invalid request: {exc}"})
        except Exception as exc:
            return self._failed(exc)
        return self._json(404, {"error": "not found"})

    def _action(self, body: dict):
        root = self.portfolio.repos().get(str(body.get("repo") or ""))
        if root is None:
            return self._json(404, {"error": "unknown repository"})
        try:
            # Two actions on one repository (two tabs, the console and the CLI) must not interleave
            # their check and their record. submit/solo run the task's tests in between, which can take
            # minutes: they rely on record() taking the lock, so other writers are not held up.
            from .events import locked
            with locked(root) if body.get("action") not in ("submit", "solo") else nullcontext():
                msg = perform(root, signer_for(load_registry(), root), body)
        except (Refused, UsageError) as exc:
            return self._json(409, {"error": str(exc), "problems": getattr(exc, "problems", []),
                                    "code": getattr(exc, "code", None), "files": getattr(exc, "files", [])})
        finally:
            self.portfolio.invalidate(root)
        # `message` in English; `action`, `target` and `by` let the page say it in the reader's language
        return self._json(200, {"ok": True, "message": msg, "action": body.get("action"),
                                "target": body.get("target"), "by": getattr(msg, "by", "")})

    def _repos(self, body: dict):
        with REGISTRY_LOCK:  # read, change, write: another request must not save in between
            return self._repos_locked(body)

    def _repos_locked(self, body: dict):
        reg = load_registry()
        op, raw = body.get("op"), str(body.get("path") or "").strip()
        if op in ("setup", "add", "signer", "remove") and not raw:
            return self._json(400, {"error": "path is required"})
        if op == "setup":
            p = Path(raw).expanduser().resolve()
            if not _browsable(p):
                return self._json(403, {"error": "that folder is outside the folders this console may browse"})
            if not p.is_dir() or not (p / ".git").exists():
                return self._json(409, {"error": f"{p} is not a git repository — EGD needs git for its trail"})
            if is_repo(p):
                return self._json(409, {"error": f"{p} already has a plan folder"})
            if not os.access(p, os.W_OK):
                return self._json(409, {"error": f"{p} is read-only here. In Docker, restart with "
                                                 "EGD_BROWSE_MODE=rw, "
                                                 "or run `egd console` locally."})
            from .templates import setup_repo
            try:
                setup_repo(p)
            except OSError as exc:
                return self._json(409, {"error": f"could not write .egd/ in {p}: {exc.strerror}"})
            op = "add"
        if op == "add":
            p = Path(raw).expanduser().resolve()
            if not _browsable(p):  # as `setup` and browsing: the CLI (`egd console add`) is not confined
                return self._json(403, {"error": "that folder is outside the folders this console may browse"})
            if not is_repo(p):
                can = p.is_dir() and (p / ".git").exists()
                return self._json(409, {"error": f"{p} has no .egd/ (or .ai/features/) yet",
                                        "setup_possible": can})
            if str(p) not in reg["repos"]:
                reg["repos"].append(str(p))
        elif op == "signer":
            p = str(Path(raw).expanduser().resolve())
            if p not in {str(x) for x in repo_paths(reg)}:
                return self._json(404, {"error": "unknown repository"})
            name = " ".join(str(body.get("name") or "").split())[:60]
            signers = dict(reg.get("signers", {}))
            if name:
                signers[p] = name
            else:
                signers.pop(p, None)  # empty: sign with the console's default name
            raw_cfg = _raw_registry()
            save_registry({"user": raw_cfg.get("user", reg["user"]), "roots": raw_cfg.get("roots", []),
                           "repos": reg["repos"], "signers": signers})
            self.portfolio.invalidate()
            return self._json(200, {"ok": True, "name": name})
        elif op == "remove":
            p = Path(raw).expanduser().resolve()
            kept = [r for r in reg["repos"] if Path(r).expanduser().resolve() != p]
            if len(kept) == len(reg["repos"]):
                found = p in repo_paths(reg)
                return self._json(404, {"error": f"{p} is not in the list" + (
                    " — it is found by scanning a folder in `roots` of console.toml; remove that folder there"
                    if found else "")})
            reg["repos"] = kept
        else:
            return self._json(400, {"error": "op must be add, setup, signer or remove"})
        raw_cfg = _raw_registry()
        save_registry({"user": raw_cfg.get("user", reg["user"]), "roots": raw_cfg.get("roots", []),
                       "repos": reg["repos"]})
        self.portfolio.invalidate()
        return self._json(200, {"ok": True})


def _browsable(p: Path) -> bool:
    return any(p == r or p.is_relative_to(r) for r in browse_roots())


def host_name(header: str | None) -> str | None:
    """The host of a Host header, lower-cased — None when the header is malformed.
    `[::1]:8780` → `[::1]`, `localhost:8780` → `localhost`, `[::1]evil` → None."""
    host = (header or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return None
        name, rest = host[:end + 1], host[end + 1:]
    else:
        name, sep, port = host.partition(":")
        rest = sep + port
    if not name or not re.fullmatch(r"(?::[0-9]{0,5})?", rest):
        return None
    return name


class _Server(http.server.ThreadingHTTPServer):
    def server_close(self):
        super().server_close()
        self.RequestHandlerClass.portfolio.wait(5)  # a background read may still be running


class _Server6(_Server):
    """An IPv6 address to listen on (`--host ::1`, `--host ::`)."""
    address_family = socket.AF_INET6


def make_server(host="127.0.0.1", port=8780, token=None, readonly=False,
                roots: list[Path] | None = None, mode: str = "console", warm: bool = True,
                remote_readonly: bool = False) -> http.server.ThreadingHTTPServer:
    """`roots` pins the server to those repositories (read-only) — the dashboard mode. With `warm`,
    every repository is read in the background from the start, so the first page load is quick."""
    portfolio = Portfolio(roots=roots)
    handler = type("ConsoleHandler", (_Handler,), {
        "portfolio": portfolio, "token": token, "csrf": _secrets.token_urlsafe(24),
        "_readonly": readonly or roots is not None, "remote_readonly": remote_readonly, "mode": mode})
    host = host[1:-1] if host.startswith("[") and host.endswith("]") else host  # "[::1]" as in a URL
    server = (_Server6 if ":" in host else _Server)((host, port), handler)
    if warm:
        portfolio.warm()
    return server
