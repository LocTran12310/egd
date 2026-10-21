import json
import unittest
from pathlib import Path

from helpers import Project

from egd import cases
from egd.graph import validate
from egd.model import Feature

DESIGN = """# Design
## Approach
Server computes totals.
## Alternatives considered
Client-side totals.
## Failure modes
- Empty cart → 422 and a message → AC-1.1
- Payment provider down → D-1
- Ghost order → AC-9
- Two tabs submit at once: accepted risk
- Đổi tiền tệ giữa chừng: Ngoài phạm vi
- Timeout while saving
  the retry is covered by D-1
- Snow day: won't fix
"""


def feature(*acs, tier="standard", design=None, **meta) -> Feature:
    f = Feature(root=Path("."), path=Path("f"), plan={
        "feature": {"tier": tier, **meta},
        "ac": [{"id": f"AC-{i}", "given": "g", "when": "w", "then": "t", **a} for i, a in enumerate(acs, 1)],
        "decision": [{"id": "D-1", "title": "fallback", "status": "accepted"}]})
    if design is not None:
        f.text = lambda name: design if name == "design.md" else ""
    return f


class Cases(unittest.TestCase):
    def test_a_case_that_is_not_happy_error_or_edge_is_a_plan_error_or_a_warning_when_imported(self):
        errors, warnings = validate(feature({"case": "sad"}), {})
        self.assertIn("AC-1: case 'sad' is not happy, error or edge", errors)
        errors, warnings = validate(feature({"case": "sad"}, imported_from="ai-dlc:x"), {})
        self.assertNotIn("AC-1: case 'sad' is not happy, error or edge", errors)
        self.assertIn("AC-1: case 'sad' is not happy, error or edge", warnings)

    def test_a_plan_with_no_case_hears_it_once(self):
        _, warnings = validate(feature({}, {}, {}), {})
        self.assertEqual(warnings.count(cases.NO_CASES), 1)
        self.assertEqual([w for w in warnings if "case" in w], [cases.NO_CASES])

    def test_only_happy_paths_warn_and_one_error_case_is_enough(self):
        self.assertEqual(cases.case_warnings(feature({"case": "happy"}, {"case": "edge"})), [cases.ONLY_HAPPY])
        self.assertEqual(cases.case_warnings(feature({"case": "happy"}, {"case": "error"})), [])
        self.assertEqual(cases.case_warnings(feature({"case": "happy"}, {})), [cases.ONLY_HAPPY])

    def test_nfr_and_lite_are_left_out(self):
        self.assertEqual(cases.case_warnings(feature({"kind": "nfr"}, {"kind": "NFR"})), [])
        self.assertEqual(cases.case_warnings(feature({"case": "error"}, {"kind": "nfr", "case": "happy"})), [])
        self.assertEqual(cases.case_warnings(feature({"case": "happy"}, {"kind": "nfr", "case": "error"})),
                         [cases.ONLY_HAPPY])
        self.assertEqual(cases.case_warnings(feature({}, tier="lite")), [])
        self.assertEqual(cases.case_warnings(feature({"case": "happy"}, tier="full")), [cases.ONLY_HAPPY])


class FailureModes(unittest.TestCase):
    def test_a_mode_is_handled_by_a_real_id_or_a_deliberate_mark(self):
        found = cases.failure_modes(feature({}, design=DESIGN.replace("AC-1.1", "AC-1")), {})
        self.assertEqual(len(found), 2, found)
        self.assertIn('failure mode "Ghost order → AC-9" names no AC or decision', found[0])  # AC-9 is not in the plan
        self.assertIn('"Snow day: won\'t fix"', found[1])
        self.assertEqual(cases.failure_modes(
            feature({}, design=DESIGN.replace("AC-1.1", "AC-1").replace("AC-9", "AC-1")),
            {"design": {"failure_mode_marks_extra": ["Won't fix"]}}), [])

    def test_ids_match_whole_and_long_bullets_are_cut(self):
        long = "x" * 80
        design = f"## Failure modes\n- Cart vanishes → AC-10\n- {long}\nnot a bullet → AC-9\n"
        found = cases.failure_modes(feature({}, design=design), {})
        self.assertEqual(len(found), 2)
        self.assertIn('"Cart vanishes → AC-10"', found[0])  # AC-1 exists, AC-10 does not
        self.assertIn(f'"{"x" * 60}…"', found[1])
        self.assertEqual(cases.failure_modes(feature({}, design="## Failure modes\nEmpty cart → 422.\n"), {}), [])


class Gates(unittest.TestCase):
    """Warnings only, under every rules version: no gate closes on them."""

    def setUp(self):
        self.p = Project()
        (self.p.feature / "design.md").write_text(DESIGN)

    def tearDown(self):
        self.p.close()

    def test_rules_v1_hears_each_once_and_no_gate_closes(self):
        event = next((self.p.feature / "events").glob("*-feature_created.json"))
        event.write_text(json.dumps({**json.loads(event.read_text()), "rules": 1}))
        self.p.pass_through("frame")
        text = self.p.ok("check", "clarify")
        self.assertIn("clarify: open", text)
        self.assertEqual(text.count(f"warning: {cases.NO_CASES}"), 1)
        self.assertIn("Fix: egd set <AC id> case=error", text)
        self.assertEqual(self.p.ok("verify").count(cases.NO_CASES), 1)  # under clarify, not again under plan
        self.p.pass_through("clarify")
        text = self.p.ok("check", "design")
        self.assertIn("design: open", text)
        self.assertEqual(text.count("names no AC or decision"), 2)  # AC-9 and the snow day
        self.assertIn("Fix: edit .egd/features/", text)
        verify = self.p.ok("verify")
        self.assertEqual(verify.count('"Ghost order → AC-9"'), 1)
        self.assertIn("⚠️ gate design", verify)
        self.p.pass_through("design", "slice")
        self.assertNotIn("Ghost order", self.p.egd("verify")[1])  # design passed: no longer asked

    def test_the_cli_writes_and_refuses_case(self):
        self.p.ok("add", "ac", "--by", "loc", "--given", "an empty cart", "--when", "checking out",
                  "--then", "422", "--levels", "unit", "--case", "error")
        self.assertIn('case = "error"', (self.p.feature / "plan.toml").read_text())
        self.assertIn("must be one of happy, error, edge", self.p.egd("set", "AC-1.1", "case=sad", "--by", "loc")[1])
        self.p.pass_through("frame")
        text = self.p.ok("check", "clarify")  # one error case is enough
        self.assertNotIn(cases.NO_CASES, text)
        self.assertNotIn(cases.ONLY_HAPPY, text)


if __name__ == "__main__":
    unittest.main()
