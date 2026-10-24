"""Proofs: reproducible evidence that an acceptance criterion holds.

A proof has a `kind` and the kind decides what its evidence looks like:

    test  — the project's test command, exit code and output tail
    http  — request/response transcripts with assertions (APIs, webhooks)
    cli   — a command's exit code and output (jobs, migrations, SQL via psql)
    ui    — browser screenshots per viewport (needs Playwright)

Every run is recorded on the trail with the commit it ran against, so `build`
can tell evidence of the current code from evidence of some earlier code.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..events import State, record, replay
from ..model import Feature
from ..util import (blob_hashes, clear_git_cache, dirty_paths, git_memo, head_sha, interpolate, iso, known_secrets,
                    load_secrets, local_record, missing_vars, plural, scope_fingerprint, secret_values,
                    source_changed_since, split_local)

REPO_ENV = "repo"
LOCAL_KINDS = ("test", "cli")
KINDS = ("test", "http", "cli", "ui")

# Every key a proof, a step and an `expect` may hold, per kind: a typo (`visibel`, `jsn`) must
# fail loudly — an assertion nobody runs is a pass nobody earned.
PROOF_KEYS = {"id", "kind", "title", "verifies", "envs", "timeout", "step", "inputs", "serial"}
KIND_KEYS = {"test": {"tests", "command"}, "cli": set(), "http": set(), "ui": {"viewports", "timeout_ms", "login"}}
STEP_KEYS = {
    "http": {"name", "method", "path", "url", "query", "headers", "json", "form", "body", "capture", "safe", "expect"},
    "cli": {"name", "run", "cwd", "env", "stdin", "safe", "expect"},
    "ui": {"name", "goto", "fill", "click", "press", "wait_for", "wait_ms", "screenshot", "full_page", "safe",
           "expect"},
}
EXPECT_KEYS = {
    "http": {"status", "json", "headers", "contains", "not_contains", "matches", "max_ms"},
    "cli": {"exit", "contains", "not_contains", "matches", "include_stderr", "max_ms"},
    "ui": {"visible", "hidden", "text", "url_contains", "title_contains"},
}


def shape_problems(proof: dict) -> list[str]:
    """Keys this kind of proof does not know — each one would be silently ignored."""
    from ..model import closest
    kind, pid = proof.get("kind"), proof.get("id", "?")
    if kind not in KINDS:
        return []  # reported where the kind is checked
    out = []

    def unknown(where, keys, known):
        for k in keys:
            if k not in known:
                hint = closest(k, sorted(known))
                out.append(f"{pid}: unknown key '{k}'{where}" + (f" — did you mean '{hint}'?" if hint else ""))
    unknown("", proof, PROOF_KEYS | KIND_KEYS[kind])
    steps = proof.get("step", [])
    if kind == "test" and steps:
        out.append(f"{pid}: a test proof runs its tests — it has no [[proof.step]]")
    for n, step in enumerate(steps if isinstance(steps, list) and kind != "test" else [], 1):
        if not isinstance(step, dict):
            continue
        unknown(f" in step {n}", step, STEP_KEYS[kind])
        if isinstance(step.get("expect"), dict):
            unknown(f" in step {n}'s expect", step["expect"], EXPECT_KEYS[kind])
    return out


@dataclass
class Context:
    root: Path
    feature: Feature
    env: str
    env_cfg: dict
    base_url: str
    secrets: dict
    redact: set
    out_dir: Path
    config: dict
    readonly: bool = False


@dataclass
class Outcome:
    status: str                      # pass | fail | error | unavailable | refused
    reason: str = ""
    steps: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    transcript: str = ""


def configured_envs(cfg: dict) -> dict[str, dict]:
    return {name: e for name, e in cfg.get("env", {}).items() if isinstance(e, dict)}


def proof_envs(proof: dict, cfg: dict) -> list[tuple[str, bool]]:
    """(env name, required) pairs this proof runs against.

    An env the config does not know stays required, so a typo shows up as a gap at `build`
    instead of quietly making the proof optional; only a configured env can opt out
    (`required = false` or `enabled = false`). `graph.validate` names the bad entry —
    `envs = "staging"` too: it is an error there, and read here as the one name it spells.
    Empty only for a proof with no enabled env to run on, which `unrunnable` reports.
    """
    envs = configured_envs(cfg)
    names = proof.get("envs")
    if isinstance(names, str):
        names = [names]
    if not names or not isinstance(names, list):
        if proof.get("kind") in LOCAL_KINDS or not envs:
            return [(REPO_ENV, True)]
        names = [n for n, e in envs.items() if e.get("enabled", True)]
    out = []
    for n in names:
        e = envs.get(n) if isinstance(n, str) else None
        if e is None:
            out.append((str(n), True))
        else:
            out.append((n, bool(e.get("enabled", True) and e.get("required", True))))
    return out


def unrunnable(f: Feature, cfg: dict) -> list[str]:
    """Proofs with no enabled environment left: a gap `build` names, never a proof that vanishes."""
    envs = configured_envs(cfg)
    out = []
    for p in f.proofs:
        names = [n for n, _ in proof_envs(p, cfg)]
        if not any(n == REPO_ENV or n not in envs or envs[n].get("enabled", True) for n in names):
            listed = f" ({', '.join(names)} disabled)" if names else ""
            out.append(f"{p['id']}: no enabled environment to run on{listed} — enable one under "
                       "[env] in .egd/config.toml, or remove the proof with an approved CR")
    return out


@git_memo()
def proof_matrix(f: Feature, st: State, cfg: dict) -> list[tuple[dict, str, bool, dict | None, str]]:
    """Every (proof, env, required, run, state) the plan asks for; git is asked once per commit.

    state is one rule for every view and gate:
        unrun   — never run, or an optional env that could not run (no evidence either way)
        fail    — the last run did not pass (a required env that was unavailable counts)
        stale   — passed, but the code or the proof changed since
        unknown — passed, but freshness cannot be told (no git): not evidence of this code
        pass    — passed on the code as it is now
    """
    latest = st.latest_proofs()
    shared = watched(f.root, cfg)
    out = []
    for p in f.proofs:
        for env, required in proof_envs(p, cfg):
            run = latest.get((p["id"], env))
            if run is None or (run.get("status") == "unavailable" and not required):
                state = "unrun"
            elif run.get("status") != "pass":
                state = "fail"
            else:
                fresh = freshness(f.root, run, p, cfg, proof_scope(f, p), f.slug, shared)
                state = "pass" if fresh else "stale" if fresh is False else "unknown"
            out.append((p, env, required, run, state))
    return out


def watched(root: Path, cfg: dict) -> list[str]:
    """Shared files whose committed change makes every feature's evidence stale, whatever its
    footprint: `[proof] watch` in config.toml plus the `watch` of each profile in `[stack] profiles`."""
    from ..profiles import active

    def globs(value) -> list[str]:
        return [value] if isinstance(value, str) else [str(g) for g in value or [] if str(g).strip()]
    out = globs((cfg.get("proof") or {}).get("watch"))
    for profile in active(root, cfg):
        out += globs(profile.get("watch"))
    return list(dict.fromkeys(out))


def proof_scope(f: Feature, proof: dict) -> list[str] | None:
    """The files whose change makes this proof's runs stale: its `inputs` (plus its own `tests`)
    when it names them — a CSS fix leaves the API proofs alone — else the feature's footprint.
    Shared files under `watch` count on top, either way."""
    inputs = proof.get("inputs")
    if isinstance(inputs, list) and inputs and all(isinstance(g, str) and g.strip() for g in inputs):
        return [*inputs, *(proof.get("tests") or [])]
    return f.footprint


def unproven(f: Feature, st: State, cfg: dict) -> set[tuple[str, str]]:
    """(proof id, env) whose evidence does not count for the code as it is now — stale, failed,
    never run, or of unknown freshness: what `egd proof run --stale-only` runs, and only that."""
    return {(p["id"], env) for p, env, _, _, state in proof_matrix(f, st, cfg) if state != "pass"}


def freshness(root: Path, run: dict, proof: dict | None = None, cfg: dict | None = None,
              footprint: list[str] | None = None, slug: str | None = None,
              watch: list[str] | None = None) -> bool | None:
    """True: evidence of the current code and the current proof. False: stale.
    None: cannot be told (no git; or local edits it ran beside, recorded on another clone).
    Local edits outside the feature's `footprint` do not count — as long as they stay as they
    were when the proof ran. Nor do commits outside it (a parallel feature merged in), unless
    they touch a shared file `watch` names. `footprint` here is the proof's scope (`proof_scope`)."""
    if proof is not None and cfg is not None:
        env_cfg = configured_envs(cfg).get(run.get("env"), {})
        if run.get("def") not in (definition_hash(proof, env_cfg, cfg), legacy_hash(proof, env_cfg)):
            return False
    if run.get("local"):
        then = local_record(root, "runs", f"{slug}/{run.get('run')}") if slug else None
        if then is None:
            return None
        if blob_hashes(root, list(then)) != then:
            return False
    changed = source_changed_since(root, run.get("sha"), footprint, watch, run.get("fp"))
    if changed is None:
        return None
    if run.get("dirty"):
        return False
    return not changed


