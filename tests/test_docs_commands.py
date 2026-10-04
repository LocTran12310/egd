"""Every `egd <command> --flag` the skills, agents and docs show exists in the CLI.

Agents follow those files literally, so a renamed command or flag must fail here, not in a
user's session. Commands and flags are read from the live parser (`build_parser()`), so a flag
added to the CLI is known here without touching this file.
"""

import argparse
import re
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts src/ on the path)

from egd.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted([*ROOT.glob("skills/**/*.md"), *ROOT.glob("agents/*.md"), *ROOT.glob("docs/**/*.md"),
               ROOT / "README.md", ROOT / "ROADMAP.md", ROOT / "CONTRIBUTING.md"])

# planned, not built: named in the roadmap and design notes on purpose
PLANNED = {"publish", "pull-signatures", ("new", "--from-issue")}

CALL = re.compile(r"(?<![\w./@-])egd[ \t]+(--?[a-z][\w-]*|[a-z][\w-]*)")
FLAG = re.compile(r"(?<![\w-])--[a-z][\w-]*")
END = re.compile(r"\s#\s|\||·|→|;|&&|\begd\b")  # where one shown command ends


def parser_flags(p: argparse.ArgumentParser) -> set[str]:
    return {o for a in p._actions for o in a.option_strings if o.startswith("--")}


def commands() -> dict[str, set[str]]:
    p = build_parser()
    subs = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    root = parser_flags(p)
    return {name: parser_flags(sp) | root for name, sp in subs.choices.items()} | {"": root}


def code(text: str):
    """(line number, code) for each fenced block line (continuations joined) and inline code span."""
    fenced, held, start = False, "", 0
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            if not held:
                start = n
            held += line.rstrip("\\") + " "
            if not line.rstrip().endswith("\\"):
                yield start, held
                held = ""
        else:
            for span in re.findall(r"`([^`\n]+)`", line):
                yield n, span


def mentions(text: str):
    """(line, command, [long flags]) for every `egd …` shown in code."""
    for n, snippet in code(text):
        for m in CALL.finditer(snippet):
            rest = snippet[m.end():]
            stop = END.search(rest)
            rest = rest[:stop.start()] if stop else rest
            word = m.group(1)
            if word.startswith("-"):  # `egd --version`; a short flag (`egd -h`) is not checked
                yield n, "", [word, *FLAG.findall(rest)] if word.startswith("--") else FLAG.findall(rest)
            else:
                yield n, word, FLAG.findall(rest)


class DocsMatchTheCli(unittest.TestCase):
    def test_every_shown_command_and_flag_exists(self):
        known = commands()
        problems, seen = [], 0
        for path in DOCS:
            for n, cmd, flags in mentions(path.read_text(encoding="utf-8")):
                seen += 1
                where = f"{path.relative_to(ROOT)}:{n}"
                if cmd in PLANNED:
                    continue
                if cmd not in known:
                    problems.append(f"{where}: no command `egd {cmd}`")
                    continue
                for flag in flags:
                    if flag not in known[cmd] and (cmd, flag) not in PLANNED:
                        problems.append(f"{where}: `egd {cmd}` has no {flag}")
        self.assertGreater(seen, 100)  # the scan itself still finds the docs' commands
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_scan_reads_code_only(self):
        text = ("Run `egd status` first; the egd skill knows.\n"
                "```bash\negd add task --by me \\\n    --touches 'src/**'   # then egd ready\n"
                "egd bug fix BUG-1 --task T-1  ·  egd bug close BUG-1 --by qa\n```\n"
                "`python3 <plugin>/bin/egd <command>` · `.egd/config.toml` · `egd --version`\n")
        self.assertEqual(list(mentions(text)), [
            (1, "status", []), (3, "add", ["--by", "--touches"]), (3, "ready", []),
            (5, "bug", ["--task"]), (5, "bug", ["--by"]), (7, "", ["--version"])])


if __name__ == "__main__":
    unittest.main()
