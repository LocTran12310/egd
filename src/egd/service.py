"""`egd console --service` — keep the console running in the background without Docker.

The operating system's own service manager runs it: a launchd agent on macOS, a systemd user
unit on Linux. It starts when you log in, survives closing the terminal, restarts if it stops,
and costs what the console costs (tens of MB). `--service off` removes it; `egd uninstall` and
`egd update` know about it.

It always runs with a token, kept in the service definition's environment (EGD_TOKEN) — never in
its arguments, which every user of the machine can read in the process list — so other people on
this machine, or on the network with --lan, cannot drive it without the link. The definition and
its log are readable by you alone, and the token is kept when the service is set up again.
"""

from __future__ import annotations

import os
import plistlib
import secrets
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "dev.egd.console"
UNIT = "egd-console.service"


def _plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _unit() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / UNIT


def _log() -> Path:
    return Path.home() / ".egd" / "console.log"


def _run(*cmd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def kind() -> str | None:
    """"launchd", "systemd", or None where neither is available (Windows, containers…)."""
    if sys.platform == "darwin" and shutil.which("launchctl"):
        return "launchd"
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        return "systemd"
    return None


def installed() -> Path | None:
    """The service definition EGD wrote, if there is one."""
    for f in (_plist(), _unit()):
        if f.is_file():
            return f
    return None


def _private(path: Path, data: bytes) -> None:
    """Write `path` readable by its owner only, from the first byte: a temporary file created 0600,
    then renamed over it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _private_log() -> None:
    """~/.egd (0700 when EGD creates it) and the log, 0600 before the service manager opens it:
    the console prints its link there."""
    log = _log()
    if not log.parent.is_dir():
        log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)  # one written before this version may be world-readable
    finally:
        os.close(fd)


def _unit_value(s: str) -> str:
    """`s` for a systemd unit line: no line breaks (they would start a new directive), and no
    specifier (`%`) expansion."""
    if any(c in s for c in "\n\r\0"):
        raise RuntimeError(f"cannot run a service with a line break in {s[:40]!r}")
    return s.replace("%", "%%")


def _unit_quote(s: str) -> str:
    """One quoted word of a systemd unit (ExecStart= argument, Environment= assignment): `\\` and `"`
    escaped, `%` doubled. `$` is doubled too where the line expands variables (ExecStart=)."""
    return '"' + _unit_value(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def unit_text(cmd: list[str], env: dict[str, str]) -> str:
    """The systemd user unit that runs `cmd` with `env`."""
    line = " ".join(_unit_quote(a).replace("$", "$$") for a in cmd)
    envs = "".join(f"Environment={_unit_quote(f'{k}={v}')}\n" for k, v in env.items())
    log = _unit_value(str(_log()))
    return (f"[Unit]\nDescription=EGD console\n\n[Service]\nExecStart={line}\n{envs}"
            f"WorkingDirectory={_unit_value(str(Path.home()))}\nRestart=always\n"
            f"StandardOutput=append:{log}\nStandardError=append:{log}\n\n"
            "[Install]\nWantedBy=default.target\n")


def _split_token(serve_args: list[str]) -> tuple[list[str], str | None]:
    """(`serve_args` without `--token X`, X): the token goes in the environment instead."""
    args, token = list(serve_args), None
    while "--token" in args:
        i = args.index("--token")
        token = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    return args, token


def install(serve_args: list[str]) -> str:
    """Write and start the service; returns the console's address. `serve_args` go to `egd console serve`."""
    how = kind()
    if how is None:
        raise RuntimeError("no service manager here (launchd or systemd) — run `egd console` in a terminal, "
                           "or use Docker for an always-on console")
    serve_args, token = _split_token(serve_args)
    cmd = [sys.executable, "-m", "egd", "console", "serve", *serve_args]
    # PYTHONPATH: the egd this command runs from — an installed tool or a checkout — is what the service runs
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONUNBUFFERED": "1",
           "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    for k in ("EGD_USER", "EGD_CONSOLE_CONFIG", "EGD_CONSOLE_ROOTS", "EGD_BROWSE_ROOTS"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    if token:
        env["EGD_TOKEN"] = token  # `egd console serve` reads it; an argument would show in `ps`
    if how == "launchd":
        text = plistlib.dumps({
            "Label": LABEL, "ProgramArguments": cmd, "EnvironmentVariables": env,
            "WorkingDirectory": str(Path.home()), "RunAtLoad": True, "KeepAlive": True,
            "StandardOutPath": str(_log()), "StandardErrorPath": str(_log())})
    else:
        text = unit_text(cmd, env).encode("utf-8")  # refuses a value that would break the unit
    _private_log()
    if how == "launchd":
        uninstall()  # a changed definition only takes effect after unloading the old one
        _private(_plist(), text)
        code, msg = _run("launchctl", "bootstrap", f"gui/{os.getuid()}", str(_plist()))
        if code != 0:
            code, msg = _run("launchctl", "load", "-w", str(_plist()))
    else:
        _private(_unit(), text)
        _run("systemctl", "--user", "daemon-reload")
        code, msg = _run("systemctl", "--user", "enable", "--now", UNIT)
    if code != 0:
        raise RuntimeError(f"the service manager refused: {msg or 'no reason given'}")
    return msg


def uninstall() -> bool:
    """Stop and remove the service; True when there was one."""
    found = False
    if _plist().is_file():
        found = True
        code, _ = _run("launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}")
        if code != 0:
            _run("launchctl", "unload", "-w", str(_plist()))
        _plist().unlink(missing_ok=True)
    if _unit().is_file():
        found = True
        _run("systemctl", "--user", "disable", "--now", UNIT)
        _unit().unlink(missing_ok=True)
        _run("systemctl", "--user", "daemon-reload")
    return found


def restart() -> bool:
    """Run the newly installed version (after `egd update`); False when there is no service."""
    if _plist().is_file():
        return _run("launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}")[0] == 0
    if _unit().is_file():
        return _run("systemctl", "--user", "restart", UNIT)[0] == 0
    return False


def running() -> bool:
    if _plist().is_file():
        code, out = _run("launchctl", "print", f"gui/{os.getuid()}/{LABEL}")
        return code == 0 and "state = running" in out
    if _unit().is_file():
        return _run("systemctl", "--user", "is-active", "--quiet", UNIT)[0] == 0
    return False


def current() -> dict | None:
    """The installed service as EGD wrote it — {"args": [...], "env": {...}, "port", "token", "lan"} —
    or None when there is none (or it cannot be read)."""
    try:
        if _plist().is_file():
            doc = plistlib.loads(_plist().read_bytes())
            args, env = [str(a) for a in doc.get("ProgramArguments", [])], dict(doc.get("EnvironmentVariables", {}))
        elif _unit().is_file():
            args, env = _read_unit(_unit().read_text(encoding="utf-8"))
        else:
            return None
    except (OSError, ValueError, plistlib.InvalidFileException, UnicodeDecodeError):
        return None
    args = args[args.index("serve") + 1:] if "serve" in args else args
    rest, token = _split_token(args)  # a service set up by an older EGD carried it as an argument
    try:
        port = int(rest[rest.index("--port") + 1])
    except (ValueError, IndexError):
        port = None
    return {"args": rest, "env": env, "port": port, "token": env.get("EGD_TOKEN") or token, "lan": "--lan" in rest}


def _read_unit(text: str) -> tuple[list[str], dict[str, str]]:
    """(ExecStart= words, Environment= assignments) of a unit `unit_text` wrote."""
    def words(s: str) -> list[str]:
        return [w.replace("%%", "%") for w in shlex.split(s, posix=True)]
    args, env = [], {}
    for ln in text.splitlines():
        if ln.startswith("ExecStart="):
            args = [w.replace("$$", "$") for w in words(ln[len("ExecStart="):])]
        elif ln.startswith("Environment="):
            for w in words(ln[len("Environment="):]):
                k, _, v = w.partition("=")
                env[k] = v
    return args, env


def current_token() -> str | None:
    """The token of the installed service — kept when it is set up again, so shared links still work."""
    return (current() or {}).get("token") or None


def link() -> str | None:
    """The installed service's address on this machine, with its token — what `--service status` shows."""
    cur = current()
    if not cur or not cur["port"]:
        return None
    return f"http://127.0.0.1:{cur['port']}/" + (f"?t={cur['token']}" if cur["token"] else "")


def serve_args(port: int, lan: bool, lan_write: bool, readonly: bool, token: str | None) -> list[str]:
    """What the service runs. Always with a token — the one given, else $EGD_TOKEN, else the installed
    service's, else a new one — so the link keeps working and nobody else on the machine can drive it.
    `install` moves it from the arguments into the service's environment."""
    token = token or os.environ.get("EGD_TOKEN") or current_token() or secrets.token_urlsafe(16)
    args = ["--port", str(port), "--token", token]
    if lan:
        args.append("--lan")
        if lan_write:
            args.append("--lan-write")
    if readonly:
        args.append("--readonly")
    return args
