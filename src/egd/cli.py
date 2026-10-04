"""`egd` — the command line. Exit codes: 0 ok · 1 refused or failed · 2 usage error."""

from __future__ import annotations

import sys

INSTALL = "curl -fsSL https://raw.githubusercontent.com/LocTran12310/egd/main/install.sh | sh"
if sys.version_info < (3, 11):  # before anything below needs tomllib
    sys.stderr.write("EGD needs Python 3.11 or newer (found %d.%d) — install one, or use the installer: %s\n"
                     % (sys.version_info[0], sys.version_info[1], INSTALL))
    sys.exit(2)

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path

# github, templates and tasks serve a few commands each and are imported inside them.
from . import RULES, __version__, gates, planfile, report, tracking
from .i18n import LANGS
from .events import read, record, replay
from .graph import assignment, longest_path, ready, validate, waves
from .model import (GATES, GATES_BY_TIER, TIERS, all_features, closest, feature_owning,
                    load_feature, resolve_feature, short_name)
from .proof import configured_envs, proof_envs, proof_matrix, run_proofs
from .team import Team, whoami
from .util import (git, git_memo, plural, Refused, UsageError, features_dir, find_root, interpolate, load_config,
                   load_secrets, load_toml, missing_vars, slugify, tracked_but_ignored, EGD_DIR)


def out(text: str = "") -> None:
    print(text, flush=True)


def ctx(args):
    root = find_root(Path(args.root) if getattr(args, "root", None) else None)
    cfg = load_config(root)
    return root, cfg, Team(root, cfg)


def signer(args, root: Path, team: Team) -> str:
    """Settle who signs into `args.by`: --by, else EGD_USER, else git user.name, as the team names them.

    A name nobody typed is announced after the command (`signed as …`), so no one signs by accident.
    """
    name, source = whoami(root, args.by)
    if source != "--by" and os.environ.get("CLAUDECODE"):
        # an agent must never sign as the person whose machine it runs on: it names the signer
        raise UsageError("inside Claude Code a signature is never implied — pass --by <who is signing>")
    args.by = team.canonical(name, source)
    if source != "--by":
        args.signed_via = source
    return args.by


def _who(args) -> str:
    """The signer for a line that names them — with where the name came from when nobody typed it,
    so `main` does not print a second `signed as …` line."""
    args.signed_shown = True
    via = getattr(args, "signed_via", None)
    return f"{args.by} (from {via})" if via else args.by


def _notes(event) -> None:
    """What the core said alongside a recorded event: tests not run, no roles defined, …"""
    for n in (event.get("notes") or []) if isinstance(event, dict) else []:
        out(f"note: {n}")


# ----------------------------------------------------------------- bare `egd`

INTRO = """\
EGD (Evidence-Gated Delivery) turns a feature idea into a brief, acceptance criteria and small
tasks, then moves the work through gates this CLI checks: tests pass, scope holds, someone else
reviews, evidence is fresh, the client signs. It all lives in .egd/, committed with your code.
There is no .egd/ here or above — start with `egd setup`, then `egd new <feature>`.
`egd -h` lists every command."""


@git_memo()
def cmd_home(args):
    """Bare `egd`: each feature's next step, and the one command worth running now."""
    try:
        root = find_root(Path(args.root) if args.root else None)
    except UsageError:
        out(INTRO)
        return 0
    cfg = load_config(root)
    team = Team(root, cfg)
    name, _ = whoami(root)
    me = (team.member(name) or {}).get("name", name) if name else ""
    features = all_features(root)
    count = f"{len(features) or 'no'} feature{'' if len(features) == 1 else 's'}"
    you = f" · you: {me}" + (" (inside Claude Code pass --by)" if os.environ.get("CLAUDECODE") else "")
    out(f"EGD · {root.name} · {count}" + (you if me else ""))
    tracked = tracked_but_ignored(root)
    if f"{EGD_DIR}/config.toml" in tracked:  # .egd/ itself is kept on this machine
        out(f"warning: .egd/ is meant to stay on this machine, yet git tracks {plural(len(tracked), 'file')} in it "
            "— `git rm -r --cached .egd` stops that (the files stay on disk)")
    elif tracked:  # a shared .egd/, with something committed that never should be
        out(f"warning: git tracks {', '.join(tracked[:3])}{' …' if len(tracked) > 3 else ''}, which EGD keeps out "
            f"of commits (secrets, sessions) — `git rm --cached {' '.join(tracked[:3])}`, and change any secret in it")
    short = {f.slug: short_name(f.slug) for f in features}
    if len(set(short.values())) < len(short):
        short = {s: s for s in short}
    ref = (lambda f: f" {short[f.slug]}") if len(features) > 1 else (lambda f: "")
    nxt = None  # (rank, feature index, command, why) — the lowest rank wins, then the oldest feature
    states = [replay(f) for f in features]
    for i, (f, st) in enumerate(zip(features, states)):
        gate = gates.next_gate(f, st)
        mine = [(tid, t.status) for tid, t in st.tasks.items()
                if t.status in ("doing", "review") and me and team.same(t.owner, me)]
        if i < 6:
            out(f"  {short[f.slug]:<28} " + (f"next gate: {gate}" if gate else gates.stage(f, st))
                + ("".join(f" · you hold {tid} ({s})" for tid, s in mine)))
        if gate is None:
            continue
        doing = [tid for tid, s in mine if s == "doing"]
        # never suggest reviewing your own task — nor any task when we cannot tell who you are
        review = [tid for tid, t in st.tasks.items() if t.status == "review" and me
                  and not team.same(t.submitted_by, me) and not team.same(t.owner, me)]
        todo = ready(f, st) if "slice" in st.gates else []
        if doing:
            cand = (0, i, f"egd submit {doing[0]} --confirm", "send it to review once its done_when holds")
        elif review:
            cand = (1, i, f"egd accept {review[0]}", "review a teammate's task (or `egd reject … --reason`)")
        elif todo:
            cand = (2, i, f"egd start {todo[0]['id']}", todo[0].get("title") or "claim a ready task")
        elif gate == "build" and any(t.status != "done" for t in st.tasks.values()):
            cand = (3, i, f"egd status{ref(f)}", "tasks are still in flight")
        else:
            cand = (4, i, f"egd check {gate}{ref(f)}", gates.NEXT[gate])
        nxt = min(nxt or cand, cand)
    if len(features) > 6:
        out(f"  … {len(features) - 6} more — `egd list`")
    rank, i, command, why = nxt or (5, 0, "egd new <name>", "start a feature")
    if rank == 4:
        f, st = features[i], states[i]
        gate = gates.next_gate(f, st)
        if not gates.check(f, st, cfg, gate):
            command, why = f"egd pass {gate}{ref(f)}", "its checks hold — sign it"
    out(f"Next: {command}   — {why}")
    out("`egd -h` lists every command.")
    return 0


# ----------------------------------------------------------------- setup / new

def cmd_setup(args):
    from . import templates
    root = Path(args.root or ".").resolve()
    written = templates.setup_repo(root)
    out("EGD is set up." if written else "EGD was already set up; nothing overwritten.")
    for p in written:
        out(f"  wrote {p.relative_to(root)}")
    if args.claude:
        try:
            path = templates.setup_claude(root)
        except ValueError as exc:
            raise UsageError(str(exc)) from exc
        out(f"  updated {path.relative_to(root)} — Claude Code offers the EGD plugin to anyone opening this repo")
    if any(p.name == "config.toml" for p in written):   # a new config: name the stack it looks like
        from . import profiles
        found = profiles.detect(root)
        if found:
            tc = next((c for c in (profiles.test_command(root, profiles.load(root, n)) for n in found) if c), None)
            templates.set_stack(root, found, tc)
            out(f"  stack: {', '.join(found)} (detected){f' · test_command = {tc}' if tc else ''} — `egd profile` shows "
                "what it asks; change it with `egd profile --use <names>`")
    if git(root, "rev-parse", "--is-inside-work-tree") is None:
        out("warning: not a git repository — drift checks and evidence freshness need git (git init)")
    elif args.local:
        out(f"  {templates.keep_local(root)}")
    out("Next: fill .egd/map.md and .egd/team.toml, then `egd new <feature>`.")


def cmd_profile(args):
    """`egd profile`: the stack profiles this repository uses and what they ask of it."""
    from . import profiles, templates
    root, cfg, team = ctx(args)
    have = profiles.available(root)
    if args.list:
        for name, (where, path) in have.items():
            try:
                title = profiles.load(root, name).get("title", "")
            except ValueError as exc:
                title = f"(unreadable: {exc})"
            out(f"{name:<12} {where:<11} {title}")
        out("add one for this repository: egd profile --new <name> [--from <profile>] (--mine: for all your repositories)")
        return 0
    if args.new:
        name = slugify(args.new)
        folder = (Path.home() / ".egd" / "profiles") if args.mine else (root / ".egd" / "profiles")
        target = folder / f"{name}.toml"
        if target.exists():
            raise UsageError(f"{target} already exists — edit it")
        if args.base and args.base not in have:
            raise UsageError(f"no profile '{args.base}' — `egd profile --list`")
        text = have[args.base][1].read_text(encoding="utf-8") if args.base else profiles.TEMPLATE.format(title=name)
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        shown = target if args.mine else target.relative_to(root)
        out(f"wrote {shown} — edit it, then `egd profile --use {name}`"
            + ("" if args.mine else " (commit it so the team shares it)"))
        return 0
    if args.use is not None:
        names = [n.strip() for n in args.use.split(",") if n.strip()]
        unknown = [n for n in names if n not in have]
        if unknown:
            hint = closest(unknown[0], list(have))
            raise UsageError(f"no profile '{unknown[0]}'" + (f" — did you mean '{hint}'?" if hint else "")
                             + f" (available: {', '.join(have)})")
        tc = None if cfg.get("proof", {}).get("test_command") is not None else next(
            (c for c in (profiles.test_command(root, profiles.load(root, n)) for n in names) if c), None)
        templates.set_stack(root, names, tc)
        out(f"stack: {', '.join(names) or '(none)'}" + (f" · test_command = {tc}" if tc else ""))
        cfg = load_config(root)
    act = profiles.active(root, cfg)
    if not act:
        found = profiles.detect(root)
        out("no stack profile set" + (f" — this repository looks like: {', '.join(found)} · "
                                       f"`egd profile --use {','.join(found)}`" if found else
                                       " — `egd profile --list` shows what there is"))
        return 0
    command = cfg.get("proof", {}).get("test_command")
    for p in act:
        out(f"{p['name']} — {p.get('title', '')} ({p['source']})")
        suggested = profiles.test_command(root, p)
        if suggested:
            out(f"  tests: {command if command else suggested + '   (suggested — not set in [proof])'}")
        if p.get("conventions"):
            out("  conventions:")
            out("\n".join("    " + ln for ln in p["conventions"].strip().splitlines()))
        if p.get("review"):
            out("  review for:")
            out("\n".join(f"    - {r}" for r in p["review"]))
        for rule in p.get("done_when", []):
            out(f"  tasks touching {', '.join(rule.get('touches', []))} are done when:")
            out("\n".join(f"    - {i}" for i in rule.get("items", [])))
    return 0


