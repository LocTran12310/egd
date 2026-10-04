"""Files written by `egd setup` and `egd new`."""

import json
from pathlib import Path

from .planfile import toml_value

MARKETPLACE = "LocTran12310/egd"


def setup_claude(root: Path, repo: str = MARKETPLACE) -> Path:
    """Make Claude Code offer the EGD plugin (skills egd, egd-proof, egd-team + agents) to
    everyone who opens this repository. Merges into .claude/settings.json; other keys stay."""
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON — fix it first") from exc
    data.setdefault("extraKnownMarketplaces", {})["egd"] = {"source": {"source": "github", "repo": repo}}
    data.setdefault("enabledPlugins", {})["egd@egd"] = True
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def setup_repo(root: Path) -> list[Path]:
    """Create `.egd/` with its starter files; never overwrites. Returns what was written."""
    base = root / ".egd"
    base.mkdir(exist_ok=True)
    (base / "features").mkdir(exist_ok=True)
    written = []
    for name, text in (("config.toml", CONFIG), ("team.toml", TEAM), ("map.md", MAP),
                       (".gitignore", GITIGNORE), ("secrets.env", SECRETS)):
        p = base / name
        if not p.exists():
            p.write_text(text, encoding="utf-8")
            written.append(p)
    return written

def keep_local(root: Path) -> str:
    """Add this folder's .egd/ to .git/info/exclude — ignored on this machine only, the repository
    unchanged. Anchored (`/app/.egd/`), so a sibling project's shared .egd/ in a monorepo stays shared."""
    from .util import git
    rel = (git(root, "rev-parse", "--git-path", "info/exclude") or "").strip()
    prefix = (git(root, "rev-parse", "--show-prefix") or "").strip()
    line = f"/{prefix}.egd/"
    exclude = root / rel
    text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if line not in text.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(text + ("" if not text or text.endswith("\n") else "\n")
                           + f"# EGD — on this machine only\n{line}\n", encoding="utf-8")
    tracked = (git(root, "ls-files", "--", ".egd") or "").splitlines()
    if tracked:
        return (f".egd/ is excluded ({rel}), but git already tracks {len(tracked)} file(s) in it — "
                "`git rm -r --cached .egd` stops that (the files stay on disk)")
    return f".egd/ stays on this machine ({rel}) — nothing of EGD is committed"


