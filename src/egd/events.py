"""The trail: one JSON file per event, replayed into the current state.

One file per event is a deliberate choice for teams. Two people moving two
tickets on two branches add two different files, so the trail never produces a
merge conflict, and there is no state file anyone could hand-edit into a gate.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .model import Feature
from .util import Refused, egd_dir, hours_between, iso, now, write_atomic

# Opened CRs and defects carry a `uid`; follow-ups name it, so a renamed collision finds its own.
OPENED = ("cr_opened", "defect_opened")


# --------------------------------------------------------------------- the writer lock

LOCK_FILE = ".lock"
LOCK_WAIT = 10.0   # seconds a writer waits for another before giving up
LOCK_STALE = 60.0  # a lock this old whose process is gone was left by a crash


class _RootLock:
    """One per repository root: a thread lock, and how deep this thread is inside `locked`."""

    def __init__(self):
        self.thread = threading.RLock()
        self.depth = 0
        self.token = ""


_ROOT_LOCKS: dict[str, _RootLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


@contextmanager
def locked(root: Path, timeout: float = LOCK_WAIT):
    """`with locked(root):` — this thread alone writes the repository's trail until the block ends.

    Wrap a check-then-record (replay, decide, record) in it so two writers cannot both pass the
    same check: across threads by a per-root re-entrant lock, across processes by `.egd/.lock`,
    created exclusively and holding the writer's pid, host and time. Re-entrant within a thread:
    nested blocks (and `record`, which takes it too) share the one lock file. Waits up to
    `timeout` seconds, then refuses; a lock file older than a minute whose process is gone is a
    crash's leftover and is removed.
    """
    key = str(Path(root).resolve())
    with _ROOT_LOCKS_GUARD:
        lock = _ROOT_LOCKS.setdefault(key, _RootLock())
    deadline = time.monotonic() + timeout
    path = egd_dir(Path(key)) / LOCK_FILE
    if not lock.thread.acquire(timeout=max(timeout, 0)):
        raise Refused(f"another egd is writing to {key} — try again")
    try:
        if lock.depth == 0 and path.parent.is_dir():
            lock.token = _take(path, deadline)
        lock.depth += 1
        try:
            yield
        finally:
            lock.depth -= 1
            if lock.depth == 0 and lock.token:
                _release(path, lock.token)
                lock.token = ""
    finally:
        lock.thread.release()


def _take(path: Path, deadline: float) -> str:
    token = f"{os.getpid()} {socket.gethostname()} {iso()} {os.urandom(4).hex()}\n"
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if _stale(path):
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                holder = _holder(path)
                raise Refused(f"another egd is writing to this repository{holder} — try again "
                              f"(if no egd is running, delete {path})") from None
            time.sleep(0.05)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)
        return token


def _release(path: Path, token: str) -> None:
    try:
        if path.read_text(encoding="utf-8") == token:  # never remove a lock someone else now holds
            path.unlink()
    except OSError:
        pass


def _holder(path: Path) -> str:
    try:
        pid, _, at, *_ = path.read_text(encoding="utf-8").split()
        return f" (pid {pid} since {at})"
    except (OSError, ValueError):
        return ""


def _stale(path: Path) -> bool:
    """Older than LOCK_STALE and its process gone (or unknowable: another host, Windows)."""
    try:
        age = time.time() - path.stat().st_mtime
        parts = path.read_text(encoding="utf-8").split()
    except OSError:
        return False  # gone already, or unreadable for now: try again
    if age < LOCK_STALE:
        return False
    if len(parts) < 2 or not parts[0].isdigit() or parts[1] != socket.gethostname() or os.name == "nt":
        return True  # on Windows os.kill(pid, 0) would terminate the process, not probe it
    try:
        os.kill(int(parts[0]), 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


# --------------------------------------------------------------------- events

def _events_dir(feature: Feature) -> Path:
    return feature.path / "events"


def new_uid() -> str:
    return os.urandom(4).hex()


def write_event(folder: Path, event: dict, stamp: str) -> Path:
    """The one way an event reaches disk: `<seq>-<stamp>-<random>-<type>.json`, written whole
    (temp file, then rename) so a crash or a full disk never leaves half an event behind."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{event['seq']:06d}-{stamp}-{os.urandom(3).hex()}-{event['type']}.json"
    write_atomic(path, json.dumps(event, indent=2, ensure_ascii=False) + "\n")
    return path


