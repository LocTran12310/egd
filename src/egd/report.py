"""Views over plan + trail: status, board, client report, metrics, PR, release note.

Everything here is generated and read-only. Nothing in a report is ever an
input to a gate.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from .events import State, replay
from .gates import SERIOUS, check, next_gate, NEXT, stage
from .graph import ready
from .i18n import strings
from .model import Feature, all_features
from .proof import proof_matrix
from .team import Team
from .util import egd_dir, git_memo, hours_between, iso, md_cell, norm_name, now, parse_iso, plural

STATUSES = ("todo", "doing", "review", "blocked", "done")


# ------------------------------------------------------------------ helpers

def counts(f: Feature, st: State) -> dict[str, int]:
    c = {s: 0 for s in STATUSES}
    for t in f.tasks:
        c[st.task(t["id"]).status] += 1
    return c


def progress(f: Feature, st: State) -> float:
    total = sum(float(t.get("estimate_h", 0) or 0) for t in f.tasks)
    done = sum(float(t.get("estimate_h", 0) or 0) for t in f.tasks if st.task(t["id"]).status == "done")
    return round(100 * done / total) if total else 0


def health(f: Feature, st: State, cfg: dict | None = None, lang: str = "en") -> tuple[str, list[str]]:
    """green / amber / red with the reasons — a judgement aid, not a gate. With `cfg`, failing
    proofs are the ones the plan still has (a crashed or refused run counts as failing)."""
    why = strings(lang)["why"]

    def said(key, n):
        return why[key].format(n=n, s="" if n == 1 else "s")
    red, amber = [], []
    serious = [d for d in st.defects.values() if d.get("status") == "open" and d.get("severity") in SERIOUS]
    if serious:
        red.append(said("defects", len(serious)))
    if cfg is not None:
        failing = {p["id"] for p, _, required, _, state in proof_matrix(f, st, cfg) if required and state == "fail"}
    else:
        failing = [r for r in st.latest_proofs().values() if r.get("status") in ("fail", "error", "refused")]
    if failing:
        red.append(said("proofs", len(failing)))
    blocked = [tid for tid, t in st.tasks.items() if t.status == "blocked"]
    if blocked:
        amber.append(said("blocked", len(blocked)))
    pending = [c for c in st.crs.values() if c.get("status") == "pending"]
    if pending:
        amber.append(said("crs", len(pending)))
    blocking = [a for a in f.assumptions if a.get("status", "open") == "open" and a.get("blocking")]
    if blocking:
        amber.append(said("assumptions", len(blocking)))
    amber += st.warnings  # e.g. one BUG id opened on two branches
    if red:
        return "red", red + amber
    if amber:
        return "amber", amber
    return "green", []


DOT = {"green": "🟢", "amber": "🟠", "red": "🔴"}


def ac_proof_status(f: Feature, st: State, cfg: dict) -> dict[str, str]:
    """AC id → pass | fail | stale | missing | unrun."""
    by_ac: dict[str, list[str]] = {a["id"]: [] for a in f.acs if a.get("id")}
    optional: dict[str, list[str]] = {}
    for p, env, required, run, state in proof_matrix(f, st, cfg):
        s = "stale" if state == "unknown" else state  # not shown to be evidence of this code
        for ac in p.get("verifies", []):
            (by_ac if required else optional).setdefault(ac, []).append(s)
    out = {}
    for ac, states in by_ac.items():
        if not states and optional.get(ac):
            # only optional environments prove it — show them, ignoring ones that could not run
            states = [s for s in optional[ac] if s != "unrun"] or optional[ac]
        if not states:
            out[ac] = "missing"
        elif all(s == "pass" for s in states):
            out[ac] = "pass"
        elif "fail" in states:
            out[ac] = "fail"
        elif "stale" in states:
            out[ac] = "stale"
        else:
            out[ac] = "unrun"
    return out


ICON = {"pass": "✅", "fail": "❌", "stale": "⏳", "unrun": "⬜", "missing": "➖"}
LATE = {"stale": " (stale — code or proof changed since)", "unknown": " (freshness unknown — no git)"}


# ------------------------------------------------------------------ status

@git_memo()
def status_text(f: Feature, cfg: dict) -> str:
    st = replay(f)
    gate = next_gate(f, st)
    marks = []
    for g in f.gates:
        marks.append(("✓" if g in st.gates else ("▶" if g == gate else "·")) + g)
    c = counts(f, st)
    rag, why = health(f, st, cfg)
    lines = [f"{f.title}  [{f.slug}] · tier {f.tier} · {DOT[rag]} {rag}",
             "gates: " + "  ".join(marks)]
    if st.closed:
        shut = st.closed
        lines.append(f"closed as {shut.get('outcome')} by {shut.get('by')} on {(shut.get('at') or '')[:10]} — {shut.get('reason')}")
        if shut.get("open_tasks"):
            lines.append(f"left undone: {', '.join(shut['open_tasks'])}")
    elif gate:
        lines.append(f"next: {gate} — {NEXT[gate]}")
        problems = check(f, st, cfg, gate)
        if problems:
            lines.append(f"{gate} is closed:")
            lines += [f"  - {p}" for p in problems[:15]]
            if len(problems) > 15:
                lines.append(f"  … {len(problems) - 15} more (`egd check {gate}`)")
        else:
            lines.append(f"{gate} is open — `egd pass {gate} --by <name>`")
    else:
        lines.append("released — every gate passed")
    if f.tasks:
        r = ready(f, st)
        lines.append("tasks: " + " · ".join(f"{v} {k}" for k, v in c.items() if v)
                     + f"  ({len(r)} ready, {progress(f, st)}% of estimate done)")
    pending = [k for k, v in st.crs.items() if v.get("status") == "pending"]
    open_d = [d for d in st.defects.values() if d.get("status") == "open"]
    if pending or open_d:
        lines.append(f"CRs pending: {', '.join(pending) or '—'} · defects open: {len(open_d)}")
    for w in why:
        lines.append(f"watch: {w}")
    if rollback_due(f, st):
        lines.append("rollback: not written yet — write it now, while the change is fresh "
                     "(release.md → ## Rollback); release needs it")
    return "\n".join(lines)


def rollback_due(f: Feature, st: State) -> bool:
    """The work is done (or built) but nobody wrote how to undo it — the moment to write it."""
    done = bool(f.tasks) and all(st.task(t["id"]).status == "done" for t in f.tasks)
    return (not st.closed and "release" in f.gates and "release" not in st.gates and (done or "build" in st.gates)
            and next_gate(f, st) != "release" and not f.sections("release.md").get("rollback"))


# ------------------------------------------------------------------ board

def _rows(root: Path, cfg: dict):
    """The features a board shows: closed ones (dropped, research, superseded) are off it."""
    for f in all_features(root):
        st = replay(f)
        if not st.closed:
            yield f, st


def board_markdown(root: Path, cfg: dict) -> str:
    lines = ["# Delivery board", "",
             f"_Generated by `egd board` at {iso()} — do not edit by hand._", "",
             "| feature | tier | gate | health | progress | doing | review | blocked | CRs pending | open defects |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    review_q, blocked_q, crs_q, bugs_q, people = [], [], [], [], {}
    sections = []
    for f, st in _rows(root, cfg):
        c = counts(f, st)
        rag, _ = health(f, st, cfg)
        gate = stage(f, st)
        pend = [k for k, v in st.crs.items() if v.get("status") == "pending"]
        open_d = [d for d in st.defects.values() if d.get("status") == "open"]
        lines.append(f"| {md_cell(f.title)} | {f.tier} | {gate} | {DOT[rag]} | {progress(f, st)}% | "
                     f"{c['doing']} | {c['review']} | {c['blocked']} | {len(pend)} | {len(open_d)} |")
        ready_ids = {t["id"] for t in ready(f, st)}
        cols = {k: [] for k in ("ready", "todo", "doing", "review", "blocked", "done")}
        for t in f.tasks:
            ts = st.task(t["id"])
            label = f"{t['id']} {t.get('title', '')}".strip()
            if ts.owner:
                label += f" — @{ts.owner}"
                if ts.status in ("doing", "review", "blocked"):
                    people.setdefault(ts.owner, []).append(f"{t['id']} ({ts.status}, {f.title})")
            key = "ready" if ts.status == "todo" and t["id"] in ready_ids else ts.status
            cols[key].append(label)
            if ts.status == "review":
                wait = hours_between(ts.submitted_at, iso()) if ts.submitted_at else 0
                review_q.append(f"{f.title} · {t['id']} — submitted by {ts.submitted_by}, waiting {wait:g}h")
            if ts.status == "blocked":
                blocked_q.append(f"{f.title} · {t['id']} — {ts.block_reason}")
        for k in pend:
            cr = st.crs[k]
            crs_q.append(f"{f.title} · {k} {cr.get('title', '')} (+{cr.get('hours', 0)}h)")
        for d in open_d:
            if d.get("severity") in SERIOUS:
                bugs_q.append(f"{f.title} · {d['defect']} [{d['severity']}] {d.get('title', '')}")
        sec = [f"### {f.title}", "", f"gate **{gate}** · {progress(f, st)}% · `{f.slug}`", ""]
        for k, items in cols.items():
            if items:
                sec.append(f"**{k}** ({len(items)})")
                sec += [f"- {i}" for i in items]
                sec.append("")
        sections.append("\n".join(sec))

    lines += ["", "## Needs attention", ""]
    for title, items in (("Review queue", review_q), ("Blocked", blocked_q),
                         ("Change requests awaiting the client", crs_q),
                         ("Serious defects", bugs_q)):
        lines.append(f"**{title}** — {len(items) or 'none'}")
        lines += [f"- {i}" for i in items]
        lines.append("")
    lines += ["## Who is on what", ""]
    lines += [f"- **{p}**: " + "; ".join(items) for p, items in sorted(people.items())] or ["- nobody has active work"]
    lines += ["", "## Features", "", *sections]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ client report

def client_report(f: Feature, cfg: dict, since_days: int = 7, lang: str = "en") -> str:
    T = strings(lang)
    st = replay(f)
    since = now() - dt.timedelta(days=since_days)
    c = counts(f, st)
    rag, why = health(f, st, cfg, lang)
    gate = T["closed"] if st.closed else next_gate(f, st) or T["released"]
    recent = [e for e in st.events if e.get("at") and parse_iso(e["at"]) >= since]
    done_now = [e["task"] for e in recent if e["type"] in ("task_accepted", "task_done_solo")]
    uat_now = [e for e in recent if e["type"] == "uat"]
    titles = {t["id"]: t.get("title", "") for t in f.tasks}
    lines = [f"# {f.title} — {T['status_report']}", "",
             f"{dt.date.today().isoformat()} · {T['covering'].format(days=since_days)}", "",
             f"## {T['summary']}", "",
             f"- {T['overall']}: {DOT[rag]} **{T['health'][rag]}**" + (f" — {'; '.join(why)}" if why else ""),
             f"- {T['stage']}: **{gate}** ({T['passed']}: {', '.join(st.gates) or T['none_yet']})",
             f"- {T['progress']}: " + T["progress_line"].format(pct=progress(f, st), done=c["done"],
                                                                total=len(f.tasks)), ""]
    lines += [f"## {T['delivered']}", ""]
    lines += [f"- {t} {titles.get(t, '')}" for t in done_now] or [f"- {T['nothing_closed']}"]
    for u in uat_now:
        lines.append(f"- UAT {u['slice']}: {T['uat_accepted'] if u.get('passed') else T['uat_returned']}"
                     + (f" — {u['note']}" if u.get("note") else ""))
    lines += ["", f"## {T['in_progress']}", ""]
    active = [(t, st.task(t["id"])) for t in f.tasks if st.task(t["id"]).status in ("doing", "review")]
    lines += [f"- {t['id']} {t.get('title', '')} ({T['status'][ts.status]})" for t, ts in active] \
        or [f"- {T['none']}"]
    lines += ["", f"## {T['next_up']}", ""]
    lines += [f"- {t['id']} {t.get('title', '')}" for t in ready(f, st)[:8]] or [f"- {T['nothing_ready']}"]
    lines += ["", f"## {T['risks']}", ""]
    risks = [f"- {a['id']}: {a.get('text', '')}" + (f" **({T['blocking']})**" if a.get("blocking") else "")
             for a in f.assumptions if a.get("status", "open") == "open"]
    risks += [f"- {tid} {T['blocked']} — {ts.block_reason}" for tid, ts in st.tasks.items()
              if ts.status == "blocked"]
    lines += risks or [f"- {T['none_open']}"]
    lines += ["", f"## {T['crs']}", ""]
    if st.crs:
        lines += [T["cr_head"], "|---|---|---|---|"]
        lines += [f"| {k} | {md_cell(v.get('title', ''))}"
                  + (f" ({T['cr_drops'].format(ids=', '.join(v['dropped']))})" if v.get("dropped") else "")
                  + f" | {v.get('hours', 0):g} | "
                  f"{T['status'].get(v.get('status'), v.get('status'))} |" for k, v in st.crs.items()]
    else:
        lines.append(f"- {T['none']}")
    lines += ["", f"## {T['quality']}", ""]
    acs = ac_proof_status(f, st, cfg)
    proved = sum(1 for s in acs.values() if s == "pass")
    lines.append("- " + T["evidence_line"].format(ok=proved, total=len(acs)))
    sev: dict[str, int] = {}
    for d in st.defects.values():
        if d.get("status") == "open":
            sev[d.get("severity", "?")] = sev.get(d.get("severity", "?"), 0) + 1
    lines.append(f"- {T['open_defects']}: " + (", ".join(f"{v} {k}" for k, v in sev.items()) or T["none"]))
    m = metrics(f, cfg, st)
    if m["defects_total"]:
        lines.append("- " + T["caught"].format(pct=100 - m["leakage_pct"]))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ metrics

def metrics(f: Feature, cfg: dict, st: State | None = None) -> dict:
    st = st or replay(f)
    est = spent = 0.0
    rows = []
    for t in f.tasks:
        ts = st.task(t["id"])
        if ts.status == "done" and ts.spent_h:
            est += float(t.get("estimate_h", 0) or 0)
            spent += float(ts.spent_h)
            rows.append((t["id"], t.get("estimate_h"), ts.spent_h))
    waits = [ts.review_wait_h for ts in st.tasks.values() if ts.review_wait_h is not None]
    defects = list(st.defects.values())
    leaked = [d for d in defects if d.get("found_in") in ("uat", "prod")]
    planned = sum(float(t.get("estimate_h", 0) or 0) for t in f.tasks)
    cr_hours = sum(float(c.get("hours", 0) or 0) for c in st.crs.values() if c.get("status") == "approved")
    return {
        "estimate_ratio": round(spent / est, 2) if est else None,
        "tasks_measured": len(rows),
        "review_wait_avg_h": round(sum(waits) / len(waits), 1) if waits else None,
        "rejections": sum(ts.rejects for ts in st.tasks.values()),
        "solo_bypasses": sum(1 for ts in st.tasks.values() if ts.solo),
        "drift_overrides": sum(ts.drift_overrides for ts in st.tasks.values()),
        "defects_total": len(defects),
        "leakage_pct": round(100 * len(leaked) / len(defects)) if defects else 0,
        "scope_growth_pct": round(100 * cr_hours / planned) if planned else 0,
        "rows": rows,
    }


def metrics_text(f: Feature, cfg: dict) -> str:
    m = metrics(f, cfg)
    ratio = m["estimate_ratio"]
    lines = [f"{f.title} — delivery metrics",
             "  estimate accuracy : " + (f"actual/estimate = {ratio}× over {plural(m['tasks_measured'], 'task')}"
                                          if ratio else "no spent hours recorded (submit --spent)"),
             "  review wait       : " + (f"{m['review_wait_avg_h']}h average" if m["review_wait_avg_h"] is not None else "—"),
             f"  rework            : {plural(m['rejections'], 'rejection')}",
             f"  review bypassed   : {plural(m['solo_bypasses'], 'task')} done solo",
             f"  scope drift       : {plural(m['drift_overrides'], 'override')}",
             f"  scope growth      : {m['scope_growth_pct']}% added by approved CRs",
             f"  defect leakage    : {m['leakage_pct']}% of {m['defects_total']} found in UAT/prod"]
    return "\n".join(lines)


# ------------------------------------------------------------------ PR + release + proof

@git_memo()
def proof_report(f: Feature, cfg: dict) -> str:
    st = replay(f)
    status = ac_proof_status(f, st, cfg)
    lines = [f"# Evidence — {f.title}", "", f"_Generated {iso()}_", "",
             "| AC | then | evidence |", "|---|---|---|"]
    for a in f.acs:
        lines.append(f"| {a['id']} | {md_cell(a.get('then', ''))} | {ICON[status.get(a['id'], 'missing')]} "
                     f"{status.get(a['id'], 'missing')} |")
    lines += ["", "## Runs", "", "| proof | kind | env | status | commit | transcript |", "|---|---|---|---|---|---|"]
    for p, env, required, run, state in proof_matrix(f, st, cfg):
        if not run:
            lines.append(f"| {p['id']} | {p.get('kind')} | {env} | ⬜ never run | | |")
            continue
        s = run["status"] + LATE.get(state, "")
        transcript = next((a for a in run.get("artifacts", []) if a.endswith("transcript.md")), "")
        lines.append(f"| {p['id']} | {p.get('kind')} | {env}{'' if required else ' (optional)'} | {s}"
                     + (f" — {md_cell(run['reason'])}" if run.get("reason") else "")
                     + f" | `{(run.get('sha') or '')[:8]}` | [{Path(transcript).parent.name}](../{transcript}) |")
    return "\n".join(lines) + "\n"


def _checked(run: dict) -> str:
    """` — checked: a, b, c …` from a run's steps: each name once, without the viewport."""
    names = list(dict.fromkeys(str(s.get("name", "")).split(":", 1)[-1] if run.get("kind") == "ui"
                               else str(s.get("name", "")) for s in run.get("steps") or [] if s.get("name")))
    return f" — checked: {', '.join(names[:4])}{' …' if len(names) > 4 else ''}" if names else ""


