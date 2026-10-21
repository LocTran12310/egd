"""Vague words in acceptance criteria — "fast", "user-friendly", "hợp lý" — that no tester can check.

A `then` that leans on such a word passes for whoever reads it kindly. The check is a word
list, not language understanding:

- a word or phrase from the list in an AC's `then`, matched whole and case-insensitively
  (Vietnamese with its diacritics, in either Unicode form), is a finding — unless the
  sentence holds a digit. Any digit counts as a measure: "within 300 ms", "≤ 2%", "3 lần",
  "HTTP 200". A sentence that names a number has already said how much; whether it is the
  right number is the reviewer's call, not a word list's.
- an AC with `kind = "nfr"` and no digit is named even with no vague word in it, so someone
  asks whether it needs a number. It is a warning only, under every rules version: many
  precise non-functional criteria have none — "no log line contains the payload", "every
  request is a GET" — and a word list cannot tell those from a wish.

`[ac]` in config.toml tunes the list: `vague_words_extra` adds words, `vague_words_allow`
drops defaults this team uses precisely.

How much a vague word weighs is the feature's rules version (see `blocking`): from rules v2 it
closes `clarify`; a feature planned under v1, or imported from another tool, is only warned.
"""

from __future__ import annotations

import re
import unicodedata

from .events import State
from .model import Feature

_TIME = 'a time, e.g. "within 300 ms at p95" or "trong 2 giây"'
_STEPS = 'an observable effort, e.g. "in at most 3 clicks" or "without opening the manual"'
_VALUE = 'the exact value or state, e.g. "the total is 300,000 and the order is pending"'
_RATE = 'a rate or a limit, e.g. "0 errors in 1,000 requests" or "≥ 60 fps"'
_LIST = "every case by name — an open list cannot be tested"

# word → what to write instead
DEFAULT_WORDS = {
    "fast": _TIME, "quick": _TIME, "quickly": _TIME, "slow": _TIME, "responsive": _TIME,
    "efficient": _TIME, "efficiently": _TIME,
    "intuitive": _STEPS, "user-friendly": _STEPS, "easy": _STEPS, "simple": _STEPS,
    "properly": _VALUE, "correctly": _VALUE, "appropriate": _VALUE, "appropriately": _VALUE,
    "reasonable": _VALUE, "as expected": _VALUE,
    "robust": _RATE, "seamless": _RATE, "seamlessly": _RATE,
    "etc.": _LIST, "and so on": _LIST,
    "nhanh": _TIME, "chậm": _TIME,
    "thân thiện": _STEPS, "dễ dùng": _STEPS, "dễ sử dụng": _STEPS, "đơn giản": _STEPS,
    "hợp lý": _VALUE, "phù hợp": _VALUE, "đúng cách": _VALUE, "như mong đợi": _VALUE,
    "ổn định": _RATE, "mượt": _RATE,
    "v.v.": _LIST, "vân vân": _LIST,
}
OTHER = "a number with its unit"
NFR = ('if it is a measure, state it, e.g. "p95 under 300 ms" or "99.9% of requests succeed"; '
       '"every …" / "no …" needs none')

_DIGIT = re.compile(r"\d")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", str(text)).casefold()


def _parts(word: str) -> list[str]:
    """The words of a phrase: spaces and hyphens are one gap ("User friendly" = "user-friendly"),
    and a trailing dot is optional ("etc" = "etc.")."""
    return [p for p in re.split(r"[\s-]+", _norm(word).strip().rstrip(".")) if p]


def _pattern(word: str) -> re.Pattern:
    """`word` whole: no letter or digit right before or after it."""
    return re.compile(r"(?<!\w)" + r"[\s-]+".join(map(re.escape, _parts(word))) + r"(?!\w)")


def _strings(value) -> list[str]:
    return [w for w in value if isinstance(w, str) and w.strip()] if isinstance(value, list) else []


def words(cfg: dict) -> dict[str, str]:
    """The list in force: the defaults minus `vague_words_allow`, plus `vague_words_extra`."""
    conf = cfg.get("ac", {}) if isinstance(cfg.get("ac"), dict) else {}
    allow = {tuple(_parts(w)) for w in _strings(conf.get("vague_words_allow"))}
    out = {w: hint for w, hint in DEFAULT_WORDS.items() if tuple(_parts(w)) not in allow}
    known = {tuple(_parts(w)) for w in out}
    for w in _strings(conf.get("vague_words_extra")):
        key = tuple(_parts(w))
        if key and key not in known:  # "..." has no word in it: it would match everywhere
            known.add(key)
            out[w.strip()] = OTHER
    return out


def findings(f: Feature, cfg: dict, *, nfr: bool = True) -> list[str]:
    """One message per AC whose `then` is vague, naming the words and what to write instead.
    `nfr=False` leaves out the number-less nfr ones: those only ever warn."""
    listed = [(w, hint, _pattern(w)) for w, hint in words(cfg).items()]
    out = []
    for a in f.acs:
        then = _norm(a.get("then", ""))
        if not then.strip() or _DIGIT.search(then):
            continue
        hits = [(w, hint) for w, hint, rx in listed if rx.search(then)]
        if hits:
            hints = list(dict.fromkeys(hint for _, hint in hits))
            out.append(f"{a.get('id')}: vague {', '.join(repr(w) for w, _ in hits)} in then — "
                       f"say how much: {'; '.join(hints)}")
        elif nfr and _norm(a.get("kind", "")).strip() == "nfr":
            out.append(f"{a.get('id')}: nfr without a number in then — {NFR}")
    return out


def blocking(f: Feature, st: State) -> bool:
    """Whether vague words close `clarify`: only for a feature planned under rules v2 or later, and
    never for one imported from another tool, whose criteria were agreed there."""
    return st.rules >= 2 and not f.meta.get("imported_from")
