# Why each gate exists

EGD assumes the failure modes of AI-assisted delivery are not technical. Agents and
people both produce plausible work fast; what goes wrong is that nobody can tell, at
a glance, whether the plausible work is the *agreed* work and whether it *actually
works*. Each gate answers one question that would otherwise be answered by vibes.

## Principles

1. **State is derived, never declared.** There is no status field to set. A task is
   "done" because the trail contains an acceptance by someone other than the
   submitter. A gate is passed because its checks held at the moment someone signed.
2. **A signature is a person.** Every `--by` is recorded. Agents never sign for
   humans. Roles decide who may sign what.
3. **Unknowns are written down.** A question not asked is an assumption; an
   assumption that would force rework is blocking; blocking assumptions close
   `clarify`.
4. **Vertical slices.** A slice is something a person can be shown. If you cannot
   write its demo, it is not a slice.
5. **Small tasks.** A task over four hours hides an unknown.
6. **Evidence over assertion.** "It works" is a claim. A proof is a reproducible run
   against a known commit with an artifact someone else can read.
7. **Scope is a contract.** After `clarify`, the acceptance criteria are fingerprinted;
   changes require a client-approved change request. Until `slice` the plan is a draft
   (`egd rm` takes entries out freely); after it, dropping planned work needs one too.
8. **Waiting is a state.** Blocked work is recorded with a reason, so the board shows
   it instead of hiding it.

## The gates

**frame — do we know why?** Most rework starts with a solution nobody tied to a
problem. The brief forces a problem, an observable success signal, and an explicit
out-of-scope list. The repository map, signed by a person, stops planners from
inventing paths and conventions.

**clarify — do we know what?** Acceptance criteria in Given/When/Then are the unit of
agreement with the client and the unit of evidence later. Declaring test levels per
criterion here is the test strategy, decided before code makes it expensive.

**design — do we know how?** One approach, the alternatives that lost, and the failure
modes a user can hit. Decisions are recorded so review does not re-open them.

**slice — do we know the order?** The plan becomes a dependency graph that a tool can
check: coverage, cycles, task size, slice length, declared files and tests. Waves and
the critical path are computed, never hand-drawn.

**build — does it work?** Every task went through review (or a recorded solo bypass).
Submission ran its tests and checked that it only touched what it declared. Every
required proof passed on the current code; a proof run before the last code change
is stale and does not count.

**accept — does the client agree?** The client walks each slice's demo and records a
verdict under their own name. Serious open defects keep the gate closed.

**release — can it ship and be undone?** A rollback plan, no open assumptions, no
proposed decisions, no serious defects.

## Metrics that fall out

Because every transition is an event with a time and a person, the trail answers
questions nobody has to track by hand: how far off estimates run, how long work
waits for review, how often it bounces, how much scope grew through change requests,
and what share of defects escaped to UAT or production.
