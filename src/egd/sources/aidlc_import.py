"""`egd import aidlc` — move a repository's ai-dlc plans (`.ai/features/`) into EGD.

One-way and read-only towards ai-dlc: `.ai/` is read, never changed, and no ai-dlc code
is run. For every feature it writes `.egd/features/<same name>/` with brief, design,
plan.toml (assumptions, acceptance criteria, decisions, slices from units of work, tasks
from tickets), release notes, and an event trail replayed from `history.jsonl` — keeping
the original people and times, each event marked `imported: "ai-dlc"`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from .. import RULES
from ..events import write_event
from ..model import load_feature
from ..planfile import toml_value
from ..templates import CONFIG, MAP, PROOF, setup_repo
from ..util import is_blank_template, md_sections, write_atomic
from .aidlc import GATES, STATUS, assumption_rows, front_matter, hours, read

GATE_MAP = {"G0": "frame", "G1": "clarify", "G2": "design", "G3": "slice", "G4": "build", "G5": "release"}
TASK_EVENT = {"ticket in_progress": "task_started", "ticket review": "task_submitted",
              "ticket done": "task_accepted", "ticket done (review bypassed)": "task_done_solo",
              "ticket blocked": "task_blocked", "ticket unblocked": "task_unblocked",
              "reopened": "task_reopened"}
MARK = "ai-dlc"


# ------------------------------------------------------------------ parsing helpers

def local_iso(raw: str | None, fallback: dt.datetime) -> str:
    """ai-dlc writes naive local times; EGD stores aware ones."""
    try:
        t = dt.datetime.fromisoformat(str(raw)) if raw else fallback
    except ValueError:
        t = fallback
    return (t if t.tzinfo else t.astimezone()).isoformat(timespec="seconds")


_GWT = re.compile(r"^\s*given\s+(.+?),?\s+when\s+(.+?),?\s+then\s+(.+)$", re.I | re.S)


def _table_acs(text: str) -> list[dict]:
    """`| AC-01 | text |` or `| AC-01 | given | when | then |` tables."""
    out, header = [], None
    for line in text.splitlines():
        if not line.startswith("|"):
            header = None if not line.strip() else header
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None and not re.match(r"^AC-\d+", cells[0]):
            header = [c.lower() for c in cells]
            continue
        if not re.match(r"^AC-\d+", cells[0]):
            continue
        row = dict(zip(header or [], cells))
        g = next((v for k, v in row.items() if "given" in k), "")
        w = next((v for k, v in row.items() if "when" in k), "")
        t = next((v for k, v in row.items() if "then" in k), "")
        text_ = t or (cells[1] if len(cells) > 1 else "")
        out.append({"id": cells[0], "title": text_[:120], "story": "", "given": g, "when": w, "then": text_})
    return out


def _list_acs(text: str) -> list[dict]:
    """`- **AC-01** — Given …, when …, then …` with wrapped continuation lines."""
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]\s+\*\*(AC-\d+)\*\*\s*[—:-]\s*(.*)$", line)
        if m:
            cur = {"id": m.group(1), "raw": m.group(2).strip()}
            out.append(cur)
        elif cur is not None and line.startswith((" ", "\t")) and line.strip():
            cur["raw"] += " " + line.strip()
        else:
            cur = None
    result = []
    for c in out:
        raw = re.sub(r"\s+", " ", c["raw"]).strip()
        m = _GWT.match(raw)
        if m:
            result.append({"id": c["id"], "title": raw[:120], "story": "", "given": m.group(1),
                           "when": m.group(2), "then": m.group(3).rstrip(".")})
        else:
            result.append({"id": c["id"], "title": raw[:120], "story": "", "given": "", "when": "", "then": raw})
    return result


def acceptance_criteria(text: str) -> list[dict]:
    found = _bold_acs(text)
    known = {a["id"] for a in found}
    for extra in (_table_acs(text), _list_acs(text)):
        for a in extra:
            if a["id"] not in known:
                found.append(a)
                known.add(a["id"])
    for a in found:
        a["then"] = a["then"] or a["title"]
        a["given"] = a["given"] or "(see the requirement)"
        a["when"] = a["when"] or a["title"]
        if a["title"] and a["then"].startswith(a["title"][:60]):  # only a name when it says something new
            a["title"] = ""
    return found


def _bold_acs(text: str) -> list[dict]:
    out, story, cur, part = [], "", None, None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            story, cur = m.group(1).strip(), None
            continue
        m = re.match(r"^\*\*(AC-\d+)\*\*\s*[—-]\s*(.+)$", line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2).strip(), "story": story, "given": "", "when": "", "then": ""}
            out.append(cur)
            part = None
            continue
        if cur is None:
            continue
        if line.strip().startswith("```"):  # ```gherkin fences around the steps
            continue
        m = re.match(r"^\s*(Given|When|Then|And|But)\b\s*(.*)$", line)
        if m:
            key = m.group(1).lower()
            # "And …" / "But …" add a clause to the step above; keep the conjunction readable
            joiner = f", {key} " if key in ("and", "but") else " "
            if key in ("and", "but"):
                key = part or "then"
            part = key
            cur[key] = (cur[key] + joiner + m.group(2).strip()) if cur[key] else m.group(2).strip()
        elif line.strip() and part and not line.startswith(("#", "**", "|", "-")):
            cur[part] = (cur[part] + " " + line.strip()).strip()
        elif not line.strip():
            part = None
    return out


def rejected_assumptions(text: str) -> list[dict]:
    rows, header = [], None
    for line in text.splitlines():
        if not line.startswith("|"):
            header = None if not line.strip() else header
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: ") or not any("assumed" in h for h in header):
            continue
        row = dict(zip(header, cells))
        aid = row.get("id", "")
        if re.match(r"^A-\d+", aid):
            text_col = next((v for k, v in row.items() if "assumed" in k), "")
            truth = next((v for k, v in row.items() if "actually" in k), "")
            rows.append({"id": aid, "text": re.sub(r"\*\*", "", text_col), "blocking": False,
                         "status": "rejected", "resolution": re.sub(r"\*\*", "", truth) or "rejected in ai-dlc"})
    return rows


def decisions(design: str, design_passed: bool) -> list[dict]:
    out = []
    for m in re.finditer(r"^###\s+(ADR-\d+)\s*[—-]\s*(.+?)\n(.*?)(?=^###\s|^##\s|\Z)", design, re.M | re.S):
        body = m.group(3)
        s = re.search(r"\*\*Status:?\*\*:?\s*(\w+)", body)
        status = (s.group(1).lower() if s else ("accepted" if design_passed else "proposed"))
        if status not in ("proposed", "accepted", "superseded"):
            status = "accepted" if status in ("approved", "done") else "proposed"
        choice = re.search(r"Decision:\s*(.+?)(?:\.\s|$)", body, re.S)
        out.append({"id": m.group(1), "title": m.group(2).strip(), "status": status,
                    "choice": re.sub(r"\s+", " ", choice.group(1)).strip() if choice else ""})
    return out


def done_when(ticket_text: str) -> list[str]:
    items = []
    sec = md_sections(ticket_text).get("done when", "")
    for line in sec.splitlines():
        m = re.match(r"^\s*-\s*\[[ xX]\]\s*(.+)$", line)
        if m:
            items.append(re.sub(r"\s+", " ", m.group(1)).strip())
    return items


# ------------------------------------------------------------------ writing

AC_KEYS = ("id", "title", "story", "given", "when", "then", "levels", "kind")

def _plan_toml(meta: dict, assumptions, acs, decs, slices, tasks) -> str:
    out = [f"# Imported from ai-dlc ({meta['imported_from']}) on {dt.date.today().isoformat()}.",
           "# ids are kept as they were, so tickets, commits and history still line up.", "", "[feature]"]
    out += [f"{k} = {toml_value(v)}" for k, v in meta.items()]

    def block(kind, items, keys):
        for it in items:
            out.extend(["", f"[[{kind}]]"])
            for k in keys:
                v = it.get(k)
                if v is None or v == "" or v == []:
                    continue
                out.append(f"{k} = {toml_value(v)}")

    block("assumption", assumptions, ("id", "text", "confidence", "blocking", "status", "resolution"))
    block("ac", acs, AC_KEYS)
    block("decision", decs, ("id", "title", "status", "choice"))
    block("slice", slices, ("id", "title", "covers", "demo"))
    block("task", tasks, ("id", "slice", "title", "estimate_h", "depends_on", "touches", "tests", "done_when"))
    return "\n".join(out) + "\n"


def _brief(title: str, intent: str) -> str:
    sec = md_sections(intent)
    intro = intent.split("\n## ", 1)[0]
    intro = "\n".join(ln for ln in intro.splitlines() if not ln.startswith("#") and not ln.startswith("<!--")).strip()
    lines = [f"# {title}", ""]
    for name in ("problem", "outcome", "success signal", "out of scope"):
        body = sec.get(name) or (intro if name == "outcome" else "")
        lines += [f"## {name.capitalize()}", body or "<!-- not present in the ai-dlc intent -->", ""]
    for name, body in sec.items():
        if name not in ("problem", "outcome", "success signal", "out of scope"):
            lines += [f"## {name.capitalize()}", body, ""]
    return "\n".join(lines)


def _design(text: str) -> str:
    text = re.sub(r"^##\s+Alternatives rejected", "## Alternatives considered", text, flags=re.M)
    text = re.sub(r"^##\s+Error taxonomy", "## Failure modes", text, flags=re.M)
    return text


class Trail:
    """Writes event files with their original time and author."""

    def __init__(self, fdir: Path):
        self.dir = fdir / "events"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.seq = 0
        self.count = 0

    def add(self, kind: str, at: str, by: str, **data):
        self.seq += 1
        event = {"type": kind, "seq": self.seq, "at": at, "by": by or "unknown", "imported": MARK, **data}
        write_event(self.dir, event, re.sub(r"[^0-9T]", "", at)[:15])
        self.count += 1


def import_feature(src: Path, dest_root: Path, by: str) -> dict:
    slug = src.name
    target = dest_root / ".egd" / "features" / slug
    if target.exists():
        return {"feature": slug, "skipped": "already imported"}
    intent = read(src / "00-intent.md")
    title_m = re.search(r"^#\s+(.+)$", intent, re.M)
    title = re.sub(r"^(Intent|Ý định)\s*[—:-]\s*", "", title_m.group(1)).strip() if title_m else slug

    state = read(src / ".aidlc-state.yaml")
    cur_m = re.search(r"^current_gate:\s*(\S+)", state, re.M)
    current = cur_m.group(1) if cur_m else "none"
    created_m = re.search(r"^created:\s*(\S+)", state, re.M)
    fallback_time = dt.datetime.fromtimestamp((src / "00-intent.md").stat().st_mtime) if (src / "00-intent.md").exists() \
        else dt.datetime.now()

    design_text = read(src / "03-logical-design.md")
    passed_ids = GATES[: GATES.index(current) + 1] if current in GATES else []

    assumptions = assumption_rows(read(src / "01-assumptions.md"))
    for a in assumptions:
        a["confidence"] = None
    assumptions += [r for r in rejected_assumptions(read(src / "01-assumptions.md"))
                    if r["id"] not in {a["id"] for a in assumptions}]
    acs = acceptance_criteria(read(src / "02-requirements.md"))
    decs = decisions(design_text, "G2" in passed_ids)

    slices, tasks, ticket_status, rollbacks = [], [], {}, []
    for udir in sorted((src / "04-units-of-work").glob("*/")):
        uow_text = read(udir / "uow.md")
        u = front_matter(uow_text)
        uid = u.get("id") or udir.name
        slices.append({"id": uid, "title": u.get("title", udir.name), "covers": u.get("verifies") or [],
                       "demo": md_sections(uow_text).get("demo script", "")})
        if u.get("rollback"):
            rollbacks.append(f"- **{uid}** — {u['rollback']}")
        for tfile in sorted((udir / "tickets").glob("*.md")):
            text = read(tfile)
            t = front_matter(text)
            tid = t.get("id") or tfile.stem
            dw = done_when(text) or ([f"verifies {', '.join(t['verifies'])}"] if t.get("verifies") else
                                     ["see the ticket in .ai/"])
            tasks.append({"id": tid, "slice": uid, "title": t.get("title", ""), "estimate_h": hours(t.get("estimate")),
                          "depends_on": t.get("depends_on") or [],
                          "touches": [p for p in (t.get("touches") or []) if isinstance(p, str)],
                          "tests": [p for p in (t.get("tests") or []) if isinstance(p, str)], "done_when": dw})
            ticket_status[tid] = STATUS.get(t.get("status", "todo"), "todo")
    for t in tasks:
        if t["estimate_h"] is None:
            t["estimate_h"] = 1

    meta = {"title": title, "tier": "standard", "client": "", "imported_from": f"{MARK}:{slug}"}
    target.mkdir(parents=True)
    (target / "brief.md").write_text(_brief(title, intent), encoding="utf-8")
    if design_text:
        (target / "design.md").write_text(_design(design_text), encoding="utf-8")
    (target / "plan.toml").write_text(_plan_toml(meta, assumptions, acs, decs, slices, tasks), encoding="utf-8")
    (target / "proof.toml").write_text(
        f"# ai-dlc verification steps for this feature are in .ai/features/{slug}/07-verification.md.\n"
        + PROOF.format(title=title.replace('"', "'")), encoding="utf-8")
    (target / "release.md").write_text(
        f"# Release — {title}\n\n## Rollback\n" + ("\n".join(rollbacks) if rollbacks else
                                                  "<!-- not recorded in ai-dlc -->") + "\n", encoding="utf-8")

    # ---- trail
    feature = load_feature(dest_root, target)
    history = []
    for line in read(src / "history.jsonl").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            history.append(e)
    history.sort(key=lambda e: e.get("ts") or e.get("at") or "")
    trail = Trail(target)
    first_at = local_iso(history[0].get("at") if history else (created_m.group(1) if created_m else None), fallback_time)
    trail.add("feature_created", first_at, history[0].get("by", by) if history else by, tier="standard", rules=RULES,
              source=f".ai/features/{slug}")
    status: dict[str, str] = {}
    gates_seen: set[str] = set()

    def pass_gate(gid: str, at: str, who: str, note: str = ""):
        egd_gate = GATE_MAP[gid]
        if gid == "G5" and "accept" not in gates_seen:
            gates_seen.add("accept")
            trail.add("gate_passed", at, who, gate="accept", note="implied by ai-dlc G5 (close)")
        if egd_gate in gates_seen:
            return
        gates_seen.add(egd_gate)
        data = {"gate": egd_gate, "note": note or f"ai-dlc {gid}"}
        if egd_gate in ("clarify", "slice"):
            data["scope"] = feature.scope_hash()
        if egd_gate == "slice":
            data["planned_tasks"] = [t["id"] for t in tasks]
            data["planned_proofs"] = []
        trail.add("gate_passed", at, who, **data)

    for e in history:
        action, at, who = e.get("action", ""), local_iso(e.get("at"), fallback_time), e.get("by", "")
        if action == "passed" and e.get("gate") in GATE_MAP:
            pass_gate(e["gate"], at, who, (e.get("evidence") or "")[:300])
        elif action in TASK_EVENT and e.get("ticket") in ticket_status:
            tid = e["ticket"]
            kind = TASK_EVENT[action]
            extra = {}
            if kind in ("task_blocked", "task_rejected", "task_reopened", "task_done_solo"):
                extra["reason"] = e.get("reason") or e.get("evidence") or f"ai-dlc: {action}"
            trail.add(kind, at, who, task=tid, **extra)
            status[tid] = {"task_started": "doing", "task_submitted": "review", "task_accepted": "done",
                           "task_done_solo": "done", "task_blocked": "blocked", "task_reopened": "doing",
                           "task_unblocked": status.get(tid, "doing")}.get(kind, status.get(tid, "todo"))
    # gates the history does not show (older features): take them from the state file
    now_at = local_iso(None, fallback_time)
    for gid in passed_ids:
        pass_gate(gid, now_at, "ai-dlc import", "passed in ai-dlc (no history entry)")
    # make each task end where its ticket says it is
    for tid, want in ticket_status.items():
        have = status.get(tid, "todo")
        if have == want:
            continue
        why = "status taken from the ai-dlc ticket"
        if want == "done":
            trail.add("task_done_solo", now_at, "ai-dlc import", task=tid, reason=why)
        elif want == "doing" and have == "todo":
            trail.add("task_started", now_at, "ai-dlc import", task=tid)
        elif want == "review":
            if have == "todo":
                trail.add("task_started", now_at, "ai-dlc import", task=tid)
            trail.add("task_submitted", now_at, "ai-dlc import", task=tid)
        elif want == "blocked":
            trail.add("task_blocked", now_at, "ai-dlc import", task=tid, reason=why)
        elif want == "doing" and have in ("review", "done"):
            trail.add("task_reopened" if have == "done" else "task_rejected", now_at, "ai-dlc import",
                      task=tid, reason=why)
    return {"feature": slug, "title": title, "gate": current, "acs": len(acs), "slices": len(slices),
            "tasks": len(tasks), "assumptions": len(assumptions), "decisions": len(decs), "events": trail.count}


def refresh_acs(src: Path, dest_root: Path) -> dict:
    """Re-read the acceptance criteria of an imported feature from `.ai/` and rewrite its [[ac]] blocks.

    For fixing what an earlier importer misread. Only gate fingerprints the importer itself wrote
    (events marked imported) follow the new text; a scope a person agreed to after the import is
    left alone, so a real change still shows up as one.
    """
    slug = src.name
    target = dest_root / ".egd" / "features" / slug
    plan_path = target / "plan.toml"
    if not plan_path.exists():
        return {"feature": slug, "skipped": "not imported"}
    old = load_feature(dest_root, target)
    if not str(old.meta.get("imported_from", "")).startswith(MARK):
        return {"feature": slug, "skipped": "not an ai-dlc import"}
    fresh = acceptance_criteria(read(src / "02-requirements.md"))
    if not fresh:
        return {"feature": slug, "skipped": "no acceptance criteria found"}
    kept = {a.get("id"): a for a in old.acs}
    for a in fresh:  # levels and kinds added in EGD since the import stay
        for k in ("levels", "kind"):
            if kept.get(a["id"], {}).get(k):
                a[k] = kept[a["id"]][k]
    text = plan_path.read_text(encoding="utf-8")
    blocks = list(re.finditer(r"\n\[\[ac\]\]\n.*?(?=\n\[|\Z)", text, re.S))
    if not blocks:
        return {"feature": slug, "skipped": "no [[ac]] blocks"}
    fresh_text = "\n\n".join("[[ac]]\n" + "\n".join(f"{k} = {toml_value(a[k])}" for k in AC_KEYS
                                                     if a.get(k) not in (None, "", [])) for a in fresh)
    start, end = blocks[0].start(), blocks[-1].end()
    middle = text[start:end]
    for b in reversed(blocks):  # drop every ac block; anything between them stays
        middle = middle[:b.start() - start] + middle[b.end() - start:]
    old_hash = old.scope_hash()
    write_atomic(plan_path, text[:start] + "\n" + fresh_text + "\n" + middle + text[end:])
    new_hash = load_feature(dest_root, target).scope_hash()
    moved, notes = 0, []
    if new_hash != old_hash:
        for ev in sorted((target / "events").glob("*.json")):
            try:
                e = json.loads(ev.read_text(encoding="utf-8"))
            except (OSError, ValueError):  # ValueError covers bad JSON and bad UTF-8
                e = None
            if not isinstance(e, dict):
                notes.append(f"skipped unreadable event {ev.name}")
                continue
            if e.get("imported") == MARK and e.get("scope") == old_hash:
                e["scope"] = new_hash
                write_atomic(ev, json.dumps(e, indent=2, ensure_ascii=False) + "\n")
                moved += 1
    changed = sum(1 for a in fresh if {k: a.get(k) for k in ("given", "when", "then")}
                  != {k: kept.get(a["id"], {}).get(k) for k in ("given", "when", "then")})
    return {"feature": slug, "acs": len(fresh), "changed": changed, "fingerprints": moved, "notes": notes}


def refresh_repo(root: Path, only: list[str] | None = None) -> list[dict]:
    src = root / ".ai" / "features"
    if not src.is_dir():
        raise FileNotFoundError(f"{root} has no .ai/features/")
    return [refresh_acs(fdir, root) for fdir in sorted(p for p in src.iterdir() if p.is_dir())
            if not only or fdir.name in only or any(o in fdir.name for o in only)]


def import_repo(root: Path, by: str, only: list[str] | None = None) -> dict:
    src = root / ".ai"
    if not (src / "features").is_dir():
        raise FileNotFoundError(f"{root} has no .ai/features/")
    created = setup_repo(root)
    base = root / ".egd"
    notes = []
    arch = read(src / "architecture.md")
    map_path = base / "map.md"
    if arch and (not map_path.exists() or is_blank_template(map_path.read_text(encoding="utf-8"))
                 or map_path.read_text(encoding="utf-8") == MAP):
        signer = re.search(r"^verified_by:[ \t]*([^#\n]*)", arch, re.M)
        body = re.sub(r"^verified_by:.*$", "", arch, count=1, flags=re.M)
        map_path.write_text(f"# Repository map\n\nreviewed_by: {(signer.group(1).strip() if signer else '')}\n\n"
                            f"<!-- imported from .ai/architecture.md -->\n\n{body.strip()}\n", encoding="utf-8")
        notes.append("map.md ← .ai/architecture.md")
    cfg_path = base / "config.toml"
    yaml = read(src / "aidlc.yaml")
    cmd = re.search(r"^evidence:\s*\n(?:\s+.*\n)*?\s+command:\s*[\"']?(.+?)[\"']?\s*$", yaml, re.M)
    if cmd and cfg_path.read_text(encoding="utf-8") == CONFIG:
        cfg_path.write_text(CONFIG.replace('# test_command = "pnpm vitest run {tests}"',
                                           f"test_command = {toml_value(cmd.group(1))}"), encoding="utf-8")
        notes.append("proof.test_command ← aidlc.yaml evidence.command")
    results = []
    for fdir in sorted(p for p in (src / "features").iterdir() if p.is_dir()):
        if only and fdir.name not in only and not any(o in fdir.name for o in only):
            continue
        results.append(import_feature(fdir, root, by))
    return {"created": [str(p.relative_to(root)) for p in created], "notes": notes, "features": results}