def cmd_new(args):
    from . import templates
    root, cfg, team = ctx(args)
    team.require(signer(args, root, team), "work")
    slug = slugify(args.name)
    for f in all_features(root):
        if short_name(f.slug) == slug and "release" not in replay(f).gates:
            raise UsageError(f"an unreleased feature named '{slug}' already exists: {f.slug} — "
                             "release it first, or pick another name")
    stamp = dt.date.today().strftime("%Y-%m-%d")
    path = features_dir(root) / f"{stamp}-{slug}"
    n = 2
    while path.exists():
        path = features_dir(root) / f"{stamp}-{slug}-{n}"
        n += 1
    path.mkdir(parents=True)
    title = args.title or args.name.replace("-", " ").strip().capitalize()
    files = {"brief.md": templates.BRIEF, "plan.toml": templates.PLAN, "proof.toml": templates.PROOF,
             "release.md": templates.RELEASE}
    if args.tier != "lite":
        files["design.md"] = templates.DESIGN
    for name, text in files.items():
        (path / name).write_text(text.format(title=title.replace('"', "'"), tier=args.tier), encoding="utf-8")
    _notes(record(load_feature(root, path), "feature_created", args.by, tier=args.tier, rules=RULES))
    out(f"created {path.relative_to(root)}  (tier {args.tier})")
    out(f"gates: {' → '.join(GATES_BY_TIER[args.tier])}")
    out("Next: write brief.md, then `egd status`.")


@git_memo()
def cmd_list(args):
    root, cfg, team = ctx(args)
    rows = all_features(root)
    if not rows:
        out("no features yet — `egd new <name>`")
    for f in rows:
        st = replay(f)
        rag, _ = report.health(f, st, cfg)
        stage = gates.stage(f, st)
        out(f"{report.DOT[rag]} {f.slug:<40} {f.tier:<9} {stage if stage in ('released', 'closed') else 'next: ' + stage:<14} "
            f"{report.progress(f, st):>3}%")


def _targets(root: Path, args) -> list:
    """The feature -f names, else every feature."""
    return [resolve_feature(root, args.feature)] if args.feature else all_features(root)


def _planned_rules(f):
    """The rules version a feature was created under — from its `feature_created` event alone, so
    `egd status` does not read the whole trail a second time after `status_text` replayed it."""
    for p in sorted((f.path / "events").glob("*-feature_created.json"))[:1]:
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("rules", RULES)
        except (OSError, ValueError, AttributeError):
            return None
    return None


@git_memo()  # one answer per git question across every feature shown
def cmd_status(args):
    root, cfg, team = ctx(args)
    targets = _targets(root, args)
    if not targets:
        out("no features yet — `egd new <name>`")
    for i, f in enumerate(targets):
        if i:
            out()
        out(report.status_text(f, cfg))
        rules = _planned_rules(f)
        if rules not in (None, RULES):
            out(f"note: planned under rules v{rules}, this egd enforces v{RULES}")


def cmd_check(args):
    root, cfg, team = ctx(args)
    gate = next((t for t in args.targets if t in GATES), None)
    rest = [t for t in args.targets if t != gate] + ([args.feature] if args.feature else [])
    if len(rest) > 1:
        raise UsageError("egd check takes one gate and one feature: egd check [gate] [-f feature]")
    try:
        f = resolve_feature(root, rest[0] if rest else None)
    except UsageError:
        hint = closest(rest[0], GATES) if rest and not gate and not args.feature else None
        if hint:
            raise UsageError(f"no gate or feature '{rest[0]}' — did you mean '{hint}'?") from None
        raise
    st = replay(f)
    gate = gate or gates.next_gate(f, st)
    if gate is None:
        out("every gate has passed")
        return 0
    problems = gates.check(f, st, cfg, gate)
    if problems:
        out(f"{gate}: closed")
        hints = [fix_hint(p, f) for p in problems] + [None]
        for i, p in enumerate(problems):
            out(f"  - {p}")
            if hints[i] and hints[i] != hints[i + 1]:  # once after a run of problems with one fix
                out(f"    Fix: {hints[i]}")
        return 1
    out(f"{gate}: open")
    return 0


# A problem a gate reports → the command (or the file) that fixes it. Gate messages name the
# problem; `egd check` adds the way out. {0}, {1} … are the pattern's groups, {slug} the feature.
FIXES = (
    (r"^(D-[\w.]+): decision still proposed", "egd set {0} status=accepted"),
    (r"^(A-[\w.]+): (?:blocking and still open|assumption still open)",
     'egd set {0} status=confirmed "resolution=<who confirmed it, and how>"'),
    (r"^(A-[\w.]+): \w+ without a resolution", 'egd set {0} "resolution=<who decided, and how>"'),
    (r"^no assumptions recorded", 'egd add assumption --text "<what you are assuming>"'),
    (r"^no acceptance criteria", 'egd add ac --given "…" --when "…" --then "…" --levels unit'),
    (r"^(AC-[\w.]+): missing '(\w+)'", 'egd set {0} "{1}=…"'),
    (r"^(AC-[\w.]+): no test strategy", "egd set {0} levels=unit   (or integration, e2e, manual)"),
    (r"^(AC-[\w.]+): not covered by any slice", 'egd add slice --title "…" --covers {0} --demo "…"'),
    (r"^(AC-[\w.]+): no proof declared",
     'egd add proof --verifies {0} --title "…" --kind ui|http|cli|test   (`egd add proof -h`)'),
    (r"^(S-[\w.]+): has no tasks", 'egd add task --slice {0} --title "…" --estimate 2 --done "…"'),
    (r"^(S-[\w.]+): no demo", 'egd set {0} "demo=1. … 2. …"'),
    (r"^(S-[\w.]+): no UAT verdict", "egd uat {0} --pass --by <client>   (or --fail --note …)"),
    (r"^(T-[\w.]+): todo$", "egd start {0}"),
    (r"^(T-[\w.]+): doing$", "egd submit {0} --confirm"),
    (r"^(T-[\w.]+): review$", "a teammate runs egd accept {0}   (or egd reject {0} --reason …)"),
    (r"^(T-[\w.]+): blocked$", "egd unblock {0}"),
    (r"^([TP]-[\w.]+): planned at .* but removed",
     'egd cr open --title "drop {0}" --reason "…", then the client runs egd cr approve'),
    (r"^(CR-\w+): change request awaiting", "egd cr approve {0} --by <client>   (or egd cr reject {0})"),
    (r"^(BUG-\w+): open", "egd bug fix {0} --task <task>, then egd bug close {0}"),
    (r"^full tier needs at least one accepted", 'egd add decision --title "…" --choice "…" --status accepted'),
    (r"^acceptance criteria changed since", 'egd cr open --title "…" --reason "…" --affects <AC ids>, '
                                            "then the client runs egd cr approve"),
    (r"^gate '(\w+)' has not been passed", "egd check {0}"),
    (r"^\.egd/map\.md has no reviewed_by", "a person who read it writes their name after reviewed_by: in .egd/map.md"),
    (r"^\.egd/map\.md (?:is missing|is still the blank)",
     "map the repository into .egd/map.md (the egd-scout agent drafts it), then a person signs reviewed_by:"),
    (r"^(brief|design|release)\.md", "edit .egd/features/{slug}/{0}.md"),
)


def fix_hint(problem: str, f) -> str | None:
    for pattern, hint in FIXES:
        m = re.match(pattern, problem)
        if m:
            return hint.format(*m.groups(), slug=f.slug)
    return None


def cmd_pass(args):
    root, cfg, team = ctx(args)
    f = resolve_feature(root, args.feature)
    e = gates.pass_gate(f, replay(f), cfg, team, args.gate, signer(args, root, team))
    out(f"passed {args.gate} for {f.slug} — signed {'as ' if getattr(args, 'signed_via', None) else ''}"
        f"{_who(args)}")
    _notes(e)
    st = replay(f)
    nxt = gates.next_gate(f, st)
    if nxt:
        out(f"next: {nxt} — {gates.NEXT[nxt]}")
    if report.rollback_due(f, st) or (nxt == "release" and not f.sections("release.md").get("rollback")):
        out("note: write release.md → ## Rollback now, while the change is fresh: what to revert, "
            "whether migrations reverse, flags, data")


def cmd_close(args):
    root, cfg, team = ctx(args)
    f = resolve_feature(root, args.feature)
    st, who = replay(f), signer(args, root, team)
    if args.undo:
        gates.reopen(f, st, team, who, args.reason)
        out(f"{f.slug} reopened — next: {gates.stage(f, replay(f))}")
        return 0
    e = gates.close(f, st, team, who, args.outcome, args.reason)
    out(f"{f.slug} closed as {args.outcome} — {gates.OUTCOMES[args.outcome]}")
    if e.get("open_tasks"):
        out(f"left undone: {', '.join(e['open_tasks'])} (recorded on the trail; nothing was passed or proven)")
    out(f"changed your mind? egd close {short_name(f.slug)} --undo --reason \"…\"")
    return 0


def cmd_graph(args):
    root, cfg, team = ctx(args)
    f = resolve_feature(root, args.feature)
    errors, warnings = validate(f, cfg)
    if f.tasks and not errors:
        length, chain = longest_path(f.tasks)
        out(f"critical path {length:g}h: {' → '.join(chain)}")
        st = replay(f)
        by_id = {t["id"]: t for t in f.tasks}
        for i, w in enumerate(waves(f.tasks), 1):
            out(f"wave {i}: " + ", ".join(f"{tid} ({'·'.join(assignment(by_id[tid], st)[:2])})" if tid in by_id else tid
                                          for tid in w))
        total = sum(float(t.get('estimate_h', 0) or 0) for t in f.tasks)
        out(f"{len(f.slices)} slices · {len(f.tasks)} tasks · {total:g}h estimated")
    for w in warnings:
        out(f"warning: {w}")
    for e in errors:
        out(f"error: {e}")
    if errors:
        return 1
    out("plan is valid" if f.tasks else "no slices or tasks yet — nothing to validate")
    return 0


def cmd_lint(args):
    """Every feature's plan and trail, for CI. Exit 1 on any error.

    Slice and task rules fail a feature once `slice` has passed; before that the plan is still
    being written, so they print as warnings and a feature on track stays green."""
    root, cfg, team = ctx(args)
    bad = 0
    for f in all_features(root):
        st = replay(f)
        errors = [f.error] if f.error else []
        early = []
        if "slice" in st.gates or "clarify" in st.gates or f.slices or f.tasks:
            plan = [e for e in validate(f, cfg)[0] if e != f.error]
            if "slice" in st.gates:
                errors += plan
            else:
                early = plan
        errors += [f"unreadable event {c}" for c in st.corrupt]
        errors += gates._scope(f, st)
        status = "ok" if not errors else plural(len(errors), "problem")
        if early:
            status += f" · {plural(len(early), 'plan warning')} — errors once `slice` passes"
        out(f"{f.slug}: {status}")
        for e in errors:
            out(f"  - {e}")
        for w in early:
            out(f"  · {w}")
        for w in st.warnings:  # e.g. ids that collided when branches merged — renamed, not lost
            out(f"  · {w}")
        bad += bool(errors)
    return 1 if bad else 0


def cmd_ready(args):
    root, cfg, team = ctx(args)
    targets = _targets(root, args)
    any_ready = False
    for f in targets:
        st = replay(f)
        if "slice" not in st.gates:
            continue
        for t in ready(f, st):
            any_ready = True
            agent, model, why = assignment(t, st)
            out(f"{t['id']:<8} {t.get('estimate_h', '?')}h  {t.get('title', '')}  [{f.slug}]  → {agent} · {model} ({why})")
    if not any_ready:
        out("nothing ready")