def _last_seq(folder: Path) -> int:
    """Highest seq, read from the 6-digit file name prefix; a file without one is parsed."""
    best = 0
    if not folder.is_dir():
        return 0
    for p in folder.glob("*.json"):
        head = p.name[:7]
        if head[:6].isdigit() and head[6:] == "-":
            best = max(best, int(head[:6]))
            continue
        try:
            seq = json.loads(p.read_text(encoding="utf-8")).get("seq", 0)
        except (OSError, ValueError, UnicodeDecodeError, AttributeError):
            continue
        if isinstance(seq, int):
            best = max(best, seq)
    return best


def record(feature: Feature, event_type: str, by: str, /, **data) -> dict:
    """Append one event. `seq` is one more than the highest seq this checkout has
    seen, so order on a single machine never depends on its clock; events made on
    two branches before a merge share a seq and fall back to time."""
    folder = _events_dir(feature)
    with locked(feature.root):  # two writers must never take the same seq
        ts = now()
        event = {"type": event_type, "seq": _last_seq(folder) + 1, "at": iso(ts), "by": by.strip(), **data}
        if event_type in OPENED:
            event.setdefault("uid", new_uid())
        write_event(folder, event, ts.strftime("%Y%m%dT%H%M%S%f"))
    return event


def read(feature: Feature) -> list[dict]:
    folder = _events_dir(feature)
    if not folder.is_dir():
        return []
    events = []
    for p in sorted(folder.glob("*.json")):
        try:
            event = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            event = None  # unparseable, or gone between listing and reading (a checkout)
        if (not isinstance(event, dict) or not isinstance(event.get("type"), str)
                or not isinstance(event.get("seq", 0), int) or not isinstance(event.get("at", ""), str)):
            event = {"type": "corrupt", "file": p.name, "at": "", "by": ""}
        event["_file"] = p.name
        events.append(event)
    events.sort(key=lambda e: (e.get("seq", 0), e.get("at", ""), e["_file"]))
    return events


@dataclass
class TaskState:
    status: str = "todo"
    owner: str | None = None
    started_at: str | None = None
    started_sha: str | None = None
    started_before: dict | None = None  # uncommitted changes already there at start: path → hash
    submitted_by: str | None = None
    submitted_at: str | None = None
    done_at: str | None = None
    spent_h: float | None = None
    rejects: int = 0
    solo: bool = False
    drift_overrides: int = 0
    blocked_from: str | None = None
    block_reason: str | None = None
    review_wait_h: float | None = None


@dataclass
class State:
    gates: dict[str, dict] = field(default_factory=dict)
    tasks: dict[str, TaskState] = field(default_factory=dict)
    uat: dict[str, dict] = field(default_factory=dict)
    crs: dict[str, dict] = field(default_factory=dict)
    defects: dict[str, dict] = field(default_factory=dict)
    proof_runs: list[dict] = field(default_factory=list)
    issues: dict[str, int] = field(default_factory=dict)
    issue_digest: dict[str, str] = field(default_factory=dict)
    corrupt: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    uids: dict[str, str] = field(default_factory=dict)  # opened event uid → its CR/BUG id here
    closed: dict | None = None  # the feature_closed event while the feature is closed

    def task(self, task_id: str) -> TaskState:
        return self.tasks.setdefault(task_id, TaskState())

    def approved_scopes(self) -> set[str]:
        scopes = {g["scope"] for g in self.gates.values() if g.get("scope")}
        scopes |= {cr["scope"] for cr in self.crs.values()
                   if cr.get("status") == "approved" and cr.get("scope")}
        return scopes

    def latest_proofs(self) -> dict[tuple[str, str], dict]:
        """(proof id, env) → its most recent run."""
        latest: dict[tuple[str, str], dict] = {}
        for run in self.proof_runs:
            latest[(run["proof"], run["env"])] = run
        return latest


def _opened(st: State, e: dict, key: str, table: dict, status: str) -> None:
    """A CR or defect opened. Two branches can each open BUG-003; both are kept, the later one
    (in trail order) renamed BUG-003b, so neither vanishes in the merge."""
    rid = e[key]
    if rid in table:
        n = 1
        while f"{rid}{chr(97 + n)}" in table:
            n += 1
        new = f"{rid}{chr(97 + n)}"
        st.warnings.append(f"{rid} was opened twice (on two branches?) — the later one, "
                           f"\"{e.get('title', '')}\", is shown as {new}")
        rid = new
    table[rid] = {**e, key: rid, "status": status}
    if e.get("uid"):
        st.uids[e["uid"]] = rid


def _followed(st: State, e: dict, key: str, table: dict) -> dict:
    """The CR or defect a follow-up acts on: by uid when it carries one, else by id (old trails)."""
    rid = st.uids.get(e.get("uid") or "", e[key])
    return table.setdefault(rid, {key: rid})


