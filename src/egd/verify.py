"""egd verify — one last pass over a feature before a person signs a gate.

It looks at everything a gate depends on, in one place: the plan, the task tests, the
evidence, the working tree and every gate still ahead. It changes nothing unless asked to
`run`: then the task tests and the required proofs run first, and the proof runs are
recorded exactly as `egd proof run` records them. It never passes a gate — it says whether
a person could, and what stands in the way.
"""

from __future__ import annotations

from .events import replay
from .gates import CHECKS
from .graph import validate
from .model import Feature
from .proof import local_note, proof_matrix, run_proofs, stale_note
from .proof.testcmd import TESTS_UNSET, run_tests
from .util import dirty_paths, git_memo, plural, split_local


@git_memo()
def verify(f: Feature, cfg: dict, *, run: bool = False, by: str | None = None, upto: str | None = None) -> dict:
    """Sections of (name, problems, notes) and whether the next gate (or `upto`) could be passed."""
    sections = []
    st = replay(f)

    errors, warnings = validate(f, cfg) if (f.slices or "clarify" in st.gates) else ([], [])
    errors = list(errors) + [f"unreadable event {c}" for c in st.corrupt]
    sections.append(("plan", errors, list(warnings) + st.warnings))

    tests = sorted({t for task in f.tasks for t in task.get("tests", [])})
    command = cfg.get("proof", {}).get("test_command")
    unset = bool(tests) and command is None
    result = run_tests(f.root, cfg, tests) if run else None
    if command == "":
        sections.append(("tests", [], ["none — this repository has no test runner (test_command = \"\")"]))
    elif unset:  # `submit` skips them too: said, not blocking
        sections.append(("tests", [], [f"⚠ {TESTS_UNSET}"]))
    elif not run:
        sections.append(("tests", [], ["not run — pass --run to run every task's tests"] if tests else []))
    elif result is None:
        sections.append(("tests", [], ["no task declares tests"]))
    else:
        ok, command = result["exit"] == 0, result["command"]
        sections.append(("tests", [] if ok else [f"exit {result['exit']}: {command}", result["output"][-800:]],
                         [f"passed in {result['ms'] / 1000:.1f}s: {command}"] if ok else []))

    if run and f.proofs:
        if not by:
            raise ValueError("running proofs needs a name (--by)")
        results = run_proofs(f, cfg, by)
        failed = [f"{r['proof']}@{r['env']}: {r['status']}" + (f" — {r['reason']}" if r.get("reason") else "")
                  for r in results if r["status"] != "pass" and r.get("required", True)]
        sections.append(("proofs", failed, [f"{len(results) - len(failed)}/{plural(len(results), 'run')} passed"]))
        st = replay(f)  # the runs just recorded count for the gates below
    elif f.proofs:  # what the trail already holds; the build gate below lists each one that does not count
        states = [state for _, _, required, _, state in proof_matrix(f, st, cfg) if required]
        good = states.count("pass")
        sections.append(("proofs", [], [f"{good}/{len(states)} pass on the current code"] if good == len(states)
                         else [f"⚠ {good}/{len(states)} pass on the current code — `--run` runs them again"]))

    dirty, local = split_local(dirty_paths(f.root), f.footprint)
    sections.append(("working tree", [], ([stale_note(dirty, "evidence taken now counts")] if dirty else [])
                     + ([local_note(local)] if local else [])))

    ahead = [g for g in f.gates if g not in st.gates]
    if upto:
        ahead = ahead[:ahead.index(upto) + 1] if upto in ahead else []
    gate_results = []
    for g in ahead:
        problems = CHECKS[g](f, st, cfg)
        gate_results.append((g, problems))
        sections.append((f"gate {g}", problems, []))

    target = (upto if upto in [g for g, _ in gate_results] else (ahead[0] if ahead else None))
    blocking = [p for name, problems, _ in sections
                if name in ("plan", "tests", "proofs") or name == f"gate {target}" for p in problems]
    if upto:  # every gate up to the one asked about must hold
        blocking += [p for g, problems in gate_results for p in problems]
    return {"feature": f.slug, "next": ahead[0] if ahead else None, "target": target,
            "ready": not blocking, "sections": sections}


def _grouped(items: list[str]) -> list[str]:
    """`AC-01: no proof`, `AC-02: no proof` → `AC-01, AC-02: no proof` — the same message once."""
    order, ids = [], {}
    for item in items:
        head, sep, msg = item.partition(": ")
        key = msg if sep and " " not in head else item
        if key not in ids:
            order.append(key)
            ids[key] = []
        if sep and " " not in head:
            ids[key].append(head)
    return [f"{', '.join(ids[k])}: {k}" if ids[k] else k for k in order]


def render(result: dict) -> str:
    lines = [f"egd verify — {result['feature']}", ""]
    for name, problems, notes in result["sections"]:
        mark = ("❌" if problems else "⚠️" if any(n.startswith("⚠") for n in notes)
                else "○" if any(n.startswith("not run") for n in notes) else "✅")
        lines.append(f"{mark} {name}")
        lines += [f"    - {p}" for p in _grouped(problems)]
        lines += [f"    · {n}" for n in _grouped(notes)]
    lines.append("")
    if result["target"] is None:
        lines.append("every gate has passed")
    elif result["ready"]:
        lines.append(f"ready: a person may now run `egd pass {result['target']}`")
    else:
        lines.append(f"not ready for `{result['target']}` — fix the ❌ items above")
    return "\n".join(lines)
