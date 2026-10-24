# Changelog

## 0.1.7 — 2026-10-24

Fewer proof re-runs. `build` still asks for fresh evidence; what makes it stale narrows, so
`RULES` stays 2: the change only loosens it.

- **Parallel features keep their evidence:** a committed change makes a proof stale only when
  it touches the feature's footprint (its tasks' `touches` and `tests`, its proofs' `tests`) or
  a shared file `watch` names. A teammate's feature merged in, in its own files, no longer sends
  every other feature back to re-prove. Uncommitted changes count as before. A feature with a
  task that declares no `touches` keeps the strict rule: any commit makes it stale.
- **`watch`:** shared files whose change can break anyone. The built-in profiles name their
  manifests and lockfiles (`package.json`, `*.lock`, `pyproject.toml`, `go.mod`, …); a
  repository adds its own under `[proof] watch` in `.egd/config.toml`. `watch = ["**"]` is the
  old rule.
- **`inputs` on a proof:** path globs that replace the feature's footprint for that proof alone,
  so a one-line CSS fix goes stale for the proofs that read the CSS, not all 26 of them. They may
  reach beyond the footprint (shared code the proof depends on); `watch` still counts on top.
  Adding or changing `inputs` leaves existing runs fresh (it is not part of the definition hash);
  an empty or broken list is a lint error.
- **`egd verify --run` re-runs only what no longer counts:** proofs still passing on this code
  are left alone (`--all` runs them anyway). Before, every `verify --run` re-ran every proof.
- **Squash merges and rebases keep evidence:** each run records a fingerprint of the content it
  covered (its scope plus `watch`). When the run's commit is gone from a clone, the same content
  is the same evidence. Runs recorded before this, on a lost commit, stay stale as before.
- **`--jobs N` / `[proof] jobs`:** proofs run N at once; `serial = true` on a proof runs it alone,
  after the rest. Like `inputs`, `serial` is not part of the definition hash.
- **Quickest first, `--fail-fast`:** runs are ordered by how long each took last time (a guess
  by kind when never timed; each run now records `ms`), so a quick failure shows before a long
  e2e starts. `--fail-fast` starts nothing new once a required run has not passed and names what
  it left unstarted. Both flags work on `egd proof run` and `egd verify --run`.
- **`egd proof run --stale-only`:** runs only the proof × env pairs whose evidence does not count
  for the code as it is — stale, failed or never run — and says "nothing to run" (exit 0) when
  all of it does. Made for CI after a merge: no agent in the loop unless a proof fails.

## 0.1.6 — 2026-10-21

A reminder only: `frame` is neither closed nor reopened, and `RULES` stays 2.

- **Map drift:** `egd verify` has a **map** section, and `egd check frame` the same warnings, when
  `.egd/map.md` no longer matches the repository: a path it names in backticks is gone, a glob
  matches nothing, a `pnpm`/`yarn`/`npm run`/`bun run` script it quotes is in no package.json, or
  the feature's tasks touch a top-level folder it never mentions. Up to ten lines per kind.
  The heuristics prefer missing a drift to a false alarm: matching is loose (any folder of the
  repository, folders in between, the extension left off), and URLs, placeholders, commands,
  npm scopes, package subpaths, gitignored and generated files, worktrees and submodules are
  left out. `make`/`just`/Python targets are not checked yet (ROADMAP).
- The `egd-scout` agent refreshes just the lines reported; phases and the Vietnamese guide say
  the map is kept true as the repository changes shape.

## 0.1.5 — 2026-10-21

Checklists in the built-in stack profiles; no CLI check, no gate and no rules change.

- **Error catalog and permission matrix:** the `node`, `python` and `go` profiles ask route work
  (done_when) for every new error code in the repository's error catalog — code, HTTP status,
  cause, user message key — and every new route declared in the permission matrix with a test for
  the denied case (401/403); their review lists look for both. `react` reviews for API errors
  shown as raw text instead of the catalog's user message.
- **Route files:** node also matches `**/controller/**` and `**/handlers/**`; python `**/views.py`
  and `**/routers/**`; go gets a route rule (`**/handlers/**`, `**/handler/**`, `**/routes/**`,
  `**/*_handler.go`).
- **Hono** is detected as `node`. Detection only runs when a repository has no `[stack] profiles`
  yet (`egd setup`, `egd profile`): a stack already chosen does not change.
- **map.md** asks for `Error catalog:` and `Permission matrix:` under Conventions; the scout
  writes a path or `none`, and reviewers treat the profile items as reminders where it says `none`.
- Profiles of your own in `~/.egd/profiles` or a repository's `.egd/profiles` are not changed:
  copy the new items in if you want them.

## 0.1.4 — 2026-10-21

Warnings only, under every rules version: no gate closes on them and `RULES` stays 2.

- **AC cases:** `[[ac]]` takes `case = "happy" | "error" | "edge"` (`egd add ac --case`,
  `egd set AC-… case=error`). A standard or full feature whose functional ACs (every kind but
  `nfr`) name no `error` case gets one line — "only happy paths", or, when no AC says its case
  at all, a single hint to mark the error ones. Shown by `egd check clarify`, `egd verify` and
  `egd graph`. Any other value is a plan error (a warning for plans imported from ai-dlc).
- **Failure modes have an owner:** each first-level bullet under design.md's
  `## Failure modes` names the AC or decision that handles it (`- OTP expired → AC-4`) or says
  `out of scope` / `accepted risk` (`ngoài phạm vi` / `chấp nhận rủi ro`; more in
  `[design] failure_mode_marks_extra`). A bullet with neither, or with an id the plan does not
  have, is a warning in `egd check design` and in `egd verify` while `design` is ahead.