def _runner(kind: str):
    if kind == "http":
        from . import http as mod
    elif kind == "cli":
        from . import cli as mod
    elif kind == "test":
        from . import testcmd as mod
    elif kind == "ui":
        from . import ui as mod
    else:
        return None
    return mod.run


def run_proofs(feature: Feature, cfg: dict, by: str, only: list[str] | None = None,
               env_filter: str | None = None, pairs: set[tuple[str, str]] | None = None,
               jobs: int = 1, fail_fast: bool = False) -> list[dict]:
    """Run the feature's proofs — narrowed by `only` (ids), `env_filter`, and `pairs`
    ((proof id, env) to run, e.g. `unproven`'s) — and record each run on the trail.

    Quickest first, by how long each took last time, so a cheap failure shows before a long
    e2e has used up its hour. `jobs` > 1 runs that many at once; a proof marked `serial = true`
    (it shares a database, a port, a seeded user) runs alone, after the rest. `fail_fast`
    starts nothing new once a required run has not passed. Returned in plan order."""
    root = feature.root
    secrets = load_secrets(root)
    envs = configured_envs(cfg)
    run_id = f"{iso().replace(':', '').replace('-', '')[:15]}-{os.urandom(2).hex()}"
    run_dir = feature.path / "runs" / run_id
    clear_git_cache()
    sha = head_sha(root)
    # before the runs (a proof may write files): what is uncommitted, and what of it is the feature's
    paths, local = split_local(dirty_paths(root), feature.footprint) if sha else ([], [])
    if len(local) > 1000:  # too many to vouch for one by one: the run counts as taken on a dirty tree
        paths, local = paths + local, []
    dirty = bool(paths) if sha else None
    if local:  # their content now, kept in this clone's notes: change one, and these runs go stale
        local_record(root, "runs", f"{feature.slug}/{run_id}", blob_hashes(root, local))
    # one redaction set for every run: whatever config and proofs reference, plus secrets.env
    redact = known_secrets(root, cfg, feature.raw_proofs)
    shared = watched(root, cfg)
    todo = []
    for proof in feature.proofs:
        if only and proof.get("id") not in only:
            continue
        for env, required in proof_envs(proof, cfg):
            if env_filter and env != env_filter:
                continue
            if pairs is not None and (proof["id"], env) not in pairs:
                continue
            todo.append((proof, env, required))
    order = {(p["id"], env): n for n, (p, env, _) in enumerate(todo)}
    latest = replay(feature).latest_proofs()
    todo.sort(key=lambda item: expected_ms(item[0], latest.get((item[0]["id"], item[1]))))
    # what each proof's evidence covers, by content: it still counts after a squash or a rebase
    fps = {p["id"]: scope_fingerprint(root, sha, proof_scope(feature, p), shared) if sha else None
           for p, _, _ in todo}

    def one(proof: dict, env: str, required: bool) -> dict:
        env_cfg = envs.get(env, {})
        out_dir = run_dir / f"{proof['id']}@{env}"
        out_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        outcome = _run_one(feature, proof, env, env_cfg, secrets, out_dir, cfg, redact)
        ms = int((time.monotonic() - started) * 1000)
        _pictures(outcome, out_dir)
        (out_dir / "transcript.md").write_text(outcome.transcript or "", encoding="utf-8")
        return record(
            feature, "proof_run", by,
            proof=proof["id"], env=env, kind=proof.get("kind"),
            verifies=proof.get("verifies", []), status=outcome.status,
            reason=outcome.reason, sha=sha, dirty=dirty, run=run_id, local=local[:20] or None,
            required=required, ms=ms, fp=fps.get(proof["id"]), **{"def": definition_hash(proof, env_cfg, cfg)},
            artifacts=[str(Path(a).relative_to(feature.path)) for a in outcome.artifacts]
            + [str((out_dir / "transcript.md").relative_to(feature.path))],
            steps=[{k: s.get(k) for k in ("name", "status", "ms")} for s in outcome.steps],
        )

    results: list[dict] = []
    stopped: list[dict] = []  # the run that stopped the rest, under fail_fast

    def take(event: dict) -> None:
        results.append(event)
        if fail_fast and event["status"] != "pass" and event.get("required", True):
            stopped.append(event)

    def batch(items: list, width: int) -> None:
        if width <= 1 or len(items) <= 1:
            for item in items:
                if stopped:
                    return
                take(one(*item))
            return
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
        queue = list(items)
        with ThreadPoolExecutor(max_workers=width) as pool:
            running: set = set()
            while queue or running:
                while queue and not stopped and len(running) < width:
                    running.add(pool.submit(one, *queue.pop(0)))
                if not running:
                    break
                done, running = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    take(future.result())

    width = max(1, int(jobs or 1))
    batch([t for t in todo if not t[0].get("serial")], width)
    batch([t for t in todo if t[0].get("serial")], 1)
    results.sort(key=lambda e: order[(e["proof"], e["env"])])
    skipped = [f"{p['id']}@{env}" for p, env, _ in todo
               if not any(r["proof"] == p["id"] and r["env"] == env for r in results)]
    notes = ([stale_note(paths)] if dirty else []) + ([local_note(local)] if local else [])
    if skipped and stopped:
        notes.append(f"--fail-fast: {stopped[0]['proof']}@{stopped[0]['env']} did not pass, so "
                     f"{plural(len(skipped), 'run')} did not start: {', '.join(skipped)}")
    if results and notes:
        for event in results:  # in memory only, for whoever prints the runs
            event["dirty_paths"] = paths[:5]
            event["notes"] = notes
    clear_git_cache()  # a proof may have written files; freshness is asked afresh
    return results


