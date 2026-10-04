"""What keeps the console light: gzip, lazily fetched assets, 304s and rescanning only what changed."""

import gzip
import http.client
import json
import re
import unittest
from unittest import mock

from test_console import ServerBase

from egd import console, web
from egd.dashboard import render


class Light(ServerBase):
    ttl = 0  # every poll looks again; only fingerprints decide

    # ---------------------------------------------------------------- gzip
    def test_gzip_only_when_asked_and_worth_it(self):
        code, h, body = self.get("/", {"Accept-Encoding": "gzip, deflate, br"})
        self.assertEqual((code, h["Content-Encoding"], h["Vary"]), (200, "gzip", "Accept-Encoding"))
        page = gzip.decompress(body).decode()
        self.assertIn(self.server.RequestHandlerClass.csrf, page)
        self.assertEqual(int(h["Content-Length"]), len(body))
        self.assertLess(len(body), len(page) // 2)
        for refused in ("identity", "gzip;q=0", "br", "*;q=0", ""):
            code, h, body = self.get("/", {"Accept-Encoding": refused})
            self.assertIsNone(h["Content-Encoding"], refused)
            self.assertIn(b"<!doctype html>", body)
        code, h, body = self.get("/nope", {"Accept-Encoding": "gzip"})  # under 1 KB: not worth it
        self.assertEqual((code, h["Content-Encoding"], body), (404, None, b"not found"))
        code, h, body = self.get("/api/portfolio", {"Accept-Encoding": "*"})
        self.assertEqual(h["Content-Encoding"], "gzip")
        self.assertEqual(json.loads(gzip.decompress(body))["user"], "loc")

    def test_accept_encoding_parsing(self):
        self.assertTrue(console.accepts_gzip("gzip"))
        self.assertTrue(console.accepts_gzip("deflate, GZIP;q=0.5"))
        self.assertTrue(console.accepts_gzip("br, *;q=0.1"))
        self.assertFalse(console.accepts_gzip("gzip;q=0, *"))
        self.assertFalse(console.accepts_gzip(None))
        self.assertFalse(console.accepts_gzip("gzip;q=oops"))

    # ---------------------------------------------------------------- lazy assets
    def test_live_page_leaves_out_the_guide_and_vietnamese(self):
        page = self.get("/")[2].decode()
        boot = json.loads(re.search(r"const BOOT = (\{.*?\}) \|\|", page).group(1))
        self.assertEqual(set(boot["assets"]), set(web.LAZY))
        for name, url in boot["assets"].items():
            self.assertEqual(url, f"ui/{name}?v={web.asset(name)[2]}")
        self.assertNotIn("function guidePage", page)
        self.assertNotIn("I18N.vi =", page)
        self.assertNotIn(".g-tabs", page)

    def test_assets_are_versioned_and_long_cached(self):
        for name, ctype in web.LAZY.items():
            body, _, version = web.asset(name)
            code, h, got = self.get(f"/ui/{name}?v={version}")
            self.assertEqual((code, h["Content-Type"], got), (200, ctype, body))
            self.assertIn("immutable", h["Cache-Control"])
            self.assertEqual(h["X-Content-Type-Options"], "nosniff")
            code, h, _ = self.get(f"/ui/{name}?v=old")  # another version: served, never kept
            self.assertEqual((code, h["Cache-Control"]), (200, "no-cache"))
        code, h, body = self.get("/ui/guide.js", {"Accept-Encoding": "gzip"})
        self.assertEqual(gzip.decompress(body), web.asset("guide.js")[0])

    def test_only_allowlisted_assets_are_served(self):
        for path in ("/ui/console.html", "/ui/", "/ui/../console.py", "/ui/..%2fconsole.py", "/ui/%2e%2e/web.py",
                     "/ui/..%2f..%2fpyproject.toml", "/ui/guide.js/..", "/ui//etc/passwd", "/ui/GUIDE.JS",
                     "/ui/guide.js%00.png", "/ui/i18n-vi.js/"):
            self.assertEqual(self.get(path)[0], 404, path)
        self.assertIsNone(web.asset("../console.py"))
        self.assertEqual(self.get("/ui/guide.js", host="evil.example")[0], 403)

    def test_snapshot_stays_self_contained(self):
        html = render({"generated": "2026-10-05T00:00:00+00:00", "repos": [], "readonly": True})
        self.assertNotRegex(html, r"""(src|href)=["']?ui/|["']ui/[\w.-]+\?v=""")
        self.assertNotIn('"assets"', html)
        for inline in ("function guidePage", "I18N.vi = ", ".g-tabs", "const I18N = {}"):
            self.assertIn(inline, html)

    # ---------------------------------------------------------------- 304 and rescans
    def test_unchanged_portfolio_is_304_and_a_change_brings_a_fresh_body(self):
        code, h, body = self.get("/api/portfolio")
        tag = h["ETag"]
        self.assertEqual((code, h["Cache-Control"]), (200, "private, no-cache"))
        self.assertTrue(tag.startswith('"') and tag.endswith('"'))
        code, h, body = self.get("/api/portfolio", {"If-None-Match": tag})
        self.assertEqual((code, body, h["ETag"]), (304, b"", tag))
        self.assertIsNone(h["Content-Length"])
        self.assertEqual(self.get("/api/portfolio", {"If-None-Match": f'"x", W/{tag}'})[0], 304)
        # an untracked file in repo a's .egd/: a new body, with the uncommitted change in it
        (self.a.root / ".egd" / "note.md").write_text("draft\n")
        code, h, body = self.get("/api/portfolio", {"If-None-Match": tag})
        self.assertEqual(code, 200)
        self.assertNotEqual(h["ETag"], tag)
        repo = next(r for r in json.loads(body)["repos"] if r["path"] == str(self.a.root.resolve()))
        self.assertEqual(repo["uncommitted"], 1)
        self.assertEqual(self.get("/api/portfolio", {"If-None-Match": h["ETag"]})[0], 304)

    def test_http11_so_browsers_revalidate_with_the_etag(self):
        # browsers ignore an ETag on an HTTP/1.0 response and would refetch the body every poll
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        host = {"Host": f"127.0.0.1:{self.port}"}
        conn.request("GET", "/api/portfolio", headers=host)
        r = conn.getresponse()
        r.read()
        self.assertEqual((r.version, r.headers["Connection"]), (11, None))
        conn.request("GET", "/api/portfolio", headers={**host, "If-None-Match": r.headers["ETag"]})  # same socket
        r = conn.getresponse()
        self.assertEqual((r.status, r.read()), (304, b""))
        conn.request("POST", "/api/action", body=b'{"x": 1}', headers=host)  # refused unread: not reused
        r = conn.getresponse()
        r.read()
        self.assertEqual((r.status, r.headers["Connection"]), (403, "close"))
        conn.close()

    def test_only_changed_repositories_are_rescanned(self):
        portfolio = console.Portfolio(ttl=0)
        with mock.patch.object(console, "repo_snapshot", wraps=console.repo_snapshot) as built:
            first, tag = portfolio.tagged()
            self.assertEqual(built.call_count, 2)
            again, tag2 = portfolio.tagged()
            self.assertEqual((built.call_count, tag2), (2, tag))  # nothing changed: nothing rebuilt
            self.assertEqual(again["repos"], first["repos"])
            self.b.commit("empty")  # a commit moves git's HEAD log: b's branch and trail status may differ
            portfolio.tagged()
            self.assertEqual([c.args[1] for c in built.call_args_list[2:]], [self.b.root.resolve()])
            portfolio.invalidate(self.a.root)  # what an action does: that repository is read again
            portfolio.tagged()
            self.assertEqual(built.call_args_list[-1].args[1], self.a.root.resolve())
            self.assertEqual(built.call_count, 4)
            portfolio.max_age = 0  # proofs go stale on code edits the fingerprint cannot see:
            portfolio.tagged()  # served as they are, and read again in the background
            portfolio.wait()
            self.assertEqual(built.call_count, 6)

    def test_an_action_shows_on_the_next_poll(self):
        tag = self.get("/api/portfolio")[1]["ETag"]
        rid = next(r["id"] for r in json.loads(self.get("/api/portfolio")[2])["repos"]
                   if r["path"] == str(self.a.root.resolve()))
        self.assertEqual(self.req("POST", "/api/action", {"repo": rid, "action": "pass", "target": "frame"},
                                  {"X-EGD-CSRF": self.csrf})[0], 200)
        code, h, body = self.get("/api/portfolio", {"If-None-Match": tag})
        self.assertEqual(code, 200)
        repo = next(r for r in json.loads(body)["repos"] if r["id"] == rid)
        self.assertNotEqual(repo["features"][0]["gate"], "frame")


class Favicon(unittest.TestCase):
    def test_icon_link_is_one_well_formed_attribute(self):
        from pathlib import Path
        html = (Path(__file__).resolve().parent.parent / "src" / "egd" / "console.html").read_text()
        href = re.search(r'<link rel="icon" href="([^"]*)">', html).group(1)
        from urllib.parse import unquote
        svg = unquote(href.removeprefix("data:image/svg+xml,"))
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"), svg[:40])


if __name__ == "__main__":
    unittest.main()
