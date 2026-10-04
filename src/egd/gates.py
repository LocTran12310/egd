"""Gate rules. Each check returns the list of reasons the gate is closed."""

from __future__ import annotations

from .events import State, record
from .graph import validate
from .model import Feature
from .proof import proof_matrix, unrunnable
from .team import Team
from .tracking import agreed_plan, anyone_may_sign, gone_from, planned
from .util import Refused, egd_dir, has_todo, is_blank_template, md_sections

BRIEF_SECTIONS = ("problem", "outcome", "success signal", "out of scope")
DESIGN_SECTIONS = ("approach", "alternatives considered", "failure modes")
SERIOUS = ("critical", "major")

NEXT = {
    "frame": "write brief.md (problem, outcome, success signal, out of scope)"
             " and get .egd/map.md reviewed",
    "clarify": "fill [[assumption]] and [[ac]] in plan.toml; resolve every blocking assumption",
    "design": "write design.md and settle every [[decision]]",
    "slice": "cut [[slice]] and [[task]] entries; `egd graph` until clean",
    "build": "work the ready tasks (`egd ready`); run `egd proof run` before passing",
    "accept": "client walks each slice's demo and records `egd uat <slice> --pass`",
    "release": "write release.md (rollback); close assumptions and serious defects",
}


def _scope(feature: Feature, state: State) -> list[str]:
    if "clarify" not in state.gates:
        return []
    if feature.scope_hash() in state.approved_scopes():
        return []
    return ["acceptance criteria changed since `clarify` was passed — record the change "
            "with `egd cr open` and get it approved"]


def _serious_defects(state: State) -> list[str]:
    return [f"{d['defect']}: open {d.get('severity')} defect — {d.get('title', '')}"
            for d in state.defects.values()
            if d.get("status") == "open" and d.get("severity") in SERIOUS]


def check_frame(f: Feature, st: State, cfg: dict) -> list[str]:
    problems = []
    brief = f.text("brief.md")
    if not brief:
        return ["brief.md is missing"]
    sections = md_sections(brief)
    for name in BRIEF_SECTIONS:
        if not sections.get(name):
            problems.append(f"brief.md: '## {name.capitalize()}' is empty")
    if has_todo(brief):
        problems.append("brief.md still contains TODO/TBD")
    if f.tier != "lite":
        map_path = egd_dir(f.root) / "map.md"
        if not map_path.exists():
            problems.append(".egd/map.md is missing — map the repository first")
        else:
            map_text = map_path.read_text(encoding="utf-8")
            reviewed = [ln.split(":", 1)[1].strip() for ln in map_text.splitlines()
                        if ln.lower().startswith("reviewed_by:")]
            if is_blank_template(map_text):
                problems.append(".egd/map.md is still the blank template — map the repository first")
            elif not reviewed or not reviewed[0]:
                problems.append(".egd/map.md has no reviewed_by — a person must confirm it")
    return problems


def check_clarify(f: Feature, st: State, cfg: dict) -> list[str]:
    problems = []
    if f.tier != "lite" and not f.assumptions:
        problems.append("no assumptions recorded — every unasked question is one")
    for a in f.assumptions:
        aid, status = a.get("id"), a.get("status", "open")
        if status not in ("open", "confirmed", "rejected"):
            problems.append(f"{aid}: status must be open, confirmed or rejected")
        if status == "open" and a.get("blocking"):
            problems.append(f"{aid}: blocking and still open — {a.get('text', '')}")
        if status != "open" and not str(a.get("resolution", "")).strip():
            problems.append(f"{aid}: {status} without a resolution note")
    if not f.acs:
        problems.append("no acceptance criteria")
    for a in f.acs:
        for part in ("given", "when", "then"):
            if not str(a.get(part, "")).strip():
                problems.append(f"{a.get('id')}: missing '{part}'")
    return problems


def check_design(f: Feature, st: State, cfg: dict) -> list[str]:
    problems = []
    design = f.text("design.md")
    if not design:
        problems.append("design.md is missing")
    else:
        sections = md_sections(design)
        for name in DESIGN_SECTIONS:
            if not sections.get(name):
                problems.append(f"design.md: '## {name.capitalize()}' is empty")
        if has_todo(design):
            problems.append("design.md still contains TODO/TBD")
    for d in f.decisions:
        if d.get("status", "proposed") == "proposed":
            problems.append(f"{d.get('id')}: decision still proposed — {d.get('title', '')}")
    if f.tier == "full" and not any(d.get("status") == "accepted" for d in f.decisions):
        problems.append("full tier needs at least one accepted [[decision]]")
    return problems


def check_slice(f: Feature, st: State, cfg: dict) -> list[str]:
    errors, _ = validate(f, cfg)
    return errors + _scope(f, st)


def _dropped(f: Feature, st: State) -> list[str]:
    """Tasks or proofs in the latest agreed plan — the `slice` gate's, or the last approved
    CR's — that have since vanished from plan.toml."""
    snapshot = agreed_plan(st)
    if snapshot is None:
        return []
    gone = gone_from(f, snapshot)
    where = ("at `slice`" if snapshot.get("type") == "gate_passed"
             else f"when {snapshot.get('cr')} was approved")
    return [f"{x}: planned {where} but removed — dropping planned work needs an approved CR"
            for x in gone]


