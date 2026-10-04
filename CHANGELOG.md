# Changelog

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
