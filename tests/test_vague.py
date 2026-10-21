import json
import unicodedata
import unittest
from pathlib import Path

from helpers import Project

from egd import vague
from egd.events import State
from egd.model import Feature

THEN = 'then = "the total is 300000 and the order is pending"'


def feature(*acs, **meta) -> Feature:
    return Feature(root=Path("."), path=Path("f"), plan={"feature": meta, "ac": [
        {"id": f"AC-{i}", "given": "g", "when": "w", **a} for i, a in enumerate(acs, 1)]})


class Words(unittest.TestCase):
    """The word list itself: what is caught, what is not, and what config changes."""

    def test_catches_english_words_whole_and_in_any_case(self):
        found = vague.findings(feature({"then": "The page loads FAST and is User Friendly"}), {})
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("AC-1: vague 'fast', 'user-friendly' in then — say how much: "))
        self.assertIn("300 ms", found[0])
        for then in ("breakfast is served", "the quickest path", "a simpler form", "steadfast"):
            self.assertEqual(vague.findings(feature({"then": then}), {}), [], then)

    def test_catches_vietnamese_with_diacritics_in_either_unicode_form(self):
        then = "Màn hình hiển thị nhanh và giá hợp lý"
        for form in ("NFC", "NFD"):
            found = vague.findings(feature({"then": unicodedata.normalize(form, then)}), {})
            self.assertEqual(len(found), 1, form)
            self.assertIn("'nhanh', 'hợp lý'", found[0])
        self.assertEqual(vague.findings(feature({"then": "mở chi nhánh mới"}), {}), [])  # nhánh ≠ nhanh
        self.assertIn("'v.v.'", vague.findings(feature({"then": "hiện tên, email, v.v."}), {})[0])

    def test_a_number_is_a_measure(self):
        for then in ("the page loads fast, within 300 ms at p95", "trang tải nhanh dưới 2 giây",
                     "it is easy: at most 3 clicks", "uptime is robust at 99.9%"):
            self.assertEqual(vague.findings(feature({"then": then}), {}), [], then)

    def test_an_nfr_needs_a_number_even_without_a_vague_word(self):
        found = vague.findings(feature({"then": "only admins can export", "kind": "nfr"},
                                       {"then": "p95 under 300 ms", "kind": "nfr"},
                                       {"then": "only admins can export"}), {})
        self.assertEqual(found, [f"AC-1: nfr without a number in then — {vague.NFR}"])
        self.assertEqual(vague.findings(feature({"then": "only admins can export", "kind": "nfr"}), {}, nfr=False), [])
        # a vague nfr is reported once, for its word
        found = vague.findings(feature({"then": "the API is fast", "kind": "NFR"}), {})
        self.assertEqual(len(found), 1)
        self.assertIn("vague 'fast'", found[0])

    def test_config_adds_and_allows_words(self):
        cfg = {"ac": {"vague_words_extra": ["snappy", "Fast", "...", 3], "vague_words_allow": ["simple", "etc"]}}
        self.assertIn("snappy", vague.findings(feature({"then": "the menu is Snappy"}), cfg)[0])
        self.assertEqual(vague.findings(feature({"then": "a simple form, labels etc."}), cfg), [])
        self.assertEqual(list(vague.words(cfg)).count("fast") + list(vague.words(cfg)).count("Fast"), 1)
        self.assertEqual(vague.findings(feature({"then": "the order is pending"}), cfg), [])  # "..." matches nothing

    def test_blocking_follows_the_rules_the_feature_was_planned_under(self):
        def state(rules):
            st = State()
            st.events = [{"type": "feature_created", "rules": rules}] if rules is not None else []
            return st
        self.assertFalse(vague.blocking(feature(), state(1)))
        self.assertFalse(vague.blocking(feature(), state(None)))  # no trail says: the oldest rules
        self.assertTrue(vague.blocking(feature(), state(2)))
        self.assertFalse(vague.blocking(feature(imported_from="ai-dlc:checkout"), state(2)))


