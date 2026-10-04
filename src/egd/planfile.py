"""Write to plan.toml (and proof.toml) without hand-editing TOML: `egd add`, `egd set`, `egd rm`.

Edits are textual — comments and layout people wrote stay where they are — and
every edit is re-parsed before it is saved and written atomically, so a write can
never leave the plan unreadable or half-written.
"""

from __future__ import annotations

import math
import re
import tomllib
from pathlib import Path

from .model import Feature, closest
from .util import UsageError, write_atomic

KINDS = {"assumption": "A", "ac": "AC", "decision": "D", "slice": "S", "task": "T"}
FIELDS = {  # what each kind of entry holds, besides its id
    "assumption": ("text", "confidence", "blocking", "status", "resolution"),
    "ac": ("story", "given", "when", "then", "levels", "kind"),
    "decision": ("title", "status", "context", "choice", "rejected", "consequences"),
    "slice": ("title", "covers", "demo"),
    "task": ("slice", "title", "estimate_h", "depends_on", "touches", "tests", "done_when", "agent", "model"),
}
CHOICES = {  # (kind, field) → allowed values; for a list field, of each item
    ("assumption", "status"): ("open", "confirmed", "rejected"),
    ("assumption", "confidence"): ("low", "medium", "high"),
    ("decision", "status"): ("proposed", "accepted", "superseded"),
    ("ac", "levels"): ("unit", "integration", "e2e", "manual"),
    ("task", "agent"): ("builder", "tester", "person"),
    ("task", "model"): ("haiku", "sonnet", "opus", "fable"),
}
LIST_FIELDS = {"levels", "covers", "depends_on", "touches", "tests", "done_when", "rejected"}
BOOL_FIELDS = {"blocking"}
TRUE, FALSE = ("true", "yes", "y", "1", "on"), ("false", "no", "n", "0", "off")
NUM_FIELDS = {"estimate_h"}
_HEADER = re.compile(r"^\s*\[\[?\s*([A-Za-z0-9_.-]+)\s*\]\]?\s*(#.*)?$")


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_value(value) -> str:
    if isinstance(value, dict):  # an inline table, e.g. a proof step's `expect`
        return "{ " + ", ".join(f"{k if _BARE_KEY.match(str(k)) else toml_value(str(k))} = {toml_value(v)}"
                                for k, v in value.items()) + " }"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    text = str(value)
    if "\n" in text:
        body = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return '"""\n' + body.strip("\n") + '"""'
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t") + '"'


def coerce(field: str, raw):
    if field in LIST_FIELDS:
        if isinstance(raw, str) and raw.strip().startswith("["):   # a JSON list keeps items that hold commas
            import json
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise UsageError(f"{field}: not a valid list — {exc}") from None
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [x.strip() for x in str(raw).split(",") if x.strip()]
    if field in BOOL_FIELDS:
        if isinstance(raw, bool):
            return raw
        word = str(raw).strip().lower()
        if word not in TRUE + FALSE:
            raise UsageError(f"{field} must be true or false, not '{raw}'")
        return word in TRUE
    if field in NUM_FIELDS:
        try:
            num = float(raw)
        except ValueError as exc:
            raise UsageError(f"{field} must be a number") from exc
        if isinstance(raw, bool) or not math.isfinite(num) or num <= 0:
            raise UsageError(f"{field} must be a positive finite number")
        return int(num) if num.is_integer() else num
    return raw


def check_fields(kind: str, fields: dict, present=()) -> None:
    """Refuse a field this kind of entry does not hold (unless the entry already has it) and an
    enum value outside its choices — a typo should not land silently in the plan."""
    known = FIELDS.get(kind)
    if known is None:
        return
    for key, raw in fields.items():
        if key == "id":
            continue
        if key not in known and key not in present:
            hint = closest(key, known)
            raise UsageError(f"a{'n' if kind[0] in 'ae' else ''} {kind} has no field '{key}'"
                             + (f" — did you mean '{hint}'?" if hint else f" (fields: {', '.join(known)})"))
        allowed = CHOICES.get((kind, key))
        if allowed is None or raw is None:
            continue
        for value in (coerce(key, raw) if key in LIST_FIELDS else [raw]):
            if value not in allowed:
                hint = closest(str(value), allowed)
                raise UsageError(f"{kind} {key} must be one of {', '.join(allowed)}, not '{value}'"
                                 + (f" — did you mean '{hint}'?" if hint else ""))


