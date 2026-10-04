"""Every English UI string the console translates has a Vietnamese entry with the same placeholders.

Keys are the literal first arguments of t("…"), tHtml("…") and N_("…") (a no-op marker for
strings kept in tables and translated later), plus data-i18n* attributes in the static markup.
Calls with a non-literal argument are ignored.
"""

import json
import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "src" / "egd"
SOURCES = [UI / "console.html", UI / "ui" / "ui.js"]

_STR = r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`((?:[^`\\$]|\\.)*)`'
CALL = re.compile(r"(?<![\w$.])(?:t|tHtml|N_)\(\s*(?:" + _STR + r")\s*[,)]")
ATTR = re.compile(r'data-i18n(?:-title|-aria)?="([^"]*)"')
PH = re.compile(r"\{(\w+)\}")


def _unescape(s: str) -> str:
    return json.loads('"' + s.replace('"', '\\"').replace("\\'", "'").replace("\\`", "`") + '"')


def used_keys() -> dict[str, str]:
    keys = {}
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        for m in CALL.finditer(text):
            raw = next(g for g in m.groups() if g is not None)
            keys.setdefault(_unescape(raw), path.name)
        for m in ATTR.finditer(text):
            keys.setdefault(m.group(1).replace("&amp;", "&"), path.name)
    keys.pop("", None)
    return keys


def vi_table() -> dict[str, str]:
    text = (UI / "ui" / "i18n-vi.js").read_text(encoding="utf-8")
    body = text[text.index("/*VI*/") + len("/*VI*/"):text.index("/*END VI*/")]
    seen = {}

    def pairs(items):
        for k, v in items:
            if k in seen:
                raise AssertionError(f"duplicate vi key: {k!r}")
            seen[k] = v
        return seen

    return json.loads(body, object_pairs_hook=pairs)


class I18nKeys(unittest.TestCase):
    def test_every_key_has_a_vietnamese_entry(self):
        vi = vi_table()
        missing = sorted(k for k in used_keys() if k not in vi)
        self.assertEqual(missing, [], "add these to I18N.vi in ui/i18n-vi.js")

    def test_placeholders_are_kept(self):
        bad = [(k, v) for k, v in vi_table().items() if sorted(PH.findall(k)) != sorted(PH.findall(v))]
        self.assertEqual(bad, [])

    def test_no_blank_translations(self):
        self.assertEqual([k for k, v in vi_table().items() if not v.strip()], [])

    def test_finds_keys(self):  # guards the scanner itself
        keys = used_keys()
        for k in ("Inbox", "{n}m ago", "{a}–{b} of {n}", "{target} waiting for review", "Healthy"):
            self.assertIn(k, keys)


if __name__ == "__main__":
    unittest.main()
