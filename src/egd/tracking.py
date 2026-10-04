"""Client-facing records: UAT verdicts, change requests, defects."""

from __future__ import annotations

from .events import State, record
from .model import Feature
from .team import Team
from .util import Refused

SEVERITIES = ("critical", "major", "minor", "trivial")
FOUND_IN = ("build", "review", "uat", "prod")


def _ref(item: dict) -> dict:
    """A follow-up names its CR/defect by the opener's uid too: ids can collide across branches."""
    return {"uid": item["uid"]} if item.get("uid") else {}


def _next_id(prefix: str, existing) -> str:
    nums = [int(x.split("-")[-1]) for x in existing if x.split("-")[-1].isdigit()]
    return f"{prefix}-{(max(nums) + 1 if nums else 1):03d}"


def anyone_may_sign(team: Team) -> list[str]:
    """With no team.toml every gate is signable by anyone — say so where it matters most."""
    return [] if team.configured else [
        "no roles defined — anyone may sign; add the client to .egd/team.toml"]


def uat(f: Feature, st: State, team: Team, slice_id: str, by: str, passed: bool, note: str) -> dict:
    team.require(by, "uat")
    f.slice(slice_id)
    if "build" not in st.gates and "build" in f.gates:
        raise Refused("UAT happens after the `build` gate")
    unfinished = [t["id"] for t in f.tasks_in(slice_id) if st.task(t["id"]).status != "done"]
    if unfinished:
        raise Refused(f"{slice_id} still has unfinished tasks: " + ", ".join(unfinished))
    if not passed and not note:
        raise Refused("a failed UAT needs --note saying what was wrong")
    event = record(f, "uat", by, slice=slice_id, passed=passed, note=note)
    event["notes"] = anyone_may_sign(team)
    return event


def cr_open(f: Feature, st: State, team: Team, by: str, title: str, reason: str,
            hours: float, affects: list[str]) -> dict:
    team.require(by, "work")
    if not title or not reason:
        raise Refused("a change request needs --title and --reason")
    cr = _next_id("CR", st.crs)
    # The CR covers the acceptance criteria and the planned work exactly as they stand now; the
    # work it drops is listed on it, so the client approves a drop it can see.
    dropped = dropped_since_agreed(f, st)
    event = record(f, "cr_opened", by, cr=cr, title=title, reason=reason, hours=hours,
                   affects=affects, scope=f.scope_hash(), dropped=dropped, **planned(f))
    event["notes"] = ([f"{cr} drops planned work: {', '.join(dropped)}"] if dropped else [])
    return event


def cr_decide(f: Feature, st: State, team: Team, cr: str, by: str, approve: bool, note: str) -> dict:
    team.require(by, "cr_approve")
    if cr not in st.crs:
        raise Refused(f"no change request {cr}")
    if st.crs[cr].get("status") != "pending":
        raise Refused(f"{cr} is already {st.crs[cr].get('status')}")
    opened = st.crs[cr]
    proposed = opened.get("scope")
    if approve and proposed and proposed != f.scope_hash():
        raise Refused(f"acceptance criteria changed after {cr} was opened — the client would be "
                      "approving edits it never saw; reject it and open a new CR")
    if approve and "planned_tasks" in opened:
        gone = gone_from(f, opened)
        if gone:
            raise Refused(f"planned work left the plan after {cr} was opened — the client would be "
                          f"approving a drop it never saw: {', '.join(gone)}",
                          [f"reject {cr} and open a new CR: it lists what the plan drops "
                           f"({', '.join(gone)}), so approving it agrees to the drop",
                           "or put the work back in plan.toml / proof.toml"])
    if not approve:
        event = record(f, "cr_rejected", by, cr=cr, **_ref(opened), note=note, scope=None)
    else:
        # Approval snapshots the scope and the planned work: the scope unlocks later gates, the
        # plan is what `build` checks for dropped tasks and proofs from now on. Nothing was dropped
        # since the CR was opened (refused above), so this plan is the one the client saw, plus
        # whatever was added since.
        event = record(f, "cr_approved", by, cr=cr, **_ref(opened), note=note,
                       scope=f.scope_hash(), **planned(f))
    event["notes"] = anyone_may_sign(team)
    return event


def planned(f: Feature) -> dict:
    return {"planned_tasks": [t["id"] for t in f.tasks],
            "planned_proofs": [p["id"] for p in f.proofs if p.get("id")]}


def gone_from(f: Feature, snapshot: dict) -> list[str]:
    """Task and proof ids in `snapshot` (an event carrying `planned(f)`) no longer in the plan."""
    tasks = {t["id"] for t in f.tasks}
    proofs = {p.get("id") for p in f.proofs}
    return ([t for t in snapshot.get("planned_tasks", []) if t not in tasks]
            + [p for p in snapshot.get("planned_proofs", []) if p not in proofs])


def agreed_plan(st: State) -> dict | None:
    """The latest agreed plan: the `slice` gate's snapshot, or the last approved CR's — in trail
    order (seq), never clock order. None when nothing was agreed, or the latest agreement predates
    snapshots (it may have dropped anything)."""
    snapshot = None
    for e in st.events:
        if (e.get("type") == "gate_passed" and e.get("gate") == "slice") or e.get("type") == "cr_approved":
            snapshot = e if "planned_tasks" in e else None
    return snapshot


def dropped_since_agreed(f: Feature, st: State) -> list[str]:
    snapshot = agreed_plan(st)
    return gone_from(f, snapshot) if snapshot else []


def defect_open(f: Feature, st: State, team: Team, by: str, title: str, severity: str,
                found_in: str, acs: list[str], task: str | None) -> dict:
    team.require(by, "work")
    if severity not in SEVERITIES:
        raise Refused(f"severity must be one of {', '.join(SEVERITIES)}")
    if found_in not in FOUND_IN:
        raise Refused(f"--found-in must be one of {', '.join(FOUND_IN)}")
    known = {a.get("id") for a in f.acs}
    unknown = [a for a in acs if a not in known]
    if unknown:
        raise Refused("unknown acceptance criteria: " + ", ".join(unknown))
    did = _next_id("BUG", st.defects)
    return record(f, "defect_opened", by, defect=did, title=title, severity=severity,
                  found_in=found_in, acs=acs, task=task)


def defect_move(f: Feature, st: State, team: Team, defect: str, by: str, to: str,
                task: str | None, note: str) -> dict:
    team.require(by, "work")
    if defect not in st.defects:
        raise Refused(f"no defect {defect}")
    current = st.defects[defect].get("status")
    allowed = {"fixed": ("open",), "closed": ("open", "fixed"), "reopened": ("fixed", "closed")}
    if current not in allowed[to]:
        raise Refused(f"{defect} is {current}; cannot mark it {to}")
    return record(f, f"defect_{to}", by, defect=defect, **_ref(st.defects[defect]), task=task, note=note)