def next_id(feature: Feature, kind: str, group: str | None = None) -> str:
    prefix = "P" if kind == "proof" else KINDS[kind]
    items = feature.raw_proofs if kind == "proof" else feature.plan.get(kind, [])
    if kind in ("ac", "task"):
        if group is None:
            if kind == "ac":
                raise UsageError("ac ids are grouped by story — pass --group N to get the next AC-N.x, "
                                 "or --id AC-N.M")
            raise UsageError("task ids are grouped by slice — pass --slice S-N to get the next T-N.x, "
                             "or --id T-N.M")
        g = re.sub(r"\D", "", group) or group
        nums = [int(m.group(1)) for it in items if isinstance(it, dict)
                if (m := re.match(rf"^{prefix}-{re.escape(g)}\.(\d+)$", str(it.get("id", ""))))]
        return f"{prefix}-{g}.{max(nums, default=0) + 1}"
    nums = [int(m.group(1)) for it in items if isinstance(it, dict)
            if (m := re.match(rf"^{prefix}-(\d+)$", str(it.get("id", ""))))]
    return f"{prefix}-{max(nums, default=0) + 1}"


def _save(path: Path, text: str) -> None:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise UsageError(f"refusing to write an unreadable {path.name}: {exc}") from exc
    write_atomic(path, text)


def add(feature: Feature, kind: str, fields: dict) -> str:
    if kind not in KINDS:
        raise UsageError(f"can add: {', '.join(KINDS)}")
    if any(it.get("id") == fields["id"] for it in feature.plan.get(kind, [])):
        raise UsageError(f"{fields['id']} already exists — `egd set {fields['id']} field=value` changes it")
    check_fields(kind, fields)
    lines = [f"[[{kind}]]"]
    for key, value in fields.items():
        if value is None or value == [] or value == "":
            continue
        lines.append(f"{key} = {toml_value(coerce(key, value))}")
    _append(feature.path / "plan.toml", lines)
    return fields["id"]


def _append(path: Path, lines: list[str]) -> None:
    old = path.read_text(encoding="utf-8").rstrip("\n") if path.exists() else ""
    _save(path, (old + "\n\n" if old else "") + "\n".join(lines) + "\n")


def add_proof(feature: Feature, proof: dict, steps: list[dict] = ()) -> str:
    """Append a [[proof]] (and its [[proof.step]] tables) to proof.toml, creating it if missing."""
    if any(isinstance(p, dict) and p.get("id") == proof["id"] for p in feature.raw_proofs):
        raise UsageError(f"{proof['id']} already exists in proof.toml")
    lines = ["[[proof]]"] + [f"{k} = {toml_value(v)}" for k, v in proof.items() if v not in (None, [], "")]
    for step in steps:
        lines += ["", "[[proof.step]]"] + [f"{k} = {toml_value(v)}" for k, v in step.items()
                                           if v not in (None, [], "", {})]
    _append(feature.path / "proof.toml", lines)
    return proof["id"]


def _block(lines: list[str], item_id: str, filename: str = "plan.toml") -> tuple[str, int, int]:
    """(table name, first line, end line exclusive) of the [[table]] whose id matches."""
    starts = [(i, m.group(1)) for i, ln in enumerate(lines) if (m := _HEADER.match(ln))]
    starts.append((len(lines), ""))
    id_line = re.compile(rf'^\s*id\s*=\s*["\']{re.escape(item_id)}["\']\s*(#.*)?$')
    for (start, name), (end, _) in zip(starts, starts[1:]):
        if not lines[start].lstrip().startswith("[["):
            continue
        span = _keys(lines, start, end).get("id")
        if span and id_line.match(lines[span[0]]):
            return name, start, end
    raise UsageError(f"no entry with id {item_id} in {filename}")


_KEY = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.*)$")