@git_memo()
def pr_body(f: Feature, cfg: dict) -> str:
    st = replay(f)
    status = ac_proof_status(f, st, cfg)
    lines = [f"## {f.title}", "", f.sections("brief.md").get("outcome", "").strip(), "",
             "### Acceptance criteria", "", "| AC | behaviour | evidence |", "|---|---|---|"]
    for a in f.acs:
        s = status.get(a["id"], "missing")
        by = ", ".join(f"{p['id']} {p.get('kind', '?')}" for p in f.proofs if a["id"] in p.get("verifies", []))
        lines.append(f"| {a['id']} | when {md_cell(a.get('when', ''))} → {md_cell(a.get('then', ''))} | {ICON[s]} {s}"
                     + (f" · {by}" if by else "") + " |")
    lines += ["", "### Slices", ""]
    for s in f.slices:
        tasks = f.tasks_in(s["id"])
        done = sum(1 for t in tasks if st.task(t["id"]).status == "done")
        lines.append(f"- **{s['id']} {s.get('title', '')}** — {done}/{plural(len(tasks), 'task')} done")
    approved = [c for c in st.crs.values() if c.get("status") == "approved"]
    if approved:
        lines += ["", "### Approved changes", ""] + [f"- {c['cr']}: {c.get('title', '')}" for c in approved]
    # only what actually ran, with where and on which code; a gap is said out loud
    ran, gaps, shots = [], [], []
    for p, env, required, run, state in proof_matrix(f, st, cfg):
        if run and run.get("status") == "pass" and state == "pass":
            shots += [f"- {p['id']} on {env}: `.egd/features/{f.slug}/{a}`"
                      for a in run.get("artifacts", []) if a.endswith("contact-sheet.png")]
        if run is None:
            gaps.append(f"- Not verified: {p['id']} ({p.get('title', '')}) on {env}"
                        + ("" if required else " — optional"))
            continue
        ran.append(f"- {p['id']} `{p.get('kind', '?')}` on {env}: {run.get('status')}"
                   f"{LATE.get(state, '')}"
                   f" — {(run.get('at') or '')[:16].replace('T', ' ')} by {run.get('by', '?')}"
                   f"{', commit ' + run['sha'][:8] if run.get('sha') else ''}" + _checked(run))
    gaps += [f"- Not verified: {a['id']} — no proof declared" for a in f.acs
             if not any(a["id"] in p.get("verifies", []) for p in f.proofs)]
    tested = [e for e in st.events if e.get("type") in ("task_submitted", "task_done_solo") and e.get("test_run")]
    for e in tested:
        r = e["test_run"]
        ran.append(f"- {e.get('task')} tests: `{r.get('command')}` exit {r.get('exit')} — "
                   f"{(e.get('at') or '')[:16].replace('T', ' ')}")
    lines += ["", "### Verification", ""] + (ran or ["- No proof or test run is recorded."]) + gaps
    rollback = f.sections("release.md").get("rollback", "").strip()
    lines += ["", "### Rollback", "", rollback or "- Not written yet: how to undo this (release.md → ## Rollback)."]
    if shots:  # the egd-pr skill uploads them; by hand, drag them into the description
        lines += ["", "### Screenshots", "", "To attach — each run's screenshots on one image:", ""] + shots
    lines += ["", f"_Plan and trail: `.egd/features/{f.slug}/` · evidence: `egd proof report {f.slug}`_"]
    return "\n".join(lines) + "\n"


