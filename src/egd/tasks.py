"""Task lifecycle: todo → doing → review → done, with block/unblock and reject.

    start   — needs the `slice` gate and every dependency done
    submit  — needs the done_when list confirmed, tests green, no scope drift
    accept  — someone other than the submitter and the task's owner
    solo    — the one-person shortcut; recorded as a bypass, never silent
"""

from __future__ import annotations

from .events import State, locked, record, replay
from .model import Feature
from .proof import redact_text
from .proof.testcmd import TESTS_UNSET, run_tests
from .team import Team
from .util import (Refused, blob_hashes, dirty_paths, files_for_task, head_sha, known_secrets, local_record,
                   matches_any)


def _need(st: State, task_id: str, *allowed: str) -> None:
    status = st.task(task_id).status
    if status not in allowed:
        raise Refused(f"{task_id} is {status}; this needs it to be {' or '.join(allowed)}")


def start(f: Feature, st: State, cfg: dict, team: Team, task_id: str, by: str, model: str | None = None) -> dict:
    team.require(by, "work")
    task = f.task(task_id)
    if st.closed:
        raise Refused(f"{f.slug} is closed ({st.closed.get('outcome')}) — nothing starts on it")
    if "slice" not in st.gates:
        raise Refused("tasks start after the `slice` gate — the plan is not settled yet")
    _need(st, task_id, "todo")
    waiting = [d for d in task.get("depends_on", []) if st.task(d).status != "done"]
    if waiting:
        raise Refused(f"{task_id} waits on " + ", ".join(f"{d} ({st.task(d).status})" for d in waiting))
    # what was already uncommitted is someone's local setup: submit leaves it out of the footprint.
    # Kept in this clone's own notes, not the trail: a content hash there would let anyone
    # confirm a guess at a local file.
    before = blob_hashes(f.root, dirty_paths(f.root)[:1000])
    if before:
        local_record(f.root, "starts", f"{f.slug}/{task_id}", before)
    event = record(f, "task_started", by, task=task_id, sha=head_sha(f.root), model=model or None)
    if before:
        event["notes"] = [f"{len(before)} uncommitted file{'' if len(before) == 1 else 's'} already here "
                          f"count as your local setup, not {task_id}'s work, while left as they are"]
    return event


def _drift(f: Feature, st: State, task: dict) -> tuple[list[str], list[str]]:
    """(files outside the task's declared footprint, notes)."""
    allowed = list(task.get("touches", [])) + list(task.get("tests", []))
    if not allowed:
        return [], ["no touches declared — scope drift not checked"]
    ts = st.task(task["id"])
    before = ts.started_before or local_record(f.root, "starts", f"{f.slug}/{task['id']}")
    files, found, unnamed = files_for_task(f.root, task["id"], ts.started_sha, before)
    notes = []
    if not found:
        notes.append(f"no commit mentions {task['id']} — only the working tree was checked; "
                     f"put the task id in commit messages")
    stray = sorted(p for p in unnamed if not matches_any(p, allowed))
    if stray:
        notes.append(f"commits that do not name {task['id']} changed files outside its touches: "
                     + ", ".join(stray[:5]) + (" …" if len(stray) > 5 else "")
                     + f" — if that was {task['id']}'s work, it is drift")
    return sorted(p for p in files if not matches_any(p, allowed)), notes


def tests_skipped(cfg: dict, task: dict) -> str | None:
    """Why `submit` will not run this task's tests, or None when it will."""
    command = cfg.get("proof", {}).get("test_command")
    if command == "":  # said on purpose: this repository has no test runner
        return None
    if not command:
        return TESTS_UNSET
    if not task.get("tests"):
        return f"tests not run — {task['id']} lists no tests"
    return None


def _verify(f: Feature, cfg: dict, task: dict) -> dict | None:
    result = run_tests(f.root, cfg, task.get("tests", []))
    if result is None:
        return None
    # the run lands on the committed trail: no secret the tests printed may go with it
    secrets = known_secrets(f.root, cfg, f.raw_proofs)
    return {"command": redact_text(result["command"], secrets), "exit": result["exit"],
            "ms": result["ms"], "tail": redact_text(result["output"], secrets)[-1500:]}


