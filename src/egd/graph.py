"""Plan validation and the computed schedule: waves, critical path, readiness."""

from __future__ import annotations

import math
import re
from collections import defaultdict

from .model import Feature
from .events import State

ID_PREFIX = {"assumption": "A-", "ac": "AC-", "decision": "D-", "slice": "S-", "task": "T-"}


def _dupes(items: list[dict]) -> list[str]:
    seen, dup = set(), []
    for it in items:
        i = it.get("id")
        if i in seen:
            dup.append(i)
        seen.add(i)
    return dup


def find_cycle(tasks: list[dict]) -> list[str] | None:
    deps = {t["id"]: list(t.get("depends_on", [])) for t in tasks if "id" in t}
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = 1
        stack.append(node)
        for nxt in deps.get(node, []):
            if nxt not in deps:
                continue
            if color.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if color.get(nxt) is None:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = 2
        return None

    for node in deps:
        if color.get(node) is None:
            found = visit(node)
            if found:
                return found
    return None


def longest_path(tasks: list[dict]) -> tuple[float, list[str]]:
    """Longest chain of estimates through depends_on, restricted to `tasks`."""
    by_id = {t["id"]: t for t in tasks}
    memo: dict[str, tuple[float, list[str]]] = {}

    def walk(tid: str) -> tuple[float, list[str]]:
        if tid in memo:
            return memo[tid]
        own = float(by_id[tid].get("estimate_h", 0) or 0)
        best: tuple[float, list[str]] = (0.0, [])
        for dep in by_id[tid].get("depends_on", []):
            if dep in by_id:
                cand = walk(dep)
                if cand[0] > best[0]:
                    best = cand
        memo[tid] = (best[0] + own, best[1] + [tid])
        return memo[tid]

    result: tuple[float, list[str]] = (0.0, [])
    for tid in by_id:
        cand = walk(tid)
        if cand[0] > result[0]:
            result = cand
    return result


def waves(tasks: list[dict]) -> list[list[str]]:
    ids = {t["id"] for t in tasks}
    deps = {t["id"]: [d for d in t.get("depends_on", []) if d in ids] for t in tasks}
    level: dict[str, int] = {}

    def lvl(tid: str, guard: frozenset = frozenset()) -> int:
        if tid in level:
            return level[tid]
        if tid in guard:
            return 0
        level[tid] = 1 + max((lvl(d, guard | {tid}) for d in deps[tid]), default=-1)
        return level[tid]

    for tid in deps:
        lvl(tid)
    out: dict[int, list[str]] = defaultdict(list)
    for tid, n in level.items():
        out[n].append(tid)
    return [sorted(out[k]) for k in sorted(out)]


