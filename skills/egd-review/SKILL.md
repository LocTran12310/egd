---
name: egd-review
description: Review a PR or branch against the team's standards, the map and the feature's ACs; findings by severity. Read-only. Use only in repos with .egd/ — review PR.
---

# EGD review

A review answers two questions: does this change do what the feature agreed, and does it
do it the way this team writes code. EGD knows the first (acceptance criteria, declared
scope, evidence); the team's standards say the second. Without `.egd/` this skill does not apply.

This skill reads and reports. It never approves, merges, comments on GitHub, runs
`egd accept`/`egd reject`, or edits files — unless the user asks for that step.

## 1. The change

A PR number or URL:

```bash
gh pr view <n> --json number,title,body,author,baseRefName,headRefName,files,additions,deletions,commits
gh pr diff <n>
```

A local branch: `git fetch -q origin <base>`, then `git log --oneline origin/<base>..HEAD` and
`git diff origin/<base>...HEAD`, where `<base>` is the branch in `[pr] base` of `.egd/config.toml`
or the default branch.

## 2. The yardsticks

1. **Team standards** — each entry of `[review] standards` in `.egd/config.toml`: read files
   from the repository; fetch URLs with whatever tool can open them (a Notion, Confluence
   or web connector). If one cannot be read, say so in the report — never review against a
   remembered version.
2. **Repository conventions** — `.egd/map.md` (naming, structure, the conventions table), and
   the stack profile: `egd profile` lists its conventions and a "review for" checklist.
3. **The feature** — find it from task ids in the commits or the branch:
   `egd status <feature>`, `plan.toml` (the tasks' `touches`, `done_when`; the ACs the slice
   covers), `design.md` (*Failure modes*), and `egd proof list <feature>`.

## 3. Read the diff for

- **Scope** — files outside the tasks' `touches`/`tests` (also what `egd submit` flags).
- **Behaviour** — does the code produce each covered AC's *then*? Failure modes handled?
- **Tests** — do they assert behaviour, or only that code runs? Is each AC proven somewhere?
- **Security** — input validation at boundaries, authorization on new endpoints and
  screens, secrets in code or logs, injection (SQL, shell, HTML), unsafe deserialization,
  new dependencies.
- **Standards and conventions** — every rule from step 2, quoted by name when flagged.
- **Upkeep** — dead code, commented-out blocks, unused imports, `any`/untyped values
  without reason, premature abstraction, missing error handling at system boundaries.

## 4. Report

```
## Overview
<title> · <author> · <head> → <base> · <files> files, +<add>/−<del>
<what it does and why, one paragraph>  ·  feature <slug>, tasks <ids>

## Look closest at
1. path/to/file.ts (function) — why: risk, complexity, regression surface

## Findings
**[BLOCKER] path/to/file.ts:42** — what is wrong (rule or AC it breaks)
> the fix, or a better shape
**[SHOULD-FIX] …**
**[NIT] …**

## Done well
2–3 specific things worth repeating.

## Verdict
Approve / Request changes / Needs discussion — the one main reason.
```

Severity: **BLOCKER** — wrong behaviour, an AC not met, a security hole, or a rule the team
marks mandatory; **SHOULD-FIX** — a real deviation that will cost later; **NIT** — style or
naming, not blocking. Every finding has a `file:line` and a concrete fix.

For a single EGD task in `review`, the `egd-reviewer` agent does the same check narrowly;
its recommendation can be pasted into `egd reject --reason`.
