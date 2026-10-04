"""`kind = "http"` — call an API, assert on the response, keep a transcript.

The transcript is the API's equivalent of a screenshot: what was sent, what
came back, which claims held. Credentials never reach it.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import Context, Outcome, redact_text, tail
from .expect import compare, json_path, present, text_checks
from ..util import md_cell

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
SENSITIVE_HEADER = re.compile(r"^(authorization|cookie|set-cookie|x-api-key|proxy-authorization)$", re.I)
# a key is sensitive by its words, not its letters: `access_token`, `apiKey`, `password` are;
# `author`, `footprint`, `tokens_used`, `sessionCount` are not
_SECRET_WORDS = {"password", "passwd", "pass", "pwd", "secret", "token", "auth", "authorization", "jwt",
                 "session", "sid", "cookie", "credential", "credentials", "otp", "pin", "bearer"}
_SECRET_PAIRS = {("api", "key"), ("private", "key"), ("secret", "key"), ("access", "key"), ("session", "id"),
                 ("client", "secret")}


def sensitive_key(key: str) -> bool:
    words = [w for w in re.split(r"[^a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key)).lower()) if w]
    return bool(words) and (words[-1] in _SECRET_WORDS or tuple(words[-2:]) in _SECRET_PAIRS
                            or "".join(words) in {"apikey", "privatekey", "sessionid", "xapikey"})


def _path_key(path: str) -> str:
    """The last key a JSON path names: `$.data.access_token` → access_token."""
    keys = re.findall(r"[A-Za-z_][\w-]*", path.removeprefix("$"))
    return keys[-1] if keys else ""
_PLACE = re.compile(r"\{([A-Za-z_][\w]*)\}")


def _fill(value, captures: dict):
    if isinstance(value, str):
        return _PLACE.sub(lambda m: str(captures[m.group(1)]) if m.group(1) in captures else m.group(0), value)
    if isinstance(value, list):
        return [_fill(v, captures) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, captures) for k, v in value.items()}
    return value


def _mask_json(doc):
    if isinstance(doc, dict):
        return {k: ("***" if sensitive_key(k) and not isinstance(v, (dict, list)) else _mask_json(v))
                for k, v in doc.items()}
    if isinstance(doc, list):
        return [_mask_json(v) for v in doc]
    return doc


def _show_body(raw: str, parsed, redact: set, limit: int) -> str:
    if parsed is not None:
        text = json.dumps(_mask_json(parsed), indent=2, ensure_ascii=False)
        lang = "json"
    else:
        text, lang = raw, ""
    return f"```{lang}\n{tail(redact_text(text, redact), limit)}\n```"


def run(proof: dict, ctx: Context) -> Outcome:
    from ..util import interpolate

    limit = int(ctx.config.get("proof", {}).get("output_limit", 6000))
    timeout = float(proof.get("timeout", 30))
    steps = proof.get("step", [])
    if not steps:
        return Outcome("error", "http proof has no [[proof.step]]")
    captures: dict = {}
    redact = set(ctx.redact)
    lines = [f"# {proof['id']} — {proof.get('title', '')}", "",
             f"env `{ctx.env}` · base `{ctx.base_url or '(none)'}` · verifies "
             + ", ".join(proof.get("verifies", [])), ""]
    rows, failed, reason = [], False, ""

    for n, raw_step in enumerate(steps, 1):
        step = _fill(interpolate(raw_step, ctx.secrets), captures)
        name = step.get("name") or f"step {n}"
        method = step.get("method", "GET").upper()
        if ctx.readonly and method not in SAFE_METHODS and not step.get("safe"):
            return Outcome("refused", f"{name}: {method} on read-only env '{ctx.env}' "
                           "(mark the step safe = true only if it changes nothing)",
                           rows, transcript="\n".join(lines))
        path = str(step.get("path", ""))
        url = step.get("url") or (path if "://" in path
                                  else ctx.base_url + ("" if not path or path.startswith(("/", "?")) else "/") + path)
        if step.get("query"):
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(step["query"])
        headers = {"Accept": "application/json", **step.get("headers", {})}
        data = None
        if "json" in step:
            data = json.dumps(step["json"]).encode()
            headers.setdefault("Content-Type", "application/json")
        elif "form" in step:
            data = urllib.parse.urlencode(step["form"]).encode()
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif "body" in step:
            data = str(step["body"]).encode()

        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, resp_headers, body = resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            status, resp_headers, body = err.code, dict(err.headers or {}), err.read()
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            ms = int((time.monotonic() - started) * 1000)
            reason = f"{name}: request failed — {getattr(err, 'reason', err)}"
            rows.append({"name": name, "status": "error", "ms": ms})
            lines += [f"## {n}. {name}", "", f"`{method} {redact_text(url, redact)}`", "",
                      f"**request failed:** {redact_text(str(getattr(err, 'reason', err)), redact)}", ""]
            failed = True
            break
        ms = int((time.monotonic() - started) * 1000)
        text = body.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            parsed = None

        expect = step.get("expect", {})
        checks: list[tuple[str, bool, str]] = []
        want_status = expect.get("status", [200, 201, 202, 204])
        allowed = want_status if isinstance(want_status, list) else [want_status]
        checks.append((f"status in {allowed}" if len(allowed) > 1 else f"status == {allowed[0]}",
                       status in allowed, f"got {status}"))
        for path, want in expect.get("json", {}).items():
            if parsed is None:
                checks.append((f"{path}", False, "response is not JSON"))
                continue
            ok, detail = compare(json_path(parsed, path), want)
            if sensitive_key(_path_key(path)) and detail not in ("ok", "missing"):
                detail = "got *** (a secret's value is never shown)"
            checks.append((f"{path} {_describe(want)}", ok, detail))
        for hname, want in expect.get("headers", {}).items():
            got = next((v for k, v in resp_headers.items() if k.lower() == hname.lower()), None)
            shown = "***" if SENSITIVE_HEADER.match(hname) and got else repr(got)
            checks.append((f"header {hname} contains {want!r}", got is not None and want in got, f"got {shown}"))
        checks += text_checks(text, expect)
        if "max_ms" in expect:
            checks.append((f"responds within {expect['max_ms']}ms", ms <= expect["max_ms"], f"took {ms}ms"))

        for cname, source in step.get("capture", {}).items():
            if source.startswith("header:"):
                hn = source.split(":", 1)[1].strip().lower()
                value = next((v for k, v in resp_headers.items() if k.lower() == hn), None)
            else:
                value = json_path(parsed, source) if parsed is not None else None
                value = value if value is not None and present(value) else None
            if value is None:
                checks.append((f"capture {cname} ← {source}", False, "not found"))
                continue
            captures[cname] = value
            secret = sensitive_key(cname) or (source.startswith("header:") and SENSITIVE_HEADER.match(
                source.split(":", 1)[1].strip())) or (not source.startswith("header:") and sensitive_key(_path_key(source)))
            if secret and isinstance(value, str) and len(value) >= 3:
                redact.add(value)

        step_ok = all(ok for _, ok, _ in checks)
        rows.append({"name": name, "status": "pass" if step_ok else "fail", "ms": ms})
        shown_headers = {k: ("***" if SENSITIVE_HEADER.match(k) else v) for k, v in headers.items()}
        lines += [f"## {n}. {name} — {'PASS' if step_ok else 'FAIL'}", "",
                  f"`{method} {redact_text(url, redact)}`", ""]
        if shown_headers:
            lines += ["<details><summary>request headers</summary>", "", "```",
                      *[f"{k}: {redact_text(str(v), redact)}" for k, v in shown_headers.items()],
                      "```", "</details>", ""]
        if data is not None:
            req_parsed = step.get("json")
            lines += ["**Request**", "", _show_body(data.decode(errors="replace"), req_parsed, redact, limit), ""]
        lines += [f"**Response** `{status}` in {ms} ms", "", _show_body(text, parsed, redact, limit), "",
                  "| | assertion | detail |", "|---|---|---|"]
        lines += [f"| {'✅' if ok else '❌'} | {md_cell(redact_text(label, redact))} | "
                  f"{md_cell(redact_text(detail, redact)) if not ok else ''} |"
                  for label, ok, detail in checks]
        lines.append("")
        if not step_ok:
            failed = True
            bad, detail = next((label, detail) for label, ok, detail in checks if not ok)
            reason = f"{name}: {redact_text(bad, redact)}" + (f" ({redact_text(detail, redact)})" if detail else "")
            break

    return Outcome("fail" if failed else "pass", reason, rows, transcript="\n".join(lines))


def _describe(want) -> str:
    if isinstance(want, dict) and set(want) == {"exists"}:
        return "exists" if want["exists"] else "is absent"
    if isinstance(want, dict):
        return " ".join(f"{k} {v!r}" for k, v in want.items())
    return f"== {want!r}"
