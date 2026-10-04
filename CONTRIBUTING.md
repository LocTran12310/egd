# Contributing

- Branch from `main`: `feat/<topic>` or `fix/<topic>`; one pull request per change.
- `python3 -m unittest discover -s tests` must pass. Add a test with every behaviour change.
- The CLI stays standard-library only (Python ≥ 3.11). Playwright is the single optional
  dependency and lives behind `proof/ui_runner.py` in its own process.
- A change to what a gate checks bumps `RULES` in `src/egd/__init__.py` and is noted in `CHANGELOG.md`.
- Keep `skills/*/SKILL.md` in step with the CLI — agents follow those files literally.

## Building the package

The CLI ships as one pure-Python wheel. Build it, then check the web UI made it in — the
console is served from package data, and a wheel without it starts but shows nothing:

```bash
uv build                                  # or: python3 -m pip install build && python3 -m build
python3 -m zipfile -l dist/egd_cli-*.whl | grep -E 'egd/console.html|egd/ui/'
```

Expect `egd/console.html` and every file of `src/egd/ui/` (`ui.css`, `ui.js`, `i18n.js`,
`i18n-vi.js`, `guide.css`, `guide.js`). A new file in `src/egd/ui/` is picked up by
`[tool.setuptools.package-data]` in `pyproject.toml`; any other non-Python file needs an entry
there. To try the wheel as users get it: `uv tool install --force dist/egd_cli-*.whl`, then
`egd --version` and `egd console`.

## Web UI

There is one web UI. `src/egd/console.html` is the app; `src/egd/web.py` assembles it with
the files in `src/egd/ui/`:

- `ui.css` (tokens and components: button, input, select, tabs, status, chip, panel, table,
  dialog, toast, inline confirm) and `ui.js` (Select, dialog, toast, theme, formatting,
  transcript renderer) — the shared design system
- `i18n.js` — `t()` and the language switch; `i18n-vi.js` — the Vietnamese strings
  (`tests/test_i18n_keys.py` checks every key has one)
- `guide.css`, `guide.js` — the "How EGD works" page

A snapshot (`egd site`, `egd board --html`) inlines all of them and stays a single
self-contained file. A served page (`egd console`, `egd serve`) inlines the design system and
`i18n.js`, and fetches `guide.*` and `i18n-vi.js` on first use from `/ui/<name>?v=<hash>`
— only the names in `web.LAZY` are served.
No framework and no build step: plain HTML, CSS and JavaScript. Use the shared classes and
`UI.select` instead of native `<select>`, `confirm()` or inline `style=""`.
