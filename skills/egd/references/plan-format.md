# plan.toml, brief.md, design.md, release.md

A feature lives in `.egd/features/<YYYY-MM-DD>-<name>/`:

```
brief.md      why            — frame
plan.toml     what + order   — clarify, design, slice
design.md     how            — design (standard/full)
proof.toml    evidence       — see the egd-proof skill
release.md    rollback       — release
events/       the trail      — written only by egd, committed
runs/         proof output   — gitignored
reports/      generated      — gitignored
```

## plan.toml

```toml
[feature]
title = "Checkout"
tier = "standard"            # lite | standard | full
client = "Acme"

[[assumption]]
id = "A-1"
text = "Prices are VND only"
confidence = "medium"        # low | medium | high
blocking = true              # being wrong forces rework
status = "open"              # open | confirmed | rejected
resolution = ""              # required once not open: who confirmed, how

[[ac]]
id = "AC-1.1"                # AC-<story>.<n>
story = "As a buyer I want to see my total"
given = "a cart with 2 × A1 at 150,000"
when = "the buyer checks out"
then = "the order total is 300,000 and status is pending"
levels = ["integration", "e2e"]   # unit | integration | e2e | manual — the test strategy
kind = "functional"          # or "nfr" for performance, security, accessibility

[[decision]]
id = "D-1"
title = "Totals are computed on the server"
status = "accepted"          # proposed | accepted | superseded
context = "…"
choice = "…"
rejected = ["compute in the browser"]
consequences = "…"

[[slice]]
id = "S-1"
title = "Checkout creates a pending order"
covers = ["AC-1.1"]
demo = """
1. Add 2 × A1 to the cart
2. Check out
3. Confirmation shows 300,000, status Pending
"""

[[task]]
id = "T-1.1"
slice = "S-1"
title = "POST /orders computes the total"
estimate_h = 3               # ≤ limits.task_hours
depends_on = []              # real data/contract dependencies only
touches = ["src/orders/**"]  # globs; drift outside these is refused at submit
tests = ["src/orders/orders.test.ts"]   # run on submit when proof.test_command is set
done_when = ["returns 201 with total and status", "422 on an empty cart"]
agent = "builder"            # optional: builder | tester | person — default picked from touches
model = "opus"               # optional: haiku | sonnet | opus | fable — default picked from size and risk
```

Rules `egd graph` enforces: unique ids; every AC covered by a slice; every slice
has a demo and at least one task; every task belongs to a slice, has done_when and
a positive estimate within the limit; dependencies exist and form no cycle; the
longest chain inside a slice stays within `limits.slice_hours`; standard/full ACs
declare `levels`; full tier needs tests on every task and a proof for every AC.

## brief.md

Four `##` sections, all required and free of TODO/TBD:
`Problem`, `Outcome`, `Success signal`, `Out of scope`.

## design.md

`Approach`, `Alternatives considered`, `Failure modes`.

## release.md

`Rollback` (required), `Checks after deploy` (recommended).

HTML comments (`<!-- -->`) are ignored by every check, so template hints can stay.