# ----------------------------------------------------------------- tasks

def _task_cmd(args, name, **kw):
    from . import tasks
    root, cfg, team = ctx(args)
    f = feature_owning(root, "task", args.task, args.feature)
    f.task(args.task)
    return f, getattr(tasks, name)(f, replay(f), cfg, team, args.task, signer(args, root, team), **kw)


def _drift_reason(args) -> str | None:
    if args.allow_drift and not (args.drift_reason or "").strip():
        raise Refused("--allow-drift needs --drift-reason '<why the change goes beyond the task's touches>'")
    return args.drift_reason


def cmd_start(args):
    f, e = _task_cmd(args, "start", model=args.model)
    t = f.task(args.task)
    out(f"{args.task} → doing (@{args.by})")
    _notes(e)
    out(f"touches: {', '.join(t.get('touches', [])) or '—'}")
    out(f"put '{args.task}' in your commit messages so its changes can be attributed")


def cmd_submit(args):
    f, e = _task_cmd(args, "submit", confirm=args.confirm, spent=args.spent,
                     allow_drift=args.allow_drift, reason=_drift_reason(args))
    out(f"{args.task} → review")
    if e.get("test_run"):
        out(f"tests passed: {e['test_run']['command']}")
    _notes(e)
    if e.get("drift"):
        out("drift recorded: " + ", ".join(e["drift"]))


def cmd_accept(args):
    _, e = _task_cmd(args, "accept")
    out(f"{args.task} → done (accepted by {_who(args)})")
    _notes(e)


def cmd_reject(args):
    _, e = _task_cmd(args, "reject", reason=args.reason)
    out(f"{args.task} → doing (rejected: {args.reason})")
    _notes(e)


def cmd_solo(args):
    f, e = _task_cmd(args, "solo", reason=args.reason, confirm=args.confirm, spent=args.spent,
                     allow_drift=args.allow_drift, drift_reason=_drift_reason(args))
    out(f"{args.task} → done without review (recorded: {args.reason})")
    _notes(e)


def cmd_block(args):
    _, e = _task_cmd(args, "block", reason=args.reason)
    out(f"{args.task} → blocked ({args.reason})")
    _notes(e)


def cmd_unblock(args):
    f, e = _task_cmd(args, "unblock")
    out(f"{args.task} → {replay(f).task(args.task).status}")
    _notes(e)


def cmd_reopen(args):
    _, e = _task_cmd(args, "reopen", reason=args.reason)
    out(f"{args.task} → doing (reopened)")
    _notes(e)


# ----------------------------------------------------------------- client records

def cmd_uat(args):
    root, cfg, team = ctx(args)
    f = feature_owning(root, "slice", args.slice, args.feature)
    if args.passed == args.failed:
        raise UsageError("give exactly one of --pass or --fail")
    e = tracking.uat(f, replay(f), team, args.slice, signer(args, root, team), args.passed, args.note or "")
    out(f"UAT {args.slice}: {'accepted' if args.passed else 'returned'} by {_who(args)}")
    _notes(e)


def cmd_cr(args):
    root, cfg, team = ctx(args)
    f = resolve_feature(root, args.feature)
    st = replay(f)
    if args.action != "list":
        signer(args, root, team)
    if args.action == "open":
        affects = _ac_ids(f, args.affects)
        e = tracking.cr_open(f, st, team, args.by, args.title, args.reason, args.hours, affects)
        out(f"{e['cr']} opened: {args.title} (+{args.hours}h) — waiting for the client")
        out("it covers the acceptance criteria as they stand now (edit plan.toml first); "
            f"the client approves with: egd cr approve {e['cr']} --by <client>")
        _notes(e)
    elif args.action in ("approve", "reject"):
        if not args.id:
            raise UsageError(f"egd cr {args.action} <CR-id> — `egd cr list` shows them")
        e = tracking.cr_decide(f, st, team, args.id, args.by, args.action == "approve", args.note or "")
        out(f"{args.id} {args.action}d by {_who(args)}")
        _notes(e)
    else:
        if not st.crs:
            out("no change requests")
        for k, v in st.crs.items():
            out(f"{k}  {v.get('status'):<9} +{v.get('hours', 0)}h  {v.get('title', '')}")


def _ac_ids(f, raw) -> list[str]:
    """Comma-separated AC ids, each one checked against the plan — like `egd bug --ac`."""
    return _refs(f, "acceptance criterion", raw, [a["id"] for a in f.acs], "none yet — `egd add ac`")


def cmd_bug(args):
    root, cfg, team = ctx(args)
    f = resolve_feature(root, args.feature)
    st = replay(f)
    if args.action != "list":
        signer(args, root, team)
    if args.action == "open":
        if not (args.title or "").strip():
            raise Refused('a defect needs --title "<what is wrong>"')
        e = tracking.defect_open(f, st, team, args.by, args.title, args.severity, args.found_in,
                                 _ac_ids(f, args.ac), args.task)
        out(f"{e['defect']} opened [{args.severity}, found in {args.found_in}]: {args.title}")
        _notes(e)
    elif args.action in ("fix", "close", "reopen"):
        if not args.id:
            raise UsageError(f"egd bug {args.action} <BUG-id> — `egd bug list` shows them")
        to = {"fix": "fixed", "close": "closed", "reopen": "reopened"}[args.action]
        e = tracking.defect_move(f, st, team, args.id, args.by, to, args.task, args.note or "")
        out(f"{args.id} → {to}")
        _notes(e)
    else:
        if not st.defects:
            out("no defects")
        for k, d in st.defects.items():
            out(f"{k}  {d.get('status'):<7} {d.get('severity'):<8} {d.get('found_in'):<6} {d.get('title', '')}")


# ----------------------------------------------------------------- proof

def cmd_proof(args):
    if args.action == "setup":
        from .proof.ui import setup
        return setup(out)
    root, cfg, team = ctx(args)
    if args.action == "login":
        from .proof.ui import login
        env = args.env or "local"
        env_cfg = cfg.get("env", {}).get(env)
        if not env_cfg:
            known = ", ".join(cfg.get("env", {})) or "none"
            raise UsageError(f"env '{env}' is not configured (configured: {known}) — add "
                             f'[env.{env}] base_url = "…" to .egd/config.toml')
        secrets = load_secrets(root)
        missing = missing_vars(env_cfg.get("base_url", ""), secrets)
        if missing:
            raise UsageError("not set: " + ", ".join(missing) + " — put them in .egd/secrets.env "
                             "(never committed) or the environment")
        return login(root, cfg, env, interpolate(env_cfg["base_url"], secrets), args.path or "/")
    f = resolve_feature(root, args.feature)
    if args.action == "list":
        with git_memo():
            for p, env, required, run, state in proof_matrix(f, replay(f), cfg):
                s = (run["status"] if run else "never run") + {"stale": " (stale)", "unknown": " (fresh?)"}.get(state, "")
                out(f"{p['id']:<6} {p.get('kind', '?'):<5} {env:<9}{'' if required else '(opt)':<6} {s:<16} "
                    f"{', '.join(p.get('verifies', []))}  {p.get('title', '')}")
        if not f.proofs:
            out("no proofs in proof.toml")
        bare = [a["id"] for a in f.acs if not any(a["id"] in p.get("verifies", []) for p in f.proofs)]
        if bare:
            out(f"no proof yet: {', '.join(bare)} — `egd add proof --verifies {bare[0]} …`")
        return 0
    if args.action == "report":
        path = report.write(report.reports_dir(f) / "evidence.md", report.proof_report(f, cfg))
        out(f"wrote {path.relative_to(root)}")
        return 0
    if not f.proofs:
        out('no proofs in proof.toml — `egd add proof --kind test --verifies AC-1.1 --title "…"` adds one')
        return 0
    only, env = _proof_filter(f, cfg, args.only, args.env)
    team.require(signer(args, root, team), "work")
    results = run_proofs(f, cfg, args.by, only=only, env_filter=env)
    if not results:
        raise UsageError("0 proofs matched " + " ".join(x for x in (
            f"--only {','.join(only)}" if only else "", f"--env {env}" if env else "") if x)
            + " — `egd proof list` shows which proof runs in which env")
    failed = 0
    for r in results:
        mark = {"pass": "✅", "fail": "❌"}.get(r["status"], "⚠️")
        out(f"{mark} {r['proof']}@{r['env']}: {r['status']}" + (f" — {r['reason']}" if r.get("reason") else ""))
        failed += r["status"] != "pass" and r.get("required", True)
    if results:
        out(f"transcripts: {(f.path / 'runs' / results[0]['run']).relative_to(root)}/")
        report.write(report.reports_dir(f) / "evidence.md", report.proof_report(f, cfg))
    from .proof import run_notes
    for n in run_notes(results):
        out(f"note: {n}")
    return 1 if failed else 0


def _proof_filter(f, cfg: dict, only_raw: str | None, env: str | None) -> tuple[list[str] | None, str | None]:
    """--only and --env checked against what exists: a typo is an error, never a silent no-op.
    Proof ids match in any case and come back as proof.toml spells them."""
    only = None
    if only_raw is not None:
        ids = [p["id"] for p in f.proofs]
        by_lower = {i.lower(): i for i in ids}
        only = []
        for x in (x.strip() for x in only_raw.split(",")):
            if not x:
                continue
            hit = by_lower.get(x.lower())
            if hit is None:
                hint = closest(x.upper(), ids, cutoff=0.75)
                raise UsageError(f"no proof {x} in {f.slug}" + (f" — did you mean {hint}?" if hint else
                                                                  f" (it has: {', '.join(ids)})"))
            only.append(hit)
        if not only:
            raise UsageError("--only needs proof ids, e.g. --only P-1,P-2")
    if env is not None:
        known = list(dict.fromkeys([*configured_envs(cfg), *(n for p in f.proofs for n, _ in proof_envs(p, cfg))]))
        if env not in known:
            hint = closest(env, known)
            raise UsageError(f"no env '{env}'" + (f" — did you mean '{hint}'?" if hint else "")
                             + f" (proofs run in: {', '.join(known) or 'none'}; envs live in .egd/config.toml)")
    return only, env


# ----------------------------------------------------------------- reports

def _show(args) -> bool:
    """Print what was written: on a terminal by default, always with --print, never with --quiet."""
    return args.print or (not args.quiet and sys.stdout.isatty())


def cmd_board(args):
    root, cfg, team = ctx(args)
    md_path, html_path = report.board_paths(root)
    text = report.board_markdown(root, cfg)
    report.write(md_path, text)
    if _show(args):
        out(text.rstrip())
        out()
    out(f"wrote {md_path.relative_to(root)}")
    if args.html:
        from .dashboard import portfolio, render
        report.write(html_path, render(portfolio(root, evidence=False)))
        out(f"wrote {html_path.relative_to(root)}")


def cmd_report(args):
    root, cfg, team = ctx(args)
    f = resolve_feature(root, args.feature)
    text = report.client_report(f, cfg, args.days, _lang(args, cfg))
    path = report.write(report.reports_dir(f) / f"status-{dt.date.today().isoformat()}.md", text)
    if args.print:  # the report alone, ready to pipe or paste
        out(text)
        return
    if _show(args):
        out(text.rstrip())
        out()
    out(f"wrote {path.relative_to(root)}")


