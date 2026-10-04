"""`egd update` and `egd uninstall` — EGD manages its own installation in one command each.

What EGD puts on a machine: the `egd` CLI (a uv or pipx tool), the Claude Code plugin `egd@egd`
(user scope) and its marketplace, the console's repository list (`~/.egd/`), and — when the console
ran in Docker — a container, a volume and images named egd. Uninstall removes exactly those. A
repository's `.egd/` (plans, trail, signatures) is project data and is never touched, nor is a
project-scope plugin entry: it lives in that repository's committed .claude/settings.json.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = "LocTran12310/egd"
SOURCE = f"git+https://github.com/{REPO}.git"
PACKAGE = "egd-cli"
PLUGIN = "egd@egd"
MARKET = "egd"


def _run(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def installer() -> str | None:
    """How this CLI was installed: "uv", "pipx", or None (a checkout, pip, or unknown).

    Asks each tool where it keeps its environments, so a custom UV_TOOL_DIR or PIPX_HOME counts.
    """
    prefix = Path(sys.prefix).resolve()
    for tool, cmd in (("uv", ["uv", "tool", "dir"]), ("pipx", ["pipx", "environment", "--value", "PIPX_LOCAL_VENVS"])):
        if shutil.which(tool):
            code, where = _run(cmd)
            if code == 0 and where and Path(where.splitlines()[-1]).resolve() in prefix.parents:
                return tool
    return None


def _plugins() -> list[dict]:
    if not shutil.which("claude"):
        return []
    code, out = _run(["claude", "plugin", "list", "--json"])
    try:
        return [p for p in json.loads(out) if p.get("id") == PLUGIN] if code == 0 else []
    except json.JSONDecodeError:
        return []


def _has_market() -> bool:
    if not shutil.which("claude"):
        return False
    code, out = _run(["claude", "plugin", "marketplace", "list"])
    return code == 0 and any(line.strip().lstrip("❯ ").split()[:1] == [MARKET] for line in out.splitlines())


def _docker() -> dict:
    if not shutil.which("docker"):
        return {}
    found = {}
    code, out = _run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Image}}"])
    names = {ln.split("\t")[0] for ln in (out if code == 0 else "").splitlines()
             if ln.count("\t") == 1 and ln.split("\t")[1].split(":")[0] == "egd"}
    found["containers"] = sorted(names)
    code, out = _run(["docker", "volume", "ls", "--format", "{{.Name}}"])
    found["volumes"] = sorted(v for v in (out if code == 0 else "").splitlines() if v in ("egd_console-data",))
    code, out = _run(["docker", "images", "egd", "--format", "{{.Repository}}:{{.Tag}}"])
    found["images"] = sorted(i for i in (out if code == 0 else "").splitlines() if i.startswith("egd:"))
    return {k: v for k, v in found.items() if v}


def _console_file() -> Path:
    """The console's own settings file — the only thing of the console uninstall deletes."""
    cfg = os.environ.get("EGD_CONSOLE_CONFIG")
    return Path(cfg).expanduser() if cfg else Path.home() / ".egd" / "console.toml"


def _remove_console_file(path: Path) -> bool:
    """Delete the settings file, then its folder only if that leaves it empty. Never a folder with
    anything else in it — ~/.egd can also be a project's .egd/ when someone ran `egd setup` at home."""
    try:
        path.unlink(missing_ok=True)
        (path.parent / "console.log").unlink(missing_ok=True)  # the background console's log
        if path.parent.name == ".egd" and not any(path.parent.iterdir()):
            path.parent.rmdir()
        return not path.exists()
    except OSError:
        return False


def plan(keep_data: bool = False) -> list[tuple[str, list[str] | None, str | None]]:
    """[(what, command or None for a folder, cwd)] — in the order they run; the CLI goes last."""
    steps: list[tuple[str, list[str] | None, str | None]] = []
    if any(p.get("scope", "user") == "user" for p in _plugins()):
        steps.append((f"Claude Code plugin {PLUGIN} (user)",
                      ["claude", "plugin", "uninstall", PLUGIN, "--scope", "user"], None))
    if _has_market():
        steps.append((f"plugin marketplace '{MARKET}'", ["claude", "plugin", "marketplace", "remove", MARKET], None))
    d = _docker()
    for c in d.get("containers", []):
        steps.append((f"Docker container {c}", ["docker", "rm", "-f", c], None))
    if not keep_data:
        for v in d.get("volumes", []):
            steps.append((f"Docker volume {v} (the console's repository list)", ["docker", "volume", "rm", v], None))
    for i in d.get("images", []):
        steps.append((f"Docker image {i}", ["docker", "rmi", i], None))
    from . import service
    if service.installed():
        steps.append((f"the background console ({service.installed()})", ["__service_off__"], None))
    from .proof.ui import home
    ui_home = home()
    if (ui_home / "pyvenv.cfg").is_file():  # only a folder `egd proof setup` made
        steps.append((f"Playwright for ui proofs ({ui_home})", ["__rmtree__"], str(ui_home)))
    console = _console_file()
    if not keep_data and console.is_file():
        steps.append((f"console settings {console} (repository list, signers)", None, str(console)))
    how = installer()
    if how == "uv":
        steps.append((f"the egd CLI (uv tool {PACKAGE})", ["uv", "tool", "uninstall", PACKAGE], None))
    elif how == "pipx":
        steps.append((f"the egd CLI (pipx {PACKAGE})", ["pipx", "uninstall", PACKAGE], None))
    return steps