def release_note(f: Feature, cfg: dict, lang: str = "en") -> str:
    T = strings(lang)
    st = replay(f)
    lines = [f"# {T['release']} — {f.title}", "", f"{dt.date.today().isoformat()}", "",
             f"## {T['what_changed']}", ""]
    for s in f.slices:
        lines.append(f"- **{s.get('title', s['id'])}**")
        for ac in s.get("covers", []):
            a = next((x for x in f.acs if x.get("id") == ac), None)
            if a:
                lines.append(f"  - {a.get('then', '')}")
    approved = [c for c in st.crs.values() if c.get("status") == "approved"]
    if approved:
        lines += ["", f"## {T['agreed_changes']}", ""]
        lines += [f"- {c['cr']}: {c.get('title', '')}" for c in approved]
    fixed = [d for d in st.defects.values() if d.get("status") in ("fixed", "closed")]
    if fixed:
        lines += ["", f"## {T['fixed']}", ""] + [f"- {d['defect']}: {d.get('title', '')}" for d in fixed]
    known = [d for d in st.defects.values() if d.get("status") == "open"]
    if known:
        lines += ["", f"## {T['known_issues']}", ""]
        lines += [f"- {d['defect']} [{d.get('severity')}]: {d.get('title', '')}" for d in known]
    out_scope = f.sections("brief.md").get("out of scope", "")
    if out_scope:
        lines += ["", f"## {T['not_included']}", "", out_scope]
    rollback = f.sections("release.md").get("rollback", "")
    if rollback:
        lines += ["", f"## {T['rollback']}", "", rollback]
    return "\n".join(lines) + "\n"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def reports_dir(f: Feature) -> Path:
    return f.path / "reports"