# What each event must carry before it may touch the state: an event missing one is reported
# as unreadable without having changed anything (never half-applied).
REQUIRED = {
    "gate_passed": ("gate", "by", "at"),
    "task_started": ("task", "by", "at"), "task_submitted": ("task", "by", "at"),
    "task_accepted": ("task", "at"), "task_rejected": ("task",), "task_done_solo": ("task", "at"),
    "task_blocked": ("task",), "task_unblocked": ("task",), "task_reopened": ("task",),
    "uat": ("slice",),
    "cr_opened": ("cr",), "cr_approved": ("cr", "by", "at"), "cr_rejected": ("cr", "by", "at"),
    "defect_opened": ("defect",), "defect_fixed": ("defect", "at"),
    "defect_closed": ("defect", "at"), "defect_reopened": ("defect", "at"),
    "proof_run": ("proof", "env"),
    "issue_linked": ("task", "number"),
}


def _valid(e: dict) -> bool:
    for k in REQUIRED.get(e.get("type"), ()):
        want = int if k == "number" else str
        if not isinstance(e.get(k), want) or isinstance(e.get(k), bool):
            return False
    return True


def replay(feature: Feature) -> State:
    st = State()
    st.events = read(feature)
    for e in st.events:
        try:
            if not _valid(e):
                raise ValueError("missing or mistyped field")
            _apply(st, e)
        except (KeyError, TypeError, ValueError, AttributeError):
            # valid JSON that is not a valid event: reported like an unreadable file, never a crash
            st.corrupt.append(e.get("_file") or e.get("file") or "?")
    return st


def _apply(st: State, e: dict) -> None:
    kind = e.get("type")
    if kind == "corrupt":
        st.corrupt.append(e["file"])
    elif kind == "feature_closed":
        st.closed = e
    elif kind == "feature_reopened":
        st.closed = None
    elif kind == "gate_passed":
        st.gates[e["gate"]] = e
    elif kind == "task_started":
        t = st.task(e["task"])
        t.status, t.owner = "doing", e["by"]
        t.started_at, t.started_sha, t.started_before = e["at"], e.get("sha"), e.get("before")
    elif kind == "task_submitted":
        t = st.task(e["task"])
        t.status, t.submitted_by, t.submitted_at = "review", e["by"], e["at"]
        t.spent_h = e.get("spent_h", t.spent_h)
        if e.get("drift_override"):
            t.drift_overrides += 1
    elif kind == "task_accepted":
        t = st.task(e["task"])
        t.status, t.done_at = "done", e["at"]
        if t.submitted_at:
            t.review_wait_h = hours_between(t.submitted_at, e["at"])
    elif kind == "task_rejected":
        t = st.task(e["task"])
        t.status, t.rejects = "doing", t.rejects + 1
    elif kind == "task_done_solo":
        t = st.task(e["task"])
        t.status, t.done_at, t.solo = "done", e["at"], True
        t.spent_h = e.get("spent_h", t.spent_h)
        if e.get("drift_override"):
            t.drift_overrides += 1
    elif kind == "task_blocked":
        t = st.task(e["task"])
        t.blocked_from, t.block_reason, t.status = t.status, e.get("reason"), "blocked"
    elif kind == "task_unblocked":
        t = st.task(e["task"])
        t.status, t.blocked_from, t.block_reason = t.blocked_from or "todo", None, None
    elif kind == "task_reopened":
        t = st.task(e["task"])
        t.status, t.done_at = "doing", None
    elif kind == "uat":
        st.uat[e["slice"]] = e
    elif kind == "cr_opened":
        _opened(st, e, "cr", st.crs, "pending")
    elif kind in ("cr_approved", "cr_rejected"):
        cr = _followed(st, e, "cr", st.crs)
        cr["status"] = "approved" if kind == "cr_approved" else "rejected"
        cr["decided_by"], cr["decided_at"] = e["by"], e["at"]
        if e.get("scope"):
            cr["scope"] = e["scope"]
    elif kind == "defect_opened":
        _opened(st, e, "defect", st.defects, "open")
    elif kind in ("defect_fixed", "defect_closed", "defect_reopened"):
        d = _followed(st, e, "defect", st.defects)
        d["status"] = {"defect_fixed": "fixed", "defect_closed": "closed",
                       "defect_reopened": "open"}[kind]
        d[f"{d['status']}_at"] = e["at"]
        if e.get("task"):
            d["fix_task"] = e["task"]
    elif kind == "proof_run":
        st.proof_runs.append(e)
    elif kind == "issue_linked":
        st.issues[e["task"]] = e["number"]
        st.issue_digest[e["task"]] = e.get("digest", "")

