---
name: egd-pr
description: Write a PR title and body from EGD's record — ACs, what was proven and where, what was not — in the repo's PR template, and attach UI proof screenshots through Claude in Chrome when the change is visible. Use only in repos with .egd/, when asked for a PR.
---

# EGD pull request

A PR description is a claim about the code. EGD already holds the evidence for that claim,
so the description is assembled from it — never from memory of what probably ran.

Run the CLI as the `egd` skill says: `python3 <this skill's directory>/../../bin/egd`, else `egd`
on PATH. Without `.egd/` this skill does not apply.

## 1. Gather

Run together:

```bash
egd pr <feature>                     # ACs with evidence status, slices, approved CRs, Verification
git fetch -q origin <base>
git log --oneline origin/<base>..HEAD
git diff --stat origin/<base>...HEAD
```

`<base>` is the branch name in `[pr] base` of `.egd/config.toml` (e.g. `main`, `staging`); without it, the repository's default branch
(`gh repo view --json defaultBranchRef -q .defaultBranchRef.name`). The feature is the one
whose task ids appear in the commits; ask if several do.

Look for a template: `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
`docs/pull_request_template.md` or `PULL_REQUEST_TEMPLATE/`.

## 2. Title

- `[pr] title` sets the shape, e.g. `{ticket}: {summary}`. `{ticket}` comes from the branch
  name, matched by `[pr] ticket`; when the branch has none, drop the prefix.
- Without config: follow recent PR titles (`gh pr list --state merged -L 10`), else an
  imperative summary under 72 characters.

## 3. Body

**With a template** — the template is the document. Keep its headings word for word, at
the same level, in the same order; write only under them; add no headings; remove its
placeholder comments. Map EGD's facts onto it:

- *what / why* sections → the feature outcome (`brief.md`) and what changed, in a few
  sentences; link the ticket and any PR this one depends on.
- *how verified / testing* sections → the **Verification** list from `egd pr`, as is: each
  proof or test run with where, when and on which commit; every gap as
  `Not verified: <what>`. Add checks run in this session or described by the user, said
  plainly. Nothing else — no "should work", no CI status you did not see.
- *screenshots* → the UI proof screenshots, attached as in step 5.
- *rollback / risk / deploy* sections → **Rollback** from `egd pr` (release.md). Not written yet?
  Draft it from the diff into release.md first — revert what, migrations, flags, data — and
  say it is a draft for a person to confirm.

**Without a template** — use `egd pr` output as the body, trimmed to what a reviewer needs.

## 4. Check, then show

- Every statement under a verification heading traces to a recorded run or this session.
- A stale proof is reported as stale, not as passing.
- Template headings match exactly; no leftovers.

Show the title and body to the user. Create or edit the PR (`gh pr create --base <base>` /
`gh pr edit --body-file`) only when they ask — it is published to the team.

## 5. Screenshots, when the change is visible

Attach them when the feature has `ui` proofs that passed on the PR's commit, or the template asks
for screenshots. Backend-only work gets none — its transcripts are in the Verification list.

1. **Pick.** `egd pr` lists them under **Screenshots**: the contact sheet (every screenshot of a
   run on one image) of each `ui` proof that passed on the current code. A step's own
   `<viewport>-NN-<step>.png` next to it can say more when one or two matter most.
2. **Look at each image before it leaves the machine.** Proof transcripts are redacted; pixels
   are not. A visible secret, token, personal or production customer data → do not attach it;
   say why.
3. **Ask.** List the files and where they go; upload only on a yes — the images become part of
   the PR. (Asking to create the PR with screenshots counts.)
4. **Upload through the user's browser** with Claude in Chrome (`mcp__claude-in-chrome__*`
   tools; load them first if deferred) — GitHub has no API for image attachments:
   - open the PR (`gh pr view <n> --json url -q .url`); the user is signed in there;
   - `find` the file input of the "Add a comment" box — never click "Attach files", it opens
     a native picker — and `file_upload` the absolute paths to that ref (under 10 MB per call);
   - wait until the comment textarea holds the `![…](https://github.com/user-attachments/assets/…)`
     lines, read them from the textarea, then **clear the box — never post the comment**;
   - put the lines under the template's screenshots heading (else a `### Screenshots` section at
     the end), each with a caption — `P-3 · desktop, mobile · <short sha>` — and
     `gh pr edit <n> --body-file <file>`.
5. **No Claude in Chrome** (tools absent, extension not connected, not signed in to GitHub), or the
   upload is refused (a path the browser tool may not read):
   list the files under the screenshots heading as `To attach: <path>` and tell the user to drag
   them into the description. Do not commit screenshots just to link them.
