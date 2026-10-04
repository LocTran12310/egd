"""`kind = "ui"` — drive a browser, assert on the page, screenshot each step.

Playwright is the one dependency in EGD, and it is optional: the browser runs
in a separate process (`ui_runner.py`) under whichever Python has Playwright,
so everything else keeps working on a machine that never installed it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import Context, Outcome, redact_text
from ..util import md_cell

RUNNER = Path(__file__).with_name("ui_runner.py")
DEFAULT_VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "mobile": {"width": 390, "height": 844, "mobile": True},
}


def home() -> Path:
    """Where `egd proof setup` puts Playwright, once per machine."""
    return Path.home() / ".egd" / "ui"


def _home_python() -> Path:
    return home() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ui_python(config: dict, root: Path | None = None) -> str:
    """$EGD_UI_PYTHON, else [ui] python (relative to the repository), else the one `egd proof setup`
    made, else the Python running egd."""
    chosen = os.environ.get("EGD_UI_PYTHON") or config.get("ui", {}).get("python")
    if chosen:
        path = Path(os.path.expanduser(chosen))
        return str(root / path if root and not path.is_absolute() and os.sep in chosen else path)
    return str(_home_python()) if _home_python().is_file() else sys.executable


def setup(out) -> int:
    """`egd proof setup`: Playwright and Chromium in ~/.egd/ui — every repository's ui proofs use it."""
    py, uv = _home_python(), shutil.which("uv")

    def make(*extra):
        out(f"creating {home()} …")
        cmd = (["uv", "venv", "-q", *extra, "--python", ">=3.11", str(home())] if uv
               else [sys.executable, "-m", "venv", *extra, str(home())])
        if subprocess.run(cmd).returncode != 0:
            out(f"egd: could not create {home()}: {' '.join(cmd)}"
                + ("" if uv else " — on Debian/Ubuntu install python3-venv first, or install uv"))
            return False
        return True
    if not py.is_file() and not make():
        return 1
    if not uv and subprocess.run([str(py), "-m", "pip", "--version"], capture_output=True).returncode != 0:
        # a folder left by an earlier attempt, or made by uv that is gone now: give it pip, else start over
        if subprocess.run([str(py), "-m", "ensurepip", "-q"], capture_output=True).returncode != 0 \
                and not make("--clear"):
            return 1
    out("installing Playwright …")
    steps = ([["uv", "pip", "install", "-q", "--python", str(py), "playwright"]] if uv
             else [[str(py), "-m", "pip", "install", "-q", "playwright"]])
    steps.append([str(py), "-m", "playwright", "install", "chromium"])
    for cmd in steps:
        if subprocess.run(cmd).returncode != 0:
            out(f"egd: failed: {' '.join(cmd)}")
            return 1
    out(f"ready — ui proofs run with {py} in every repository (`egd uninstall` removes it)")
    return 0


_READY = ("import os, sys\nfrom playwright.sync_api import sync_playwright\n"
          "with sync_playwright() as p:\n    sys.exit(0 if os.path.exists(p.chromium.executable_path) else 1)")