def _value_end(lines: list[str], i: int, end: int) -> int:
    """Index after the last line of the value that starts on line i.

    Follows multi-line strings (\"\"\" and ''') and arrays, ignoring brackets and
    delimiters that sit inside quoted strings, so text inside a value is never
    mistaken for a key.
    """
    value = _KEY.match(lines[i]).group(2)
    for delim in ('"""', "'''"):
        if value.startswith(delim):
            rest = value[3:]
            if delim in rest:
                return i + 1
            j = i + 1
            while j < end and delim not in lines[j]:
                j += 1
            return min(j + 1, end)
    if not value.startswith("["):
        return i + 1
    depth, j, text = 0, i, value
    while True:
        quote = None
        k = 0
        while k < len(text):
            c = text[k]
            if quote:
                if c == "\\" and quote == '"':
                    k += 1
                elif c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "#":
                break
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return j + 1
            k += 1
        j += 1
        if j >= end:
            return end
        text = lines[j]


def _keys(lines: list[str], start: int, end: int) -> dict[str, tuple[int, int]]:
    """Top-level keys of a table block → (first line, end line exclusive)."""
    found: dict[str, tuple[int, int]] = {}
    i = start + 1
    while i < end:
        m = _KEY.match(lines[i])
        if m and not lines[i].lstrip().startswith("#"):
            stop = _value_end(lines, i, end)
            found.setdefault(m.group(1), (i, stop))
            i = stop
        else:
            i += 1
    return found


def set_fields(feature: Feature, item_id: str, fields: dict) -> str:
    path = feature.path / "plan.toml"
    lines = path.read_text(encoding="utf-8").splitlines()
    name, start, end = _block(lines, item_id)
    if "id" in fields:
        raise UsageError("ids cannot be changed with set")
    for key in fields:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise UsageError(f"'{key}' is not a valid field name")
    check_fields(name, fields, _keys(lines, start, end))
    for key, raw in fields.items():
        new = f"{key} = {toml_value(coerce(key, raw))}".splitlines()
        spans = _keys(lines, start, end)
        if key in spans:
            hit, stop = spans[key]
            lines[hit:stop] = new
            end += len(new) - (stop - hit)
        else:
            insert_at = end
            while insert_at > start + 1 and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines[insert_at:insert_at] = new
            end += len(new)
    _save(path, "\n".join(lines) + "\n")
    return name


def remove(feature: Feature, item_id: str, filename: str = "plan.toml") -> str:
    """Delete the [[table]] with this id — with its sub-tables such as [[proof.step]] — and return
    its kind. Comments that introduce the next entry stay with it."""
    path = feature.path / filename
    lines = path.read_text(encoding="utf-8").splitlines()
    name, start, end = _block(lines, item_id, filename)
    while end < len(lines):  # [[proof.step]] / [proof.step.expect] belong to the [[proof]] above
        m = _HEADER.match(lines[end])
        if not m or not m.group(1).startswith(name + "."):
            break
        end = next((i for i in range(end + 1, len(lines)) if _HEADER.match(lines[i])), len(lines))
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    del lines[start:end]
    while start < len(lines) and start > 0 and not lines[start].strip() and not lines[start - 1].strip():
        del lines[start]  # no double blank line where the entry was
    text = "\n".join(lines).strip("\n")
    _save(path, text + "\n" if text else "")
    return name


def set_section(feature: Feature, filename: str, heading: str, body: str) -> None:
    """Replace one `## heading` section of a feature's Markdown file (release.md's Rollback): its
    template comment and any old text go, the other sections stay as they are."""
    body = body.strip()
    if not body:
        raise UsageError(f"{heading} needs some text")
    path = feature.path / filename
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else [f"# {feature.title}", ""]
    start = next((i for i, ln in enumerate(lines) if ln.strip().lower() == f"## {heading}".lower()), None)
    if start is None:
        lines += ["", f"## {heading}", body, ""]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        lines[start + 1:end] = [body, ""]
    write_atomic(path, "\n".join(lines).rstrip("\n") + "\n")


def rollback_missing(feature: Feature) -> bool:
    return not feature.sections("release.md").get("rollback")
