import unittest

import helpers  # noqa: F401  (puts src/ on sys.path)

from egd.core import (GATE_INFO, GATE_INFO_VI, PROOF_KINDS, PROOF_KINDS_VI, TRANSITIONS, TRANSITIONS_VI, TRUST,
                      TRUST_VI, core_info)
from egd.model import GATES, GATES_BY_TIER
from egd.proof import KINDS
from egd.util import DEFAULT_CONFIG


class CoreInfo(unittest.TestCase):
    """The explanation page must describe exactly the gates, kinds and moves the code has."""

    def test_every_gate_and_proof_kind_is_described(self):
        self.assertEqual(set(GATE_INFO), set(GATES))
        self.assertEqual(set(PROOF_KINDS), set(KINDS))
        for name, info in GATE_INFO.items():
            self.assertTrue(info["proves"] and info["rules"], name)

    def test_rules_only_name_tiers_that_run_the_gate(self):
        for name, info in GATE_INFO.items():
            tiers_with_gate = {t for t, gates in GATES_BY_TIER.items() if name in gates}
            for rule in info["rules"]:
                self.assertTrue(set(rule["tiers"]) & tiers_with_gate, (name, rule["text"]))

    def test_payload(self):
        data = core_info(DEFAULT_CONFIG["roles"])
        self.assertEqual([g["name"] for g in data["gates"]], list(GATES))
        accept = next(g for g in data["gates"] if g["name"] == "accept")
        self.assertEqual(accept["signs"], ["client", "po"])
        self.assertEqual(len(data["transitions"]), len(TRANSITIONS))

    def test_vietnamese_covers_every_item(self):
        self.assertEqual(set(GATE_INFO_VI), set(GATE_INFO))
        for name, info in GATE_INFO.items():
            vi = GATE_INFO_VI[name]
            self.assertTrue(vi["proves"], name)
            self.assertEqual(len(vi["rules"]), len(info["rules"]), name)
            self.assertTrue(all(vi["rules"]), name)
            self.assertEqual("records" in vi, "records" in info, name)
        self.assertEqual(set(PROOF_KINDS_VI), set(PROOF_KINDS))
        self.assertEqual(set(TRANSITIONS_VI), {t["cmd"] for t in TRANSITIONS})
        self.assertEqual(len(TRUST_VI), len(TRUST))
        self.assertTrue(all(x["title"] and x["text"] for x in TRUST_VI))

    def test_vietnamese_payload_has_the_english_shape(self):
        roles = DEFAULT_CONFIG["roles"]
        en, vi = core_info(roles), core_info(roles, "vi")

        def shape(x):
            if isinstance(x, dict):
                return {k: shape(v) for k, v in x.items()}
            if isinstance(x, list):
                return [shape(v) for v in x]
            return type(x).__name__

        self.assertEqual(shape(vi), shape(en))
        self.assertEqual(vi["tiers"], en["tiers"])
        self.assertEqual(vi["roles"], en["roles"])
        for ge, gv in zip(en["gates"], vi["gates"]):
            self.assertEqual(gv["name"], ge["name"])
            self.assertEqual(gv["signs"], ge["signs"])
            self.assertEqual([r["tiers"] for r in gv["rules"]], [r["tiers"] for r in ge["rules"]])
            self.assertNotEqual(gv["proves"], ge["proves"])
        for te, tv in zip(en["transitions"], vi["transitions"]):
            self.assertEqual((tv["cmd"], tv["who"]), (te["cmd"], te["who"]))
            self.assertNotEqual(tv["checks"], te["checks"])
        self.assertEqual([k["kind"] for k in vi["proof_kinds"]], [k["kind"] for k in en["proof_kinds"]])
        self.assertEqual(core_info(roles, "fr"), en)


if __name__ == "__main__":
    unittest.main()
