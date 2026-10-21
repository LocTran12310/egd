"""What a change to some acceptance criteria reaches: slices, tasks, proofs, hours.

Computed from the plan's own graph — a slice `covers` ACs, a task belongs to a slice, a proof
`verifies` ACs — and the trail's state, so nobody lists it by hand. A task names no AC itself:
it is reached through its slice, so every task of a slice covering a changed AC is counted.

It is information, never a gate: `egd cr impact` previews it, `egd cr open` prints it and keeps
a copy on the `cr_opened` event, so reports show what the client was told when they decided.
"""

from __future__ import annotations

from .events import State
from .model import Feature
from .proof import proof_matrix

DONE, ACTIVE, TODO = "done", "active", "todo"
# worst first: what a proof's envs add up to
_PROOF_ORDER = ("fail", "unrun", "stale", "unknown", "pass")


def _group(status: str) -> str:
    """done → it must be reopened or added to; doing, review, blocked → being worked; todo → not begun."""
    return DONE if status == "done" else TODO if status == "todo" else ACTIVE


def impact(f: Feature, st: State, cfg: dict, acs: list[str]) -> dict:
    """What changing `acs` reaches, as plain data (it is stored on the `cr_opened` event).

    slices: [{id, title, uat}] — uat is "passed", "failed" or None; a passed one is walked again.
    tasks:  [{id, title, status, group, estimate_h}] — group is done, active or todo.
    proofs: [{id, state, envs}] — state is the worst of its envs (required ones, if any); every
            one of them runs again once the ACs change, whatever it says now.
    hours:  the estimates of every task reached, added up — a rough upper bound: a change rarely
            redoes a whole task, and work it adds is not in the plan yet.
    """
    wanted = set(acs)
    slices = [s for s in f.slices if wanted & set(s.get("covers") or [])]
    slice_ids = {s["id"] for s in slices}
    tasks = [t for t in f.tasks if t.get("slice") in slice_ids]
    envs: dict[str, dict[str, tuple[bool, str]]] = {}
    for p, env, required, _, state in proof_matrix(f, st, cfg):
        if wanted & set(p.get("verifies") or []):
            envs.setdefault(p["id"], {})[env] = (required, state)
    proofs = []
    for pid, runs in envs.items():
        counted = [s for req, s in runs.values() if req] or [s for _, s in runs.values()]
        proofs.append({"id": pid, "state": min(counted, key=_PROOF_ORDER.index),
                       "envs": {env: s for env, (_, s) in runs.items()}})
    out_tasks = []
    for t in tasks:
        status = st.task(t["id"]).status
        est = t.get("estimate_h")
        est = float(est) if isinstance(est, (int, float)) and not isinstance(est, bool) else 0.0
        out_tasks.append({"id": t["id"], "title": t.get("title", ""), "status": status,
                          "group": _group(status), "estimate_h": est})
    uat = {s: ("passed" if v.get("passed") else "failed") for s, v in st.uat.items()}
    return {
        "acs": sorted(wanted),
        "slices": [{"id": s["id"], "title": s.get("title", ""), "uat": uat.get(s["id"])} for s in slices],
        "tasks": out_tasks,
        "proofs": proofs,
        "hours": round(sum(t["estimate_h"] for t in out_tasks), 2),
    }


def _ids(items: list[dict]) -> str:
    return ", ".join(i["id"] for i in items)


def lines(imp: dict) -> list[str]:
    """The impact as a short table for the terminal."""
    out = [f"impact of changing {', '.join(imp['acs'])}:"]
    slices = imp["slices"]
    uat = [s for s in slices if s.get("uat") == "passed"]
    out.append("  slices   " + (", ".join(s["id"] + (" (UAT passed — walk it again)" if s in uat else "")
                                        for s in slices) or "none cover them"))
    by = {g: [t for t in imp["tasks"] if t["group"] == g] for g in (DONE, ACTIVE, TODO)}
    if imp["tasks"]:
        if by[DONE]:
            out.append(f"  tasks    done, to reopen or add to: {_ids(by[DONE])}")
        if by[ACTIVE]:
            out.append("  tasks    in progress: " + ", ".join(f"{t['id']} ({t['status']})" for t in by[ACTIVE]))
        if by[TODO]:
            out.append(f"  tasks    not started: {_ids(by[TODO])}")
    else:
        out.append("  tasks    none")
    out.append("  proofs   " + (", ".join(f"{p['id']} ({p['state']})" for p in imp["proofs"])
                                + " — each runs again once the ACs change" if imp["proofs"] else "none verify them"))
    out.append(f"  hours    ≤ {imp['hours']:g}h — the estimates of every task above, a rough upper bound")
    return out


def _list(imp: dict, key: str) -> list[dict]:
    """A list of entries from a stored snapshot — the trail is a file someone could have edited."""
    items = imp.get(key)
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def brief(imp) -> dict | None:
    """The counts a page shows for a stored snapshot; None when the CR carries none (older CRs)."""
    if not isinstance(imp, dict):
        return None
    hours = imp.get("hours")
    out = {"uat": [str(s.get("id")) for s in _list(imp, "slices") if s.get("uat") == "passed"],
           "done": sum(1 for t in _list(imp, "tasks") if t.get("group") == DONE),
           "proofs": len(_list(imp, "proofs")),
           "hours": hours if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0 else 0}
    return out if any(out.values()) else None


def summary(imp: dict, T: dict) -> str:
    """One line for a report or a list: `T` holds the words (i18n.strings(lang))."""
    b = brief(imp)
    if not b:
        return ""
    parts = []
    if b["uat"]:
        parts.append(T["impact_uat"].format(ids=", ".join(b["uat"])))
    if b["done"]:
        parts.append(T["impact_done"].format(n=b["done"]))
    if b["proofs"]:
        parts.append(T["impact_proofs"].format(n=b["proofs"]))
    if b["hours"]:
        parts.append(T["impact_hours"].format(h=f"{b['hours']:g}"))
    return T["impact"].format(what="; ".join(parts)) if parts else ""
