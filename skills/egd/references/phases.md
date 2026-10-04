# What each phase involves

## frame — why are we doing this?

1. If `.egd/map.md` is a blank template, map the repository: stack and versions,
   layout, how to run/test/deploy, conventions a newcomer would break, environments,
   and what could not be determined. Spawn `egd-scout` for this. Leave
   `reviewed_by:` empty — the person who reads the map and confirms it is true
   writes their own name there.
2. Draft `brief.md` with the user. A good *Success signal* is observable
   ("checkout errors in Sentry drop below 5/day"), not a restatement of the outcome.
   *Out of scope* is the cheapest place to prevent a future change request.
3. `egd check frame`, then ask the user to pass it (`egd pass frame --by <them>`).

## clarify — what exactly will be true?

Split every unknown into one of two piles:

- **Discoverable** — the repository answers it: existing endpoints, schema, auth,
  naming, test setup. Look it up. Asking a person something the code answers wastes
  the one round of questions you get.
- **Undiscoverable** — only a person knows: intent, priority, business rules,
  contracts with systems not in the repo, what the client will accept.

Ask the undiscoverable ones in **one batch of at most seven questions**, each with
your default answer, so silence still produces a decision. Allow one follow-up
round at most. Whatever is still unknown becomes an `[[assumption]]`:

- `confidence` — how sure you are of the default
- `blocking = true` when being wrong forces rework, not just a tweak

Write acceptance criteria as Given/When/Then that a tester could execute without
asking you anything. Add `levels` — where each criterion will be tested — because
that is the test strategy, decided before code exists. Non-functional requirements
(latency, permissions, accessibility) are ACs too, with `kind = "nfr"`.

## design — how will it work?

Short is fine; empty is not. *Approach*: components, data, contracts touched.
*Alternatives considered*: what lost and why, so nobody re-litigates it in review.
*Failure modes*: what a user sees for validation errors, missing records, conflicts,
permission denied, an upstream being down, timeouts. Every real choice becomes a
`[[decision]]` and must leave `proposed` before the gate opens.

## slice — in what order, in what pieces?

- A **slice** is the smallest thing a person can be shown working. Write its `demo`
  first: if you cannot write steps a client could follow, it is not a slice.
- **Tasks** are ≤ 4h. A bigger estimate hides an unknown — split it or add an
  assumption.
- `depends_on` lists only real dependencies (needs that table, needs that contract).
  Writing order is not a dependency. `egd graph` prints the waves that can run in
  parallel and the critical path.
- `touches` comes from the map. A task that touches no declared paths cannot be
  checked for drift.
- Declare `tests` per task and a `[[proof]]` per AC (see egd-proof) now, while the
  behaviour is fresh — of the kind egd-proof picks: a result someone sees gets a `ui` proof.
  No test runner in the repository? Say so with `test_command = ""` under `[proof]`.

## build — make it real

`egd ready` (each task with its agent and model) → `egd start T-x --by <dev> [--model …]` → implement inside `touches` → commit with
the task id in the message → `egd submit T-x --by <dev> --confirm --spent <h>`.
Submit runs the task's tests (when `proof.test_command` is set), checks that only
declared files changed (commits mentioning the id + the working tree), and records
the done_when list as attested by the submitter. A different person accepts.

When the last task is submitted, draft `release.md` → *Rollback* from the change itself, while
it is fresh: what to revert (the PR or commits), whether migrations reverse, flags to switch off,
data written that a revert leaves behind. A person confirms it at `release`; `egd pr` shows it.

When every task is done: commit the code, `egd proof run --by <you>`, `egd check build`, pass
`build`; commit the trail when `.egd/` is shared (never when it is local). Passing proofs on the
current code are what open the gate.

## accept — does the client agree?

Walk the client through each slice's demo (staging is ideal). Record the verdict
under their name: `egd uat S-1 --by <client> --pass` or `--fail --note "<what>"`.
Problems found here are defects with `--found-in uat`; they count against leakage.

## release — can it ship and be undone?

Write `release.md` → *Rollback*: exact steps, and whether migrations reverse.
Close remaining assumptions. `egd release-note` drafts the client-facing note.
