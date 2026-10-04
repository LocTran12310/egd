"""Assembles the one EGD web page: shared design system + app + boot + optional data.

A snapshot (`egd site`, `egd board --html`) carries everything inline so the file works offline.
A served page links the design system (`EAGER`) from its head instead of inlining it, under
versioned urls `ui/<name>?v=<hash>` the browser keeps for good — so a reload re-sends only the
page itself. ui.js and i18n.js go as one script, joined in that order exactly as a snapshot
inlines them: ui.js's top level calls i18n.js's functions, which only hoisting within one
script makes available. What most visits never use — the guide and the Vietnamese strings (`LAZY`) — it
fetches on first use from the same kind of url (see `asset`). The page's own app script stays inline.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).parent

CSS, JS = "text/css; charset=utf-8", "text/javascript; charset=utf-8"
# fetched by a live page on first use
LAZY = {"guide.css": CSS, "guide.js": JS, "i18n-vi.js": JS}
# linked from a live page's head: served name → the ui/ files it joins, in order
EAGER = {"ui.css": ("ui.css",), "ui-core.js": ("ui.js", "i18n.js")}
# the only names the server hands out under ui/
SERVED = {**{n: CSS if n.endswith(".css") else JS for n in EAGER}, **LAZY}

_CSS_SLOT = re.compile(r"<style>\s*/\*EGD_UI_CSS\*/\s*</style>")
_JS_SLOT = re.compile(r"<script>\s*/\*EGD_UI_JS\*/\s*</script>")


def _json(data) -> str:
    # escaping every "<" keeps any text (even "<!--<script") from changing how the page parses
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


@functools.lru_cache(maxsize=16)
def _load(parts: tuple[str, ...], marks: tuple) -> tuple[bytes, str]:
    body = b"\n".join((HERE / "ui" / n).read_bytes() for n in parts)
    return body, hashlib.sha256(body).hexdigest()[:12]


def asset(name: str) -> tuple[bytes, str, str] | None:
    """(content, content type, version) of one served name; None for any name not in SERVED.
    Read and hashed again only when its files change."""
    ctype = SERVED.get(name)
    if ctype is None:
        return None
    parts = EAGER.get(name, (name,))
    marks = tuple((st.st_mtime_ns, st.st_size) for st in ((HERE / "ui" / n).stat() for n in parts))
    body, version = _load(parts, marks)
    return body, ctype, version


def asset_url(name: str) -> str:
    """Versioned: the browser may keep the file for good, a new release gets a new url."""
    return f"ui/{name}?v={asset(name)[2]}"


def page(boot: dict, data: dict | None = None) -> str:
    html = (HERE / "console.html").read_text(encoding="utf-8")
    ui = HERE / "ui"
    live = data is None
    css_names = ("ui.css",) if live else ("ui.css", "guide.css")
    js_names = ("ui.js", "i18n.js") if live else ("ui.js", "i18n.js", "i18n-vi.js", "guide.js")
    if live:
        boot = {**boot, "assets": {n: asset_url(n) for n in LAZY}}
        link = f'<link rel="stylesheet" href="{asset_url("ui.css")}">'
        scripts = f'<script src="{asset_url("ui-core.js")}"></script>'  # js_names, joined in order
        html, linked_css = _CSS_SLOT.subn(lambda _: link, html, count=1)
        html, linked_js = _JS_SLOT.subn(lambda _: scripts, html, count=1)
        # a page whose slots changed shape still works: whatever could not be linked is inlined
        css_names, js_names = css_names[:0 if linked_css else 1], js_names[:0 if linked_js else 2]
    css = "\n".join((ui / n).read_text(encoding="utf-8") for n in css_names)
    js = "\n".join((ui / n).read_text(encoding="utf-8") for n in js_names)
    html = html.replace("/*EGD_UI_CSS*/", css)
    html = html.replace("/*EGD_UI_JS*/", js)
    html = html.replace("/*EGD_BOOT*/null", _json(boot))
    html = html.replace("/*EGD_DATA*/null", "null" if data is None else _json(data))
    if boot.get("mode") == "dashboard":
        html = html.replace("<title>EGD Console</title>", "<title>EGD dashboard</title>")
    return html
