"""`kind = "cli"` — run commands, assert on exit code and output.

Covers what has no screen and no endpoint: migrations, cron jobs, queue
consumers, a `psql -c` against a read replica, a CLI the product ships.
"""

from __future__ import annotations

import os
import subprocess
import time

from . import Context, Outcome, redact_text, tail
from .expect import text_checks
from ..util import md_cell


def run(proof: dict, ctx: Context) -> Outcome:
    from ..util import interpolate

    limit = int(ctx.config.get("proof", {}).get("output_limit", 6000))
    timeout = float(proof.get("timeout", ctx.config.get("proof", {}).get("timeout", 600)))
    steps = proof.get("step", [])
    if not steps:
        return Outcome("error", "cli proof has no [[proof.step]]")
    lines = [f"# {proof['id']} — {proof.get('title', '')}", "",
             f"env `{ctx.env}` · verifies " + ", ".join(proof.get("verifies", [])), ""]
    rows, reason = [], ""
    for n, raw in enumerate(steps, 1):
        step = interpolate(raw, ctx.secrets)
        name = step.get("name") or f"step {n}"
        if ctx.readonly and not step.get("safe"):
            return Outcome("refused", f"{name}: commands on read-only env '{ctx.env}' must be "
                           "marked safe = true", rows, transcript="\n".join(lines))
        command = step.get("run")
        if not command:
            return Outcome("error", f"{name}: no `run` command", rows, transcript="\n".join(lines))
        env = {**os.environ, **{k: str(v) for k, v in step.get("env", {}).items()},
               "EGD_ENV": ctx.env, "EGD_BASE_URL": ctx.base_url, "EGD_OUT": str(ctx.out_dir)}
        cwd = ctx.root / step.get("cwd", ".")
        started = time.monotonic()
        try:
            proc = subprocess.run(command, shell=True, cwd=cwd, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout, input=step.get("stdin"))
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            code, out, err = None, exc.stdout or "", f"timed out after {timeout:g}s"
            out = out.decode(errors="replace") if isinstance(out, bytes) else out
        ms = int((time.monotonic() - started) * 1000)
        expect = step.get("expect", {})
        want = expect.get("exit", 0)
        checks = [(f"exit == {want}", code == want, f"got {code}")]
        checks += text_checks(out + ("\n" + err if expect.get("include_stderr", True) else ""), expect)
        if "max_ms" in expect:
            checks.append((f"finishes within {expect['max_ms']}ms", ms <= expect["max_ms"], f"took {ms}ms"))
        ok = all(c[1] for c in checks)
        rows.append({"name": name, "status": "pass" if ok else "fail", "ms": ms})
        lines += [f"## {n}. {name} — {'PASS' if ok else 'FAIL'}", "",
                  "```console", f"$ {redact_text(command, ctx.redact)}",
                  tail(redact_text(out, ctx.redact), limit).rstrip(), "```", ""]
        if err.strip():
            lines += ["<details><summary>stderr</summary>", "", "```",
                      tail(redact_text(err, ctx.redact), limit).rstrip(), "```", "</details>", ""]
        lines += [f"exit `{code}` in {ms} ms", "", "| | assertion | detail |", "|---|---|---|"]
        lines += [f"| {'✅' if c[1] else '❌'} | {md_cell(c[0])} | {md_cell(c[2]) if not c[1] else ''} |"
                  for c in checks]
        lines.append("")
        if not ok:
            reason = f"{name}: " + next(c[0] for c in checks if not c[1])
            return Outcome("fail", reason, rows, transcript="\n".join(lines))
    return Outcome("pass", "", rows, transcript="\n".join(lines))
