"""Browser half of `kind = "ui"`. Runs as its own process; imports nothing from egd.

run mode:   plan JSON on stdin  → result JSON as the last stdout line
login mode: plan JSON in argv[1] → headed browser, saves storage state
"""

import json
import re
import sys
import time
from pathlib import Path

SAFE_STEP_KEYS = {"name", "goto", "wait_for", "wait_ms", "expect", "screenshot", "full_page", "login"}


def emit(obj):
    print(json.dumps(obj))
    sys.stdout.flush()


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        emit({"status": "unavailable",
              "reason": f"Playwright is not installed for {sys.executable} — run `egd proof setup` once "
                        "(or point [ui] python / EGD_UI_PYTHON at a Python that has it)"})
        return
    if len(sys.argv) > 1:
        login(sync_playwright, json.loads(sys.argv[1]))
        return
    emit(run(sync_playwright, json.loads(sys.stdin.read())))


def login(sync_playwright, plan):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(plan["url"])
        print("Sign in in the browser window, then press Enter here to save the session.",
              file=sys.stderr)
        sys.stdin.readline()
        context.storage_state(path=plan["save_state"])
        browser.close()
    print(f"saved session to {plan['save_state']}", file=sys.stderr)


def redact(text, values):
    for v in sorted(values, key=len, reverse=True):
        text = text.replace(v, "***")
    return text


def run(sync_playwright, plan):
    out_dir = Path(plan["out_dir"])
    values = plan.get("redact", [])
    ignore = [re.compile(p) for p in plan.get("ignore_console", [])]
    results, status, reason = [], "pass", ""
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # Playwright is here, its browser is not
            return {"status": "unavailable", "steps": [],
                    "reason": "Chromium for Playwright is missing — run `egd proof setup` once "
                              f"({str(exc).splitlines()[0][:120]})"}
        for vp_name, vp in plan["viewports"].items():
            context = browser.new_context(
                viewport={"width": vp["width"], "height": vp["height"]},
                is_mobile=bool(vp.get("mobile")), has_touch=bool(vp.get("mobile")),
                storage_state=plan.get("storage_state"),
            )
            context.set_default_timeout(plan.get("timeout_ms", 15000))
            page = context.new_page()
            errors = []
            page.on("console", lambda m, e=errors: m.type == "error" and e.append(m.text))
            page.on("pageerror", lambda exc, e=errors: e.append(str(exc)))
            for n, step in enumerate(plan["steps"], 1):
                name = step.get("name") or f"step {n}"
                started = time.monotonic()
                checks = []
                errors.clear()
                try:
                    if plan.get("readonly") and set(step) - SAFE_STEP_KEYS and not step.get("safe"):
                        raise RuntimeError("interaction on a read-only env (mark the step safe = true)")
                    if "goto" in step:
                        goto = str(step["goto"])
                        url = goto if "://" in goto else plan["base_url"] + ("" if goto.startswith(("/", "?"))
                                                                              else "/") + goto
                        page.goto(url, wait_until="domcontentloaded")
                    for sel, value in step.get("fill", {}).items():
                        page.fill(sel, str(value))
                    if "click" in step:
                        page.click(step["click"])
                    if "press" in step:
                        page.keyboard.press(step["press"])
                    if "wait_for" in step:
                        page.wait_for_selector(step["wait_for"], state="visible")
                    if "wait_ms" in step:
                        page.wait_for_timeout(step["wait_ms"])
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    checks = assertions(page, step.get("expect", {}))
                except Exception as exc:
                    checks.append(("step ran", False, redact(str(exc).splitlines()[0], values)))
                real_errors = [e for e in errors if not any(p.search(e) for p in ignore)]
                if plan.get("fail_on_console_error", True) and real_errors:
                    checks.append(("no console errors", False, redact(real_errors[0][:200], values)))
                shot, want = None, step.get("screenshot", True)
                if want:
                    shot = out_dir / f"{vp_name}-{n:02d}-{slug(name)}.png"
                    try:
                        if isinstance(want, str):  # a selector: just that element, scrolled into view
                            page.locator(want).first.screenshot(path=str(shot), timeout=5000)
                        else:
                            page.screenshot(path=str(shot), full_page=step.get("full_page", True))
                    except Exception as exc:
                        shot = None
                        if isinstance(want, str):  # the evidence asked for is missing: say so
                            checks.append((f"screenshot {want}", False,
                                           redact(str(exc).splitlines()[0][:200], values)))
                ok = all(c[1] for c in checks)
                results.append({"name": name, "viewport": vp_name, "status": "pass" if ok else "fail",
                                "login": bool(step.get("login")),
                                "ms": int((time.monotonic() - started) * 1000),
                                "screenshot": str(shot) if shot else None, "checks": checks})
                if not ok:
                    status = "fail"
                    reason = reason or f"{vp_name} · {name}: " + next(c[0] + " — " + c[2] for c in checks if not c[1])
                    break
            context.close()
        contact = contact_sheet(browser, results, out_dir)
        browser.close()
    return {"status": status, "reason": reason, "steps": results, "contact": contact}


def contact_sheet(browser, results, out_dir):
    """Every screenshot of the run on one image, ready to paste into a PR."""
    import base64
    import html as _html
    shots = [r for r in results if r.get("screenshot")]
    if not shots:
        return None
    cells = []
    for r in shots:
        data = base64.b64encode(Path(r["screenshot"]).read_bytes()).decode()
        color = "#1e8449" if r["status"] == "pass" else "#c0392b"
        cells.append(f'<figure><img src="data:image/png;base64,{data}"><figcaption>'
                     f'<b style="color:{color}">{r["status"].upper()}</b> {_html.escape(r["viewport"])} · '
                     f'{_html.escape(r["name"])}</figcaption></figure>')
    page = browser.new_page(viewport={"width": 1600, "height": 900})
    page.set_content(
        "<style>body{margin:16px;font:14px -apple-system,Segoe UI,sans-serif;background:#fff}"
        ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}"
        "figure{margin:0;border:1px solid #ddd;border-radius:8px;overflow:hidden}"
        "img{width:100%;max-height:520px;object-fit:cover;object-position:top;display:block}"
        "figcaption{padding:6px 10px;border-top:1px solid #ddd}</style>"
        f'<div class="g">{"".join(cells)}</div>')
    target = out_dir / "contact-sheet.png"
    page.screenshot(path=str(target), full_page=True)
    page.close()
    return str(target)


def assertions(page, expect):
    checks = []
    for sel in expect.get("visible", []):
        checks.append((f"visible {sel}", page.locator(sel).first.is_visible(), "not visible"))
    for sel in expect.get("hidden", []):
        checks.append((f"hidden {sel}", not page.locator(sel).first.is_visible(), "visible"))
    for sel, text in expect.get("text", {}).items():
        try:
            got = page.locator(sel).first.inner_text(timeout=3000)
        except Exception:
            got = None
        checks.append((f"{sel} contains {text!r}", got is not None and text in got, f"got {got!r}"[:200]))
    if "url_contains" in expect:
        checks.append((f"url contains {expect['url_contains']!r}", expect["url_contains"] in page.url, page.url))
    if "title_contains" in expect:
        title = page.title()
        checks.append((f"title contains {expect['title_contains']!r}", expect["title_contains"] in title, title))
    return checks


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "step"


if __name__ == "__main__":
    main()
