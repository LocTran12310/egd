"""The live portfolio carries feature summaries; a feature's detail comes from /api/feature."""

import datetime as dt
import gzip
import json
import unittest
import urllib.parse

from test_console import ServerBase

from egd import console
from egd.dashboard import SIGNATURES, feature_summary, portfolio, render

HEAVY = {"acs", "slices", "proofs", "assumptions", "decisions", "metrics", "gate_checks"}


class Detail(ServerBase):
    ttl = 0

    @classmethod
    def prepare(cls):
        a = cls._a
        a.pass_through("frame", "clarify", "design", "slice")
        a.ok("start", "T-1.1", "--by", "an")
        a.ok("submit", "T-1.1", "--by", "an", "--confirm")
        a.ok("reject", "T-1.1", "--by", "loc", "--reason", "needs a test for the empty cart")
        a.commit("work")

    def portfolio(self, headers=None):
        code, h, body = self.get("/api/portfolio", headers)
        return code, h, json.loads(body) if body else None

    def repo_a(self, data):
        return next(r for r in data["repos"] if r["path"] == str(self.a.root.resolve()))

    def feature(self, rid, slug, headers=None):
        code, h, body = self.get("/api/feature?" + urllib.parse.urlencode({"repo": rid, "slug": slug}), headers)
        if body and h["Content-Encoding"] == "gzip":
            body = gzip.decompress(body)
        return code, h, json.loads(body) if body else None

    def test_portfolio_carries_summaries_only(self):
        repo = self.repo_a(self.portfolio()[2])
        f = repo["features"][0]
        self.assertTrue(f["partial"])
        self.assertFalse(HEAVY & set(f), HEAVY & set(f))
        self.assertEqual((f["title"], f["gate"], f["tier"]), ("Checkout", "build", "standard"))
        tasks = {t["id"]: t for t in f["tasks"]}
        self.assertEqual(tasks["T-1.1"], {"id": "T-1.1", "title": "POST /orders", "slice": "S-1", "estimate": 3,
                                          "col": "doing", "status": "doing", "owner": "an", "rejects": 1,
                                          "submitted_by": "an"})
        self.assertNotIn("owner", tasks["T-1.2"])  # unset fields are left out
        # people counts last week's accepted work: only recent signatures, no notes
        self.assertTrue(f["trail"])
        self.assertTrue(all(e["type"] in SIGNATURES for e in f["trail"]))
        self.assertTrue(all(set(e) == {"at", "by", "type", "target"} for e in f["trail"]))
        self.assertIn("task_rejected", [e["type"] for e in f["trail"]])
        self.assertTrue(f["rev"])

    def test_feature_endpoint_has_the_detail(self):
        repo = self.repo_a(self.portfolio()[2])
        summary = repo["features"][0]
        code, h, f = self.feature(repo["id"], summary["slug"], {"Accept-Encoding": "gzip"})
        self.assertEqual((code, h["Cache-Control"], h["ETag"]), (200, "private, no-cache", f'"{summary["rev"]}"'))
        self.assertEqual(h["Content-Encoding"], "gzip")
        self.assertLessEqual(HEAVY, set(f))
        self.assertNotIn("partial", f)
        self.assertEqual(f["slices"][0]["demo"], "check out, see 300000")
        self.assertEqual({t["id"]: t["done_when"] for t in f["tasks"]},
                         {"T-1.1": ["returns 201"], "T-1.2": ["shows total"]})
        self.assertIn("task_started", [e["type"] for e in f["trail"]])
        self.assertEqual(f["trail"][-1]["note"], "needs a test for the empty cart")
        self.assertEqual((f["repo"], f["next_problems"], f["rev"]),
                         (repo["id"], summary["next_problems"], summary["rev"]))
        # the summary is the detail cut down: every value it keeps is the detail's
        for k in set(summary) - {"tasks", "trail", "crs", "defects", "partial"}:
            self.assertEqual(summary[k], f[k], k)

    def test_feature_is_304_until_it_changes(self):
        repo = self.repo_a(self.portfolio()[2])
        rid, slug = repo["id"], repo["features"][0]["slug"]
        tag = self.feature(rid, slug)[1]["ETag"]
        code, h, body = self.get("/api/feature?" + urllib.parse.urlencode({"repo": rid, "slug": slug}),
                                 {"If-None-Match": tag})
        self.assertEqual((code, body, h["ETag"]), (304, b"", tag))
        self.a.ok("submit", "T-1.1", "--by", "an", "--confirm")  # submitted again: the feature changes
        code, h, f = self.feature(rid, slug, {"If-None-Match": tag})
        self.assertEqual(code, 200)
        self.assertNotEqual(h["ETag"], tag)
        self.assertEqual(f'"{self.repo_a(self.portfolio()[2])["features"][0]["rev"]}"', h["ETag"])

    def test_unknown_repository_or_feature_is_404_and_hosts_are_checked(self):
        repo = self.repo_a(self.portfolio()[2])
        slug = repo["features"][0]["slug"]
        self.assertEqual(self.feature("nope", slug)[0], 404)
        self.assertEqual(self.feature(repo["id"], "nope")[0], 404)
        self.assertEqual(self.get("/api/feature")[0], 404)
        path = "/api/feature?" + urllib.parse.urlencode({"repo": repo["id"], "slug": slug})
        self.assertEqual(self.get(path, host="evil.example")[0], 403)
        self.assertEqual(self.get("/api/trail", host="evil.example")[0], 403)

    def test_trail_endpoint_signed_or_everything(self):
        rid = self.repo_a(self.portfolio()[2])["id"]
        code, h, body = self.get("/api/trail")
        events = next(r for r in json.loads(body)["repos"] if r["id"] == rid)["features"][0]["trail"]
        self.assertTrue(events and all(e["type"] in SIGNATURES for e in events))
        self.assertEqual(events[-1]["note"], "needs a test for the empty cart")
        self.assertEqual(self.get("/api/trail", {"If-None-Match": h["ETag"]})[0], 304)
        code, h2, body = self.get("/api/trail?all=1")
        events = next(r for r in json.loads(body)["repos"] if r["id"] == rid)["features"][0]["trail"]
        self.assertIn("task_submitted", [e["type"] for e in events])
        self.assertNotEqual(h2["ETag"], h["ETag"])

    def test_snapshot_keeps_every_feature_in_full(self):
        data = portfolio(self.a.root)
        f = data["repos"][0]["features"][0]
        self.assertNotIn("partial", f)
        self.assertLessEqual(HEAVY, set(f))
        self.assertEqual(f["tasks"][0]["done_when"], ["returns 201"])
        html = render(data)
        self.assertIn('"done_when": ["returns 201"]', html)
        self.assertIn("check out, see 300000", html)
        self.assertEqual(console.Portfolio(ttl=0).get()["repos"][0]["features"][0].get("partial"), None)

    def test_summary_keeps_only_recent_signatures(self):
        def ev(at, kind, target):
            return {"seq": 1, "at": at, "by": "b", "type": kind, "target": target, "note": "ok"}
        fd = {"slug": "x", "tasks": [], "trail": [
            ev("2026-01-01T00:00:00+00:00", "task_accepted", "T-1"),  # too old
            ev("2026-10-04T00:00:00+00:00", "task_started", "T-2"),   # not a signature
            ev("2026-10-04T00:00:00", "task_accepted", "T-2"),        # no zone: read as UTC
            ev(None, "gate_passed", "build")]}                        # no time: left out
        since = dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc)
        self.assertEqual(feature_summary(fd, since)["trail"],
                         [{"at": "2026-10-04T00:00:00", "by": "b", "type": "task_accepted", "target": "T-2"}])


if __name__ == "__main__":
    unittest.main()