def kept_plugins() -> list[str]:
    """Where the plugin stays enabled for a repository — its settings, often shared through git."""
    kept = []
    for p in _plugins():
        scope = p.get("scope", "user")
        if scope != "user":
            name = "settings.local.json" if scope == "local" else "settings.json"
            repo = p.get("projectPath") or "a repository"
            note = "" if scope == "local" else " (shared with the team; remove it there if you want)"
            kept.append(f"Claude Code plugin {PLUGIN} left in {repo}/.claude/{name}{note}")
    return kept


def uninstall(yes: bool, keep_data: bool, dry: bool, out) -> int:
    steps = plan(keep_data)
    how = installer()
    if not steps:
        out("Nothing of EGD is installed here.")
    else:
        out("This removes:")
        for what, _, _ in steps:
            out(f"  - {what}")
    out("Kept: every repository's .egd/ (plans, trail, signatures, .auth/ sessions) — that is project data.")
    for line in kept_plugins():
        out(line)
    if any(cmd == ["__rmtree__"] for _, cmd, _ in steps):
        out("Kept: Playwright's browser download (ms-playwright in your cache folder) — other tools share it.")
    if how is None:
        out(f"The egd CLI itself runs from {Path(sys.argv[0]).resolve().parent.parent} "
            f"(a checkout or pip install) — remove that yourself if you want it gone.")
    if not steps or dry:
        return 0
    if not yes:
        if not sys.stdin.isatty():
            out("Run again with --yes to confirm (no terminal to ask in).")
            return 2
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            out("Nothing removed.")
            return 1
    failed = 0
    for what, cmd, cwd in steps:
        if cmd is None:  # the console's settings file — see _remove_console_file
            ok, msg = _remove_console_file(Path(cwd)), ""
        elif cmd == ["__rmtree__"]:
            shutil.rmtree(cwd, ignore_errors=True)
            ok, msg = not Path(cwd).exists(), ""
            with contextlib.suppress(OSError):
                Path(cwd).parent.rmdir()  # ~/.egd, when nothing else is left in it
        elif cmd == ["__service_off__"]:
            from . import service
            ok, msg = service.uninstall() or not service.installed(), ""
        else:
            code, msg = _run(cmd, cwd)
            ok = code == 0
        out(f"  {'✓' if ok else '✗'} {what}" + ("" if ok else f" — {msg.splitlines()[-1] if msg else 'failed'}"))
        failed += not ok
    out("EGD is uninstalled." if not failed else f"{failed} step{'' if failed == 1 else 's'} failed — see above.")
    out(f"Install again: curl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | sh")
    return 1 if failed else 0


def _rewrite_service(service, cur: dict) -> bool:
    """Write an older background console's definition again, the way this egd does — same port,
    same options, same token (so shared links keep working), same EGD_* settings."""
    args = cur["args"][cur["args"].index("serve") + 1:] if "serve" in cur["args"] else []
    if "--token" in args:
        i = args.index("--token")
        args = args[:i] + args[i + 2:]
    keep = {k: v for k, v in cur.get("env", {}).items() if k.startswith("EGD_") and k != "EGD_TOKEN"}
    saved = {k: os.environ.get(k) for k in keep}
    os.environ.update(keep)
    try:
        import secrets
        service.install(args + ["--token", cur.get("token") or secrets.token_urlsafe(16)])
        return True
    except RuntimeError:
        return False
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def update(out) -> int:
    """The CLI from the latest commit, the plugin, and the console image when it runs in Docker."""
    failed = 0
    how = installer()
    if how == "uv":
        cmds = [["uv", "tool", "install", "--reinstall", "--python", ">=3.11", SOURCE]]
    elif how == "pipx":
        cmds = [["pipx", "install", "--force", SOURCE]]
    else:
        out(f"The egd CLI runs from {Path(sys.argv[0]).resolve().parent.parent} — update it there "
            f"(git pull), or install it with the one-line installer.")
        cmds = []
    if shutil.which("claude"):  # bring the plugin up to date — or add it, when Claude Code is here
        cmds += [["claude", "plugin", "marketplace", "update", MARKET] if _has_market()
                 else ["claude", "plugin", "marketplace", "add", REPO],
                 ["claude", "plugin", "update", PLUGIN] if _plugins()
                 else ["claude", "plugin", "install", PLUGIN]]
    for cmd in cmds:
        code, msg = _run(cmd)
        out(f"  {'✓' if code == 0 else '✗'} {' '.join(cmd[:4])}" + ("" if code == 0 else f" — {msg.splitlines()[-1] if msg else 'failed'}"))
        failed += code != 0
    from . import service
    if service.installed():
        cur = service.current()
        if cur and ("--token" in cur["args"] or not cur.get("token")):
            ok = _rewrite_service(service, cur)  # written by an older egd: token in argv, or none
        else:
            ok = service.restart()
        out(f"  {'✓' if ok else '✗'} background console restarted on the new version")
        failed += not ok
    if _docker().get("containers"):
        out("  · the console runs in Docker: rebuild it from the egd checkout with `docker compose up -d --build`")
    if shutil.which("claude"):
        out("  · restart Claude Code to load the new plugin")
    return 1 if failed else 0