def proof_jobs(cfg: dict, asked: int | None = None) -> int:
    """How many proofs run at once: --jobs, else `[proof] jobs` in config.toml, else one."""
    value = asked if asked is not None else (cfg.get("proof") or {}).get("jobs", 1)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


# how long a proof never timed is guessed to take: a browser costs more than a command
GUESS_MS = {"test": 60_000, "cli": 30_000, "http": 30_000, "ui": 300_000}


def expected_ms(proof: dict, last: dict | None) -> int:
    """How long this proof took last time (its steps' sum on runs recorded before `ms`), else a
    guess by kind — what `run_proofs` sorts by, quickest first."""
    if last:
        if isinstance(last.get("ms"), int):
            return last["ms"]
        steps = [s.get("ms") for s in last.get("steps") or [] if isinstance(s, dict)]
        if steps and all(isinstance(m, int) for m in steps):
            return sum(steps)
    return GUESS_MS.get(proof.get("kind"), 60_000)


IMAGES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def run_images(folder: Path) -> list[Path]:
    """The pictures a run left in its folder, the contact sheet first."""
    try:
        found = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGES]
    except OSError:
        return []
    return sorted(found, key=lambda p: (p.name != "contact-sheet.png", p.name))


def _pictures(outcome: Outcome, out_dir: Path) -> None:
    """Any kind of proof may take screenshots — a cli step running its own browser script writes
    them to $EGD_OUT. Whatever pictures the run folder holds become the run's evidence, shown in
    the transcript, the console and `egd pr`."""
    listed = {Path(a).name for a in outcome.artifacts}
    extra = [p for p in run_images(out_dir) if p.name not in listed]
    if not extra:
        return
    outcome.artifacts += [str(p) for p in extra]
    shown = [p for p in extra if f"]({p.name})" not in (outcome.transcript or "")]
    if shown:
        outcome.transcript = (outcome.transcript or "").rstrip() + "\n\n## Screenshots\n\n" + "\n\n".join(
            f"![{p.stem}]({p.name})" for p in shown) + "\n"


