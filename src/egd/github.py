"""Mirror tasks into GitHub Issues so the whole team tracks one backlog.

The plan stays the source of truth: issues are written from it, never read
back into it. Status travels as an `egd:<status>` label; done tasks close their
issue. Uses the `gh` CLI, so it inherits whatever account `gh` is signed in as.
"""

from __future__ import annotations

from .events import record, replay
from .model import Feature
from .team import Team
from .util import UsageError

STATUS_COLORS = {"todo": "cfd3d7", "doing": "1d76db", "review": "fbca04",
                 "blocked": "d93f0b", "done": "0e8a16"}
GH_TIMEOUT = 60  # seconds per gh call; a hung network should not hang the CLI


def _gh(args: list[str], dry: bool, log: list[str]) -> str:
    log.append("gh " + " ".join(_q(a) for a in args))
    if dry:
        return ""
    import subprocess
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT)
    except FileNotFoundError:
        raise UsageError("the GitHub CLI `gh` is not installed or not on PATH — install it from "
                         "https://cli.github.com, run `gh auth login`, then sync again "
                         "(`--dry-run` shows the calls without it)") from None
    except subprocess.TimeoutExpired:
        raise UsageError(f"gh {' '.join(args[:3])}… did not answer within {GH_TIMEOUT}s — "
                         "check the network and `gh auth status`, then sync again") from None
    except OSError as exc:
        raise UsageError(f"could not run gh: {exc}") from None
    if out.returncode != 0:
        if "already" in out.stderr.lower():  # closing a closed issue, reopening an open one
            return ""
        raise UsageError(f"gh {' '.join(args[:3])}… failed: {out.stderr.strip()}")
    return out.stdout


def _q(a: str) -> str:
    return a if a and all(c.isalnum() or c in "-_:/.#=@" for c in a) else repr(a)


def body(f: Feature, task: dict) -> str:
    sl = next((s for s in f.slices if s.get("id") == task.get("slice")), {})
    acs = [a for a in f.acs if a.get("id") in sl.get("covers", [])]
    lines = [f"**Feature:** {f.title} · **Slice:** {sl.get('id', '?')} {sl.get('title', '')}",
             f"**Estimate:** {task.get('estimate_h', '?')}h"
             + (f" · **Depends on:** {', '.join(task['depends_on'])}" if task.get("depends_on") else ""),
             "", "### Done when", ""]
    lines += [f"- [ ] {item}" for item in task.get("done_when", [])]
    if acs:
        lines += ["", "### Acceptance criteria (slice)", ""]
        lines += [f"- **{a['id']}** given {a.get('given', '')}, when {a.get('when', '')}, then {a.get('then', '')}"
                  for a in acs]
    if task.get("touches"):
        lines += ["", "### Touches", ""] + [f"- `{p}`" for p in task["touches"]]
    lines += ["", f"<sub>Managed by EGD from `.egd/features/{f.slug}/plan.toml` — edit the plan, "
                  f"not this issue. Move status with `egd start|submit|accept {task['id']}`.</sub>"]
    return "\n".join(lines)


def sync(features: list[Feature], cfg: dict, team: Team, by: str, dry: bool = False) -> list[str]:
    import hashlib
    repo = cfg.get("github", {}).get("repo")
    if not repo:
        raise UsageError("set [github] repo = \"owner/name\" in .egd/config.toml")
    log: list[str] = []
    if not dry:
        for status, color in STATUS_COLORS.items():
            _gh(["label", "create", f"egd:{status}", "--color", color, "--force", "--repo", repo], dry, [])
        _gh(["label", "create", "egd", "--color", "5319e7", "--force", "--repo", repo], dry, [])
    for f in features:
        st = replay(f)
        for task in f.tasks:
            tid = task["id"]
            ts = st.task(tid)
            title = f"[{f.title}] {tid} {task.get('title', '')}".strip()
            text = body(f, task)
            assignee = team.github_handle(ts.owner)
            digest = hashlib.sha1(f"{title}\n{text}\n{ts.status}\n{assignee}".encode()).hexdigest()[:12]
            number = st.issues.get(tid)
            if number is None:
                args = ["issue", "create", "--repo", repo, "--title", title, "--body", text,
                        "--label", "egd", "--label", f"egd:{ts.status}"]
                if assignee:
                    args += ["--assignee", assignee]
                out = _gh(args, dry, log)
                if dry:
                    continue
                try:
                    number = int(out.strip().rstrip("/").split("/")[-1])
                except ValueError as exc:
                    raise UsageError(f"could not read the issue number from gh output: {out!r}") from exc
                record(f, "issue_linked", by, task=tid, number=number, digest=digest)
                if ts.status == "done":
                    _gh(["issue", "close", str(number), "--repo", repo], dry, log)
                continue
            if st.issue_digest.get(tid) == digest:
                continue
            others = [f"egd:{s}" for s in STATUS_COLORS if s != ts.status]
            args = ["issue", "edit", str(number), "--repo", repo, "--title", title, "--body", text,
                    "--add-label", f"egd:{ts.status}", "--remove-label", ",".join(others)]
            if assignee:
                args += ["--add-assignee", assignee]
            _gh(args, dry, log)
            _gh(["issue", "close" if ts.status == "done" else "reopen", str(number), "--repo", repo], dry, log)
            if not dry:
                record(f, "issue_linked", by, task=tid, number=number, digest=digest)
    return log
