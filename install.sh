#!/bin/sh
# EGD installer — what it does and how to call it: usage() below, or `sh -s -- -h`.
set -eu

REPO="LocTran12310/egd"
SRC="${EGD_SOURCE:-git+https://github.com/$REPO.git}"   # EGD_SOURCE: install from elsewhere (testing)
PLUGIN=1
usage() {
  cat <<'EOF'
EGD installer — the `egd` CLI and, when Claude Code is here, its plugin.

  curl -fsSL https://raw.githubusercontent.com/LocTran12310/egd/main/install.sh | sh
  curl -fsSL …/install.sh | sh -s -- --no-plugin      # the CLI only

Later: `egd update` to update, `egd uninstall` to remove everything again.
EOF
}
for arg in "$@"; do
  case "$arg" in
    --no-plugin) PLUGIN=0 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'egd install: unknown option %s\n' "$arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
fail() { printf 'egd install: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is needed — install it, then run this again"

# uv brings its own Python, so nothing else is needed; pipx uses the Python it was installed with
if command -v uv >/dev/null 2>&1; then
  uv tool install --reinstall --quiet --python ">=3.11" "$SRC" || fail "uv could not install egd"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force "$SRC" >/dev/null || fail "pipx could not install egd (it needs Python 3.11 or newer)"
else
  fail "needs uv (recommended) or pipx. Get uv with:
  curl -LsSf https://astral.sh/uv/install.sh | sh
then run this installer again."
fi
say "✓ egd CLI"

if [ "$PLUGIN" = 1 ] && command -v claude >/dev/null 2>&1; then
  claude plugin marketplace add "$REPO" >/dev/null 2>&1 || claude plugin marketplace update egd >/dev/null 2>&1 || true
  if claude plugin install egd@egd >/dev/null 2>&1; then
    say "✓ Claude Code plugin — restart Claude Code to load it"
  else
    say "· Claude Code plugin not installed — run: claude plugin marketplace add $REPO && claude plugin install egd@egd"
  fi
elif [ "$PLUGIN" = 1 ]; then
  say "· Claude Code not found — the plugin can be added later with: egd update"
fi

if command -v egd >/dev/null 2>&1; then
  say ""
  say "Ready: $(egd --version)"
  say "  cd your-repo && egd setup && egd new <feature>"
  say "  egd update · egd uninstall · egd -h"
else
  say ""
  say "Installed, but egd is not on your PATH yet."
  command -v uv >/dev/null 2>&1 && say "Run: uv tool update-shell   (then open a new terminal)"
  command -v pipx >/dev/null 2>&1 && say "Run: pipx ensurepath   (then open a new terminal)"
fi
