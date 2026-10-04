"""Who is on the team and what each person may sign."""

from __future__ import annotations

import os
from pathlib import Path

from .util import Refused, UsageError, egd_dir, git, load_toml, norm_name


def whoami(root: Path, given: str | None = None) -> tuple[str, str]:
    """The name to sign with and where it came from: --by, else EGD_USER, else git user.name."""
    if given and given.strip():
        return given.strip(), "--by"
    env = os.environ.get("EGD_USER", "").strip()
    if env:
        return env, "EGD_USER"
    return (git(root, "config", "user.name") or "").strip(), "git user.name"


class Team:
    def __init__(self, root: Path, config: dict):
        raw = load_toml(egd_dir(root) / "team.toml")
        self.members: list[dict] = raw.get("member", [])
        self.roles_for = config.get("roles", {})

    @property
    def configured(self) -> bool:
        return bool(self.members)

    def member(self, name: str) -> dict | None:
        key = norm_name(name)
        for m in self.members:
            aliases = [m.get("name", ""), m.get("github", ""), *m.get("aliases", [])]
            if key in {norm_name(a) for a in aliases if a}:
                return m
        return None

    def canonical(self, name: str, source: str = "--by") -> str:
        """`name` as team.toml spells it; refused, with the way out, when it matches nobody.

        With no team configured the name is used as given.
        """
        if not name:
            raise UsageError("who is signing? pass --by NAME, or `export EGD_USER=NAME`, "
                             "or set `git config user.name`")
        if not self.configured:
            return name
        member = self.member(name)
        if member is not None:
            return member.get("name") or member.get("github") or name
        team = ", ".join(m.get("name", "?") + (f" (@{m['github']})" if m.get("github") else "")
                         for m in self.members)
        how = (["sign as one of them: --by NAME, or `export EGD_USER=NAME`"] if source != "--by"
               else ["check the spelling — a member's name, github handle or alias works"])
        raise Refused(f"'{name}'{'' if source == '--by' else f' (from {source})'} is not in .egd/team.toml",
                      [f"team: {team}", *how,
                       f"or, if that is you, add it to your entry: aliases = [\"{name}\"]"])

    def require(self, name: str, action: str) -> None:
        """Refuse when a configured team says `name` may not perform `action`.

        With no team.toml, anyone may sign anything — solo use stays frictionless,
        and the trail still records who did what.
        """
        if not name or not name.strip():
            raise Refused("a name is required (--by)")
        if not self.configured:
            return
        member = self.member(name)
        if member is None:
            raise Refused(f"'{name}' is not in .egd/team.toml")
        allowed = set(self.roles_for.get(action, []))
        if allowed and not allowed & set(member.get("roles", [])):
            raise Refused(f"'{name}' lacks a role allowed to {action.replace('_', ' ')} "
                          f"(needs one of: {', '.join(sorted(allowed))})")

    def same(self, a: str | None, b: str | None) -> bool:
        """One person, whichever of their names or aliases was typed."""
        if not a or not b:
            return False
        if norm_name(a) == norm_name(b):
            return True
        ma, mb = self.member(a), self.member(b)
        return ma is not None and ma is mb

    def github_handle(self, name: str | None) -> str | None:
        if not name:
            return None
        m = self.member(name)
        return m.get("github") if m else None
