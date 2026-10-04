"""`kind = "test"` — the project's own test runner as evidence.

Also used by `egd submit`: when config sets `proof.test_command`, a task's
`tests` run before it may move to review.
"""

from __future__ import annotations

import shlex
import time
from pathlib import Path

from ..util import clear_git_cache
from . import Context, Outcome, redact_text, tail


TESTS_UNSET = "tests not run — set test_command under [proof] in .egd/config.toml"


def build_command(template: str, tests: list[str]) -> str:
    joined = " ".join(shlex.quote(t) for t in tests)
    return template.replace("{tests}", joined) if "{tests}" in template else f"{template} {joined}".strip()


def execute(root: Path, command: str, timeout: float) -> tuple[int | None, str, int]:
    import subprocess
    started = time.monotonic()
    try:
        proc = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        code, output = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        code, output = None, f"timed out after {timeout:g}s"
    finally:
        clear_git_cache()  # whatever ran may have written files
    return code, output, int((time.monotonic() - started) * 1000)


def run_tests(root: Path, cfg: dict, tests: list[str]) -> dict | None:
    """`[proof] test_command` over `tests`: {command, exit, ms, output}; None when either is missing."""
    template = cfg.get("proof", {}).get("test_command")
    if not template or not tests:
        return None
    command = build_command(template, tests)
    code, output, ms = execute(root, command, float(cfg["proof"].get("timeout", 600)))
    return {"command": command, "exit": code, "ms": ms, "output": output}


def run(proof: dict, ctx: Context) -> Outcome:
    cfg = ctx.config.get("proof", {})
    template = proof.get("command") or cfg.get("test_command")
    if not template:
        return Outcome("error", "no `command` on the proof and no proof.test_command in config")
    command = build_command(template, proof.get("tests", []))
    code, output, ms = execute(ctx.root, command, float(proof.get("timeout", cfg.get("timeout", 600))))
    ok = code == 0
    transcript = "\n".join([
        f"# {proof['id']} — {proof.get('title', '')}", "",
        "verifies " + ", ".join(proof.get("verifies", [])), "",
        "```console", f"$ {redact_text(command, ctx.redact)}",
        tail(redact_text(output, ctx.redact), int(cfg.get("output_limit", 6000))).rstrip(), "```", "",
        f"exit `{code}` in {ms} ms — {'PASS' if ok else 'FAIL'}",
    ])
    return Outcome("pass" if ok else "fail", "" if ok else f"exit {code}",
                   [{"name": "tests", "status": "pass" if ok else "fail", "ms": ms}],
                   transcript=transcript)