- Templates show both conventions; the reviewer agent checks failure modes against their AC ids;
  the tester agent and the `egd-proof` skill prove an error-case AC by driving the failure.

## 0.1.3 — 2026-10-21

- **Change requests work out their impact.** From the ACs a CR `--affects`, through the plan's
  graph (slice `covers` → its tasks; proof `verifies`) and the trail: slices whose UAT passed and
  must be walked again, tasks done / in progress / not started, every proof that must run again
  with its last state, and the reached tasks' estimates added up — a rough upper bound.
  `egd cr impact --affects …` previews it without recording anything; `egd cr open` prints it
  before "waiting for the client" and keeps the snapshot on the `cr_opened` event. A `--hours`
  well under the estimate prints a note, never a refusal. The client report (English and
  Vietnamese), the console's change-request table and its inbox show a one-line summary; CRs
  opened before keep showing as they did. Information only: no gate and no rules change.

## 0.1.2 — 2026-10-21

- **An `nfr` AC with no number only warns**, under every rules version: it no longer closes
  `clarify` under rules v2. On real plans every number-less nfr was precise as written
  ("no log line contains the payload", "every request is a GET"). Vague words still close
  `clarify` for features created under rules v2. `RULES` stays 2: the change only loosens it.

## 0.1.1 — 2026-10-21

**Rules v2.** Features created from this version on record `rules = 2`; every feature created
before keeps rules v1 and nothing about its gates changes.

- **Vague acceptance criteria:** a `then` that leans on a vague word — `fast`, `quick(ly)`,
  `user-friendly`, `easy`, `properly`, `as expected`, `etc.` … or `nhanh`, `thân thiện`, `dễ dùng`,
  `hợp lý`, `phù hợp`, `ổn định`, `v.v.` … — with no number in the sentence is reported with the
  word and a measurable rewrite. An AC with `kind = "nfr"` must name a number even without one.
  Under rules v2 this closes `clarify`; under rules v1, and for plans imported from ai-dlc, it is a
  warning in `egd check clarify`, `egd verify` and `egd graph`. `[ac]` in config.toml:
  `vague_words_extra` adds words, `vague_words_allow` drops defaults.

## 0.1.0 — 2026-10-04

First release.

- **Gates and tiers:** `frame`, `clarify`, `design`, `slice`, `build`, `accept`, `release`, computed
  from the plan and an append-only trail (one file per event); tiers `lite`, `standard`, `full`.
- **Planning without TOML:** `egd add` (assumption, ac, decision, slice, task, proof), `egd set`,
  `egd rm`; references checked when written, atomic writes, `egd add <kind> -h` with an example.
- **Scope lock:** ACs fingerprinted at `clarify`; change requests the client approves; dropping
  planned work after `slice` needs one too.
- **Tasks:** start, submit, accept, reject, solo, block — separation of duties, done_when attested,
  tests run on submit, drift checked by commit message. Local edits already there at `egd start`
  and left alone are not drift.
- **Proofs:** `test`, `cli`, `http` and `ui` (Playwright screenshots per viewport or of one
  element, a contact sheet per run); secrets redacted; read-only environments. `egd proof setup`
  installs Playwright once per machine; a shared `[[ui.login]]` signs every ui proof in.
- **Freshness:** a proof counts only on the code it ran on; changes outside the feature's
  footprint are named, not counted. `test_command = ""` for a repository with no test runner.
- **Verify and report:** `egd verify` (a last pass that never signs), `egd pr` (AC → evidence,
  what ran where, every gap), client reports and release notes in English or Vietnamese, metrics,
  standup, board, dashboards (`egd serve`, `egd site`).
- **Stack profiles:** `egd profile` — python, node, react and go built in; your own in
  `~/.egd/profiles`, a repository's own in `.egd/profiles` (it wins). Detected at `egd setup`; each
  gives a test command, conventions, a review checklist and done_when items by the files a task
  touches.
- **Agents and models per task:** `agent` (builder, tester, person) and `model` (haiku, sonnet,
  opus, fable) on a task, or picked from its size and risk; shown by `egd ready`, `egd graph` and
  the board, recorded on `egd start --model`.
- **Closing without release:** `egd close --as dropped|research|superseded --reason` for a feature
  that stops — no gate passed, open tasks recorded, out of the inbox; `--undo` reopens it.
- **Client and quality:** UAT per slice; defects with severity and where they were found.
- **Team:** roles in `team.toml`; `--by` defaults to you and is echoed; one writer per repository
  at a time; GitHub Issues sync; `egd import aidlc` from ai-dlc.
- **Console:** every repository on one screen — an inbox of what needs a person, acting in place
  (signed per repository, role-checked), board, people, approval trail, an in-app guide in English
  and Vietnamese; `--lan` to share it, `--service` to keep it running, or Docker.
- **Setup:** `egd setup` (`--local` keeps `.egd/` on this machine, `--claude` offers the plugin to
  the team); bare `egd` shows the next step, `egd <feature>` a feature's status.
- **Install, update, uninstall:** a one-line installer (uv or pipx, plus the plugin), `egd update`,
  `egd uninstall` (lists, asks, keeps every repository's `.egd/`). macOS and Linux.
- **Claude Code plugin:** skills `egd`, `egd-proof`, `egd-team`, `egd-commit`, `egd-pr`,
  `egd-review`, `egd-verify`; agents `egd-scout`, `egd-builder`, `egd-tester`, `egd-reviewer`.
- **Light:** standard library only, plain files in your repository, no server or account.
