# EGD in CI

## Validate plans on every pull request, publish the board

`.github/workflows/egd.yml` in a project that uses EGD:

```yaml
name: egd
on:
  pull_request:
  push:
    branches: [main]

jobs:
  egd:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }          # freshness checks need history
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pipx install git+https://github.com/LocTran12310/egd.git
      - run: egd lint                     # exit 1 on a broken plan or trail
      - run: |
          egd board
          cat .egd/BOARD.md >> "$GITHUB_STEP_SUMMARY"
```

## Re-prove after a merge, without an agent

Evidence stays fresh across a merge that only touches other features' files; what a merge did
reach — a file in the feature's footprint, or a shared file under `watch` — goes stale.
`--stale-only` re-runs just those proofs, and exits 0 when nothing needs running:

```yaml
      - run: |
          for f in $(ls .egd/features); do
            egd proof run "$f" --by egd-bot --stale-only --jobs 4 --fail-fast
          done
```

Only a failure needs a person or an agent to look. Squash merges and rebases are fine: each run
records a fingerprint of the content it covered, so a commit missing from the CI clone does not
make its evidence stale. Mark proofs that share a database or a port `serial = true` before
raising `--jobs`.

To pin a version, append `@v0.1.6` to the install URL. Or vendor
`bin/` + `src/` into the project.

## Keep GitHub Issues in sync

```yaml
  sync:
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions: { issues: write, contents: write }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pipx install git+https://github.com/LocTran12310/egd.git
      - run: egd sync github --by egd-bot
        env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}" }
      - run: |
          git config user.name egd-bot
          git config user.email egd-bot@users.noreply.github.com
          git add .egd && git commit -m "egd: sync issues" || true
          git push
```

`sync` records the issue number for each task in the trail, so commit the trail
back as above. Add `egd-bot` to `team.toml` if roles are configured.
