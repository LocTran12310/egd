---
name: egd-commit
description: Commit an EGD task's work — stage only its files, name the task id; push only when asked, never force-push. Use only in repos with .egd/, when asked to commit or push.
---

# EGD commit

EGD attributes code to a task by its id in commit messages: `egd submit` checks that a
task only changed what it declared (`touches`, `tests`), and it can only see the commits
that name the task. A commit without the id is work EGD cannot account for.

Run the CLI as the `egd` skill says: `python3 <this skill's directory>/../../bin/egd`, else `egd`
on PATH. Without `.egd/` this skill does not apply.

## 1. Know what is being committed

Run together:

```bash
git status --porcelain --untracked-files=all
git diff HEAD --stat
git log --oneline -12
egd board --print    # who holds which task (or: egd status <feature>)
```

Find the task in `doing` that this work belongs to (holder = the user, or the one the
user names). Read its `touches` and `tests` in `.egd/features/<feature>/plan.toml`.
If no task is in progress, ask which task this is — or whether it is work outside EGD.

## 2. Choose the files

Stage paths explicitly, one by one. Never `git add -A`, `git add .` or `git commit -a`.

- **In:** files the task declares (`touches`, `tests`) that the diff changed.
- **Ask first:** changed files outside the task's footprint. Either they belong to another
  task (leave them), or the task needs re-planning (`egd set T-x touches=...`), or they are
  incidental (they will show as scope drift on submit, with a reason).
- **Never:** `.env*`, `.egd/secrets.env`, `.egd/.auth/`, keys, tokens, credentials, large
  binaries, build output, `.egd/features/*/runs/` unless the team commits evidence.
- **The trail:** `.egd/` changes (events, plan) go in their own commit (`egd: trail`) — only
  when the team shares `.egd/`. If `git check-ignore -q --no-index .egd/config.toml` succeeds,
  EGD stays on this machine (`egd setup --local`, or a .gitignore): stage nothing under `.egd/`,
  not even files git already tracks there. If bare `egd` warns that git still tracks some, tell
  the user (`git rm -r --cached .egd` is their call) — do not run it.

Say what you left out and why.

## 3. Write the message

- Start from the repository's own style (`git log`): tense, prefixes, length.
- Name the task id (`T-1.1`) — as a prefix, scope or trailer, whichever fits that style.
  If the branch carries a ticket (see `[pr] ticket` in `.egd/config.toml`), keep it too.
- Say **why** in the subject when it fits; details in the body only if they help a reviewer.
- Attribution trailers: follow the repository and the user's instructions — add none the
  repository does not use.

Pass the message with a heredoc so it survives quoting:

```bash
git commit -m "$(cat <<'EOF'
T-1.1: keep the date a user picks as a calendar day
EOF
)"
```

## 4. Hooks, then push only if asked

- A pre-commit hook fails: read it, fix the cause, re-stage, make a **new** commit (the
  failed one never happened — do not `--amend`). Never `--no-verify` unless the user says so.
- Push only when the user asked to push — to the branch's upstream (`git push`, or
  `git push -u origin HEAD` the first time). Otherwise stop after the commit.
- Rejected because the remote moved: **stop** and tell the user. Never force-push, never
  rebase someone else's work without being asked.

## 5. Report

Two or three sentences: the commit (hash, message), the files in it, what was left out,
and where it was pushed (if it was). If the task is finished, suggest the next step:
`egd submit <task> --by <name> --confirm` (or the `egd-verify` skill before a gate).
