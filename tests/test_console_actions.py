"""Console actions obey the same rules as the CLI: separation of duties, roles, reasons."""

import unittest

from test_console import ServerBase

TEAM = '''[[member]]
name = "loc"
roles = ["pm"]

[[member]]
name = "An"
roles = ["dev"]

[[member]]
name = "Acme PO"
roles = ["client"]
'''


class Actions(ServerBase):
    """Repository a: past `slice`, a team of pm (the console user), dev and client."""

    @classmethod
    def prepare(cls):
        a = cls._a
        (a.root / ".egd" / "team.toml").write_text(TEAM)
        a.pass_through("frame", "clarify", "design", "slice")
        a.commit("team")

    def last(self):
        return self.a.ok("trail").strip().splitlines()[-1]

    def test_the_console_user_cannot_accept_their_own_task(self):
        self.a.ok("start", "T-1.1", "--by", "loc")
        self.a.ok("submit", "T-1.1", "--by", "loc", "--confirm")
        code, res = self.act(self.a, "accept", "T-1.1")
        self.assertEqual(code, 409, res)
        self.assertIn("someone else", res["error"])
        self.assertRegex(self.last(), r"task_submitted\s+T-1\.1")

    def test_client_decisions_need_a_client_role(self):
        self.a.ok("cr", "open", "--by", "loc", "--title", "VAT", "--reason", "new law")
        before = self.last()
        for action, target in (("cr_approve", "CR-001"), ("cr_reject", "CR-001"),
                               ("uat_pass", "S-1"), ("uat_fail", "S-1")):
            code, res = self.act(self.a, action, target, note="seen")
            self.assertEqual(code, 409, (action, res))
            self.assertIn("lacks a role", res["error"])
        self.assertEqual(self.last(), before)  # nothing was recorded

    def test_reject_and_block_need_a_reason_and_are_recorded(self):
        self.a.ok("start", "T-1.1", "--by", "An")
        self.a.ok("submit", "T-1.1", "--by", "An", "--confirm")
        self.assertEqual(self.act(self.a, "reject", "T-1.1")[0], 409)
        code, res = self.act(self.a, "reject", "T-1.1", reason="no test for an empty cart")
        self.assertEqual(code, 200, res)
        self.assertRegex(self.last(), r"loc\s+task_rejected\s+T-1\.1")
        self.assertEqual(self.act(self.a, "block", "T-1.1")[0], 409)
        code, res = self.act(self.a, "block", "T-1.1", note="waiting on the sandbox key")
        self.assertEqual(code, 200, res)
        self.assertRegex(self.last(), r"loc\s+task_blocked\s+T-1\.1")
        self.assertEqual(self.act(self.a, "unblock", "T-1.1")[0], 200)

    def test_unknown_action_is_refused(self):
        code, res = self.act(self.a, "launch_rockets", "T-1.1")
        self.assertEqual(code, 409)
        self.assertIn("unknown action", res["error"])


if __name__ == "__main__":
    unittest.main()


class OneWriterAtATime(Actions):
    def test_four_simultaneous_accepts_record_one(self):
        import threading
        self.a.ok("start", "T-1.1", "--by", "An")
        self.a.ok("submit", "T-1.1", "--by", "An", "--confirm")
        codes = []
        go = threading.Barrier(4)

        def accept():
            go.wait()
            codes.append(self.act(self.a, "accept", "T-1.1")[0])
        threads = [threading.Thread(target=accept) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(sorted(codes), [200, 409, 409, 409])
        trail = self.a.ok("trail")
        self.assertEqual(trail.count("task_accepted"), 1, trail)