def playwright_ready(config: dict, root: Path | None = None) -> bool:
    """Whether the Python that runs ui proofs has Playwright and its Chromium."""
    try:
        return subprocess.run([ui_python(config, root), "-c", _READY], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def state_path(ctx_root: Path, config: dict, env: str) -> Path:
    template = config.get("ui", {}).get("storage_state", ".egd/.auth/{env}.json")
    return ctx_root / template.replace("{env}", env)


def viewports(config: dict) -> dict:
    return {**DEFAULT_VIEWPORTS, **config.get("ui", {}).get("viewports", {})}


def login_steps(env_cfg: dict, config: dict) -> list[dict]:
    """`[[env.<name>.login]]`, else `[[ui.login]]`: how to sign in, run first in every viewport.

    Each viewport is a fresh browser, so each signs in on its own — no saved session to expire,
    no refresh token one viewport spends before the next. Signing in changes nothing: safe on a
    read-only env. No screenshots unless a step asks for one.
    """
    steps = env_cfg.get("login") or config.get("ui", {}).get("login") or []
    # safe unless a step says otherwise; `login` marks them so reports can leave them out
    return [{"name": "sign in", "screenshot": False, "safe": True, **s, "login": True}
            for s in steps if isinstance(s, dict)]


def _named(steps: list, secrets: dict) -> list:
    """Steps with ${…} filled in — except their names, which become file names and captions."""
    from ..util import interpolate
    filled = interpolate(steps, secrets)
    return [{**s, "name": raw.get("name")} if isinstance(s, dict) and isinstance(raw, dict) and raw.get("name")
            else s for s, raw in zip(filled, steps)]


def run(proof: dict, ctx: Context) -> Outcome:
    from ..util import interpolate

    if not ctx.base_url:
        return Outcome("unavailable", f"env '{ctx.env}' has no base_url")
    known = viewports(ctx.config)
    wanted = proof.get("viewports", ["desktop"])
    wanted = [wanted] if isinstance(wanted, str) else wanted
    unknown = [v for v in wanted if v not in known]
    if unknown:
        return Outcome("error", "unknown viewport(s): " + ", ".join(unknown))
    state = state_path(ctx.root, ctx.config, ctx.env)
    sign_in = login_steps(ctx.env_cfg, ctx.config) if proof.get("login", True) else []
    plan = {
        "mode": "run",
        "base_url": ctx.base_url.rstrip("/"),
        "out_dir": str(ctx.out_dir),
        "storage_state": str(state) if state.exists() and not sign_in else None,
        "viewports": {v: known[v] for v in wanted},
        "steps": _named(sign_in + proof.get("step", []), ctx.secrets),
        "fail_on_console_error": ctx.config.get("ui", {}).get("fail_on_console_error", True),
        "ignore_console": ctx.config.get("ui", {}).get("ignore_console", []),
        "timeout_ms": int(proof.get("timeout_ms", 15000)),
        "redact": sorted(ctx.redact),
        "readonly": ctx.readonly,
    }
    try:
        proc = subprocess.run([ui_python(ctx.config, ctx.root), str(RUNNER)], input=json.dumps(plan),
                              capture_output=True, text=True,
                              timeout=float(proof.get("timeout", 900)))
    except FileNotFoundError:
        return Outcome("unavailable", f"python not found: {ui_python(ctx.config, ctx.root)} — `egd proof setup`")
    last = (proc.stdout.strip().splitlines() or [""])[-1]
    try:
        result = json.loads(last)
    except json.JSONDecodeError:
        return Outcome("error", "ui runner crashed: " + redact_text(proc.stderr.strip()[-400:], ctx.redact))

    lines = [f"# {proof['id']} — {proof.get('title', '')}", "",
             f"env `{ctx.env}` · base `{ctx.base_url}` · verifies "
             + ", ".join(proof.get("verifies", [])), ""]
    artifacts = []
    if result.get("contact"):
        artifacts.append(result["contact"])
        lines += ["![contact sheet](contact-sheet.png)", ""]
    for s in result.get("steps", []):
        lines += [f"## {s['viewport']} · {s['name']} — {s['status'].upper()}", ""]
        if s.get("screenshot"):
            shot = Path(s["screenshot"])
            artifacts.append(str(shot))
            lines += [f"![{s['name']} ({s['viewport']})]({shot.name})", ""]
        if s.get("checks"):
            lines += ["| | assertion | detail |", "|---|---|---|"]
            lines += [f"| {'✅' if ok else '❌'} | {md_cell(label)} | {md_cell(detail) if not ok else ''} |"
                      for label, ok, detail in s["checks"]]
            lines.append("")
    return Outcome(result.get("status", "error"), result.get("reason", ""),
                   [{"name": f"{s['viewport']}:{s['name']}", "status": s["status"], "ms": s.get("ms")}
                    for s in result.get("steps", []) if not s.get("login")],
                   artifacts, "\n".join(lines))


def login(root: Path, config: dict, env: str, base_url: str, path: str = "/") -> int:
    """Headed browser; a person signs in; the session is saved for later runs."""
    target = state_path(root, config, env)
    target.parent.mkdir(parents=True, exist_ok=True)
    plan = {"mode": "login", "url": base_url.rstrip("/") + path, "save_state": str(target)}
    return subprocess.run([ui_python(config, root), str(RUNNER), json.dumps(plan)]).returncode
