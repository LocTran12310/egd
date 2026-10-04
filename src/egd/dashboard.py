"""The single-repository dashboard — the console's own page, pinned to one repository.

    egd serve          live, read-only, from this checkout (127.0.0.1 by default)
    egd site --out D   static folder with data and evidence, deployable anywhere
    egd board --html   single file with data, no evidence

There is one web UI in EGD (console.html + ui/). This module only decides what it shows.
`feature_data` is the shape every view of a feature uses; `feature_summary` is the part of it the
lists need, which is all a live page loads up front (it fetches the rest when a feature opens).
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from pathlib import Path

from .events import replay
from .gates import stage
from .graph import assignment, ready
from .model import Feature, all_features
from .proof import proof_matrix
from .report import ac_proof_status, counts, health, metrics, progress
from .util import git_memo, norm_name, now

# Evidence is served as inert content only: images and text, never HTML or SVG.
SERVABLE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
            ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
            ".json": "text/plain; charset=utf-8"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
TRAIL_LIMIT = 300

# What the lists need of a feature — inbox, board, pickable, people, features, search and a feature
# page's header. Everything else in `feature_data` (criteria, slices and their demo scripts, proofs,
# assumptions, decisions, gate checks, metrics, task checklists, the trail) is the
# feature's detail: a live page fetches it from /api/feature when it needs it, and the approval
# trail page fetches its events from /api/trail.
SUMMARY_KEYS = ("slug", "title", "tier", "client", "repo", "source", "gate", "gates", "health",
                "progress", "counts", "last_activity", "next_problems", "rev")
SUMMARY_TASK = ("id", "title", "slice", "estimate", "col", "status")
SUMMARY_TASK_SET = ("spent", "owner", "reason", "solo", "rejects", "submitted_by")  # left out when unset
# events a person signed: the approval trail lists them, people counts last week's accepted tasks
SIGNATURES = frozenset({"gate_passed", "task_accepted", "task_rejected", "task_done_solo", "cr_approved",
                        "cr_rejected", "uat", "feature_closed", "feature_reopened"})
RECENT_DAYS = 8  # people shows "done in the last 7 days"; a day more covers clocks and time zones


def _target(e: dict) -> str:
    return str(e.get("task") or e.get("gate") or e.get("slice") or e.get("cr") or e.get("defect")
               or e.get("proof") or e.get("outcome") or "")


def namer(team=None):
    """`name` → how team.toml spells that person (their name, else github handle), or `name` as given
    when the team does not know it. Only what is shown changes; the trail keeps what was signed."""
    canon = {}
    for m in (team.members if team is not None else []):
        shown = m.get("name") or m.get("github")
        for a in [m.get("name"), m.get("github"), *m.get("aliases", [])]:
            if a and shown:
                canon.setdefault(norm_name(a), shown)
    return lambda name: canon.get(norm_name(name), name) if isinstance(name, str) and name else name


def _shots(f: Feature, arts: list[str]) -> list[str]:
    """A run's pictures: the ones it recorded, else whatever its folder holds — a proof that wrote
    screenshots before EGD collected them still shows them."""
    from .proof import IMAGES, run_images
    shots = [a for a in arts if a.lower().endswith(IMAGES)]
    transcript = next((a for a in arts if a.endswith("transcript.md")), None)
    if shots or not transcript:
        return shots
    folder = f.path / Path(transcript).parent
    return [str(p.relative_to(f.path)) for p in run_images(folder)]


@git_memo()
def feature_data(f: Feature, cfg: dict, team=None, st=None) -> dict:
    """Everything the page shows of a feature. With `team` (a Team), the people in it — owners,
    signers, the trail — carry the names team.toml gives them. `st` is the feature's replayed
    trail when the caller has it already (replaying is the costly part)."""
    who = namer(team)
    st = st if st is not None else replay(f)
    ready_ids = {t["id"] for t in ready(f, st)} if "slice" in st.gates else set()
    rag, why = health(f, st, cfg)
    evidence = ac_proof_status(f, st, cfg)

    tasks = []
    for t in f.tasks:
        ts = st.task(t["id"])
        col = "ready" if ts.status == "todo" and t["id"] in ready_ids else ts.status
        agent, model, why = assignment(t, st)
        tasks.append({"agent": agent, "model": model, "model_why": why,"id": t["id"], "title": t.get("title", ""), "slice": t.get("slice"),
                      "estimate": t.get("estimate_h"), "spent": ts.spent_h, "col": col,
                      "status": ts.status, "owner": who(ts.owner), "reason": ts.block_reason,
                      "depends_on": t.get("depends_on", []), "done_when": t.get("done_when", []),
                      "issue": st.issues.get(t["id"]), "solo": ts.solo, "rejects": ts.rejects,
                      "submitted_by": who(ts.submitted_by)})

    proofs, by_id = [], {}
    for p, env, required, run, state in proof_matrix(f, st, cfg):
        if p.get("id") not in by_id:
            by_id[p.get("id")] = {"id": p.get("id"), "kind": p.get("kind"), "title": p.get("title", ""),
                                  "verifies": p.get("verifies", []), "runs": []}
            proofs.append(by_id[p.get("id")])
        if not run:
            by_id[p.get("id")]["runs"].append({"env": env, "required": required, "status": "unrun"})
            continue
        arts = run.get("artifacts", [])
        by_id[p.get("id")]["runs"].append({
            "env": env, "required": required, "status": run.get("status"),
            "reason": run.get("reason"), "at": run.get("at"), "by": who(run.get("by")),
            "sha": (run.get("sha") or "")[:8], "stale": state in ("stale", "unknown"),
            "transcript": next((a for a in arts if a.endswith("transcript.md")), None),
            "shots": _shots(f, arts)})

    gates = []
    for g in f.gates:
        ev = st.gates.get(g)
        gates.append({"name": g, "passed": bool(ev), "by": ev and who(ev.get("by")), "at": ev and ev.get("at")})

    trail = [{"seq": e.get("seq"), "at": e.get("at"), "by": who(e.get("by")), "type": e.get("type"),
              "target": _target(e), "note": e.get("reason") or e.get("note") or e.get("title") or ""}
             for e in st.events[-TRAIL_LIMIT:]]

    m = metrics(f, cfg, st)
    m.pop("rows", None)
    from .gates import CHECKS
    gate_checks = {}
    for g in f.gates:
        try:
            gate_checks[g] = CHECKS[g](f, st, cfg)
        except Exception as exc:  # a check must never break the page
            gate_checks[g] = [f"could not evaluate: {exc}"]
    return {
        "slug": f.slug, "title": f.title, "tier": f.tier, "client": f.meta.get("client", ""),
        "gate": stage(f, st), "closed": ({k: st.closed.get(k) for k in ("outcome", "reason", "by", "at", "open_tasks")}
                                         if st.closed else None), "gates": gates, "health": rag, "why": why,
        "progress": progress(f, st), "counts": counts(f, st),
        "acs": [{**{k: a.get(k, "") for k in ("id", "title", "story", "given", "when", "then", "kind")},
                 "levels": a.get("levels", []), "evidence": evidence.get(a.get("id"), "missing")}
                for a in f.acs],
        "slices": [{"id": s.get("id"), "title": s.get("title", ""), "covers": s.get("covers", []),
                    "demo": s.get("demo", ""),
                    "uat": {**{k: st.uat[s["id"]].get(k) for k in ("passed", "at", "note")},
                            "by": who(st.uat[s["id"]].get("by"))}
                    if s.get("id") in st.uat else None} for s in f.slices],
        "tasks": tasks, "proofs": proofs,
        "assumptions": f.assumptions, "decisions": f.decisions,
        "crs": [{"id": k, **{x: v.get(x) for x in ("title", "status", "hours", "reason", "at")},
                 "by": who(v.get("by")), "decided_by": who(v.get("decided_by"))} for k, v in st.crs.items()],
        "defects": [{"id": k, **{x: v.get(x) for x in ("title", "status", "severity", "found_in", "at", "acs")},
                     "by": who(v.get("by"))} for k, v in st.defects.items()],
        "metrics": m, "trail": trail, "gate_checks": gate_checks,
    }


def feature_summary(fd: dict, since: _dt.datetime | None = None) -> dict:
    """`fd` (a `feature_data`, or an imported feature of the same shape) cut to what the lists use.
    Its trail keeps only what was signed since `since` (default: `RECENT_DAYS` ago). `partial`
    tells the page the rest is to be fetched; a snapshot carries the whole feature and never it."""
    since = since or now() - _dt.timedelta(days=RECENT_DAYS)
    out = {k: fd[k] for k in SUMMARY_KEYS if k in fd}
    out["tasks"] = [{**{k: t.get(k) for k in SUMMARY_TASK}, **{k: t[k] for k in SUMMARY_TASK_SET if t.get(k)}}
                    for t in fd.get("tasks", [])]
    # the features table counts pending CRs and open defects; their text belongs to the detail
    out["crs"] = [{"id": c.get("id"), "status": c.get("status")} for c in fd.get("crs", [])]
    out["defects"] = [{k: d.get(k) for k in ("id", "status", "severity")} for d in fd.get("defects", [])]
    out["trail"] = [{k: e.get(k) for k in ("at", "by", "type", "target")} for e in fd.get("trail", [])
                    if e.get("type") in SIGNATURES and _after(e.get("at"), since)]
    out["partial"] = True
    return out


def _after(at, since: _dt.datetime) -> bool:
    try:
        t = _dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)) >= since


def portfolio(root: Path, only: list[str] | None = None, evidence: bool = True) -> dict:
    """The console's data for exactly this repository, read-only — and, since it is made to be
    shared, without local paths (no repository path, registry or roots; hints say ".")."""
    from .console import Portfolio
    data = Portfolio(ttl=0, roots=[root]).get()
    for r in data["repos"]:
        if only:
            r["features"] = [f for f in r["features"] if f["slug"] in only]
            r["inbox"] = [i for i in r["inbox"] if not i.get("slug") or i["slug"] in only]
    return {**data, "readonly": True, "evidence": evidence, "user": ""}


def render(data: dict) -> str:
    """A self-contained snapshot page (no server needed)."""
    from .web import page
    return page({"csrf": "", "readonly": True, "mode": "dashboard"}, data)


def safe_runs(folder: Path, root: Path) -> Path | None:
    """A feature folder's runs/, resolved — or None when it leads outside the folder, or the folder
    outside the repository (a symlink): evidence is served and exported from the repository only."""
    try:
        top, home, runs = root.resolve(), folder.resolve(), (folder / "runs").resolve()
    except (OSError, RuntimeError):  # a symlink loop
        return None
    return runs if home.is_relative_to(top) and runs.is_relative_to(home) else None


def export_site(root: Path, cfg: dict, out: Path, only: list[str] | None = None,
                evidence: bool = True) -> tuple[Path, int]:
    data = portfolio(root, only, evidence)
    out.mkdir(parents=True, exist_ok=True)
    copied = 0
    if evidence:
        by_slug = {f.slug: f for f in all_features(root)}
        for r in data["repos"]:
            for fd in r["features"]:
                f = by_slug.get(fd["slug"])
                runs = safe_runs(f.path, root) if f is not None else None
                if runs is None:
                    continue
                for p in fd["proofs"]:
                    for run in p["runs"]:
                        for rel in [run.get("transcript"), *run.get("shots", [])]:
                            if not rel:
                                continue
                            src = (f.path / rel).resolve()
                            if not src.is_file() or not src.is_relative_to(runs) or src.suffix not in SERVABLE:
                                continue
                            dst = (out / "files" / r["id"] / f.slug / src.relative_to(f.path.resolve())).resolve()
                            if not dst.is_relative_to(out.resolve()):
                                continue
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, dst)
                            copied += 1
    (out / "index.html").write_text(render(data), encoding="utf-8")
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return out / "index.html", copied


def make_server(root: Path, cfg: dict, host: str = "127.0.0.1", port: int = 8770, token: str | None = None):
    """Live dashboard: the console server pinned to this repository, read-only."""
    from .console import make_server as console_server
    return console_server(host, port, token, readonly=True, roots=[root], mode="dashboard")