class Gate(unittest.TestCase):
    """How much a vague AC weighs at `clarify`, through the commands people run."""

    def setUp(self):
        self.p = Project()
        self.plan = self.p.feature / "plan.toml"
        self.plan.write_text(self.plan.read_text().replace(THEN, 'then = "the order page loads fast"'))
        self.p.pass_through("frame")

    def tearDown(self):
        self.p.close()

    def planned_under(self, rules):
        event = next((self.p.feature / "events").glob("*-feature_created.json"))
        event.write_text(json.dumps({**json.loads(event.read_text()), "rules": rules}))

    def test_rules_v2_closes_clarify(self):
        text = self.p.refused("check", "clarify")
        self.assertIn("clarify: closed", text)
        self.assertEqual(text.count("AC-1.1: vague 'fast' in then"), 1)
        self.assertIn('Fix: egd set AC-1.1 "then=…', text)
        self.assertIn("vague 'fast'", self.p.refused("pass", "clarify", "--by", "loc"))
        verify = self.p.refused("verify")
        self.assertEqual(verify.count("vague 'fast'"), 1)  # in the clarify gate, not again as a plan warning
        self.assertIn("not ready for `clarify`", verify)
        self.plan.write_text(self.plan.read_text().replace("loads fast", "loads within 2 s"))
        self.p.pass_through("clarify")

    def test_rules_v1_only_warns(self):
        self.planned_under(1)
        text = self.p.ok("check", "clarify")
        self.assertIn("clarify: open", text)
        self.assertEqual(text.count("warning: AC-1.1: vague 'fast' in then"), 1)
        self.assertIn("features planned under rules v2 close clarify on a vague word", text)
        verify = self.p.ok("verify")
        self.assertEqual(verify.count("vague 'fast'"), 1)
        self.assertIn("ready: a person may now run `egd pass clarify`", verify)
        self.assertIn("warning: AC-1.1: vague 'fast'", self.p.ok("graph"))
        self.p.pass_through("clarify")
        verify = self.p.egd("verify")[1]  # past clarify it stays a plan warning, said once
        self.assertEqual(verify.count("vague 'fast'"), 1)

    def test_an_nfr_without_a_number_only_warns_even_under_rules_v2(self):
        self.plan.write_text(self.plan.read_text().replace(
            'then = "the order page loads fast"', 'then = "no log line contains the payload"\nkind = "nfr"'))
        text = self.p.ok("check", "clarify")
        self.assertIn("clarify: open", text)
        self.assertEqual(text.count("warning: AC-1.1: nfr without a number in then"), 1)
        self.assertNotIn("close clarify", text)
        self.assertEqual(self.p.ok("verify").count("nfr without a number"), 1)
        self.p.pass_through("clarify")

    def test_an_imported_plan_only_warns(self):
        self.plan.write_text(self.plan.read_text().replace('[feature]\n', '[feature]\nimported_from = "ai-dlc:checkout"\n'))
        text = self.p.ok("check", "clarify")
        self.assertIn("warning: AC-1.1: vague 'fast'", text)
        self.assertNotIn("rules v2", text)
        self.p.pass_through("clarify")

    def test_config_words_reach_the_gate(self):
        cfg = self.p.root / ".egd" / "config.toml"
        cfg.write_text(cfg.read_text().replace('[ac]\n', '[ac]\nvague_words_allow = ["fast"]\n'
                                                         'vague_words_extra = ["snappy"]\n', 1))
        self.assertNotIn("vague", self.p.ok("check", "clarify"))
        self.plan.write_text(self.plan.read_text().replace("loads fast", "feels snappy"))
        self.assertIn("AC-1.1: vague 'snappy'", self.p.refused("check", "clarify"))
        self.assertIn("warning: AC-1.1: vague 'snappy'", self.p.ok("graph"))


if __name__ == "__main__":
    unittest.main()