def local_note(paths: list[str]) -> str:
    """Uncommitted edits outside the feature: named, and said not to count."""
    shown = ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")
    return f"local changes outside this feature's touches do not count against its evidence: {shown}"


def stale_note(paths: list[str], subject: str = "these runs count") -> str:
    """Why evidence taken on a dirty tree does not count, naming what is dirty."""
    shown = ", ".join(paths[:5]) + (f", … {len(paths) - 5} more" if len(paths) > 5 else "")
    return (f"⚠ {plural(len(paths), 'uncommitted file')} ({shown}) — {subject} "
            "as stale for `build`: commit, then run the proofs again (build artefacts belong in .gitignore)")


def run_notes(results: list[dict]) -> list[str]:
    """The notes of a `run_proofs` result, each once."""
    return list(dict.fromkeys(n for r in results for n in r.get("notes", [])))


def _run_one(feature, proof, env, env_cfg, secrets, out_dir, cfg, redact=frozenset()) -> Outcome:
    kind = proof.get("kind")
    runner = _runner(kind)
    if runner is None:
        return Outcome("error", f"unknown kind '{kind}' (expected one of {', '.join(KINDS)})")
    if env != REPO_ENV and env not in configured_envs(cfg):
        return Outcome("unavailable", f"env '{env}' is not configured in .egd/config.toml")
    if env_cfg and not env_cfg.get("enabled", True):
        return Outcome("unavailable", f"env '{env}' is disabled")
    shape = shape_problems(proof)
    if shape:
        return Outcome("error", "; ".join(shape))
    raw_url = env_cfg.get("base_url", "")
    if kind in ("http", "ui") and not raw_url and not (kind == "http" and all(
            isinstance(s, dict) and s.get("url") for s in proof.get("step", []))):
        return Outcome("unavailable", f"{kind} proofs need an env with a base_url — add "
                       '[env.local] base_url = "http://localhost:3000" to .egd/config.toml'
                       if env == REPO_ENV else f"env '{env}' has no base_url")
    signs_in = []
    if kind == "ui" and proof.get("login", True):
        from .ui import login_steps
        signs_in = login_steps(env_cfg, cfg)
    needed = missing_vars(json.dumps([raw_url, proof, signs_in]), secrets)
    if needed:
        return Outcome("unavailable", "not set: " + ", ".join(sorted(set(needed)))
                       + " (export it or put it in .egd/secrets.env)")
    ctx = Context(
        root=feature.root, feature=feature, env=env, env_cfg=env_cfg,
        base_url=interpolate(raw_url, secrets).rstrip("/"),
        secrets=secrets, redact=set(redact) | secret_values([raw_url, proof, signs_in], secrets),
        out_dir=out_dir, config=cfg, readonly=bool(env_cfg.get("readonly", False)),
    )
    try:
        outcome = runner(proof, ctx)
    except Exception as exc:  # a broken proof is evidence of nothing, never a crash
        outcome = Outcome("error", f"{type(exc).__name__}: {exc}")
    # last line of defence: nothing a runner produced may carry a substituted secret
    outcome.reason = redact_text(outcome.reason or "", ctx.redact)
    outcome.transcript = redact_text(outcome.transcript or "", ctx.redact)
    for s in outcome.steps:  # step names may carry ${…} too, and they land on the trail
        s["name"] = redact_text(str(s.get("name") or ""), ctx.redact)
    return outcome


