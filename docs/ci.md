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

To pin a version, append `@v0.1.0` to the install URL. Or vendor
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
