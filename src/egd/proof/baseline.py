"""Does the evidence bite? A proof that also passes without the feature's changes proves nothing
about them: eight tests pass, a check is taken out of the code, and the eight still pass.

After a proof passes, `egd proof run` takes the feature's changes back out — each file inside the
feature's touches goes back to how it was when its first task started, while its tests stay as
they are now — and runs the proof again. In place, so the same dependencies, caches and services
are there; then every file comes back. On that code the proof has to fail:

    bites      — it did not pass without the change: evidence of the change
    toothless  — it passed there too: it cannot tell this change from no change
    skipped    — it could not be checked this time; `reason` says why

Only `test` and `cli` proofs on the repository itself are checked: an `http` or `ui` proof calls a
server egd did not start, which would still run the new code. A proof that guards behaviour that
held before the feature too (a regression check, a refactor's) says `baseline = false`.

Stopped half-way, egd leaves `.git/egd-baseline.json` naming what it took out; the next
`egd proof run` puts it back first. A file someone edited meanwhile is left as they made it.
"""

from __future__ import annotations

import json
import os

from ..events import State, record, replay
from ..model import Feature
from ..util import EGD_DIR, blob_hashes, clear_git_cache, dirty_paths, git, matches_any, write_atomic

MARK = "egd-baseline.json"


def enabled(cfg: dict) -> bool:
    """`[proof] baseline = false` turns the check off for the repository: not run, not asked for."""
    return (cfg.get("proof") or {}).get("baseline", True) is not False


def not_checked(proof: dict, env: str) -> str | None:
    """Why this proof is never checked against the code before the feature; None when it is."""
    from . import LOCAL_KINDS, REPO_ENV
    if proof.get("baseline") is False:
        return "baseline = false: a regression proof — it held before this feature too"
    if proof.get("kind") not in LOCAL_KINDS or env != REPO_ENV:
        return f"a {proof.get('kind')} proof on {env} reaches code egd does not take out"
    return None


def base_sha(f: Feature, st: State) -> str | None:
    """Where the feature's changes begin: the commit its first task started on."""
    ids = {t.get("id") for t in f.tasks}
    for e in st.events:
        if e.get("type") == "task_started" and e.get("task") in ids and e.get("sha"):
            return e["sha"]
    return None


def _tree(root, rev: str) -> dict[str, tuple[str, str]] | None:
    """path → (mode, blob) of every file under the EGD root (outside .egd/) at `rev`."""
    out = git(root, "-c", "core.quotePath=false", "ls-tree", "-r", "-z", rev)
    if out is None:
        return None
    files = {}
    for entry in out.split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if path and len(parts) == 3 and parts[1] == "blob" and not path.startswith(EGD_DIR + "/"):
            files[path] = (parts[0], parts[2])
    return files


def taken_out(f: Feature, base: str, head: str) -> tuple[dict, str | None, str | None]:
    """(path → (mode, blob) at `base`, or None where the file did not exist yet; why not; its code).
    Every file inside the footprint that differs between the two, the tests left out."""
    footprint = f.footprint
    if footprint is None:
        return {}, "a task declares no touches — the feature's changes cannot be told apart", "notouches"
    kept = [g for t in f.tasks for g in t.get("tests", [])] + [g for p in f.proofs for g in (p.get("tests") or [])]
    then, now = _tree(f.root, base), _tree(f.root, head)
    if then is None or now is None:
        return {}, "git could not list the files before the feature", "nobase"
    plan = {p: then.get(p) for p in sorted(set(then) | set(now))
            if matches_any(p, footprint) and not matches_any(p, kept) and then.get(p) != now.get(p)}
    if not plan:
        return {}, "the feature has changed nothing outside its tests since its first task started", "unchanged"
    return plan, None, None