def expand(values) -> set:
    """Each secret plus the encodings it can appear in: URL, form, JSON, repr."""
    import urllib.parse
    out = set()
    for v in values:
        if not isinstance(v, str) or len(v) < 3:
            continue
        out |= {v, urllib.parse.quote(v, safe=""), urllib.parse.quote_plus(v),
                json.dumps(v)[1:-1], repr(v)[1:-1]}
    return {x for x in out if len(x) >= 3}


def redact_text(text: str, redact: set) -> str:
    for value in sorted(expand(redact), key=len, reverse=True):
        text = text.replace(value, "***")
    return text


def definition_hash(proof: dict, env_cfg: dict, cfg: dict | None = None) -> str:
    """Changing what a proof asserts, verifies or runs against makes its old runs stale — and only
    that: whether an env is required or enabled says nothing about what the run showed."""
    used = {k: env_cfg.get(k) for k in ("base_url", "readonly") if k in env_cfg}
    if proof.get("kind") == "ui" and cfg is not None:
        from .ui import login_steps, viewports
        known = viewports(cfg)
        wanted = proof.get("viewports", ["desktop"])
        used["ui"] = {"login": login_steps(env_cfg, cfg) if proof.get("login", True) else [],
                      "viewports": {v: known.get(v) for v in ([wanted] if isinstance(wanted, str) else wanted)}}
    # `inputs` says when a run goes stale and `serial` how it is scheduled, not what it showed:
    # changing either re-proves nothing
    asserted = {k: v for k, v in proof.items() if k not in ("inputs", "serial")}
    canon = json.dumps([asserted, used], sort_keys=True, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def legacy_hash(proof: dict, env_cfg: dict) -> str:
    """How runs recorded before 0.1.0's final cut were hashed — still evidence while nothing changed."""
    return hashlib.sha256(json.dumps([proof, env_cfg], sort_keys=True, default=str).encode()).hexdigest()[:16]


def tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "… (truncated)\n" + text[-limit:]
