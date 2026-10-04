---
name: egd
description: EGD (Evidence-Gated Delivery) — plan, build and close features through gates a CLI enforces — brief, acceptance criteria, slices, tasks, evidence, UAT, release. Use when the repo has .egd/ or the user names EGD (lên plan EGD, EGD gate, what's next in EGD).
---

# EGD — Evidence-Gated Delivery

EGD keeps a feature honest from idea to release. The plan lives in files a team
can review; progress lives in an append-only event log; every gate is a function
of both, computed by the `egd` CLI. **The CLI is the authority — never your memory
of the conversation.**

## Running the CLI

One rule for every EGD skill and agent: use this plugin's own copy — it always matches these
skills, no install needed:

```bash
egd() { python3 "<this skill's directory>/../../bin/egd" "$@"; }; egd status
```

Each Bash call starts a fresh shell, so begin every call with that definition — or run
`python3 /abs/path/bin/egd …` each time. A function, not a variable: `E="python3 …/bin/egd"; $E …`
fails in zsh, which does not split words. Subagents get that path, resolved to an absolute one,
from you in their prompt. Fall back to `egd` on PATH only when the plugin's copy is missing (it
may be another version). It needs Python ≥ 3.11 and hands itself over to a newer `python3.1x` on
PATH when `python3` is older. Examples below write `egd` for either.

Bare `egd` prints each feature's next step and `you: <name>` — who this machine signs as.
Confirm that name with the user once per session; it is the `--by` to use when they ask
you to sign for them.

## The three rules that make it work

1. **`egd status` first.** At the start of any session that touches a feature, and
   whenever you are unsure what to do next. It names the next gate and exactly why
   it is closed. Never infer progress from chat history.
2. **Never edit `events/`.** State is replayed from those files. Moving a task or a
   gate by writing an event by hand — or by deleting one — is the single move that
   turns the whole system into theatre. Satisfy the check instead.
3. **Never sign for a human.** `--by` is a signature recorded forever. Pass a gate,
   accept a task, approve a CR or record UAT only under the name of the person who
   told you to, in this conversation. If nobody did, stop and ask. People at a terminal
   may leave `--by` out (it defaults to them); you always write it — inside Claude Code
   the CLI refuses an implied signer.

**`.egd/` may be local.** When git ignores it (`git check-ignore -q --no-index .egd/config.toml`
succeeds — `egd setup --local`, or a .gitignore), the team keeps EGD on each machine: never
`git add -f` anything under `.egd/`, never commit the trail. Bare `egd` warns when git still
tracks files there; untracking them is the user's call.

## Gates

| Gate | Proves | Closed until |
|---|---|---|
| `frame` | we know why | `brief.md` has Problem / Outcome / Success signal / Out of scope, no TODO; `.egd/map.md` has a human `reviewed_by:` (standard/full) |
| `clarify` | we know what | assumptions recorded; no blocking one open; resolved ones carry a resolution; every AC has given/when/then |
| `design` | we know how | `design.md` has Approach / Alternatives considered / Failure modes; no decision `proposed` (full: ≥1 accepted) |
| `slice` | we know the order | `egd graph` clean: AC coverage, demo per slice, done_when + estimate per task, limits, no cycles, test levels per AC, proofs (full) |
| `build` | it works | every task done via review or recorded solo; no pending CR; every required proof passing on the current code |
| `accept` | the client agrees | every slice has a passing UAT verdict; no open critical/major defect |
| `release` | it can ship | `release.md` has Rollback; no open assumption or proposed decision; no serious defect |

Tiers choose the path: **lite** (bugfix, < 1 day) skips `design` and `accept`;
**standard** runs all seven; **full** (fixed-price, risky) also requires a proof for
every AC, tests on every task and an accepted decision.

After `clarify`, the acceptance criteria are fingerprinted. Changing them later
closes every following gate until a change request is approved: edit the ACs, then
`egd cr open` (it snapshots them), then the client runs `egd cr approve`. Dropping a task
or proof planned at `slice` needs a CR too (`egd rm <id> --cr`, then `egd cr open`). Scope
creep becomes visible and billable.

## Commands

```bash
egd setup [--claude] [--local]          # once per repo: .egd/ (+ plugin for the team; --local: never committed)
egd profile [--use react,node] [--list] # the stack: test command, conventions, review list, done_when
egd new <name> --tier standard --by <you>
egd status [feature]                    # next gate, blockers, health (`egd <feature>` alone too)
egd check [gate] [feature]              # why a gate is closed — changes nothing
egd pass <gate> [feature] --by <human>  # refused unless check is clean
egd graph [feature]                     # validate plan; waves; critical path
egd ready                               # tasks whose dependencies are done

egd start  T-1.1 --by <dev>
egd submit T-1.1 --by <dev> --confirm [--spent 2.5]    # tests run, drift checked
egd accept T-1.1 --by <reviewer>                       # must differ from submitter
egd reject T-1.1 --by <reviewer> --reason "<why>"
egd solo   T-1.1 --by <dev> --confirm --reason "<why no review>"   # recorded bypass
egd block  T-1.1 --by <dev> --reason "<waiting on>"  ·  egd unblock T-1.1 --by <dev>
egd trail [feature]                     # the event log, human-readable
```

Plan entries can be written by command instead of editing TOML — handy for PMs and
safer for agents, since every write is re-parsed before it is saved and ids are
generated:

