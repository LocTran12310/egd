---
name: egd-team
description: EGD team and client side — roles in team.toml, board, standup, change requests, defects, UAT, client reports, metrics, GitHub Issues sync. Use in repos with .egd/ — standup, ai đang làm gì, báo cáo khách hàng.
---

# EGD for teams and clients

Run the CLI as the `egd` skill says: `python3 <this skill's directory>/../../bin/egd`, else `egd`
on PATH. Everything here reads the same plan
files and event trail; nothing here can open a gate on its own.

## Setting a repo up for a team

1. `egd setup` creates `.egd/` (`config.toml`, `team.toml`, `map.md`, `.gitignore`, `secrets.env`).
2. Fill `team.toml` — `name`, optional `github` handle and `aliases`, `roles`:

```toml
[[member]]
name = "Linh"
github = "linh-dev"
roles = ["pm", "lead", "qa", "dev"]

[[member]]
name = "Acme PO"
roles = ["client"]
```

3. `[roles]` in `config.toml` says which roles may sign what (defaults: `gate` →
   pm/lead, `review` → dev/qa/lead/pm, `uat` and `cr_approve` → client/po).
   With no members listed anyone may sign anything — fine for solo work.
4. Commit `.egd/` (minus what its `.gitignore` excludes). One file per event, so
   teammates moving tasks on different branches never conflict.

## One backlog for everyone

```bash
egd standup [--hours 24]          # per person: what moved, what they hold; review queue; blocked
egd board [--html] [--quiet]      # .egd/BOARD.md (printed on a terminal) and a single-file .egd/board.html
egd ready                         # what can be picked up now, across features
egd serve [--host 0.0.0.0]        # live read-only dashboard (token generated when shared)
egd site --out <dir> [--no-evidence] [--feature x]   # static dashboard to host anywhere
egd sync github [--dry-run] --by <you>
```

The board and the dashboard (serve/site) show, across features: gate, health (🟢🟠🔴 with
reasons), progress by estimate, the review queue with waiting time, blocked tasks, CRs awaiting
the client, serious defects and who is on what, filterable by person; per feature, the brief,
slices with UAT, ACs with evidence, proof runs with transcripts and screenshots, and the trail.
**A site export contains plans, trail and evidence** — never publish a client project's export
on a public host; use access control or `--no-evidence`.

`egd sync github` mirrors each task of every sliced feature into an issue in `[github] repo`
(done_when checklist, ACs, touches, label `egd:<status>`, assignee from `team.toml`'s `github`,
closed when done), as whichever account `gh` is signed in as. The plan stays the source of
truth — edit the plan, not the issue. Run it after status changes, or in CI.

## The console: every repository at once

```bash
egd console --user <name> --scan <folder>   # identity + where repositories live (~/.egd/console.toml)
egd console                                 # → http://127.0.0.1:8780
egd console add <path> | list | remove <id>
egd console signer <id> --name "Minh Le"      # who actions in that repo are signed as (empty: default)
egd console --readonly                      # view only
```

The inbox gathers, across repositories, what needs a person (reviews, gates, CRs and UAT,
blocking assumptions, unsigned maps), what is stuck and hygiene (uncommitted trail, plan
errors). Browser actions are the CLI's own transitions, signed as `team.toml` names the
console user and checked against its roles; the event files still need committing. An agent
never drives the console for a person — it is the human's signing surface.

## Moving a repository from ai-dlc

`egd import aidlc --by <name> [--feature <part of name>]` turns each `.ai/features/` plan into
`.egd/features/<same name>/` with its history replayed; `.ai/` is never changed and re-runs skip what is imported.

## Scope, defects, acceptance

`egd uat … --by <client>`, `egd cr approve` and `egd pass` are signatures: run them only under
the name of the person who decided, in this conversation; otherwise ask.

```bash
egd cr open --by <pm> --title "Add VAT line" --reason "new regulation" --hours 3 --affects AC-1.1
egd cr approve CR-001 --by <client>        # or: egd cr reject CR-001 --by <client> --note …
egd cr list

egd bug open --by <who> --title "Total ignores qty" --severity major --found-in uat --ac AC-1.1
egd bug fix BUG-001 --by <dev> --task T-1.3  ·  egd bug close BUG-001 --by <qa>  ·  egd bug list

egd uat S-1 --by <client> --pass
egd uat S-1 --by <client> --fail --note "total shows before tax"
```

Changing ACs after `clarify` needs an approved CR (rule in the `egd` skill): edit the ACs (the CLI
warns and prints the `egd cr open` to run), then open the CR — approval is refused if they change
again before the client signs. Dropping planned work after `slice` is the same:
`egd rm T-1.2 --cr`, then `egd cr open`. Open
`critical`/`major` defects keep `accept` and `release` closed; `--found-in` drives leakage.

## Reporting

```bash
egd report [feature] [--days 7] [--lang en|vi] [--print]   # client status report → reports/status-<date>.md
egd metrics [feature] [--json]
egd pr [feature]                            # PR description with AC → evidence
egd release-note [feature] [--lang en|vi] [--print]
egd trail [feature] [--last 20]
```

Set the default language with `[report] lang = "vi"` in config.toml. The client report is for
a non-technical reader: health with reasons, stage, % of estimated work done, delivered this
period, in progress and next, open risks, CRs and quality (ACs with passing evidence, open
defects, share caught before UAT). Metrics come from the trail, nobody enters them: estimate
accuracy (tasks submitted with `--spent`), review wait, rework, review bypassed, scope growth
(approved CR hours vs. plan) and defect leakage (defects found in UAT or prod).

## CI

`egd lint` validates every feature's plan and trail and exits 1 on problems. A workflow can
run it on every PR and publish the board to the job summary — see
https://github.com/LocTran12310/egd/blob/main/docs/ci.md.

Weekly: `egd report <feature>` to the client, `egd metrics` for yourself.
