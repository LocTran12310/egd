---
name: egd-proof
description: EGD evidence — picks the right kind per AC by itself (http, cli, test, ui screenshots), writes and runs the proofs that gate `build`. Use in repos with .egd/ for proof, evidence, screenshots, kiểm chứng, bằng chứng.
---

# EGD proof

A proof is a reproducible claim: *this acceptance criterion holds, on this code, in
this environment*, with an artifact a reviewer or client can read. The `build` gate
opens only when every required proof passed on the current code.

Run the CLI as the `egd` skill says: `python3 <this skill's directory>/../../bin/egd`, else `egd`
on PATH.

## Choose the evidence yourself

Do not ask which kind to use — decide per AC, from what the AC promises and what the code changed:

```bash
egd proof list <feature>                         # what exists, and which ACs have nothing
git diff --name-only origin/<base>...HEAD        # what changed (or each task's `touches`)
```

| The AC's *then* / the change | Kind | Evidence |
|---|---|---|
| Someone **sees** something: a screen, a layout, a rendered formula, a chart, a message, a mobile view — or the change touches components, pages, templates, CSS | `ui` | a screenshot per step per viewport, with assertions |
| An endpoint answers: REST/GraphQL, webhook receiver | `http` | request, response, assertion table |
| A job, migration, script, import, CLI; data in a given state afterwards (`psql -c`, `mysql -e` on a safe DB) | `cli` | command, exit code, output |
| A rule or calculation the test suite covers | `test` | test command output |

Rules:

- **Visible means `ui`.** A unit test shows the logic; only a screenshot shows the client what
  they will see. A visual change gets a `ui` proof — next to its `test` proof, not instead of it.
- **Backend needs no screenshot.** An HTTP transcript showing the right total *is* the evidence.
- **One AC can need two kinds** — the endpoint (`http`) and the page that shows its result (`ui`).
- Every AC gets at least one proof (`full` requires it). A proof shows what it claims:
`npx tsc --noEmit` proves the types, not that an error toast appears. Say which kind you chose and why in one
  line per AC, then add them — the user can still say otherwise.
