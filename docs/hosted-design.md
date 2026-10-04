# Design note — hosted dashboard with client sign-off

Status: **proposal**, not built. `egd serve` (live, local) and `egd site` (static
export) cover viewing. This note is about the one thing they cannot do: let a client
sign for themselves.

## The problem

Today a PM records `egd uat S-1 --by "Acme PO" --pass` and `egd cr approve CR-001 --by
"Acme PO"`. The trail says the client agreed; in truth the PM typed the client's
name. Roles in `team.toml` stop the wrong *teammate* from signing, but not the right
name being typed by the wrong person. For freelancers, client signatures on scope
changes and acceptance are the most valuable records EGD produces, so they should be
real.

## Goals

- A client opens a link, signs in, sees the slice demo and its evidence, and records
  UAT pass/fail or approves/rejects a CR. The resulting event is attributable to an
  authenticated identity.
- The repository stays the source of truth. The hosted side never edits plans.
- Works for private client projects without exposing anything publicly.

## Non-goals

- Editing plans, moving tasks or passing build gates from the web.
- Replacing GitHub/GitLab as the code host.

## Shape

```
repo (.egd/)  ──CI: egd publish──▶  EGD host  ◀── client browser (magic-link sign-in)
     ▲                                  │
     └──── CI: egd pull-signatures ◀────┘   signed events become files in events/
```

1. **Publish.** CI runs `egd publish` on main: uploads the `dashboard.portfolio()` JSON plus the
   latest evidence for each feature, authenticated with a per-project token.
2. **View.** The host serves the same page (`console.html`, assembled by `web.page`), scoped
   per project, behind sign-in. Teammates and clients get role-scoped views (clients: their features,
   slices, evidence, CRs; no trail internals).
3. **Sign.** On a slice page, a client with role `client`/`po` can record UAT; on a CR,
   approve or reject. The host creates an event with
   `{type, slice|cr, by, at, identity: {email, provider}, signature}` where
   `signature` is an Ed25519 signature by the host over the canonical event body.
4. **Return.** CI (or `egd pull-signatures` locally) fetches pending signed events,
   verifies the host signature against the project's pinned public key, and writes
   them into `events/` as ordinary event files. Replay treats them like any other
   event; reports mark them "signed by client".
5. **Trust.** `egd lint` verifies every event that carries a signature. A CR approval
   without a valid host signature can be flagged (config: `require_signed_client = true`)
   so gates refuse unsigned client verdicts.

## Data and security

- Per-project publish token (write-only), per-user sessions via email magic links.
- Evidence uploads pass through the same redaction already applied to transcripts;
  the host stores only what `egd site` would export.
- Retention per project; deleting a project removes all uploads.
- No secrets ever leave the repository: `secrets.env` and `.auth/` are never read by publish.

## Open questions

- Self-hosted (single binary / container) first, or a shared hosted instance?
- Magic links only, or also Google/Microsoft sign-in for client organisations?
- Should a client be able to comment on a failing proof, creating a defect draft?

## Rough cost

Host service with sign-in, storage and signing: ~2–3 weeks for a first usable version;
`egd publish` / `egd pull-signatures` and signature verification in the CLI: ~3–4 days.
Worth starting once a pilot client actually wants to sign online.