def set_stack(root: Path, names: list[str], test_command: str | None = None) -> None:
    """Write `[stack] profiles` (and, when given and unset, `[proof] test_command`) into config.toml,
    keeping every other line as it is."""
    import re
    path = root / ".egd" / "config.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = "profiles = " + toml_value(list(names))
    if re.search(r"^\[stack\]\s*$", text, re.M):
        head = re.search(r"^\[stack\]\s*$", text, re.M)
        nxt = re.search(r"^\[", text[head.end():], re.M)
        end = head.end() + (nxt.start() if nxt else len(text) - head.end())
        body = text[head.end():end]
        body = re.sub(r"^profiles\s*=.*$", line, body, flags=re.M) if re.search(r"^profiles\s*=", body, re.M) \
            else "\n" + line + body
        text = text[:head.end()] + body + text[end:]
    else:
        text = text.rstrip("\n") + "\n\n# Stack profiles: test command, conventions, review checklist, done_when (`egd profile`)\n" \
            f"[stack]\n{line}\n"
    if test_command and not re.search(r"^test_command\s*=", text, re.M):
        new = "test_command = " + toml_value(test_command)
        if re.search(r"^# test_command = .*$", text, re.M):
            text = re.sub(r"^# test_command = .*$", new, text, count=1, flags=re.M)
        elif re.search(r"^\[proof\]\s*$", text, re.M):
            text = re.sub(r"^\[proof\]\s*$", "[proof]\n" + new, text, count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")


CONFIG = """\
# EGD configuration — the team's shared settings (committed unless `egd setup --local`).
# Secrets and URLs you do not want here go in .egd/secrets.env (never committed)
# and are referenced as ${NAME}.

[limits]
task_hours = 4        # a task bigger than this must be split
slice_hours = 16      # longest dependency chain inside one slice

[proof]
# Runs a task's `tests` on `egd submit` and backs `kind = "test"` proofs.
# {tests} is replaced by the task's test paths.
# test_command = "pnpm vitest run {tests}"
# test_command = ""      # no test runner in this repository: say so, and EGD stops asking for tests
timeout = 600

[report]
lang = "en"           # client reports and release notes: en | vi

# Who may sign what. Names and roles live in team.toml.
[roles]
gate = ["pm", "lead"]                 # pass frame … build, release
review = ["dev", "qa", "lead", "pm"]  # accept / reject a task
uat = ["client", "po"]                # UAT verdicts and the accept gate
cr_approve = ["client", "po"]         # approve or reject change requests

# Environments proofs run against. `required` ones gate `build`.
# [env.local]
# base_url = "http://localhost:3000"
#
# [env.staging]
# base_url = "${STAGING_URL}"
#
# [env.prod]
# base_url = "${PROD_URL}"
# required = false
# readonly = true      # only GET/HEAD/OPTIONS and steps marked safe = true

# [ui]
# python = "/path/to/python"   # only to use another Python with Playwright than `egd proof setup`'s
# storage_state = ".egd/.auth/{env}.json"  # saved by `egd proof login --env <name>`
#
# How ui proofs sign in — first, in every viewport; values from secrets.env:
# [[ui.login]]
# goto = "/login"
# fill = { "input[name=email]" = "${QA_EMAIL}", "input[name=password]" = "${QA_PASSWORD}" }
# click = "button[type=submit]"
# wait_for = "text=Dashboard"

# [github]
# repo = "owner/name"   # `egd sync github` mirrors tasks into this repo's issues

# How this repository ships — read by the egd-commit, egd-pr and egd-review skills.
# [pr]
# base = "main"                       # branch PRs target; diffs are taken against origin/<base>
# title = "{ticket}: {summary}"       # {ticket} comes from the branch name, e.g. ABC-123
# ticket = '[A-Z]+-\\d+'              # how a ticket id looks in branch names (a literal '…' string)
#
# [review]
# standards = ["docs/standards.md"]   # files or URLs with this team's coding rules
"""

TEAM = """\
# Who is on the team. With no members listed, anyone may sign anything.
# Roles used by default: pm, lead, dev, qa, client, po.

# [[member]]
# name = "Linh"
# github = "linh-dev"
# aliases = ["Linh Nguyen"]   # other spellings, e.g. her git user.name
# roles = ["pm", "lead", "qa", "dev"]
#
# [[member]]
# name = "An"
# github = "an-dev"
# roles = ["dev"]
#
# [[member]]
# name = "Acme PO"
# roles = ["client"]
"""

MAP = """\
# Repository map

reviewed_by:
<!-- A person writes their name above after reading this map and confirming it is
     true. The `frame` gate (standard/full tier) refuses until they do. -->

## Stack
<!-- languages, frameworks, versions, package manager -->

## Layout
<!-- top-level directories and what lives where -->

## Run, test, deploy
<!-- the exact commands -->

## Conventions
<!-- naming, error handling, state, API style — things a newcomer would get wrong -->

## Environments
<!-- local / staging / prod URLs (no secrets), how auth works -->

## Unknowns
<!-- what could not be determined from the code -->
"""

GITIGNORE = """\
secrets.env
.auth/
.lock
BOARD.md
board.html
features/*/runs/
features/*/reports/
"""

SECRETS = """\
# Never committed. Values here fill ${NAME} in config.toml and proof.toml.
# STAGING_URL=https://staging.example.com
# QA_EMAIL=qa@example.com
# QA_PASSWORD=
"""

BRIEF = """\
# {title}

## Problem
<!-- Whose problem, what it costs them today. -->

## Outcome
<!-- What is true for the user once this ships. One paragraph. -->

## Success signal
<!-- How you will know it worked: a number, an event, a sentence from the client. -->

## Out of scope
<!-- What this deliberately does not do. Saying it now prevents a CR later. -->
"""

DESIGN = """\
# Design — {title}

## Approach
<!-- The shape of the solution: components, data, contracts. -->

## Alternatives considered
<!-- What else was on the table and why it lost. -->

## Failure modes
<!-- What goes wrong and what the user sees: validation, not found, conflict,
     upstream down, permission denied, timeout. -->
"""

RELEASE = """\
# Release — {title}

## Rollback
<!-- The exact steps to undo this release, and whether data migrations reverse. -->

## Checks after deploy
<!-- What to look at in the first hour: dashboards, logs, a smoke proof. -->
"""

class _Toml(str):
    """A TOML template: `{title}` lands in comments (kept to one line), `{title_toml}` is the
    title as a TOML string, escaped — a backslash or quote in a title cannot break plan.toml."""

    def format(self, *args, **kwargs):
        title = str(kwargs.get("title", ""))
        kwargs.update(title=" ".join(title.split()), title_toml=toml_value(" ".join(title.split())))
        # `tier = "…"` is padded so its comment lines up with the one after `client = ""`
        kwargs.setdefault("tier_pad", " " * max(1, 16 - len(str(kwargs.get("tier", "")))))
        return str.format(self, *args, **kwargs)


PLAN = _Toml("""\
# Plan for {title}. Edited by people; read by `egd`.
# Ids: A-n assumptions · AC-n.n criteria · D-n decisions · S-n slices · T-n.n tasks

[feature]
title = {title_toml}
tier = "{tier}"{tier_pad}# lite | standard | full
client = ""              # who accepts this work

# ---- clarify -----------------------------------------------------------
# Every question you did not ask is an assumption. Blocking = being wrong
# forces rework. Resolve blocking ones before `clarify`.
#
# [[assumption]]
# id = "A-1"
# text = "Prices are in VND only"
# confidence = "medium"   # low | medium | high
# blocking = true
# status = "open"         # open | confirmed | rejected
# resolution = ""         # required once confirmed or rejected

# [[ac]]
# id = "AC-1.1"
# story = "As a buyer I want to see my order total"
# given = "a cart with 2 × A1 at 150,000"
# when = "the buyer checks out"
# then = "the order total is 300,000 and the order is pending"
# levels = ["integration", "e2e"]   # test strategy: unit | integration | e2e | manual

# ---- design ------------------------------------------------------------
# [[decision]]
# id = "D-1"
# title = "Compute totals on the server"
# status = "accepted"     # proposed | accepted | superseded
# context = "Client-side totals drift from tax rules"
# choice = "API returns the computed total"
# rejected = ["compute in the browser"]
# consequences = "Checkout needs one more round trip"

# ---- slice -------------------------------------------------------------
# A slice is demoable on its own. Tasks are ≤ limits.task_hours.
#
# [[slice]]
# id = "S-1"
# title = "Checkout creates a pending order"
# covers = ["AC-1.1"]
# demo = \"\"\"
# 1. Add 2 × A1 to the cart
# 2. Check out
# 3. The confirmation shows 300,000 and status Pending
# \"\"\"
#
# [[task]]
# id = "T-1.1"
# slice = "S-1"
# title = "POST /orders computes the total"
# estimate_h = 3
# depends_on = []
# touches = ["src/orders/**"]
# tests = ["src/orders/orders.test.ts"]
# done_when = ["returns 201 with total and status", "rejects an empty cart with 422"]
""")

PROOF = _Toml("""\
# Evidence for {title}. One [[proof]] verifies one or more acceptance criteria.
# Pick the kind by what the AC promises: ui — what someone sees (screenshots); http — an endpoint;
# cli — a job, a migration, data; test — logic. Add one by command, then assert the values the AC names:
#   egd add proof --kind ui --verifies AC-1.1 --title "…" --path /orders/latest --expect-visible "text=300,000"
#   egd add proof --kind http --verifies AC-1.1 --title "…" --path /api/orders --expect-status 200
#   egd add proof --kind cli --verifies AC-1.1 --title "…" --run "pnpm db:migrate" --expect-exit 0
#   egd add proof --kind test --verifies AC-1.1 --title "…" --tests src/orders.test.ts
# Every key: the egd-proof skill. Run them: egd proof run
""")