def board_paths(root: Path) -> tuple[Path, Path]:
    return egd_dir(root) / "BOARD.md", egd_dir(root) / "board.html"



# ------------------------------------------------------------------ standup

VERB = {"feature_closed": "closed", "feature_reopened": "reopened",
        "task_started": "started", "task_submitted": "submitted", "task_accepted": "accepted",
        "task_rejected": "sent back", "task_done_solo": "finished solo", "task_blocked": "blocked",
        "task_unblocked": "unblocked", "task_reopened": "reopened", "gate_passed": "passed gate",
        "uat": "UAT", "cr_opened": "opened", "cr_approved": "approved", "cr_rejected": "rejected",
        "defect_opened": "reported", "defect_fixed": "fixed", "defect_closed": "closed",
        "proof_run": "ran proofs"}


def standup(root: Path, cfg: dict, hours: int = 24) -> str:
    """Per person: what moved since `hours` ago, and what they hold now."""
    since = now() - dt.timedelta(hours=hours)
    team = Team(root, cfg)
    spelled: dict[str, str] = {}

    def who(name: str | None) -> str:
        """One person, one line: team.toml's name for any of their names or aliases."""
        member = team.member(name) if name else None
        name = (member.get("name") or member.get("github") or name) if member else (name or "?")
        return spelled.setdefault(norm_name(name), name)

    moved: dict[str, dict[str, int]] = {}
    holding: dict[str, list[str]] = {}
    review_q, blocked_q, ready_n = [], [], 0
    for f in all_features(root):
        st = replay(f)
        proofs_seen: set[tuple] = set()
        for e in st.events:
            if not e.get("at") or parse_iso(e["at"]) < since or e.get("type") not in VERB:
                continue
            target = (e.get("task") or e.get("gate") or e.get("slice") or e.get("cr") or e.get("defect")
                      or (f"as {e['outcome']}" if e.get("outcome") else ""))
            if e["type"] == "proof_run":
                key = (who(e.get("by")), e.get("run"))
                if key in proofs_seen:
                    continue
                proofs_seen.add(key)
                target = ""
            if e["type"] == "uat":
                target += " ✓" if e.get("passed") else " ✗"
            line = " ".join(x for x in (VERB[e["type"]], target) if x) + f" ({f.title})"
            seen = moved.setdefault(who(e.get("by")), {})
            seen[line] = seen.get(line, 0) + 1
        for t in [] if st.closed else f.tasks:  # a closed feature holds nobody
            ts = st.task(t["id"])
            if ts.owner and ts.status in ("doing", "review", "blocked"):
                holding.setdefault(who(ts.owner), []).append(f"{t['id']} {ts.status} ({f.title})")
            if ts.status == "review":
                review_q.append(f"{t['id']} by {who(ts.submitted_by)} ({f.title})")
            if ts.status == "blocked":
                blocked_q.append(f"{t['id']} — {ts.block_reason} ({f.title})")
        if "slice" in st.gates:
            ready_n += len(ready(f, st))
    lines = [f"Standup — last {hours}h", ""]
    people = sorted(set(moved) | set(holding), key=str.lower)
    for p in people:
        lines.append(f"{p}")
        for m, n in moved.get(p, {}).items():
            lines.append(f"  · {m}" + (f" ×{n}" if n > 1 else ""))
        if holding.get(p):
            lines.append(f"  now: {'; '.join(holding[p])}")
    if not people:
        lines.append("no activity and nothing in hand")
    lines += ["", f"review queue: {'; '.join(review_q) or 'empty'}",
              f"blocked: {'; '.join(blocked_q) or 'nothing'}",
              f"ready to pick up: {plural(ready_n, 'task')}"]
    return "\n".join(lines)
