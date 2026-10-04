"""Read-only view of repositories planned with the ai-dlc workflow (`.ai/features/`).

Nothing here writes. It reads the plan files a team already has — feature folders,
unit-of-work and ticket front matter, the assumption table, the event history — and
reshapes them into the same structure the console renders for EGD repositories, so
a PM sees every project on one screen while those repositories keep their own tool.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..util import git, md_sections, plural

GATES = ["G0", "G1", "G2", "G3", "G4", "G5"]
GATE_LABEL = {"G0": "G0 intent", "G1": "G1 requirements", "G2": "G2 design", "G3": "G3 plan",
              "G4": "G4 build", "G5": "G5 close"}
STATUS = {"todo": "todo", "in_progress": "doing", "review": "review", "blocked": "blocked", "done": "done"}
EVENT = {"passed": "gate_passed", "ticket in_progress": "task_started", "ticket review": "task_submitted",
         "ticket done": "task_accepted", "ticket done (review bypassed)": "task_done_solo",
         "ticket blocked": "task_blocked", "ticket unblocked": "task_unblocked", "reopened": "task_reopened",
         "ticket todo": "task_reset", "scope": "scope_recorded"}


def is_repo(root: Path) -> bool:
    return (root / ".ai" / "features").is_dir()


# ------------------------------------------------------------------ parsing

def front_matter(text: str) -> dict:
    """The small YAML subset these files use: scalars, inline lists, block lists, # comments."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    data: dict = {}
    key = None
    for raw in text[3:end].splitlines():
        line = re.sub(r"\s+#.*$", "", raw).rstrip()
        if not line.strip():
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and key:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_scalar(item.group(1)))
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = [_scalar(v) for v in value[1:-1].split(",") if v.strip()]
        else:
            data[key] = _scalar(value) if value else []
    return data


def _scalar(v: str):
    v = v.strip().strip("'\"")
    return v


def hours(v) -> float | None:
    m = re.match(r"^\s*([\d.]+)\s*h?\s*$", str(v or ""))
    return float(m.group(1)) if m else None


def assumption_rows(text: str) -> list[dict]:
    rows, header = [], None
    for line in text.splitlines():
        if not line.startswith("|"):
            header = None if not line.strip() else header
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        aid = row.get("id", "")
        if not re.match(r"^A-\d+", aid) or "status" not in row:
            continue
        status = row.get("status", "").lower()
        rows.append({"id": aid, "text": re.sub(r"\*\*", "", row.get("assumption", "")),
                     "blocking": row.get("blocking", "").lower().startswith("y"),
                     "status": {"pending": "open", "open": "open"}.get(status, status or "open"),
                     "resolution": "" if row.get("resolution") in ("—", "-") else row.get("resolution", "")})
    return rows


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# ------------------------------------------------------------------ one feature

