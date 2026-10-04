"""Assertions shared by the proof kinds, and a small JSON-path reader."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\.([A-Za-z_$][\w$-]*)|\[(-?\d+)\]|\['([^']*)'\]|\[\"([^\"]*)\"\]")
_MISSING = object()


def json_path(doc, path: str):
    """`$.items[0].price` → value, or _MISSING. Supports dot, [n], ['key']."""
    path = path.strip()
    if not path.startswith("$"):
        raise ValueError(f"JSON path must start with $: {path}")
    rest, cur, pos = path[1:], doc, 0
    while pos < len(rest):
        m = _TOKEN.match(rest, pos)
        if not m:
            raise ValueError(f"cannot parse JSON path {path!r} at {rest[pos:]!r}")
        key, idx, q1, q2 = m.groups()
        pos = m.end()
        if idx is not None:
            if not isinstance(cur, list):
                return _MISSING
            i = int(idx)
            if not -len(cur) <= i < len(cur):
                return _MISSING
            cur = cur[i]
        else:
            name = key if key is not None else (q1 if q1 is not None else q2)
            if not isinstance(cur, dict) or name not in cur:
                return _MISSING
            cur = cur[name]
    return cur


def present(value) -> bool:
    return value is not _MISSING


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _eq(a, b) -> bool:
    if _num(a) and _num(b):
        return abs(a - b) < 1e-9
    return a == b


TYPES = {"string": str, "number": (int, float), "bool": bool, "array": list,
         "object": dict, "null": type(None)}


def compare(actual, expected) -> tuple[bool, str]:
    """Literal → equality. A table → operators: eq ne gt gte lt lte contains
    matches exists len type in."""
    if not isinstance(expected, dict):
        if not present(actual):
            return False, "missing"
        return _eq(actual, expected), f"got {actual!r}"
    for op, want in expected.items():
        if op == "exists":
            if present(actual) != bool(want):
                return False, "missing" if want else f"present ({actual!r})"
            continue
        if not present(actual):
            return False, "missing"
        ok = {
            "eq": lambda: _eq(actual, want),
            "ne": lambda: not _eq(actual, want),
            "gt": lambda: _num(actual) and actual > want,
            "gte": lambda: _num(actual) and actual >= want,
            "lt": lambda: _num(actual) and actual < want,
            "lte": lambda: _num(actual) and actual <= want,
            "contains": lambda: want in actual if isinstance(actual, (str, list, dict)) else False,
            "matches": lambda: isinstance(actual, str) and re.search(want, actual) is not None,
            "len": lambda: hasattr(actual, "__len__") and len(actual) == want,
            "type": lambda: isinstance(actual, TYPES.get(want, object))
                            and not (want == "number" and isinstance(actual, bool)),
            "in": lambda: any(_eq(actual, w) for w in want),
        }.get(op)
        if ok is None:
            return False, f"unknown operator '{op}'"
        if not ok():
            shown = actual if not isinstance(actual, (list, dict)) else f"{type(actual).__name__}({len(actual)})"
            return False, f"{op} {want!r}: got {shown!r}"
    return True, "ok"


def text_checks(text: str, expect: dict) -> list[tuple[str, bool, str]]:
    """contains / not_contains / matches over a body or output."""
    rows = []
    for needle in expect.get("contains", []):
        rows.append((f"contains {needle!r}", needle in text, ""))
    for needle in expect.get("not_contains", []):
        rows.append((f"does not contain {needle!r}", needle not in text, ""))
    pattern = expect.get("matches")
    for pat in ([pattern] if isinstance(pattern, str) else pattern or []):
        rows.append((f"matches /{pat}/", re.search(pat, text, re.M) is not None, ""))
    return rows