def validate(feature: Feature, config: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) for the plan's structure. Errors block the `slice` gate."""
    errors: list[str] = []
    warnings: list[str] = []
    limits = config.get("limits", {})
    max_task = float(limits.get("task_hours", 4))
    max_slice = float(limits.get("slice_hours", 16))
    tier = feature.tier
    no_runner = config.get("proof", {}).get("test_command") == ""  # said on purpose: no test runner here
    # Plans imported from another tool already went through that tool's gates: policy rules
    # (sizes, test levels, demos) only warn there; integrity rules (ids, references, cycles) still fail.
    policy = warnings if feature.meta.get("imported_from") else errors

    if feature.error:
        errors.append(feature.error)
    tier_raw = feature.meta.get("tier", "standard")
    if tier_raw not in ("lite", "standard", "full"):
        errors.append(f"[feature] tier '{tier_raw}' is not lite, standard or full")
    raw = {k: feature.plan.get(k, []) for k in ("assumption", "ac", "decision", "slice", "task")}
    raw["proof"] = feature.raw_proofs
    for kind, items in raw.items():
        for it in items:
            if not isinstance(it, dict) or not it.get("id"):
                errors.append(f"a [[{kind}]] entry has no id")
    for kind, items in (("assumption", feature.assumptions), ("ac", feature.acs),
                        ("decision", feature.decisions), ("slice", feature.slices),
                        ("task", feature.tasks), ("proof", feature.proofs)):
        for d in _dupes(items):
            errors.append(f"duplicate {kind} id {d}")

    ac_ids = {a.get("id") for a in feature.acs}
    slice_ids = {s.get("id") for s in feature.slices}
    task_ids = {t.get("id") for t in feature.tasks}

    for a in feature.acs:
        for part in ("given", "when", "then"):
            if not str(a.get(part, "")).strip():
                errors.append(f"{a.get('id')}: missing '{part}'")
        if tier != "lite" and not a.get("levels"):
            policy.append(f"{a.get('id')}: no test strategy — set levels = [\"unit\"|\"integration\"|\"e2e\"|\"manual\"]")

    covered: set[str] = set()
    for s in feature.slices:
        sid = s.get("id")
        covers = s.get("covers", [])
        if not covers:
            policy.append(f"{sid}: covers no acceptance criteria")
        for ac in covers:
            if ac not in ac_ids:
                errors.append(f"{sid}: covers unknown {ac}")
        covered |= set(covers)
        if not str(s.get("demo", "")).strip():
            policy.append(f"{sid}: no demo — a slice nobody can be shown is not a slice")
        members = feature.tasks_in(sid)
        if not members:
            policy.append(f"{sid}: has no tasks")
        elif not find_cycle(members):
            length, chain = longest_path(members)
            if length > max_slice:
                policy.append(f"{sid}: critical path {length:g}h > {max_slice:g}h "
                              f"({' → '.join(chain)}) — split the slice")

    for ac in sorted(ac_ids - covered - {None}):
        policy.append(f"{ac}: not covered by any slice")

    for t in feature.tasks:
        tid = t.get("id")
        if t.get("slice") not in slice_ids:
            errors.append(f"{tid}: slice '{t.get('slice')}' does not exist")
        for dep in t.get("depends_on", []):
            if dep == tid:
                errors.append(f"{tid}: depends on itself")
            elif dep not in task_ids:
                errors.append(f"{tid}: depends on unknown {dep}")
        est = t.get("estimate_h")
        if isinstance(est, bool) or not isinstance(est, (int, float)) or not math.isfinite(est) or est <= 0:
            errors.append(f"{tid}: estimate_h must be a positive number of hours")
        elif est > max_task:
            policy.append(f"{tid}: {est:g}h > {max_task:g}h — split it")
        if not t.get("done_when"):
            policy.append(f"{tid}: empty done_when")
        if not t.get("touches"):
            warnings.append(f"{tid}: no touches — scope drift cannot be checked")
        if not t.get("tests") and not no_runner:
            (errors if tier == "full" else warnings).append(f"{tid}: no tests listed")

    cycle = find_cycle(feature.tasks)
    if cycle:
        errors.append("dependency cycle: " + " → ".join(cycle))

    known_envs = {n for n, e in config.get("env", {}).items() if isinstance(e, dict)} | {"repo"}
    proved: set[str] = set()
    for p in feature.proofs:
        if "envs" in p:  # a typo here must not quietly make the proof optional
            envs = p["envs"]
            if not isinstance(envs, list) or not envs or not all(isinstance(n, str) for n in envs):
                errors.append(f"{p.get('id')}: envs must be a non-empty list of env names, "
                              f"e.g. envs = [\"staging\"] — or leave it out for every enabled env")
            else:
                for n in envs:
                    if n not in known_envs:
                        errors.append(f"{p.get('id')}: env '{n}' is not configured — "
                                      f"known: {', '.join(sorted(known_envs))}")
        from .proof import shape_problems  # noqa: PLC0415 — only when a plan has proofs
        errors += shape_problems(p)
        for ac in p.get("verifies", []):
            if ac not in ac_ids:
                errors.append(f"{p.get('id')}: verifies unknown {ac}")
            proved.add(ac)
    for ac in sorted(ac_ids - proved - {None}):
        msg = f"{ac}: no proof declared in proof.toml"
        (errors if tier == "full" else warnings).append(msg)

    for kind, items in (("ac", feature.acs), ("slice", feature.slices), ("task", feature.tasks)):
        prefix = ID_PREFIX[kind]
        for it in items:
            if it.get("id") and not str(it["id"]).startswith(prefix):
                warnings.append(f"{it['id']}: {kind} ids conventionally start with {prefix}")
    return errors, warnings


_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|e2e|spec)(/|$)|(^|/)test_|[._-](test|spec)\.")
_RISKY = re.compile(r"secur|auth|login|passw|token|secret|permission|role|migrat|schema|payment|billing|"
                    r"encrypt|crypto|concurren|race|lock|webhook|delete|purge", re.I)


def assignment(task: dict, state: State | None = None) -> tuple[str, str, str]:
    """(agent, model, why) for a task: what its plan says, else a pick from what it is.

    agent: `tester` when every path it touches is a test, else `builder` (`person` only by hand).
    model: `opus` when it is risky (security, money, migrations, concurrency…) or was sent back
    before; `haiku` when it is small (≤ 1h) and plain; `sonnet` otherwise. A stronger model gets
    no more say: signatures stay a person's.
    """
    touches = [str(p) for p in task.get("touches", []) if str(p).strip()]
    agent = task.get("agent") or ("tester" if touches and all(_TEST_PATH.search(p) for p in touches) else "builder")
    if task.get("model"):
        return agent, task["model"], "set in the plan"
    text = " ".join([str(task.get("title", "")), *touches])
    rejects = state.task(task["id"]).rejects if state is not None and task.get("id") else 0
    est = task.get("estimate_h") if isinstance(task.get("estimate_h"), (int, float)) else 2
    if rejects:
        return agent, "opus", "sent back before"
    if _RISKY.search(text):
        return agent, "opus", "risky: " + _RISKY.search(text).group(0).lower()
    if est <= 1:
        return agent, "haiku", "small"
    return agent, "sonnet", "usual"


def ready(feature: Feature, state: State) -> list[dict]:
    if state.closed:  # dropped, a finished spike, or replaced: nothing in it is to be picked up
        return []
    done = {tid for tid, t in state.tasks.items() if t.status == "done"}
    out = []
    for t in feature.tasks:
        if state.task(t["id"]).status != "todo":
            continue
        if all(d in done for d in t.get("depends_on", [])):
            out.append(t)
    return out
