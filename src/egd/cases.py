"""Does the plan say what happens when things go wrong?

Two questions, both answered with warnings only, under every rules version — they ask a person to
look, they never close a gate:

- **AC cases.** An `[[ac]]` may say `case = "happy" | "error" | "edge"`. A standard or full feature
  whose functional ACs (every `kind` but `nfr`) name no `error` case has planned only the path
  where everything works. A plan written before `case` existed hears this once, in one line — not
  once per AC.
- **Failure modes.** Each first-level bullet under design.md's `## Failure modes` should say what
  handles it: the id of an AC or a decision that exists in the plan ("- OTP expired → AC-4"), or a
  mark that it is deliberately not handled ("out of scope", "accepted risk", "ngoài phạm vi",
  "chấp nhận rủi ro"; `[design] failure_mode_marks_extra` in config.toml adds more).

A `case` that is none of the three is a plan error (`graph.validate`), like any value the CLI
would refuse — a warning for a plan imported from another tool.
"""

from __future__ import annotations

import re
import unicodedata

from .model import Feature
from .planfile import CHOICES
from .util import md_sections

CASES = CHOICES["ac", "case"]  # happy, error, edge
DEFAULT_MARKS = ("out of scope", "accepted risk", "ngoài phạm vi", "chấp nhận rủi ro")
FAILURE_MODES = "failure modes"
SHOWN = 60  # characters of a bullet quoted in its warning

NO_CASES = ('no acceptance criterion says its case — mark the ones where something goes wrong '
            'case = "error" (happy | error | edge)')
ONLY_HAPPY = 'only happy paths: no acceptance criterion has case = "error" — what does the user see when it fails?'

_BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")


def _norm(text) -> str:
    return unicodedata.normalize("NFC", str(text)).casefold()


def bad_cases(f: Feature) -> list[str]:
    """ACs whose `case` is not one of CASES."""
    return [f"{a.get('id')}: case '{a['case']}' is not happy, error or edge"
            for a in f.acs if "case" in a and a["case"] not in CASES]


def case_warnings(f: Feature) -> list[str]:
    """At most one line for the feature: no AC says its case, or none says `error`."""
    if f.tier == "lite":
        return []
    functional = [a for a in f.acs if _norm(a.get("kind", "")).strip() != "nfr"]
    if not functional:
        return []
    if not any("case" in a for a in functional):
        return [NO_CASES]
    if not any(a.get("case") == "error" for a in functional):
        return [ONLY_HAPPY]
    return []


def bullets(section: str) -> list[str]:
    """First-level bullets of a markdown section, each with the indented lines under it."""
    found: list[list[str]] = []
    current: list[str] | None = None
    for line in section.splitlines():
        m = _BULLET.match(line)
        if m:
            current = [m.group(1)]
            found.append(current)
        elif current is not None and line[:1] in (" ", "\t") and line.strip():
            current.append(line.strip())
        elif line.strip():
            current = None  # a paragraph ends the bullet before it
    return ["\n".join(b) for b in found]


def marks(cfg: dict) -> list[str]:
    conf = cfg.get("design", {}) if isinstance(cfg.get("design"), dict) else {}
    extra = conf.get("failure_mode_marks_extra")
    extra = [m for m in extra if isinstance(m, str) and m.strip()] if isinstance(extra, list) else []
    return [_norm(m).strip() for m in (*DEFAULT_MARKS, *extra)]


def _names(text: str, item_id: str) -> bool:
    """`AC-1.1` named whole: not inside `AC-1.10` or `XAC-1.1`; a full stop after it is fine."""
    return re.search(r"(?<![\w.-])" + re.escape(item_id) + r"(?![\w-]|\.\w)", text) is not None


def failure_modes(f: Feature, cfg: dict) -> list[str]:
    """One warning per failure mode in design.md that names no AC or decision and is not marked."""
    section = md_sections(f.text("design.md")).get(FAILURE_MODES, "")
    if not section:
        return []
    ids = [str(x["id"]) for x in (*f.acs, *f.decisions)]
    known_marks = marks(cfg)
    out = []
    for b in bullets(section):
        if any(_names(b, i) for i in ids) or any(m in _norm(b) for m in known_marks):
            continue
        first = " ".join(b.splitlines()[0].split())
        shown = first if len(first) <= SHOWN else first[:SHOWN].rstrip() + "…"
        out.append(f'design.md: failure mode "{shown}" names no AC or decision that handles it — '
                   'add the id ("… → AC-4"), or mark it out of scope / accepted risk')
    return out