def _finish(f, st, cfg, task_id, by, kind, confirm, allow_drift, drift_reason, allowed=("doing",), **data) -> dict:
    """submit and solo: done_when confirmed, footprint checked, tests green, then recorded."""
    task = f.task(task_id)
    if not confirm:
        items = "\n".join(f"  - {i}" for i in task.get("done_when", []))
        raise Refused(f"confirm the done_when list with --confirm:\n{items}")
    outside, notes = _drift(f, st, task)
    if outside and not allow_drift:
        raise Refused(f"{task_id} changed files outside its touches/tests",
                      outside + ["re-plan the task, or pass --allow-drift --drift-reason '<why>'"],
                      code="drift", files=outside)
    if allow_drift and not (drift_reason and drift_reason.strip()):
        raise Refused("--allow-drift needs --drift-reason")
    run = _verify(f, cfg, task)
    if run and run["exit"] != 0:
        raise Refused(f"tests failed (exit {run['exit']}): {run['command']}", [run["tail"][-800:]])
    skipped = None if run else tests_skipped(cfg, task)
    with locked(f.root):  # the tests took a while: someone may have blocked or rejected it meanwhile
        try:
            _need(replay(f), task_id, *allowed)
        except Refused as exc:
            raise Refused(f"{task_id} changed while its tests ran — {exc}") from None
        event = record(f, kind, by, task=task_id, **data, drift=outside, drift_override=bool(outside),
                       drift_reason=drift_reason if outside else None, test_run=run,
                       tests_skipped=skipped, sha=head_sha(f.root))
    event["notes"] = notes + ([skipped] if skipped else [])
    return event


def submit(f, st, cfg, team, task_id, by, confirm=False, spent=None,
           allow_drift=False, reason=None) -> dict:
    team.require(by, "work")
    _need(st, task_id, "doing")
    return _finish(f, st, cfg, task_id, by, "task_submitted", confirm, allow_drift, reason,
                   spent_h=spent, done_when=f.task(task_id).get("done_when", []))


def accept(f, st, cfg, team, task_id, by) -> dict:
    team.require(by, "review")
    _need(st, task_id, "review")
    ts = st.task(task_id)
    for who, did in ((ts.submitted_by, "submitted"), (ts.owner, "did")):
        if team.same(who, by):
            raise Refused(f"{by} {did} {task_id}; someone else must accept it "
                          "(working alone? `egd solo` records the bypass)")
    return record(f, "task_accepted", by, task=task_id)


def reject(f, st, cfg, team, task_id, by, reason) -> dict:
    team.require(by, "review")
    _need(st, task_id, "review")
    if not reason:
        raise Refused("a rejection needs --reason")
    return record(f, "task_rejected", by, task=task_id, reason=reason)


def solo(f, st, cfg, team, task_id, by, reason, confirm=False, spent=None,
         allow_drift=False, drift_reason=None) -> dict:
    team.require(by, "work")
    _need(st, task_id, "doing", "review")
    if not reason:
        raise Refused("skipping review needs --reason (it is recorded)")
    return _finish(f, st, cfg, task_id, by, "task_done_solo", confirm, allow_drift, drift_reason,
                   allowed=("doing", "review"), reason=reason, spent_h=spent)


def block(f, st, cfg, team, task_id, by, reason) -> dict:
    team.require(by, "work")
    f.task(task_id)
    _need(st, task_id, "todo", "doing", "review")
    if not reason:
        raise Refused("blocking needs --reason — what is it waiting on?")
    return record(f, "task_blocked", by, task=task_id, reason=reason)


def unblock(f, st, cfg, team, task_id, by) -> dict:
    team.require(by, "work")
    _need(st, task_id, "blocked")
    return record(f, "task_unblocked", by, task=task_id)


def reopen(f, st, cfg, team, task_id, by, reason) -> dict:
    team.require(by, "review")
    _need(st, task_id, "done")
    if not reason:
        raise Refused("reopening needs --reason")
    return record(f, "task_reopened", by, task=task_id, reason=reason)