def _blob(root, blob: str) -> bytes:
    import subprocess  # lazily, as util.git does: most commands never get here
    proc = subprocess.run(["git", "cat-file", "blob", blob], cwd=root, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise OSError(f"git cannot read blob {blob}")
    return proc.stdout


def _put(root, path: str, entry) -> None:
    """Make `path` hold `entry` ((mode, blob)), or be gone (None)."""
    target = root / path
    if target.is_symlink() or target.is_file():
        target.unlink()
    if entry is None:
        return
    mode, blob = entry
    data = _blob(root, blob)
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "120000":
        os.symlink(data.decode(), target)
        return
    target.write_bytes(data)
    if mode == "100755":
        target.chmod(0o755)


def _marker(root):
    rel = (git(root, "rev-parse", "--git-path", MARK) or "").strip()
    return (root / rel) if rel else None


def _put_back(root, back: dict, taken: dict) -> list[str]:
    """Every file to how it was (`back`), unless someone changed it from what egd left (`taken`,
    path → blob or ""): then it stays theirs, and is named."""
    now = blob_hashes(root, [p for p in back if not (root / p).is_symlink()])
    left = []
    for path, entry in back.items():
        if path in now and now[path] != taken.get(path, ""):
            left.append(path)
            continue
        _put(root, path, entry)
    clear_git_cache()
    if not left:
        return []
    shown = ", ".join(left[:5]) + (f" and {len(left) - 5} more" if len(left) > 5 else "")
    return [f"changed while the baseline ran, so left as it is now: {shown} — "
            "`git checkout HEAD -- <file>` brings back the committed version"]


def recover(root) -> list[str]:
    """Put back what a stopped baseline run took out; notes for whoever runs next."""
    mark = _marker(root)
    if mark is None or not mark.is_file():
        return []
    try:
        data = json.loads(mark.read_text(encoding="utf-8"))
        back = {p: tuple(v) if v else None for p, v in data["back"].items()}
        left = _put_back(root, back, data["taken"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"a stopped baseline run left {mark} and it could not be read back ({exc}) — "
                "check `git status` in the feature's touches, then delete that file"]
    mark.unlink(missing_ok=True)
    return [f"put back {len(back)} file{'' if len(back) == 1 else 's'} a stopped baseline run had taken out"] + left


def _before(root, plan: dict, head: str, work) -> list[str]:
    """Run `work()` on the code before the feature (`plan`), then put the code back."""
    now = _tree(root, head) or {}
    back = {p: now.get(p) for p in plan}
    taken = {p: e[1] if e else "" for p, e in plan.items()}
    mark = _marker(root)
    write_atomic(mark, json.dumps({"head": head, "back": back, "taken": taken}))
    try:
        for path, entry in plan.items():
            _put(root, path, entry)
        clear_git_cache()
        work()
    finally:
        notes = _put_back(root, back, taken)
        mark.unlink(missing_ok=True)
    return notes


def bite(feature: Feature, by: str, candidates: list[tuple[dict, dict]], again, width: int = 1) -> list[dict]:
    """Run each passed proof again on the code before the feature and record what it showed.
    `candidates`: (proof, its passing run event), all from one `run_proofs`; `again(proof, env)`
    runs one proof and returns (Outcome, ms, artifacts). Returns the `proof_baseline` events."""
    if not candidates:
        return []
    root = feature.root
    head = candidates[0][1].get("sha")

    def skip(reason: str, why: str) -> list[dict]:
        return [record(feature, "proof_baseline", by, proof=e["proof"], env=e["env"], run=e["run"],
                       status="skipped", why=why, reason=reason) for _, e in candidates]

    base = base_sha(feature, replay(feature))
    if not head:
        return skip("no git: there is no code before the feature to run against", "nobase")
    if base is None:
        return skip("no task of this feature has started — nothing marks where its changes begin", "nobase")
    if git(root, "cat-file", "-e", f"{base}^{{commit}}") is None:
        return skip(f"{base[:9]}, where the first task started, is not in this clone (rebased or squashed?)",
                    "nobase")
    plan, reason, why = taken_out(feature, base, head)
    if reason:
        return skip(reason, why)
    touched = sorted(set(dirty_paths(root)) & set(plan))
    if touched:
        return skip("uncommitted changes in the files it would take out (" + ", ".join(touched[:3])
                    + ") — commit them, then run the proof again", "dirty")

    results: dict[tuple[str, str], tuple] = {}

    def work() -> None:
        def one(proof, event):
            results[(event["proof"], event["env"])] = again(proof, event["env"])
        if width <= 1 or len(candidates) == 1:
            for proof, event in candidates:
                one(proof, event)
            return
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=width) as pool:
            list(pool.map(lambda c: one(*c), candidates))

    notes = _before(root, plan, head, work)
    events = []
    for proof, event in candidates:
        outcome, ms, artifacts = results[(event["proof"], event["env"])]
        events.append(record(
            feature, "proof_baseline", by, proof=event["proof"], env=event["env"], run=event["run"],
            status="toothless" if outcome.status == "pass" else "bites", base=base,
            outcome=outcome.status, reason=outcome.reason, ms=ms, taken=len(plan), artifacts=artifacts))
    if notes:
        for e in events:
            e["notes"] = notes
    return events


def findings(f: Feature, st: State, cfg: dict) -> tuple[list[str], list[str]]:
    """(what closes `build`, what a person should hear there) about proofs and the code before the
    feature. Toothless evidence closes it under rules v3; a proof never checked closes it on the
    `full` tier. Older plans hear both as warnings."""
    from . import proof_matrix
    if not enabled(cfg):
        return [], []
    strict = st.rules >= 3
    problems, warns = [], []
    opted = []
    for p, env, required, run, state in proof_matrix(f, st, cfg):
        if state != "pass" or not required:
            continue
        reason = not_checked(p, env)
        if reason:
            if p.get("baseline") is False:
                opted.append(f"{p['id']}@{env}")
            continue
        b = st.baselines.get((p["id"], env, run.get("run")))
        name = f"{p['id']}@{env}"
        if b and b.get("status") == "bites":
            continue
        if b and b.get("status") == "toothless":
            (problems if strict else warns).append(
                f"{name}: passes without this feature's changes too — it cannot tell them from no change; "
                "assert what the AC changes (`baseline = false` only on a proof guarding what already held)")
        elif b and b.get("why") == "unchanged":
            warns.append(f"{name}: not checked against the code before this feature — {b.get('reason')}")
        else:
            why = b.get("reason") if b else "this run was not checked (`--no-baseline`, or egd before 0.1.8)"
            (problems if strict and f.tier == "full" else warns).append(
                f"{name}: not checked against the code before this feature — {why}; "
                f"`egd proof run --only {p['id']}` checks it")
    if opted:
        warns.append(f"{', '.join(opted)}: baseline = false — not checked against the code before this "
                     "feature (a regression proof); the person passing `build` vouches for that")
    return problems, warns


def unchecked(f: Feature, st: State, cfg: dict) -> set[tuple[str, str]]:
    """(proof id, env) still passing whose check the gate asks for and the trail lacks — what
    `--stale-only` and `verify --run` run again, so the check happens."""
    if not enabled(cfg) or st.rules < 3 or f.tier != "full":
        return set()
    from . import proof_matrix
    out = set()
    for p, env, required, run, state in proof_matrix(f, st, cfg):
        if state == "pass" and required and not not_checked(p, env):
            b = st.baselines.get((p["id"], env, run.get("run")))
            if b is None or (b.get("status") == "skipped" and b.get("why") != "unchanged"):
                out.add((p["id"], env))
    return out


def verdict(st: State, proof: dict, env: str, run: dict | None, cfg: dict) -> str:
    """One word for views: bites, toothless, unchecked — or "" where the check does not apply."""
    if not run or run.get("status") != "pass" or not enabled(cfg) or not_checked(proof, env):
        return ""
    b = st.baselines.get((proof["id"], env, run.get("run")))
    return b.get("status") if b and b.get("status") != "skipped" else "unchecked"