def check_build(f: Feature, st: State, cfg: dict) -> list[str]:
    problems = _dropped(f, st)
    for t in f.tasks:
        status = st.task(t["id"]).status
        if status != "done":
            problems.append(f"{t['id']}: {status}")
    for cr in st.crs.values():
        if cr.get("status") == "pending":
            problems.append(f"{cr['cr']}: change request awaiting a decision")
    problems += unrunnable(f, cfg)
    for p, env, required, run, state in proof_matrix(f, st, cfg):
        if not required or state == "pass":
            continue
        if run is None:
            problems.append(f"{p['id']}@{env}: never run — `egd proof run`")
        elif state == "fail":
            problems.append(f"{p['id']}@{env}: last run {run.get('status')}"
                            + (f" — {run['reason']}" if run.get("reason") else ""))
        elif state == "stale":
            problems.append(f"{p['id']}@{env}: stale — the code or the proof changed since it ran")
        else:
            problems.append(f"{p['id']}@{env}: freshness cannot be verified without git")
    return problems + _scope(f, st)


def check_accept(f: Feature, st: State, cfg: dict) -> list[str]:
    problems = []
    for s in f.slices:
        verdict = st.uat.get(s["id"])
        if verdict is None:
            problems.append(f"{s['id']}: no UAT verdict")
        elif not verdict.get("passed"):
            problems.append(f"{s['id']}: UAT failed — {verdict.get('note', '')}")
    return problems + _serious_defects(st) + _scope(f, st)


def check_release(f: Feature, st: State, cfg: dict) -> list[str]:
    problems = []
    release = f.sections("release.md")
    if not release.get("rollback"):
        problems.append("release.md: '## Rollback' is empty — how is this undone?")
    for a in f.assumptions:
        if a.get("status", "open") == "open":
            problems.append(f"{a.get('id')}: assumption still open")
    for d in f.decisions:
        if d.get("status", "proposed") == "proposed":
            problems.append(f"{d.get('id')}: decision still proposed")
    errors, _ = validate(f, cfg)
    problems += [f"plan: {e}" for e in errors]
    return problems + _serious_defects(st) + _scope(f, st)


CHECKS = {
    "frame": check_frame,
    "clarify": check_clarify,
    "design": check_design,
    "slice": check_slice,
    "build": check_build,
    "accept": check_accept,
    "release": check_release,
}

GATE_ROLE = {"accept": "uat"}


def next_gate(f: Feature, st: State) -> str | None:
    """The first gate not passed — None once released, or while the feature is closed."""
    if st.closed:
        return None
    for g in f.gates:
        if g not in st.gates:
            return g
    return None


def check(f: Feature, st: State, cfg: dict, gate: str) -> list[str]:
    if gate not in f.gates:
        raise Refused(f"'{gate}' is not a gate for tier {f.tier} ({', '.join(f.gates)})")
    problems = []
    idx = f.gates.index(gate)
    for earlier in f.gates[:idx]:
        if earlier not in st.gates:
            problems.append(f"gate '{earlier}' has not been passed")
            break
    if st.corrupt:
        problems.append("unreadable event files: " + ", ".join(st.corrupt))
    if f.error:
        problems.append(f.error)
    return problems + CHECKS[gate](f, st, cfg)


def stage(f: Feature, st: State) -> str:
    """Where a feature stands in one word: its next gate, "released", or "closed"."""
    return "closed" if st.closed else next_gate(f, st) or "released"


OUTCOMES = {"dropped": "stopped — it will not ship", "research": "a spike: what it found is the answer",
            "superseded": "replaced by other work"}


def close(f: Feature, st: State, team: Team, by: str, outcome: str, reason: str) -> dict:
    """End a feature that will not be released — dropped mid-way, a research spike, or replaced.
    Nothing is passed or proven by it: the trail says it stopped, why, and what was left open."""
    team.require(by, "gate")
    if st.closed:
        raise Refused(f"{f.slug} is already closed ({st.closed.get('outcome')}) — `egd close --undo` reopens it")
    if "release" in st.gates:
        raise Refused(f"{f.slug} is released — there is nothing to close")
    if outcome not in OUTCOMES:
        raise Refused(f"--as is one of: {', '.join(OUTCOMES)}")
    if not (reason or "").strip():
        raise Refused("closing needs --reason: why it stops" + (", and what it found" if outcome == "research" else ""))
    open_tasks = sorted(tid for tid, t in st.tasks.items() if t.status != "done")
    open_tasks += sorted(t["id"] for t in f.tasks if t["id"] not in st.tasks)
    return record(f, "feature_closed", by, outcome=outcome, reason=reason.strip(),
                  open_tasks=open_tasks, next_gate=next_gate(f, st))


def reopen(f: Feature, st: State, team: Team, by: str, reason: str) -> dict:
    team.require(by, "gate")
    if not st.closed:
        raise Refused(f"{f.slug} is not closed")
    if not (reason or "").strip():
        raise Refused("reopening needs --reason")
    return record(f, "feature_reopened", by, reason=reason.strip())


def pass_gate(f: Feature, st: State, cfg: dict, team: Team, gate: str, by: str) -> dict:
    if st.closed:
        raise Refused(f"{f.slug} is closed ({st.closed.get('outcome')}) — `egd close --undo --reason …` first")
    team.require(by, GATE_ROLE.get(gate, "gate"))
    if gate in st.gates:
        raise Refused(f"'{gate}' was already passed by {st.gates[gate]['by']} at {st.gates[gate]['at']}")
    problems = check(f, st, cfg, gate)
    if problems:
        raise Refused(f"gate '{gate}' is closed", problems)
    data = {"gate": gate}
    if gate in ("clarify", "slice"):
        data["scope"] = f.scope_hash()
    if gate == "slice":
        data.update(planned(f))
    event = record(f, "gate_passed", by, **data)
    event["notes"] = anyone_may_sign(team) if gate in GATE_ROLE else []  # the client's gate
    return event
