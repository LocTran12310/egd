"""A feature on disk: brief, plan, design, proofs, release notes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .util import UsageError, features_dir, load_toml, md_sections

TIERS = ("lite", "standard", "full")

GATES = ("frame", "clarify", "design", "slice", "build", "accept", "release")

GATES_BY_TIER = {
    "lite": ("frame", "clarify", "slice", "build", "release"),
    "standard": GATES,
    "full": GATES,
}

PLAN_KINDS = ("assumption", "ac", "decision", "slice", "task")


def closest(word: str, choices, cutoff: float = 0.6) -> str | None:
    """The choice a typo most likely meant, if any is close enough."""
    import difflib  # only on a miss
    hit = difflib.get_close_matches(word, [str(c) for c in choices], n=1, cutoff=cutoff)
    return hit[0] if hit else None


@dataclass
class Feature:
    root: Path
    path: Path
    plan: dict = field(default_factory=dict)
    raw_proofs: list = field(default_factory=list)
    error: str | None = None

    def _items(self, kind: str) -> list[dict]:
        """Entries that can be addressed at all; `egd graph` reports the rest."""
        return [it for it in self.plan.get(kind, []) if isinstance(it, dict) and it.get("id")]

    @property
    def proofs(self) -> list[dict]:
        return [p for p in self.raw_proofs if isinstance(p, dict) and p.get("id")]

    # ---------------------------------------------------------------- identity
    @property
    def slug(self) -> str:
        return self.path.name

    @property
    def meta(self) -> dict:
        meta = self.plan.get("feature", {})
        return meta if isinstance(meta, dict) else {}

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.slug

    @property
    def tier(self) -> str:
        tier = self.meta.get("tier", "standard")
        return tier if tier in TIERS else "standard"

    @property
    def gates(self) -> tuple[str, ...]:
        return GATES_BY_TIER[self.tier]

    # ---------------------------------------------------------------- content
    def text(self, name: str) -> str:
        p = self.path / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def sections(self, name: str) -> dict[str, str]:
        return md_sections(self.text(name))

    @property
    def assumptions(self) -> list[dict]:
        return self._items("assumption")

    @property
    def acs(self) -> list[dict]:
        return self._items("ac")

    @property
    def decisions(self) -> list[dict]:
        return self._items("decision")

    @property
    def slices(self) -> list[dict]:
        return self._items("slice")

    @property
    def tasks(self) -> list[dict]:
        # a hand-written `touches = "src/**"` is one glob, not eight one-letter ones
        return [{**t, **{k: [t[k]] for k in ("touches", "tests", "depends_on", "done_when")
                         if isinstance(t.get(k), str)}} for t in self._items("task")]

    @property
    def footprint(self) -> list[str] | None:
        """Every path the feature may change: its tasks' touches and tests, its proofs' tests.
        None while a task declares no touches — then nothing can be told to lie outside it."""
        if not self.tasks or any(not t.get("touches") for t in self.tasks):
            return None
        return ([g for t in self.tasks for g in (*t.get("touches", []), *t.get("tests", []))]
                + [g for p in self.proofs for g in (p.get("tests") or [])])

    def task(self, task_id: str) -> dict:
        for t in self.tasks:
            if t.get("id") == task_id:
                return t
        raise UsageError(f"{self.slug}: no task {task_id}")

    def slice(self, slice_id: str) -> dict:
        for s in self.slices:
            if s.get("id") == slice_id:
                return s
        raise UsageError(f"{self.slug}: no slice {slice_id}")

    def tasks_in(self, slice_id: str) -> list[dict]:
        return [t for t in self.tasks if t.get("slice") == slice_id]

    def scope_hash(self) -> str:
        """Fingerprint of the agreed behaviour. Changing it after `clarify` needs a CR."""
        import hashlib
        import json
        canon = sorted(
            (a.get("id", ""), a.get("given", ""), a.get("when", ""), a.get("then", ""))
            for a in self.acs
        )
        return hashlib.sha256(json.dumps(canon).encode()).hexdigest()[:16]


def load_feature(root: Path, path: Path) -> Feature:
    """A broken file makes this feature report an error instead of breaking every command."""
    errors = []
    try:
        plan = load_toml(path / "plan.toml")
    except UsageError as exc:
        plan, _ = {}, errors.append(str(exc))
    try:
        proofs = load_toml(path / "proof.toml").get("proof", [])
    except UsageError as exc:
        proofs, _ = [], errors.append(str(exc))
    return Feature(root=root, path=path, plan=plan,
                   raw_proofs=proofs if isinstance(proofs, list) else [],
                   error="; ".join(errors) or None)


def all_features(root: Path) -> list[Feature]:
    base = features_dir(root)
    if not base.is_dir():
        return []
    return [load_feature(root, p) for p in sorted(base.iterdir()) if (p / "plan.toml").exists()]


def short_name(slug: str) -> str:
    """`2026-10-05-checkout` → `checkout`: the part people type."""
    return slug.split("-", 3)[-1]


def _names(features: list[Feature]) -> list[str]:
    """What to call each feature in a message: its short name, or the slug when two share one."""
    short = [short_name(f.slug) for f in features]
    return short if len(set(short)) == len(short) else [f.slug for f in features]


def resolve_feature(root: Path, ref: str | None) -> Feature:
    features = all_features(root)
    if not features:
        raise UsageError("no features yet — `egd new <name>`")
    if ref is None:
        if len(features) == 1:
            return features[0]
        raise UsageError("several features exist — name one with -f <feature>: " + ", ".join(_names(features)))
    for match in ((f for f in features if f.slug == ref), (f for f in features if short_name(f.slug) == ref)):
        hit = list(match)
        if len(hit) == 1:
            return hit[0]
    partial = [f for f in features if ref in f.slug]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise UsageError(f"'{ref}' is ambiguous: " + ", ".join(f.slug for f in partial))
    names = _names(features)
    hint = closest(ref, names)
    if hint:
        raise UsageError(f"no feature matches '{ref}' — did you mean '{hint}'?")
    raise UsageError(f"no feature matches '{ref}' — features: {', '.join(names[:8])}"
                     + (f" … and {len(names) - 8} more (`egd list`)" if len(names) > 8 else ""))


def feature_owning(root: Path, kind: str | None, item_id: str, ref: str | None) -> Feature:
    """The feature named by `ref`, else the one whose plan holds `item_id` (a `kind`, or any kind)."""
    if ref:
        return resolve_feature(root, ref)
    features = all_features(root)
    kinds = (kind,) if kind else PLAN_KINDS
    owners = [f for f in features if any(it["id"] == item_id for k in kinds for it in f._items(k))]
    if len(owners) == 1:
        return owners[0]
    if owners:
        raise UsageError(f"{item_id} exists in several features — pass -f <feature>: "
                         + ", ".join(f.slug for f in owners))
    what = f"no feature has {kind} {item_id}" if kind else f"no entry with id {item_id} in any plan.toml"
    known = [it["id"] for f in features for k in kinds for it in f._items(k)]
    hint = closest(item_id, known, cutoff=0.75)  # ids are short: only a near miss
    raise UsageError(what + (f" — did you mean {hint}?" if hint else ""))