def cmd_metrics(args):
    root, cfg, team = ctx(args)
    for f in _targets(root, args):
        if args.json:
            out(json.dumps({"feature": f.slug, **report.metrics(f, cfg)}, indent=2))
        else:
            out(report.metrics_text(f, cfg))


def cmd_verify(args):
    from . import verify
    root, cfg, team = ctx(args)
    f = resolve_feature(root, args.feature)
    if args.run and f.proofs:
        team.require(signer(args, root, team), "work")
    if args.gate and args.gate not in f.gates:
        raise UsageError(f"'{args.gate}' is not a gate for tier {f.tier} ({', '.join(f.gates)})")
    result = verify.verify(f, cfg, run=args.run, by=args.by, upto=args.gate)
    out(json.dumps(result, indent=2, ensure_ascii=False) if args.json else verify.render(result))
    return 0 if result["ready"] or result["target"] is None else 1


def cmd_pr(args):
    root, cfg, team = ctx(args)
    out(report.pr_body(resolve_feature(root, args.feature), cfg))


def cmd_release_note(args):
    root, cfg, team = ctx(args)
    f = resolve_feature(root, args.feature)
    path = report.write(report.reports_dir(f) / "release-note.md",
                        report.release_note(f, cfg, _lang(args, cfg)))
    out(path.read_text(encoding="utf-8") if args.print else f"wrote {path.relative_to(root)}")


def cmd_sync(args):
    from . import github
    root, cfg, team = ctx(args)
    if not args.dry_run:
        signer(args, root, team)
    targets = [f for f in _targets(root, args) if "slice" in replay(f).gates or args.include_unplanned]
    log = github.sync(targets, cfg, team, args.by or "dry-run", dry=args.dry_run)
    for line in log:
        out(line)
    out(f"{plural(len(log), 'gh call')}{' (dry run)' if args.dry_run else ''}")


def _event_target(e: dict) -> str:
    """What an event is about: its gate, task, slice, CR, defect or proof."""
    return next((str(e[k]) for k in ("gate", "task", "slice", "cr", "defect", "proof") if e.get(k)), "")


def cmd_trail(args):
    root, cfg, team = ctx(args)
    f = resolve_feature(root, args.feature)
    events = read(f)[-args.last:] if args.last else read(f)
    for e in events:
        what = _event_target(e)
        extra = e.get("reason") or e.get("note") or e.get("status") or ""
        out(f"{e.get('at', ''):<26} {e.get('by', ''):<14} {e.get('type', ''):<16} {what} {extra}".rstrip())


def _lang(args, cfg) -> str:
    lang = args.lang or cfg.get("report", {}).get("lang", "en")
    if lang not in LANGS:
        raise UsageError(f"--lang must be one of {', '.join(LANGS)}")
    return lang


# ----------------------------------------------------------------- plan editing

ADD_FIELDS = planfile.FIELDS
ADD_KINDS = (*planfile.FIELDS, "proof")
REQUIRED = {"assumption": ("text",), "ac": ("given", "when", "then"), "decision": ("title",),
            "slice": ("title",), "task": ("slice", "title", "estimate_h"), "proof": ("kind", "title", "verifies")}
DEFAULTS = {"assumption": {"status": "open", "confidence": "medium", "blocking": False},
            "decision": {"status": "proposed"}}
PROOF_KINDS = {  # what `egd add proof` writes for each kind, besides kind, title, verifies, envs
    "test": ("tests", "run"),
    "cli": ("run", "expect_exit", "expect_output"),
    "http": ("method", "path", "expect_status"),
    "ui": ("path", "expect_visible", "viewports"),
}

# Every `egd add` option: (flag, field, argparse settings, {kind: what it means there}).
ADD_OPTIONS = (
    ("--text", "text", {"metavar": "TEXT"}, {"assumption": "what is assumed"}),
    ("--confidence", "confidence", {"choices": planfile.CHOICES["assumption", "confidence"]},
     {"assumption": "low | medium | high (default: medium)"}),
    ("--blocking", "blocking", {"action": "store_true", "default": None},
     {"assumption": "clarify cannot pass while it is open"}),
    ("--status", "status", {"metavar": "STATUS"},
     {"assumption": "open | confirmed | rejected", "decision": "proposed | accepted | superseded (default: proposed)"}),
    ("--resolution", "resolution", {"metavar": "TEXT"}, {"assumption": "who confirmed it, and how"}),
    ("--story", "story", {"metavar": "TEXT"}, {"ac": "the user story it belongs to"}),
    ("--given", "given", {"metavar": "TEXT"}, {"ac": "the starting situation"}),
    ("--when", "when", {"metavar": "TEXT"}, {"ac": "the action"}),
    ("--then", "then", {"metavar": "TEXT"}, {"ac": "the observable result"}),
    ("--levels", "levels", {"metavar": "LIST"}, {"ac": "comma-separated test levels — unit, integration, e2e, manual"}),
    ("--kind", "kind", {"metavar": "KIND"}, {"ac": "free-form category, e.g. functional or nfr",
                                             "proof": "test | cli | http | ui"}),
    ("--title", "title", {"metavar": "TEXT"}, {"decision": "short title", "slice": "short title",
                                               "task": "short title", "proof": "one line, shown in reports"}),
    ("--context", "context", {"metavar": "TEXT"}, {"decision": "the forces at play"}),
    ("--choice", "choice", {"metavar": "TEXT"}, {"decision": "what was chosen"}),
    ("--rejected", "rejected", {"metavar": "LIST"}, {"decision": "comma-separated alternatives not taken"}),
    ("--consequences", "consequences", {"metavar": "TEXT"}, {"decision": "what follows from it"}),
    ("--covers", "covers", {"metavar": "AC,…"}, {"slice": "comma-separated AC ids"}),
    ("--demo", "demo", {"metavar": "TEXT"}, {"slice": "how to show it working"}),
    ("--slice", "slice", {"metavar": "S-N"}, {"task": "the slice it belongs to (sets the id T-N.<next>)"}),
    ("--estimate", "estimate_h", {"type": float, "metavar": "HOURS"}, {"task": "estimate in hours"}),
    ("--depends-on", "depends_on", {"metavar": "T,…"}, {"task": "comma-separated task ids"}),
    ("--touches", "touches", {"metavar": "GLOBS"}, {"task": "comma-separated globs it may change"}),
    ("--tests", "tests", {"metavar": "PATHS"}, {"task": "comma-separated test paths run on submit",
                                                "proof": "test: comma-separated paths for [proof] test_command"}),
    ("--done", "done_when", {"action": "append", "metavar": "TEXT"}, {"task": "a done_when item (repeatable)"}),
    ("--agent", "agent", {"metavar": "WHO"}, {"task": "builder | tester | person (default: picked from what it touches)"}),
    ("--model", "model", {"metavar": "MODEL"}, {"task": "haiku | sonnet | opus | fable (default: picked from size and risk)"}),
    ("--verifies", "verifies", {"metavar": "AC,…"}, {"proof": "comma-separated AC ids it demonstrates"}),
    ("--run", "run", {"metavar": "CMD"}, {"proof": "cli: the command to run; test: replaces [proof] test_command"}),
    ("--expect-exit", "expect_exit", {"type": int, "metavar": "N"}, {"proof": "cli: the exit code expected (default: 0)"}),
    ("--expect-output", "expect_output", {"metavar": "TEXT"}, {"proof": "cli: text the output must contain"}),
    ("--method", "method", {"metavar": "VERB"}, {"proof": "http: GET, POST, … (default: GET)"}),
    ("--path", "path", {"metavar": "PATH"}, {"proof": "http: appended to the env's base_url, e.g. /api/orders; "
                                                      "ui: the page to open, e.g. /orders/latest"}),
    ("--expect-status", "expect_status", {"type": int, "metavar": "CODE"},
     {"proof": "http: the status expected (default: 200)"}),
    ("--expect-visible", "expect_visible", {"action": "append", "metavar": "SELECTOR"},
     {"proof": "ui: what must be on the page, e.g. 'text=300,000' (repeatable)"}),
    ("--viewports", "viewports", {"metavar": "LIST"}, {"proof": "ui: desktop, mobile (default: desktop)"}),
    ("--envs", "envs", {"metavar": "LIST"}, {"proof": "comma-separated env names (default: every enabled env; "
                                                      "test/cli: the repository)"}),
)
FLAG_OF = {field: flag for flag, field, _, _ in ADD_OPTIONS}
ADD_EXAMPLES = {
    "assumption": 'egd add assumption --text "Prices are in VND only" --blocking',
    "ac": 'egd add ac --given "a cart with 2 × A1" --when "the buyer checks out" '
          '--then "the order is pending, total 300,000" --levels integration',
    "decision": 'egd add decision --title "Totals are computed on the server" --choice "server" --status accepted',
    "slice": 'egd add slice --title "Checkout creates a pending order" --covers AC-1.1 --demo "check out, see 300,000"',
    "task": "egd add task --slice S-1 --title \"POST /orders\" --estimate 3 --touches 'src/orders/**' "
            '--tests tests/orders.test.ts --done "returns 201 with the total"',
    "proof": 'egd add proof --kind test --verifies AC-1.1 --title "Order total" --tests tests/orders.test.ts',
}
ADD_WHAT = {"assumption": "an assumption", "ac": "an acceptance criterion (Given/When/Then)",
            "decision": "a design decision", "slice": "a slice — something a person can be shown",
            "task": "a task of a slice", "proof": "a proof to .egd/features/<feature>/proof.toml"}


def _add_help(kind: str) -> str:
    """`egd add <kind> -h`: that kind's options only, with an example."""
    fields = ("kind", "title", "verifies", "envs", *[x for k in PROOF_KINDS.values() for x in k]) \
        if kind == "proof" else ADD_FIELDS[kind]
    rows = [(flag + ("" if kw.get("action") == "store_true" else f" {kw.get('metavar', 'VALUE')}"),
             ("* " if field in REQUIRED[kind] else "  ") + kinds[kind])
            for flag, field, kw, kinds in ADD_OPTIONS if field in fields and kind in kinds]
    if kind == "ac":
        rows.append(("--group N", "  story number: the id becomes AC-N.<next> (default: 1 for the first AC, "
                                  "else the group of the last one)"))
    rows += [("--id ID", "  explicit id (default: the next free one)"), ("-f, --feature F", "  " + FEATURE_HELP),
             ("--by NAME", "  " + BY_HELP)]
    width = max(len(r[0]) for r in rows) + 2
    lines = [f"usage: egd add {kind} [options]", "", f"Add {ADD_WHAT[kind]}.", "",
             f"options for {kind} (* required):"] + [f"  {a:<{width}}{b}" for a, b in rows]
    if kind == "proof":
        lines += ["", "cli also needs --run; http and ui need --path. Several steps, captured values, clicks",
                  "and JSON assertions are written in proof.toml — the egd-proof skill lists every key."]
    return "\n".join(lines + ["", "example:", f"  {ADD_EXAMPLES[kind]}"])


