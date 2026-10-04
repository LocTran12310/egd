# Roadmap

The backlog for EGD itself. Open an issue before starting anything here.

## In 0.1
- Gates, tiers, event trail with per-feature sequence numbers, task lifecycle, scope lock and CRs,
  defects, UAT
- Proofs: `http`, `cli`, `test`, `ui` (with contact sheet); redaction; freshness
- `egd verify` — one last pass before a gate (plan, task tests, required proofs, every gate ahead)
- Team: roles, board, standup, GitHub Issues sync, client report in English and Vietnamese, metrics
- Plan editing by command: `egd add`, `egd set`
- Dashboard: `egd serve` (live), `egd site` (static export), `egd board --html`
- Console: every repository on one screen — inbox, pickable work, board, features, people,
  approval trail, a page per repository; in-place actions; English and Vietnamese; a guide
- Import from ai-dlc (one-way, read-only)
- Stack profiles (python, node, react, go; yours in ~/.egd/profiles, a repository's own in
  .egd/profiles): test command, conventions, review checklist, done_when by the files a task touches
- Claude Code plugin: skills `egd`, `egd-proof`, `egd-team`, `egd-commit`, `egd-pr`, `egd-review`,
  `egd-verify`; four subagents. Docker image for the console

## Next
- [ ] Pilot on a real client project; fix what hurts; publish the numbers as a case study
- [ ] Publish to PyPI as `egd-cli` (the `egd` name is taken)
- [ ] Automated tests for the web UI (markdown rendering, tables, dialogs; a headless page load)
- [ ] Live-test `egd sync github` on a real team board; GitHub Projects (v2) status field
- [ ] `egd new --from-issue <url>` — seed a brief from an issue
- [ ] Estimate calibration: per-person multiplier suggested from `metrics`
- [ ] Jira / Linear sync adapters

## Not in scope
EGD is a delivery process for the people who build the software — plan, build, prove, release.
Project-management tooling stays out: deadlines and forecasts, budgets and billing, portfolio
planning across clients, risk registers, resource planning. Use a PM tool for those.

## Later
- [ ] Hosted dashboard with client sign-off and a signed-in identity per person —
      see [docs/hosted-design.md](docs/hosted-design.md)
- [ ] Signed events for tamper evidence
