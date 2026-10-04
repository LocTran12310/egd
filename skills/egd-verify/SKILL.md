---
name: egd-verify
description: Final pass before a person signs an EGD gate or opens a PR — `egd verify --run`, then each AC read against the change. Never passes gates. Use only in repos with .egd/ — verify, is it ready, kiểm tra lần cuối.
---

# EGD verify

The last look before a signature. The CLI does the mechanical part; this skill adds the
judgment a check cannot make, then hands the decision back to a person.

Run the CLI as the `egd` skill says (`python3 <this skill's directory>/../../bin/egd`, else `egd` on
PATH); `--by` is the name the user confirmed there
(bare `egd` shows it). Without `.egd/` this skill does not apply.

## 1. Mechanical pass

```bash
egd verify <feature> --run --by "<name>"              # next gate
egd verify <feature> --run --by "<name>" --gate release   # everything up to release
```

`--run` runs every task's tests (`[proof] test_command`) and every required proof, and
records the proof runs like `egd proof run`. Without `--run` it only reads.

It reports, each ✅ or ❌: the plan, the tests, the proofs, the working tree (uncommitted
code makes fresh evidence stale), and each gate ahead with what keeps it closed. Exit 0
means the target gate could be passed now.

Fix what is mechanical and in scope (a proof needs re-running after a commit, a plan
field is missing) — then run it again. Anything that needs a person — an open assumption,
a UAT verdict, a pending change request, a serious defect — is reported, not worked around.

## 2. Judgment pass

For each acceptance criterion the feature covers:

1. Find the code that makes its *then* true and the proof or test that shows it.
2. If the AC has no proof, or the proof checks something weaker than the *then* says,
   that is a finding — however green the gate looks. An AC about what someone sees, proven
   only by a unit test, is missing its `ui` proof (the `egd-proof` skill adds it).
3. Walk the slice's demo script where it can be done from here (an HTTP call, a CLI run,
   a UI proof); say which steps only a person can walk (the client's UAT).

Then across the change: diff against the tasks' `touches` (anything else?), leftover debug
output, TODOs, commented-out code, secrets, and whether `design.md`'s *Failure modes*
are handled.

## 3. Report

```
## <feature> — verify <date>
Target: <gate> · <ready | not ready>

### Checks (egd verify)
✅/❌ plan · tests · proofs · working tree · gate <g> …

### Acceptance criteria
| AC | then (short) | shown by | status |
| AC-1.1 | … | P-2 http@staging, pass, 9f3c1a2 | ✅ |
| AC-2.1 | … | — | ⚠ no proof |

### Findings
- [BLOCKER|SHOULD-FIX|NIT] file:line or AC — what, and the fix

### Needs a person
- UAT for S-2 · CR-1 awaits the client · A-3 still open

### Next
`egd pass <gate> --by <a person with that role>` — or what to do first.
```

Never run `egd pass`, `egd accept`, `egd uat` or `egd cr approve` from this skill: those
are signatures, and a signature is a person's.
