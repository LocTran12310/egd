# Proof kinds — every key

Common to all proofs:

| key | meaning |
|---|---|
| `id` | `P-n`, unique in the feature |
| `kind` | `http` · `cli` · `test` · `ui` |
| `title` | one line, shown in reports |
| `verifies` | AC ids this proof demonstrates |
| `envs` | env names to run in; default every enabled env (`test`/`cli`: `repo`) |
| `timeout` | seconds for the whole proof |

## http

Each `[[proof.step]]`:

| key | meaning |
|---|---|
| `name` | label in the transcript |
| `method` | default `GET` |
| `path` / `url` | `path` is appended to the env's `base_url`; `url` is absolute |
| `query` | table → query string |
| `headers` | table |
| `json` / `form` / `body` | request body (JSON, urlencoded, raw) |
| `capture` | `{ name = "$.json.path" }` or `{ name = "header:Location" }`; use later as `{name}` |
| `safe` | allow a non-GET on a `readonly` env (only if it changes nothing) |
| `expect.status` | int or list; default `[200, 201, 202, 204]` |
| `expect.json` | `{ "$.path" = value-or-operators }` |
| `expect.headers` | `{ Name = "substring" }` |
| `expect.contains` / `not_contains` | substrings of the body |
| `expect.matches` | regex (or list) over the body |
| `expect.max_ms` | response time ceiling |

JSON paths: `$.a.b`, `$.items[0].price`, `$.items[-1]`, `$['odd key']`.

## cli

| key | meaning |
|---|---|
| `run` | shell command, run from the repo root (or `cwd`) |
| `cwd` | relative directory |
| `env` | extra environment variables; `EGD_ENV`, `EGD_BASE_URL` and `EGD_OUT` (the run's folder) are always set |
| — | pictures the command writes to `$EGD_OUT` (png, jpg, webp) become the run's screenshots: transcript, console, `egd pr` |
| `stdin` | text piped to the command |
| `safe` | required to run on a `readonly` env |
| `expect.exit` | default `0` |
| `expect.contains` / `not_contains` / `matches` | over stdout + stderr |
| `expect.include_stderr` | default `true` |
| `expect.max_ms` | duration ceiling |

## test

| key | meaning |
|---|---|
| `command` | overrides `proof.test_command`; `{tests}` is replaced by `tests` |
| `tests` | paths passed to the command |

## ui

| key | meaning |
|---|---|
| `viewports` | names from `[ui.viewports]`; default `["desktop"]`. `desktop` and `mobile` are defined — list `mobile` to get it |
| `timeout_ms` | per action, default 15000 |
| `login` | `false`: skip the shared sign-in (`[[ui.login]]` / `[[env.<name>.login]]`) |

Each step: `goto` (path), `fill` (`{ selector = value }`), `click`, `press`,
`wait_for` (selector), `wait_ms`, `screenshot` (default true; a selector captures just that
element), `full_page` (default true), `safe`, and `expect`:

| expect key | meaning |
|---|---|
| `visible` | selectors that must be visible |
| `hidden` | selectors that must not be visible |
| `text` | `{ selector = "substring" }` |
| `url_contains` / `title_contains` | substrings |

Selectors are Playwright selectors (`text=Pending`, `#total`, `role=button[name="Pay"]`).

## Assertion operators

A plain value means equality. A table applies every operator in it:

| op | passes when |
|---|---|
| `eq` / `ne` | equal / not equal (numbers compared numerically) |
| `gt` `gte` `lt` `lte` | numeric comparison |
| `contains` | substring / list member / dict key |
| `matches` | regex search on a string |
| `exists` | `true`: path present · `false`: absent |
| `len` | length equals |
| `type` | `string` `number` `bool` `array` `object` `null` |
| `in` | value is one of a list |

Example: `"$.items" = { len = 2 }`, `"$.total" = { gt = 0, lt = 1000000 }`.

## Statuses

`pass` · `fail` (an assertion did not hold) · `error` (the proof itself is broken) ·
`unavailable` (env or variables missing, Playwright absent) · `refused` (a write on a
read-only env). Only `pass` on fresh code counts for `build`.