```bash
egd add assumption --by <you> --text "…" [--blocking] [--confidence low|medium|high]
egd add ac --by <you> --group 1 --given "…" --when "…" --then "…" --levels unit,e2e
egd add decision --by <you> --title "…" [--status accepted] [--choice "…"]
egd add slice --by <you> --title "…" --covers AC-1.1,AC-1.2 --demo "1. … 2. …"
egd add task --by <you> --slice S-1 --title "…" --estimate 3 --touches "src/x/**" \
    --tests path/to/test --done "…" --done "…" [--depends-on T-1.1]
egd set A-1 status=confirmed "resolution=…" --by <you>     # edit fields in place
egd rm T-1.2 --by <you>          # remove an entry; after `slice` only with --cr, then `egd cr open`
```

`egd add <kind> -h` lists one kind's options with an example. References are checked when
written (`--covers`, `--slice`, `--depends-on`), the first AC needs no `--group`, and
`egd add proof` writes test, cli, http and ui proofs (see `egd-proof`).

Proof commands are in the `egd-proof` skill; team, client and reporting commands
(board, CR, bugs, UAT, report, metrics, GitHub sync) in `egd-team`. Committing a task's work
is `egd-commit`; the PR description `egd-pr`; reviewing a PR `egd-review`; the last pass before
a gate `egd-verify` (`egd verify <feature> --run --by <you> [--gate release]`).

## Working a phase

Read `references/phases.md` at the phase you are in, and `references/plan-format.md`
for the exact shape of `plan.toml`. In short:

- **frame** — map the repo first (spawn `egd-scout`), then draft the brief with the
  user. The map is the scout's draft; only a person may fill `reviewed_by:`.
- **clarify** — answer from the repository what it can answer; ask the human one
  batch of at most 7 questions about what it cannot (intent, priorities, contracts
  not yet settled); everything still unknown becomes an `[[assumption]]` with
  confidence and `blocking`. Never a silent guess.
- **design** — one approach, the alternatives that lost and why, the failure modes
  a user can hit. Each real choice becomes a `[[decision]]`.
- **slice** — cut **vertical** slices a person can be shown (`demo`), never layers.
  Tasks ≤ 4h, `depends_on` only for real data/contract dependencies, `touches` from
  the map so drift can be checked, `tests` named up front, plus the done_when items `egd profile`
  asks of tasks touching those files (`egd add task` prints them). Each AC gets its proof now, of
  the kind `egd-proof` picks — an AC about what someone sees gets a `ui` proof (screenshots) that
  walks the slice's demo,
  not only a unit test. Nobody should have to ask "where is the evidence?" later.
- **build** — `egd ready`, `egd start`, implement inside `touches`, put the task id in
  every commit message, `egd submit --confirm`. Someone else accepts. When the last task is
  in, draft `release.md` → Rollback from the diff — not at the last gate, when nobody remembers.

## Agents

The plugin ships four subagents. Use them when work is handed off, parallelised, or
when the separation they enforce matters; small local work can follow the same rules
in this session. Run at most three at once. Give each one the absolute path of the CLI
(`<this skill's directory>/../../bin/egd`, resolved) and the `--by` name to use.

| Agent | Phase | Refuses |
|---|---|---|
| `egd-scout` | frame | signing `reviewed_by:`; editing anything but `.egd/map.md` |
| `egd-builder` | build | files outside the task's `touches`; accepting its own work |
| `egd-tester` | build | changing production code to make a test or proof pass |
| `egd-reviewer` | before accept | running `egd accept`; fixing what it finds |

**Handing out a wave.** `egd ready` (or `egd graph`'s waves) names each task's agent and model:
`T-1.2 … → builder · opus (risky: auth)`. The plan can set them (`agent`, `model` on the task;
`egd set T-1.2 model=haiku`); unset, EGD picks — `opus` for risky work (security, money,
migrations, concurrency) or a task sent back before, `haiku` for small plain work (≤ 1h),
`sonnet` otherwise; `tester` when the task touches only tests. For each ready task of the wave:
`egd start <task> --by <the agent's name> --model <model>` (recorded on the trail), then spawn
that agent with that model, at most three at once. `agent = "person"`: leave it for a human.
A stronger model earns no more say — gates, accepts and UAT stay a person's.

End every subagent run with a five-line handoff: outcome, files, commands with
pass/fail, blockers, next action. No pasted logs or diffs.

## When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Slice named after a layer ("API layer") | horizontal cut | re-cut around something a user can see |
| Every task depends on the previous one | order invented from writing order | keep only real dependencies; `egd graph` shows waves |
| Task estimated 6h | hidden unknowns | split until each piece is ≤ the limit |
| Submit refused for drift | task touched files it did not declare | re-plan `touches`, or `--allow-drift --drift-reason` (recorded). Local edits already there at `egd start` and left alone are not drift |
| A ticket stops: dropped mid-way, a research spike, or replaced | it will never release | `egd close <feature> --as dropped\|research\|superseded --reason "…" --by <the person who decided>` — no gate passed, open tasks recorded; `--undo` reopens. For research the reason says what it found |
| "Review later" / "I'll review the branch at the end" | a person defers review | `egd solo <task> --by <that person> --confirm --reason "review deferred to …"` — recorded, never silent; `--confirm` attests the done_when list in that person's name |
| Evidence is only a status line | proofs that show nothing a person can look at | `egd-proof`: `ui` screenshots for what users see, assertions on the values the AC names |
| Build refused: proof stale | code changed after the proof ran | commit, `egd proof run` again |
| Gates closed after editing an AC | scope changed after `clarify` | `egd cr open`, client approves |
| Ten rounds of questions | interrogating one at a time | one batch ≤ 7; the rest become assumptions |