def feature(rid: str, fdir: Path) -> dict:
    state = read(fdir / ".aidlc-state.yaml")
    m = re.search(r"^current_gate:\s*(\S+)", state, re.M)
    current = m.group(1) if m else "none"
    passed = GATES[: GATES.index(current) + 1] if current in GATES else []

    history = []
    for line in read(fdir / "history.jsonl").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            history.append(e)
    history.sort(key=lambda e: e.get("ts") or e.get("at") or "")
    gate_by = {e.get("gate"): e for e in history if e.get("action") == "passed"}
    owner: dict[str, str] = {}
    for e in history:
        if e.get("action") == "ticket in_progress" and e.get("ticket"):
            owner[e["ticket"]] = e.get("by", "")

    intent = read(fdir / "00-intent.md")
    title_m = re.search(r"^#\s+(.+)$", intent, re.M)
    title = re.sub(r"^(Intent|Ý định)\s*[—:-]\s*", "", title_m.group(1)).strip() if title_m else fdir.name

    slices, tasks = [], []
    for udir in sorted((fdir / "04-units-of-work").glob("*/")):
        uow_text = read(udir / "uow.md")
        u = front_matter(uow_text)
        uid = u.get("id") or udir.name.split("-", 2)[0] + "-" + udir.name.split("-")[1]
        demo = md_sections(uow_text).get("demo script", "")
        slices.append({"id": uid, "title": u.get("title", udir.name), "covers": u.get("verifies") or [],
                       "demo": demo, "uat": None, "status": u.get("status")})
        for tfile in sorted((udir / "tickets").glob("*.md")):
            t = front_matter(read(tfile))
            tid = t.get("id") or tfile.stem
            tasks.append({"id": tid, "title": t.get("title", ""), "slice": uid,
                          "estimate": hours(t.get("estimate")), "spent": None,
                          "status": STATUS.get(t.get("status", "todo"), "todo"),
                          "owner": owner.get(tid), "reason": None,
                          "depends_on": t.get("depends_on") or [], "done_when": [],
                          "issue": None, "solo": False, "rejects": 0})
    done = {t["id"] for t in tasks if t["status"] == "done"}
    for t in tasks:
        t["col"] = ("ready" if all(d in done for d in t["depends_on"]) else "todo") \
            if t["status"] == "todo" else t["status"]
    for e in history:
        tid = e.get("ticket")
        t = next((x for x in tasks if x["id"] == tid), None)
        if not t:
            continue
        if e.get("action") == "ticket done (review bypassed)":
            t["solo"] = True
        if e.get("action") == "reopened":
            t["rejects"] += 1
        if e.get("action") == "ticket blocked":
            t["reason"] = e.get("reason") or e.get("evidence")

    acs = [{"id": a, "then": text.strip(), "given": "", "when": "", "story": "", "kind": "",
            "levels": [], "evidence": "missing"}
           for a, text in re.findall(r"^\*\*(AC-\d+)\*\*\s*[—-]\s*(.+)$", read(fdir / "02-requirements.md"), re.M)]
    assumptions = assumption_rows(read(fdir / "01-assumptions.md"))

    counts = {s: 0 for s in ("todo", "doing", "review", "blocked", "done")}
    for t in tasks:
        counts[t["status"]] += 1
    total = sum(t["estimate"] or 0 for t in tasks)
    progress = round(100 * sum(t["estimate"] or 0 for t in tasks if t["status"] == "done") / total) if total else (
        100 if current == "G5" else 0)
    blocking = [a for a in assumptions if a["blocking"] and a["status"] == "open"]
    why = []
    if counts["blocked"]:
        why.append(plural(counts["blocked"], "blocked ticket"))
    if blocking:
        why.append(f"{plural(len(blocking), 'blocking assumption')} open")
    health = "red" if counts["blocked"] else ("amber" if blocking or counts["review"] else "green")

    trail = [{"seq": i, "at": e.get("at"), "by": e.get("by", ""),
              "type": EVENT.get(e.get("action", ""), e.get("action", "")),
              "target": e.get("ticket") or e.get("gate") or "",
              "note": (e.get("evidence") or e.get("reason") or "")[:200]} for i, e in enumerate(history[-300:])]
    review_waits, sub_at = [], {}
    for e in history:
        if e.get("action") == "ticket review":
            sub_at[e.get("ticket")] = e.get("at")
        elif e.get("action") == "ticket done" and e.get("ticket") in sub_at:
            from ..util import hours_between
            try:
                review_waits.append(hours_between(sub_at.pop(e["ticket"]), e["at"]))
            except (TypeError, ValueError):
                pass
    nxt = GATES[GATES.index(current) + 1] if current in GATES and current != "G5" else (
        None if current == "G5" else "G0")
    return {
        "slug": fdir.name, "title": title, "tier": "ai-dlc", "client": "", "repo": rid,
        "gate": GATE_LABEL.get(nxt, "released") if nxt else "released",
        "gates": [{"name": GATE_LABEL[g], "passed": g in passed, "by": gate_by.get(g, {}).get("by"),
                   "at": gate_by.get(g, {}).get("at")} for g in GATES],
        "health": health, "why": why, "progress": progress, "counts": counts,
        "brief": {k: v for k, v in md_sections(intent).items() if k in ("problem", "success signal", "out of scope")},
        "acs": acs, "slices": slices, "tasks": tasks, "proofs": [],
        "assumptions": assumptions, "decisions": [], "crs": [], "defects": [],
        "metrics": {"estimate_ratio": None, "tasks_measured": 0,
                    "review_wait_avg_h": round(sum(review_waits) / len(review_waits), 1) if review_waits else None,
                    "rejections": sum(t["rejects"] for t in tasks), "solo_bypasses": sum(t["solo"] for t in tasks),
                    "drift_overrides": 0, "defects_total": 0, "leakage_pct": 0, "scope_growth_pct": 0},
        "trail": trail, "last_activity": history[-1].get("at") if history else None,
        "next_problems": ["managed by ai-dlc — gate checks run in that tool; shown here read-only"],
        "source": "ai-dlc",
    }


# ------------------------------------------------------------------ repository

def snapshot(rid: str, root: Path, item, act) -> dict:
    base = root / ".ai"
    arch = read(base / "architecture.md")
    signed = bool(re.search(r"^verified_by:[ \t]*\S", arch, re.M))
    features, inbox = [], []
    for fdir in sorted(p for p in (base / "features").iterdir() if p.is_dir()):
        f = feature(rid, fdir)
        features.append(f)
        ref = type("F", (), {"slug": f["slug"], "title": f["title"]})
        for t in f["tasks"]:
            if t["status"] == "review":
                inbox.append(item("decide", "review", rid, ref, f"{t['id']} waiting for review", t["title"],
                                  hint="review it in ai-dlc (read-only here)", target=t["id"]))
            elif t["status"] == "blocked":
                inbox.append(item("unblock", "blocked", rid, ref, f"{t['id']} blocked",
                                  t["title"] + (f" — {t['reason']}" if t.get("reason") else ""), target=t["id"]))
        for a in f["assumptions"]:
            if a["blocking"] and a["status"] == "open":
                inbox.append(item("decide", "assumption", rid, ref, f"{a['id']} is blocking and unanswered",
                                  a["text"][:300], hint="Being wrong here forces rework.", target=a["id"]))
    if arch and not signed:
        inbox.append(item("decide", "map", rid, None, "Architecture map is unsigned",
                          ".ai/architecture.md has no verified_by — a person must read and confirm it."))
    status = git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all", "--", ".ai") or ""
    pending = [ln for ln in status.splitlines() if ln.strip() and "/evidence/" not in ln and ".auth" not in ln]
    if pending:
        inbox.append(item("hygiene", "uncommitted", rid, None, f"{plural(len(pending), 'plan/trail change')} not committed",
                          "Teammates cannot see these until they are committed and pushed."))
    branch = (git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None
    return {"id": rid, "name": root.name, "path": str(root), "branch": branch, "uncommitted": len(pending),
            "map_signed": signed, "team": [], "features": features, "inbox": inbox,
            "source": "ai-dlc", "readonly": True}