- **Never fall back to a weaker kind** because the right one is not set up. If `ui` needs
  Playwright or an env, set it up (below, with the user's go-ahead) or report
  `Not verified: <what someone sees>` — not a green test standing in for a screenshot.

## Adding a proof

The common shapes have a command — it picks the next `P-n` id, checks the AC ids and env names,
and writes a valid entry into `.egd/features/<feature>/proof.toml`:

```bash
egd add proof --by <you> --kind test --verifies AC-1.1 --title "Order total" --tests tests/orders.test.ts
egd add proof --by <you> --kind cli --verifies AC-1.2 --title "Settings are valid" \
    --run "python manage.py check" --expect-exit 0 --expect-output "no issues"
egd add proof --by <you> --kind http --verifies AC-1.1 --title "Orders answer" --path /api/orders \
    --expect-status 200 --envs local,staging
egd add proof --by <you> --kind ui --verifies AC-1.1 --title "Confirmation shows the total" \
    --path /orders/latest --expect-visible "text=300,000" --viewports desktop,mobile
egd rm P-3 --by <you>                # remove one (after `slice`: --cr, then `egd cr open`)
```

An http or ui proof added this way is one request or one page — a start. Assert the values the AC
names by editing it in proof.toml: more steps, `capture`, `expect.json`; for ui `click`, `fill`,
`wait_for`, `expect.text`.

## proof.toml

```toml
[[proof]]
id = "P-1"
kind = "http"
title = "Checkout returns a pending order with the right total"
verifies = ["AC-1.1"]
envs = ["local", "staging"]          # default: every enabled env (test/cli: the repo itself)

[[proof.step]]
name = "sign in"
method = "POST"
path = "/api/auth/login"
json = { email = "${QA_EMAIL}", password = "${QA_PASSWORD}" }
capture = { token = "$.accessToken" }        # names matching token/secret are redacted

[[proof.step]]
name = "create order"
method = "POST"
path = "/api/orders"
headers = { Authorization = "Bearer {token}" }
json = { items = [{ sku = "A1", qty = 2 }] }
expect.status = 201
expect.json = { "$.total" = 300000, "$.status" = "pending", "$.id" = { exists = true } }
expect.max_ms = 800
```

Read `references/kinds.md` for every key of every kind and the assertion operators
(`eq ne gt gte lt lte contains matches exists len type in`).

**An assertion is what makes it evidence.** A step that only checks "200 OK" proves
the server answered, not that the total is right. Assert the value the AC names.
A known bug is recorded honestly as a proof that asserts the correct behaviour and
fails — the transcript becomes the bug report.

## Environments and secrets

`.egd/config.toml`:

```toml
[env.local]
base_url = "http://localhost:3000"

[env.staging]
base_url = "${STAGING_URL}"

[env.prod]
base_url = "${PROD_URL}"
required = false        # reported, never blocks build
readonly = true         # http: only GET/HEAD/OPTIONS; cli/ui: only steps marked safe = true
```

`${NAME}` resolves from the process environment or `.egd/secrets.env` (gitignored).
A proof whose variables are missing reports **unavailable** with the missing names —
it never runs half-configured. Unavailable on a `required` env keeps `build` closed;
make the env optional or provide the values.

Secrets never reach transcripts: substituted values, sensitive headers
(Authorization, Cookie, X-Api-Key) and JSON keys like `password`/`token`/`secret`
are written as `***`.

## Running

```bash
egd proof run [feature] --by <you> [--env staging] [--only P-1,P-2]   # an unknown env or id is an error
egd proof list [feature]          # latest status per proof × env, stale marked
egd proof report [feature]        # reports/evidence.md: AC → evidence table + run links
egd proof login --env staging     # ui: sign in once by hand, session saved to .egd/.auth/
```

Freshness: each run records the commit. If anything outside `.egd/` changed since —
or the tree was dirty when it ran — the run is **stale** and does not count for
`build`. So: commit the code, run proofs, pass `build`; commit the trail when `.egd/` is shared
(never when it is local).

## UI proofs

```toml
[[proof]]
id = "P-3"
kind = "ui"
verifies = ["AC-1.2"]
viewports = ["desktop", "mobile"]    # default ["desktop"]; desktop 1440×900, mobile 390×844; more in [ui.viewports]

[[proof.step]]
name = "confirmation"
goto = "/orders/latest"
wait_for = "text=Pending"
expect = { visible = ["text=300,000"], hidden = ["text=Error"] }
```

Needs Playwright — `egd add proof --kind ui` says when it is missing. Once per machine, with
the user's go-ahead (it downloads Chromium, ~150 MB):

```bash
egd proof setup        # Playwright in ~/.egd/ui; every repository uses it, nothing to configure
```

(`[ui] python` or `EGD_UI_PYTHON` point at another Python with Playwright instead.)
The app must be running at the env's `base_url` — start it if it is not, and say so.

**Signing in — once, for every ui proof.** Write the login in `.egd/config.toml`; it runs
first in every viewport, credentials come from `.egd/secrets.env`:

```toml
[[ui.login]]                      # or [[env.staging.login]] when an env signs in differently
goto = "/login"
fill = { "input[name=email]" = "${QA_EMAIL}", "input[name=password]" = "${QA_PASSWORD}" }
click = "button[type=submit]"
wait_for = "text=Dashboard"
```

Each viewport is a fresh browser that signs in itself — no saved session to expire, no
rotating refresh token spent by the first viewport. A proof of the login page itself sets
`login = false`. `egd proof login --env <name>` (a person signs in by hand, the session is
saved) is the fallback for logins a script cannot do: SSO, captcha, a code by SMS.

`screenshot = "<selector>"` on a step captures just that element, scrolled into view — the
preview below an editor on a phone, one chart, one toast. Without Playwright, UI proofs report
unavailable and everything else still works. Console errors fail a
step unless matched by `[ui] ignore_console = ["regex"]`. Each UI run also writes a
`contact-sheet.png` — every screenshot of the run on one image, ready for the PR.

## The slice's demo is the script

A slice's `demo` in plan.toml is what the client walks at UAT. When its ACs are something a person
sees, the `ui` proof walks the same path first, so UAT confirms what the proof already showed:

1. One `ui` proof per slice: `verifies` = the slice's visible ACs, `viewports` = desktop and mobile
   when the screen is used on phones.
2. Each demo step becomes a `[[proof.step]]`, in order, named after the step so the transcript
   reads like the demo: "Open Deals" → `goto`; "fill the form" → `fill`; "click Export" → `click`
   + `wait_for` what follows.
3. Each step's `expect` asserts what the AC's *then* promises at that point — the total, the toast,
   the empty state — with `visible`/`hidden`/`text`, not just that the page loaded.
4. A step a script cannot do — an email arriving, a phone call, another system — stays for UAT:
   write it in the proof's title or the PR as `Not verified: <step> — walked at UAT`.

Keep them in step: when the demo changes, the proof changes with it.

## For the PR and the client

`egd pr` prints a PR description with the AC → evidence table. Screenshots live in
`runs/<run>/<P-n>@<env>/` (`contact-sheet.png` holds a whole run), gitignored by default; the
`egd-pr` skill attaches them to the pull request.