class _AddHelp(argparse.Action):
    """-h for `egd add`: after a kind, only that kind's options."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        kind = getattr(namespace, "entry", None)
        out(_add_help(kind) if kind in ADD_KINDS else parser.format_help().rstrip())
        parser.exit()


def _refs(f, what: str, raw, known: list[str], none_yet: str = "none yet") -> list[str]:
    """Comma-separated ids, each one checked against `known` — a typo is refused with its likely fix."""
    ids = [str(x).strip() for x in (raw if isinstance(raw, list) else str(raw or "").split(",")) if str(x).strip()]
    for x in ids:
        if x not in known:
            hint = closest(x, known, cutoff=0.75)
            raise Refused(f"unknown {what} {x} in {f.slug}"
                          + (f" — did you mean {hint}?" if hint else f" (it has: {', '.join(known) or none_yet})"))
    return ids


def _given(args, fields) -> list[str]:
    """The `egd add` options actually passed among `fields`."""
    return [k for k in fields if getattr(args, k, None) not in (None, [])]


def _stray(args, kind: str, allowed) -> None:
    extra = [FLAG_OF[k] for k in _given(args, FLAG_OF) if k not in allowed]
    if extra:
        raise UsageError(f"egd add {kind} does not take {', '.join(extra)} — `egd add {kind} -h` lists its options")


def _ac_group(f) -> str:
    """The story group for a new AC: 1 for the first one, else the group of the last one written."""
    for a in reversed(f.acs):
        m = re.match(r"^AC-(\d+)\.", str(a["id"]))
        if m:
            return m.group(1)
    return "1"


def _warn_scope(root: Path, f, ac_ids: list[str]) -> None:
    """After an AC changed: once `clarify` passed, say how to record it instead of failing later."""
    st = replay(f)
    if "clarify" not in st.gates:
        return
    pending = {cr.get("scope") for cr in st.crs.values() if cr.get("status") == "pending"}
    if load_feature(root, f.path).scope_hash() in st.approved_scopes() | pending:
        return
    affects = f" --affects {','.join(ac_ids)}" if ac_ids else ""
    out(f'warning: scope changed since clarify — record it: egd cr open --title "…" --reason "…"{affects}')


def cmd_add(args):
    root, cfg, team = ctx(args)
    team.require(signer(args, root, team), "work")
    f = resolve_feature(root, args.feature)
    kind = args.entry
    if kind == "proof":
        return _add_proof(args, root, f, cfg)
    _stray(args, kind, ADD_FIELDS[kind])
    fields = {k: getattr(args, k) for k in _given(args, ADD_FIELDS[kind])}
    missing = [k for k in REQUIRED[kind] if k not in fields]
    if missing:
        raise UsageError(f"egd add {kind} needs " + ", ".join(FLAG_OF[m] for m in missing)
                         + f" — `egd add {kind} -h` shows an example")
    for k, v in DEFAULTS.get(kind, {}).items():
        fields.setdefault(k, v)
    if kind == "task" and not fields.get("done_when"):
        raise UsageError('a task needs at least one --done "<observable result>" (repeatable)')
    if kind == "slice" and "covers" in fields:
        _ac_ids(f, fields["covers"])
    if kind == "task":
        _refs(f, "slice", fields["slice"], [s["id"] for s in f.slices], "none yet — `egd add slice`")
        if "depends_on" in fields:
            _refs(f, "task", fields["depends_on"], [t["id"] for t in f.tasks])
    group = args.group or (fields.get("slice") if kind == "task" else _ac_group(f) if kind == "ac" else None)
    item_id = args.id or planfile.next_id(f, kind, group)
    planfile.add(f, kind, {"id": item_id, **fields})
    out(f"added {item_id} to {f.slug}/plan.toml")
    if kind == "task":
        from . import profiles
        ask = [i for i in profiles.done_when_for(profiles.active(root, cfg), fields.get("touches") or [])
               if i not in (fields.get("done_when") or [])]
        if ask:
            out("note: the stack profile also asks of a task touching these files:")
            out("".join(f"  - {i}\n" for i in ask).rstrip())
            both = json.dumps([*fields["done_when"], *ask], ensure_ascii=False).replace("'", "'\\''")
            out(f"  add what applies: egd set {item_id} 'done_when={both}'")
    if kind == "ac":
        _warn_scope(root, f, [item_id])


def _add_proof(args, root: Path, f, cfg: dict):
    kind = args.kind
    if kind not in PROOF_KINDS:
        hint = closest(kind, PROOF_KINDS) if kind else None
        raise UsageError("egd add proof needs --kind test, cli, http or ui"
                         + (f" — did you mean '{hint}'?" if hint else ""))
    _stray(args, f"proof --kind {kind}", ("kind", "title", "verifies", "envs", *PROOF_KINDS[kind]))
    missing = [FLAG_OF[k] for k in ("title", "verifies") if not getattr(args, k)]
    missing += {"cli": ["--run"] if not args.run else [], "http": ["--path"] if not args.path else [],
                "ui": ["--path"] if not args.path else []}.get(kind, [])
    if missing:
        raise UsageError(f"egd add proof --kind {kind} needs " + ", ".join(missing)
                         + " — `egd add proof -h` shows an example")
    proof = {"id": args.id or planfile.next_id(f, "proof"), "kind": kind, "title": args.title,
             "verifies": _ac_ids(f, args.verifies)}
    if args.envs:
        known = [*configured_envs(cfg), "repo"]
        proof["envs"] = []
        for env in (x.strip() for x in args.envs.split(",") if x.strip()):
            if env not in known:
                hint = closest(env, known)
                raise UsageError(f"no env '{env}'" + (f" — did you mean '{hint}'?" if hint else "")
                                 + f" (configured: {', '.join(known)}; add [env.{env}] to .egd/config.toml)")
            proof["envs"].append(env)
    steps, notes = [], []
    if kind == "test":
        proof["tests"] = planfile.coerce("tests", args.tests or [])
        proof["command"] = args.run
        if not args.run and cfg.get("proof", {}).get("test_command") == "":
            raise UsageError('this repository says it has no test runner (test_command = "") — pass --run '
                             '"<command>", or use --kind cli')
        if not args.run and not cfg.get("proof", {}).get("test_command"):
            notes.append('no test command yet — set test_command = "…{tests}" under [proof] in .egd/config.toml, '
                         'or pass --run "<command>"')
    elif kind == "cli":
        expect = {"exit": 0 if args.expect_exit is None else args.expect_exit}
        if args.expect_output:
            expect["contains"] = args.expect_output
        steps.append({"name": args.title, "run": args.run, "expect": expect})
    elif kind == "ui":
        from .proof.ui import playwright_ready, viewports
        known = viewports(cfg)
        proof["viewports"] = [v.strip() for v in (args.viewports or "desktop").split(",") if v.strip()]
        for v in proof["viewports"]:
            if v not in known:
                hint = closest(v, list(known))
                raise UsageError(f"no viewport '{v}'" + (f" — did you mean '{hint}'?" if hint else "")
                                 + f" (known: {', '.join(known)}; add more under [ui.viewports])")
        step = {"name": args.title, "goto": args.path if args.path.startswith(("/", "http://", "https://"))
                else "/" + args.path}
        if args.expect_visible:
            step["expect"] = {"visible": args.expect_visible}
        else:
            notes.append("no --expect-visible: the run screenshots the page but asserts nothing about it")
        steps.append(step)
        if not configured_envs(cfg):
            notes.append('ui proofs open an env — add [env.local] base_url = "http://localhost:3000" '
                         "to .egd/config.toml")
        if not playwright_ready(cfg, root):
            notes.append("screenshots need Playwright — run `egd proof setup` once on this machine "
                         "(downloads Chromium, ~150 MB)")
        notes.append("clicks, form fills and more checks go in proof.toml — the egd-proof skill lists every key")
    else:
        path = args.path if args.path.startswith(("/", "http://", "https://")) else "/" + args.path
        steps.append({"name": args.title, "method": (args.method or "GET").upper(),
                      "url" if "://" in path else "path": path,
                      "expect": {"status": 200 if args.expect_status is None else args.expect_status}})
        if not configured_envs(cfg):
            notes.append('http proofs call an env — add [env.local] base_url = "http://localhost:3000" '
                         "to .egd/config.toml")
        notes.append("several requests, captured values and JSON assertions go in proof.toml — "
                     "the egd-proof skill lists every key")
    planfile.add_proof(f, proof, steps)
    out(f"added {proof['id']} to {f.slug}/proof.toml ({kind}, verifies {', '.join(proof['verifies'])})")
    for n in notes:
        out(f"note: {n}")
    out(f"run it: egd proof run --only {proof['id']}")


def cmd_set(args):
    root, cfg, team = ctx(args)
    team.require(signer(args, root, team), "work")
    f = feature_owning(root, None, args.id, args.feature)
    fields = {}
    for pair in args.pairs:
        if "=" not in pair:
            raise UsageError(f"expected field=value, got '{pair}' — e.g. egd set {args.id} status=confirmed")
        k, _, v = pair.partition("=")
        fields[k.strip()] = v
    kind = planfile.set_fields(f, args.id, fields)
    out(f"updated {args.id} ({kind}): " + ", ".join(fields))
    if kind == "ac":
        _warn_scope(root, f, [args.id])


def _holder(root: Path, item_id: str, ref: str | None):
    """(feature, kind) of the plan entry or proof with this id."""
    features = [resolve_feature(root, ref)] if ref else all_features(root)

    def kind_in(f):
        kind = next((k for k in planfile.FIELDS if any(it["id"] == item_id for it in f._items(k))), None)
        return kind or ("proof" if any(p["id"] == item_id for p in f.proofs) else None)

    owners = [(f, k) for f in features if (k := kind_in(f))]
    if len(owners) == 1:
        return owners[0]
    if owners:
        raise UsageError(f"{item_id} exists in several features — pass -f <feature>: "
                         + ", ".join(f.slug for f, _ in owners))
    known = [it["id"] for f in features for k in planfile.FIELDS for it in f._items(k)]
    known += [p["id"] for f in features for p in f.proofs]
    hint = closest(item_id, known, cutoff=0.75)
    where = features[0].slug if ref else "any plan.toml or proof.toml"
    raise UsageError(f"no entry {item_id} in {where}" + (f" — did you mean {hint}?" if hint else ""))


def cmd_rm(args):
    """Remove a plan entry or a proof. Free while the plan is being written; after `slice` the
    agreed work changes only through a change request the client approves."""
    root, cfg, team = ctx(args)
    team.require(signer(args, root, team), "work")
    f, kind = _holder(root, args.id, args.feature)
    st = replay(f)
    if "slice" in st.gates:
        if kind in ("assumption", "decision"):
            status = "rejected" if kind == "assumption" else "superseded"
            raise Refused(f"{args.id} is part of the agreed plan — keep the record and change its status: "
                          f"egd set {args.id} status={status}")
        if kind == "task" and st.task(args.id).status != "todo":
            raise Refused(f"{args.id} is {st.task(args.id).status} — only a task nobody has started can be removed")
        if not args.cr:
            raise Refused(f"after slice this needs a change request: egd cr open --title \"drop {args.id}\" "
                          f"--reason \"…\" — remove it with `egd rm {args.id} --cr` first, then open the CR; "
                          "`build` stays closed until the client approves it")
    users = []
    if kind == "ac":
        users += [f"{s['id']} covers it" for s in f.slices if args.id in s.get("covers", [])]
        users += [f"{p['id']} verifies it" for p in f.proofs if args.id in p.get("verifies", [])]
    elif kind == "slice":
        users += [f"{t['id']} belongs to it" for t in f.tasks_in(args.id)]
    elif kind == "task":
        users += [f"{t['id']} depends on it" for t in f.tasks if args.id in t.get("depends_on", [])]
    if users:
        raise Refused(f"{args.id} is still referenced — change these first (egd set … or egd rm …)", users)
    filename = "proof.toml" if kind == "proof" else "plan.toml"
    planfile.remove(f, args.id, filename)
    out(f"removed {args.id} ({kind}) from {f.slug}/{filename}")
    if "slice" in st.gates:
        out(f'next: egd cr open --title "drop {args.id}" --reason "…" — the client approves with egd cr approve')
    elif kind == "ac":
        _warn_scope(root, f, [])


# ----------------------------------------------------------------- team views

def cmd_standup(args):
    root, cfg, team = ctx(args)
    out(report.standup(root, cfg, args.hours))


def _listen(args, make):
    """`make(token)` → a server, with a token whenever it listens beyond this machine.

    Returns (server, url, local, token); the url carries the token so it opens straight away."""
    from .dashboard import LOCAL_HOSTS
    if getattr(args, "lan", False):
        args.host, args.no_token = "0.0.0.0", False
    local = args.host in LOCAL_HOSTS
    token = args.token or os.environ.get("EGD_TOKEN") or None
    if token is None and not local and not args.no_token:
        import secrets
        token = secrets.token_urlsafe(16)
    server = make(token)
    host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url = f"http://{host}:{server.server_address[1]}/" + (f"?t={token}" if token else "")
    return server, url, local, token


def _for_log(text: str) -> str:
    """A link as printed: whole on a terminal; in a log file (the background console's) without
    its token — `egd console --service status` shows the link to whoever may run it."""
    if sys.stdout.isatty():
        return text
    import re
    return re.sub(r"\?t=[\w-]+", "?t=…", text)


def _lan_urls(port: int, token: str | None) -> list[str]:
    """The addresses other machines on your network can open (no packet is sent to find them)."""
    import socket
    ips = []
    for probe in ("10.255.255.255", "192.168.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as so:
                so.connect((probe, 1))
                ip = so.getsockname()[0]
        except OSError:
            continue
        if ip and not ip.startswith("127.") and ip not in ips:
            ips.append(ip)
    return [f"http://{ip}:{port}/" + (f"?t={token}" if token else "") for ip in ips]


def _say_lan(args, server, token, who: str | None = None) -> None:
    if not getattr(args, "lan", False):
        return
    urls = _lan_urls(server.server_address[1], token)
    out(_for_log("on your network: " + (" · ".join(urls) if urls else "(no network address found)")))
    if who is not None:
        out(f"visitors act as {who or '(no user)'} — every action is signed with that name"
            if getattr(args, "lan_write", False) else
            "visitors from other machines only look; actions stay on this machine (--lan-write to allow)")
    out("share the link only with your team; anyone holding it can read every repository listed here")


def _serve(server) -> None:
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        out("stopped")
    finally:
        server.server_close()


def cmd_serve(args):
    from .dashboard import make_server
    root, cfg, team = ctx(args)
    server, url, local, token = _listen(args, lambda token: make_server(root, cfg, args.host, args.port, token))
    out(f"EGD dashboard (read-only) → {url}")
    _say_lan(args, server, token)
    if args.lan:
        pass  # _say_lan said who may open it
    elif not local and token:
        out("listening beyond this machine — share the link with the token only with your team")
    elif not local:
        out("no token: anyone who can reach this port can read the dashboard")
    out("Ctrl+C to stop")
    _serve(server)


def cmd_site(args):
    from .dashboard import export_site
    root, cfg, team = ctx(args)
    only = [resolve_feature(root, x).slug for x in args.feature] if args.feature else None
    index, copied = export_site(root, cfg, Path(args.out).resolve(), only, not args.no_evidence)
    out(f"wrote {index} ({plural(copied, 'evidence file')})")
    out("This folder contains your plan, trail and evidence. On a public host (e.g. GitHub Pages of a "
        "public repo) anyone can read it — use access control for client work, or --no-evidence.")


def cmd_update(args):
    from .selfmanage import update
    return update(out)


def cmd_uninstall(args):
    from .selfmanage import uninstall
    return uninstall(args.yes, args.keep_data, args.dry_run, out)


def cmd_import(args):
    from .sources.aidlc_import import import_repo, refresh_repo
    root = Path(args.root or ".").resolve()
    if not (root / ".ai" / "features").is_dir():
        raise UsageError(f"{root} has no .ai/features/ — run this in the ai-dlc repository's root, "
                         "or pass --root DIR")
    if args.refresh:
        try:
            rows = refresh_repo(root, args.feature or None)
        except OSError as exc:
            raise UsageError(f"refresh stopped: {exc}") from None
        for r in rows:
            if "skipped" in r:
                out(f"  skip  {r['feature']} ({r['skipped']})")
            else:
                out(f"  ✓ {r['feature']:<48} {r['acs']:>2} ACs · {r['changed']:>2} reworded · "
                    f"{plural(r['fingerprints'], 'imported fingerprint')} updated")
        return
    signer(args, root, Team(root, {}))
    try:
        report_ = import_repo(root, args.by, args.feature or None)
    except OSError as exc:
        raise UsageError(f"import stopped: {exc}") from None
    for c in report_["created"]:
        out(f"  wrote {c}")
    for n in report_["notes"]:
        out(f"  {n}")
    done = [r for r in report_["features"] if "skipped" not in r]
    for r in report_["features"]:
        if "skipped" in r:
            out(f"  skip  {r['feature']} ({r['skipped']})")
        else:
            out(f"  ✓ {r['feature']:<48} {r['gate']:<5} {r['tasks']:>3} tasks · {r['acs']:>2} ACs · "
                f"{r['events']:>3} events")
    out(f"imported {plural(len(done), 'feature')} into .egd/ — .ai/ was read, not changed. Nothing is committed.")
    out("next: `egd lint`, review .egd/, then commit it.")


def cmd_console(args):
    from . import console
    reg = console.load_registry()
    raw = load_toml(console.config_path())
    changed = False
    if args.user:
        raw["user"], changed = args.user, True
    if args.scan:
        roots = raw.get("roots", [])
        for d in args.scan:
            p = str(Path(d).expanduser().resolve())
            if p not in roots:
                roots.append(p)
        raw["roots"], changed = roots, True
    repos = [r.get("path") for r in raw.get("repo", []) if isinstance(r, dict)]

    def save(**extra) -> Path:  # user and roots as this command left them, the rest as given
        return console.save_registry({"user": raw.get("user", ""), "roots": raw.get("roots", []), "repos": repos,
                                      **extra})
    if args.action in ("add", "remove"):
        if not args.path:
            raise UsageError(f"egd console {args.action} <path>")
        p = Path(args.path).expanduser().resolve()
        if args.action == "add":
            if not console.is_repo(p):
                raise UsageError(f"{p} has no .egd/ (or .ai/features/) — run `egd setup` there first")
            if str(p) not in repos:
                repos.append(str(p))
        else:
            ids = console.repo_ids([Path(r) for r in repos])
            target = ids.get(args.path)
            repos = [r for r in repos if Path(r).resolve() not in (p, target)]
        changed = True
    if changed:
        out(f"updated {save()}")
        reg = console.load_registry()
    if args.action == "signer":
        if not args.path or args.name is None:
            raise UsageError('egd console signer <id|path> --name "Minh Le"   (empty name: use the default)')
        ids = console.repo_ids(console.repo_paths(reg))
        target = ids.get(args.path) or Path(args.path).expanduser().resolve()
        if target not in ids.values():
            raise UsageError(f"no repository '{args.path}' in the console — `egd console list` shows them")
        signers = dict(reg.get("signers", {}))
        name = " ".join(args.name.split())[:60]
        if name:
            signers[str(target)] = name
        else:
            signers.pop(str(target), None)
        save(signers=signers)
        out(f"{target.name}: actions are signed as {name or (reg['user'] or '(default — not set)') + ' (default)'}")
        return 0
    if args.service:
        return _console_service(args, reg)
    if args.action in ("add", "remove", "list") or (changed and args.action is None and not args.serve):
        found = console.repo_ids(console.repo_paths(reg))
        out(f"user: {reg['user'] or '(not set — egd console --user <name>)'}")
        signers = reg.get("signers", {})
        for rid, p in found.items():
            own = signers.get(str(p))
            out(f"  {rid:<24} {p}" + (f"   signs as {own}" if own else ""))
        if not found:
            out("  no repositories yet — add them in the console (Repositories → Add repository…),"
                " or: egd console add <path> · egd console --scan <folder>")
        if args.action is None:  # only settings changed: say how to open it
            from . import service
            out(f"open it: {service.link() or 'egd console   → http://127.0.0.1:8780'}")
        return 0
    if args.action != "serve" and args.host in ("127.0.0.1", "localhost") and not args.lan:  # not the service itself
        from . import service
        cur = service.current()
        if cur and cur.get("port") == args.port and service.running():  # it is already on: hand over its link
            out(f"EGD Console runs in the background → {service.link()}")
            out("open that link once — this browser remembers it · `egd console --service off` stops it")
            return 0
    try:
        server, url, _, token = _listen(args, lambda token: console.make_server(
            # anyone not on this machine only looks — under --lan, or any other non-local --host
            args.host, args.port, token, args.readonly, remote_readonly=not args.lan_write))
    except OSError as exc:
        if exc.errno not in (48, 98):  # address in use (macOS, Linux)
            raise
        from . import service
        hint = ("the console already runs in the background (egd console --service status)"
                if service.running() else "another program uses it")
        raise UsageError(f"port {args.port} is taken — {hint}; open http://127.0.0.1:{args.port}/ "
                         f"or pick another with --port") from None
    out(_for_log(f"EGD Console → {url}"))
    out(f"signed as {reg['user'] or '(no user — actions refused until `egd console --user <name>`)'}"
        f"{' · read-only' if args.readonly else ''} · {len(console.repo_paths(reg))} repositories")
    _say_lan(args, server, token, who=reg["user"])
    _serve(server)


def _console_service(args, reg) -> int:
    """`egd console --service [on|off|status]` — the console in the background, no terminal needed."""
    from . import service
    if args.service == "off":
        out("background console removed" if service.uninstall() else "no background console was set up")
        return 0
    if args.service == "status":
        where = service.installed()
        if not where:
            out("no background console — `egd console --service` sets one up")
        else:
            out(f"background console: {'running' if service.running() else 'stopped'} ({where})")
            link, cur = service.link(), service.current()
            if link:
                out(f"open it: {link}")
            if cur and cur.get("lan") and cur.get("port"):
                for u in _lan_urls(cur["port"], cur.get("token")):
                    out(f"on your network: {u}")
            out(f"log: {service._log()}")
        return 0
    serve = service.serve_args(args.port, args.lan, args.lan_write, args.readonly, args.token)
    try:
        service.install(serve)
    except RuntimeError as exc:
        raise UsageError(str(exc)) from None
    import time
    import urllib.error
    import urllib.request
    url = f"http://127.0.0.1:{args.port}/"
    for _ in range(40):  # wait until it answers, so the link below works when clicked
        try:
            urllib.request.urlopen(url + "api/boot", timeout=0.5)
            break
        except urllib.error.HTTPError:
            break  # it answered — a token-protected console says 403 to a request without one
        except Exception:
            time.sleep(0.25)
    else:
        out(f"started, but it does not answer yet — see {service._log()}")
    token = serve[serve.index("--token") + 1] if "--token" in serve else None
    out(f"EGD Console runs in the background → {url}" + (f"?t={token}" if token else ""))
    out(f"signed as {reg['user'] or '(no user — egd console --user <name>)'} · starts when you log in, "
        "keeps running after the terminal closes")
    if args.lan:
        for u in _lan_urls(args.port, token):
            out(f"on your network: {u}")
        out("visitors only look; actions stay on this machine" if not args.lan_write
            else f"visitors act as {reg['user']} — every action is signed with that name")
    out("stop it: egd console --service off")
    return 0


# ----------------------------------------------------------------- parser

BY_HELP = "who signs (default: you — $EGD_USER, else git user.name)"
FEATURE_HELP = "feature slug or part of it"

GROUPS = (
    ("Start", ("new", "list", "status")),
    ("Plan & gates", ("add", "set", "rm", "remove", "graph", "check", "pass", "close")),
    ("Build", ("ready", "start", "submit", "accept", "reject", "solo", "block", "unblock", "reopen")),
    ("Evidence", ("proof", "verify", "pr", "release-note", "lint")),
    ("Team & client", ("uat", "cr", "bug", "standup", "report", "metrics", "sync")),
    ("Views", ("board", "trail", "serve", "site", "console")),
    ("Setup & import", ("setup", "profile", "import", "update", "uninstall")),
)


class _Parser(argparse.ArgumentParser):
    """argparse that answers a mistyped command or choice with "did you mean …?"."""

    def _check_value(self, action, value):
        if action.choices is None or value in action.choices:
            return super()._check_value(action, value)
        what = "command" if isinstance(action, argparse._SubParsersAction) else (
            action.metavar or (action.option_strings[-1] if action.option_strings else action.dest))
        # a command suggestion must be a near miss: `remove` → `reopen` would mislead
        hint = closest(value, action.choices, cutoff=0.7 if what == "command" else 0.6)
        if hint:
            msg = f"unknown {what} '{value}' — did you mean '{hint}'?"
        elif what == "command":
            msg = f"unknown command '{value}' — `egd -h` lists them all"
        else:
            msg = f"unknown {what} '{value}' (one of: {', '.join(map(str, action.choices))})"
        raise argparse.ArgumentError(None, msg)


def _grouped(helps: dict[str, str]) -> str:
    """The command list for `egd -h`, by what you are doing."""
    width = max(map(len, helps)) + 2
    lines, seen = [], set()
    for title, names in GROUPS + (("Other", tuple(n for n in helps if not any(n in g for _, g in GROUPS))),):
        names = [n for n in names if n in helps and n not in seen]
        if names:
            lines += ["", f"{title}:"] + [f"  {n:<{width}}{helps[n]}" for n in names]
            seen.update(names)
    return "\n".join(lines[1:] + [
        "", "`egd <command> -h` shows its options. --by defaults to you: $EGD_USER, else git user.name.",
        "-f/--feature names the feature on every command that works on one; "
        "`egd status checkout` works too.",
        "Bare `egd` shows where things stand and what to run next."])


def _feature(sp, *, positional=False, repeat=False) -> None:
    """-f/--feature, and for commands that always took it positionally, `[feature]` as well."""
    if positional:
        sp.add_argument("feature_arg", nargs="?", metavar="feature", help=FEATURE_HELP + " — same as -f")
    if repeat:
        sp.add_argument("-f", "--feature", action="append", help="only these features (repeatable; part of "
                                                                  "the name works)")
    else:
        sp.add_argument("-f", "--feature", help=FEATURE_HELP)


def _one_feature(args) -> None:
    """Fold the positional `[feature]` into args.feature, so commands read one place."""
    given = getattr(args, "feature_arg", None)
    if given is None:
        return
    if args.feature and args.feature != given:
        raise UsageError(f"two features named: '{given}' and -f '{args.feature}' — give one")
    args.feature = given


def _home_feature(args) -> None:
    """`egd -f login` is `egd status -f login`; `egd -f login graph` is `egd graph -f login`."""
    given = args.home_feature
    if given is None:
        return
    if not args.cmd:
        args.cmd, args.fn, args.feature, args.feature_arg = "status", cmd_status, given, None
        return
    if not hasattr(args, "feature"):
        raise UsageError(f"egd {args.cmd} does not work on one feature — drop -f")
    if isinstance(args.feature, list):
        args.feature = [*args.feature, given]
    elif args.feature and args.feature != given:
        raise UsageError(f"two features named: -f '{given}' and -f '{args.feature}' — give one")
    else:
        args.feature = given


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="egd", usage="egd [--root DIR] <command> [options]",
                description="Evidence-Gated Delivery — features move through gates the CLI checks.",
                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", metavar="DIR", help="project root (default: nearest directory holding .egd/)")
    p.add_argument("-f", "--feature", dest="home_feature", metavar="FEATURE",
                   help="`egd -f <feature>` alone is `egd status -f <feature>`")
    p.add_argument("--version", action="version", version=f"egd {__version__} (rules v{RULES})")
    sub = p.add_subparsers(dest="cmd", metavar="<command>", help=argparse.SUPPRESS)
    helps: dict[str, str] = {}

    def add(name, fn, help_, *, feature=False, **kw):
        helps[name] = help_
        sp = sub.add_parser(name, help=help_, description=help_, **kw)
        sp.set_defaults(fn=fn)
        if feature:
            _feature(sp, positional=True)
        return sp

    def by(sp, help_=BY_HELP):
        sp.add_argument("--by", metavar="NAME", help=help_)

    sp = add("setup", cmd_setup, "create .egd/ in this repository")
    sp.add_argument("--claude", action="store_true",
                    help="also enable the EGD Claude Code plugin for this repo (.claude/settings.json)")
    sp.add_argument("--local", action="store_true",
                    help="keep .egd/ on this machine: git ignores it through .git/info/exclude, nothing is committed")
    sp = add("profile", cmd_profile, "stack profiles: test command, conventions, review checklist, done_when")
    sp.add_argument("--list", action="store_true", help="every profile there is, and where it comes from")
    sp.add_argument("--use", metavar="NAMES", help="the profiles this repository uses, comma-separated (empty: none)")
    sp.add_argument("--new", metavar="NAME", help="write a profile to edit: .egd/profiles/NAME.toml")
    sp.add_argument("--from", dest="base", metavar="PROFILE", help="--new: start from this profile")
    sp.add_argument("--mine", action="store_true", help="--new: in ~/.egd/profiles, for all your repositories")
    sp = add("new", cmd_new, "start a feature")
    sp.add_argument("name", help="short name, e.g. checkout (becomes the folder YYYY-MM-DD-<name>)")
    sp.add_argument("--tier", default="standard", choices=TIERS,
                    help="lite skips design and accept; full also needs an accepted decision (default: standard)")
    sp.add_argument("--title", help="human title for reports (default: from the name)")
    by(sp)
    add("list", cmd_list, "features with health and next gate")
    add("status", cmd_status, "where a feature stands and what blocks it", feature=True)
    sp = add("check", cmd_check, "why a gate is closed (changes nothing)")
    sp.add_argument("targets", nargs="*", metavar="gate|feature",
                    help="a gate, a feature, or both (default: the next gate of the only feature)")
    _feature(sp)
    sp = add("pass", cmd_pass, "pass a gate — refused unless its checks hold")
    sp.add_argument("gate", choices=GATES)
    _feature(sp, positional=True)
    by(sp)
    sp = add("close", cmd_close, "close a feature that will not ship: dropped, a research spike, or superseded",
             feature=True)
    sp.add_argument("--as", dest="outcome", default="dropped", choices=tuple(gates.OUTCOMES),
                    help="dropped (default) · research: a spike, the reason says what it found · superseded")
    sp.add_argument("--reason", help="why it stops — and, for research, what it found or where that is written")
    sp.add_argument("--undo", action="store_true", help="reopen a closed feature (with --reason)")
    by(sp)
    add("graph", cmd_graph, "validate the plan; show waves and critical path", feature=True)
    add("lint", cmd_lint, "validate every feature (for CI)")
    add("ready", cmd_ready, "tasks whose dependencies are done", feature=True)

    def task_parser(name, fn, help_, *, reason=None, finish=False):
        sp = add(name, fn, help_)
        sp.add_argument("task", help="task id, e.g. T-1.2")
        _feature(sp)
        by(sp)
        if reason:
            sp.add_argument("--reason", required=True, help=reason)
        if finish:
            sp.add_argument("--confirm", action="store_true", help="confirm every done_when item holds")
            sp.add_argument("--spent", type=float, metavar="HOURS",
                            help="actual hours spent (feeds estimate accuracy)")
            sp.add_argument("--allow-drift", action="store_true",
                            help="accept changes outside the task's touches (needs --drift-reason; recorded)")
            sp.add_argument("--drift-reason", metavar="WHY", help="why the change goes beyond the task's touches")
        return sp

    sp = task_parser("start", cmd_start, "claim a ready task")
    sp.add_argument("--model", choices=planfile.CHOICES["task", "model"],
                    help="the model doing the work, recorded on the trail (an agent's run)")
    sp = task_parser("submit", cmd_submit, "send a task to review", finish=True)
    sp.add_argument("--reason", dest="drift_reason", help=argparse.SUPPRESS)  # before --drift-reason existed
    task_parser("accept", cmd_accept, "accept a reviewed task (not your own)")
    task_parser("reject", cmd_reject, "send a task back with a reason", reason="what has to change (recorded)")
    task_parser("solo", cmd_solo, "finish without review — recorded as a bypass",
                reason="why review is skipped (recorded)", finish=True)
    task_parser("block", cmd_block, "mark a task as waiting on something", reason="what it is waiting on")
    task_parser("unblock", cmd_unblock, "resume a blocked task")
    task_parser("reopen", cmd_reopen, "reopen a done task", reason="what is wrong with it (recorded)")

    sp = add("uat", cmd_uat, "record the client's verdict on a slice")
    sp.add_argument("slice", help="slice id, e.g. S-1")
    _feature(sp)
    by(sp)
    sp.add_argument("--pass", dest="passed", action="store_true", help="the client accepts the slice")
    sp.add_argument("--fail", dest="failed", action="store_true", help="the client returns it (needs --note)")
    sp.add_argument("--note", help="what the client said; required with --fail")

    sp = add("cr", cmd_cr, "change requests: open | approve | reject | list")
    sp.add_argument("action", choices=("open", "approve", "reject", "list"))
    sp.add_argument("id", nargs="?", help="CR id, e.g. CR-001 (approve, reject)")
    _feature(sp)
    by(sp)
    sp.add_argument("--title", help="open: what changes, in the client's words")
    sp.add_argument("--reason", help="open: why the change is needed")
    sp.add_argument("--hours", type=float, default=0, help="open: extra hours it costs (default: 0)")
    sp.add_argument("--affects", help="open: comma-separated AC ids it changes")
    sp.add_argument("--note", help="approve/reject: the client's note")

    sp = add("bug", cmd_bug, "defects: open | fix | close | reopen | list")
    sp.add_argument("action", choices=("open", "fix", "close", "reopen", "list"))
    sp.add_argument("id", nargs="?", help="defect id, e.g. BUG-001 (fix, close, reopen)")
    _feature(sp)
    by(sp)
    sp.add_argument("--title", help="open: what is wrong (required)")
    sp.add_argument("--severity", default="major", choices=tracking.SEVERITIES,
                    help="open: %(choices)s (default: %(default)s)")
    sp.add_argument("--found-in", default="build", choices=tracking.FOUND_IN,
                    help="open: where it was found — %(choices)s (default: %(default)s)")
    sp.add_argument("--ac", help="open: comma-separated AC ids it breaks")
    sp.add_argument("--task", help="the task that caused (open) or fixes (fix) it")
    sp.add_argument("--note", help="fix/close/reopen: what was done or seen")

    sp = add("proof", cmd_proof, "evidence: run | list | report | login | setup (Playwright, once per machine)")
    sp.add_argument("action", choices=("run", "list", "report", "login", "setup"))
    _feature(sp, positional=True)
    by(sp)
    sp.add_argument("--env", help="run: only this environment; login: the one to log in to (default: local)")
    sp.add_argument("--only", help="comma-separated proof ids")
    sp.add_argument("--path", help="login: path to open")

    sp = add("board", cmd_board, "team board across features (.egd/BOARD.md)")
    sp.add_argument("--html", action="store_true", help="also write .egd/board.html")
    sp.add_argument("--print", action="store_true", help="print the board even when not on a terminal")
    sp.add_argument("--quiet", action="store_true", help="only write the file")
    sp = add("report", cmd_report, "client status report", feature=True)
    sp.add_argument("--days", type=int, default=7, help="how far back it looks (default: 7)")
    sp.add_argument("--lang", choices=LANGS, help="en or vi (default: [report] lang in config, else en)")
    sp.add_argument("--print", action="store_true", help="print only the report, even when not on a terminal")
    sp.add_argument("--quiet", action="store_true", help="only write the file")
    sp = add("metrics", cmd_metrics, "estimate accuracy, review wait, rework, leakage", feature=True)
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    add("pr", cmd_pr, "pull request description with evidence", feature=True)
    sp = add("verify", cmd_verify, "one last pass before a gate: plan, tests, evidence, every gate ahead",
             feature=True)
    sp.add_argument("--run", action="store_true", help="run task tests and required proofs first (recorded)")
    by(sp, "who runs the proofs (with --run; default: you)")
    sp.add_argument("--gate", choices=GATES, help="check every gate up to this one (default: the next)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp = add("release-note", cmd_release_note, "release note for the client", feature=True)
    sp.add_argument("--lang", choices=LANGS, help="en or vi (default: [report] lang in config, else en)")
    sp.add_argument("--print", action="store_true", help="print it instead of naming the file")
    sp = add("standup", cmd_standup, "what moved per person, and what each holds now")
    sp.add_argument("--hours", type=int, default=24, help="how far back it looks (default: 24)")
    sp = add("serve", cmd_serve, "live read-only web dashboard")
    sp.add_argument("--host", default="127.0.0.1", help="address to listen on (default: %(default)s)")
    sp.add_argument("--lan", action="store_true",
                    help="open it to your local network: a token-protected link to share; visitors only look")
    sp.add_argument("--port", type=int, default=8770, help="(default: %(default)s; 0 picks a free one)")
    sp.add_argument("--token", help="required when not on localhost; generated if omitted (or set EGD_TOKEN)")
    sp.add_argument("--no-token", action="store_true",
                    help="skip the token on a non-local host — only when the port is not reachable "
                         "from other machines, e.g. a container published on 127.0.0.1")
    sp = add("site", cmd_site, "export the dashboard as a static site")
    sp.add_argument("--out", default="egd-site", help="folder to write (default: %(default)s)")
    _feature(sp, repeat=True)
    sp.add_argument("--no-evidence", action="store_true", help="leave transcripts and screenshots out")

    sp = add("update", cmd_update, "update egd itself: the CLI and the Claude Code plugin")
    sp = add("uninstall", cmd_uninstall, "remove egd from this machine (never a repository's .egd/)")
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    sp.add_argument("--keep-data", action="store_true",
                    help="keep the console's repository list (~/.egd/ and its Docker volume)")
    sp.add_argument("--dry-run", action="store_true", help="only list what would be removed")

    helps["import"] = "bring plans from another tool into .egd/ (one-way)"
    sp = sub.add_parser("import", help=helps["import"],
                        description="Read .ai/features/ (ai-dlc) and write equivalent EGD features. "
                                    ".ai/ is never changed and nothing is committed.")
    sp.set_defaults(fn=cmd_import)
    sp.add_argument("source", choices=("aidlc",))
    by(sp)
    sp.add_argument("--refresh", action="store_true",
                    help="re-read acceptance criteria of features already imported (fixes misread text)")
    _feature(sp, repeat=True)

    helps["console"] = "web console across all your repositories"
    sp = sub.add_parser("console", help=helps["console"],
                        description="Serve the console, or manage its repository list.")
    sp.set_defaults(fn=cmd_console)
    sp.add_argument("action", nargs="?", choices=("serve", "add", "remove", "signer", "list"))
    sp.add_argument("--name", help="signer: who actions in that repository are signed as (empty: the default)")
    sp.add_argument("path", nargs="?", help="repository path (add/remove) or id (remove)")
    sp.add_argument("--user", help="your default name; actions are signed with it unless a repository has its own signer")
    sp.add_argument("--scan", action="append", help="folder to scan for repositories with .egd/ (repeatable)")
    sp.add_argument("--host", default="127.0.0.1", help="address to listen on (default: %(default)s)")
    sp.add_argument("--lan", action="store_true",
                    help="open it to your local network: a token-protected link to share; visitors only look")
    sp.add_argument("--port", type=int, default=8780, help="(default: %(default)s; 0 picks a free one)")
    sp.add_argument("--token", help="required when not on localhost; generated if omitted (or set EGD_TOKEN)")
    sp.add_argument("--no-token", action="store_true",
                    help="no token on a non-local host — only when the port is reachable from this machine alone")
    sp.add_argument("--readonly", action="store_true", help="view only; no actions")
    sp.add_argument("--lan-write", action="store_true",
                    help="with --lan, let other machines act too — every action is signed with your name")
    sp.add_argument("--serve", action="store_true", help="serve after applying --user/--scan")
    sp.add_argument("--service", nargs="?", const="on", choices=("on", "off", "status"),
                    help="run it in the background (launchd/systemd): starts at login, survives the terminal; "
                         "off removes it; status shows it")

    sp = add("add", cmd_add, "add an assumption, ac, decision, slice or task to plan.toml, or a proof "
                             "to proof.toml", add_help=False)
    sp.add_argument("-h", "--help", action=_AddHelp, help="show this help; `egd add <kind> -h` shows only "
                                                          "that kind's options, with an example")
    sp.add_argument("entry", choices=ADD_KINDS, metavar="kind", help=" | ".join(ADD_KINDS))
    _feature(sp)
    by(sp)
    sp.add_argument("--id", help="explicit id (default: next free one)")
    sp.add_argument("--group", metavar="N", help="ac: story number — the id becomes AC-N.<next> "
                                                 "(default: 1 for the first AC, else the group of the last one)")
    for flag, dest, kw, kinds in ADD_OPTIONS:
        sp.add_argument(flag, dest=dest, help="; ".join(f"{k}: {v}" for k, v in kinds.items()), **kw)
    sp = add("set", cmd_set, "change fields of an existing plan entry: egd set A-1 status=confirmed ...")
    sp.add_argument("id", help="entry id, e.g. A-1, AC-1.2, D-1, S-1, T-1.2")
    sp.add_argument("pairs", nargs="+", metavar="field=value",
                    help="lists are comma-separated; booleans are true or false")
    _feature(sp)
    by(sp)
    sp = add("rm", cmd_rm, "remove a plan entry or a proof (after slice: with a change request); alias: remove",
             aliases=["remove"])
    sp.add_argument("id", help="entry id, e.g. A-1, AC-1.2, D-1, S-1, T-1.2, P-1")
    _feature(sp)
    by(sp)
    sp.add_argument("--cr", action="store_true",
                    help="after slice: remove it now, then record the change with `egd cr open`")
    sp = add("sync", cmd_sync, "mirror tasks into GitHub Issues")
    sp.add_argument("target", choices=("github",))
    _feature(sp)
    by(sp)
    sp.add_argument("--dry-run", action="store_true", help="print the gh calls without making them")
    sp.add_argument("--include-unplanned", action="store_true", help="also features before the slice gate")
    sp = add("trail", cmd_trail, "the event log for a feature", feature=True)
    sp.add_argument("--last", type=int, metavar="N", help="only the last N events")
    p.epilog = _grouped(helps)
    return p


def _os_error(exc: OSError) -> str:
    """`[Errno 2] No such file or directory: 'x'` → `no such file or directory: x`."""
    if exc.strerror and exc.filename:
        return f"{exc.strerror[0].lower()}{exc.strerror[1:]}: {exc.filename}"
    return str(exc.strerror or exc)


# Commands that read the trail or plan, decide, then write: one writer per repository at a time
# (the console takes the same lock). submit/solo and proof runs execute tests in between, so they
# only lock their write (events.record does), never the whole run.
LOCKED = {"new", "pass", "close", "start", "accept", "reject", "block", "unblock", "reopen", "uat", "cr", "bug",
          "add", "set", "rm", "remove"}


def _writer(args):
    from contextlib import nullcontext
    if args.cmd not in LOCKED:
        return nullcontext()
    try:
        root = find_root(Path(args.root) if args.root else None)
    except UsageError:
        return nullcontext()
    from .events import locked
    return locked(root)


def _feature_word(parser, argv: list[str]) -> list[str]:
    """A first word that is no command but is a feature's exact name means `-f <that feature>`.
    Exact only: a mistyped command must still say so, not quietly show some feature."""
    i, root = 0, None
    while i < len(argv) and argv[i].startswith("--root"):  # `--root DIR` / `--root=DIR` may come first
        root = argv[i].split("=", 1)[1] if "=" in argv[i] else (argv[i + 1] if i + 1 < len(argv) else None)
        i += 1 if "=" in argv[i] else 2
    if i >= len(argv) or argv[i].startswith("-"):
        return argv
    commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
    if argv[i] in commands:
        return argv
    try:
        features = all_features(find_root(Path(root) if root else None))
    except (UsageError, OSError):
        return argv
    if not any(argv[i] in (f.slug, short_name(f.slug)) for f in features):
        return argv
    return [*argv[:i], "-f", *argv[i:]]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_feature_word(parser, list(sys.argv[1:] if argv is None else argv)))
    try:
        _home_feature(args)
        _one_feature(args)
        with _writer(args):
            code = args.fn(args) if args.cmd else cmd_home(args)
        if getattr(args, "signed_via", None) and not getattr(args, "signed_shown", False):
            out(f"signed as {args.by} (from {args.signed_via})")
        return int(code or 0)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        for p in exc.problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    except UsageError as exc:
        print(f"egd: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # output piped into `head` and closed early
        sys.stderr.close()
        return 0
    except OSError as exc:  # a missing file or tool the command did not expect — a message, not a traceback
        print(f"egd: {_os_error(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
