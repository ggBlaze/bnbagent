#!/usr/bin/env bash
# BNB Agent — publish to a private GitHub repo.
#
# Usage:
#   1. Create a NEW EMPTY private repo on GitHub (no README, no .gitignore).
#      → https://github.com/new  (set visibility = Private, leave all checkboxes OFF)
#   2. Run:  bash scripts/publish.sh git@github.com:<you>/<repo>.git
#   3. (Optional) push the tag:  git push origin v1.0.0
#
# Idempotent — safe to re-run.

set -e

REMOTE="${1:-}"

if [ -z "$REMOTE" ]; then
  echo "Usage: bash scripts/publish.sh <git-url>"
  echo "Example: bash scripts/publish.sh git@github.com:yourname/bnbagent.git"
  echo ""
  echo "Get the URL from:  https://github.com/<you>/<repo>  → Code → SSH"
  exit 1
fi

cd "$(dirname "$0")/.."

# Sanity checks
if [ ! -d .git ]; then
  echo "✗ not a git repo — run: git init -b main"
  exit 1
fi

if [ -z "$(git log --oneline 2>/dev/null)" ]; then
  echo "✗ no commits yet — commit first"
  exit 1
fi

# Set the remote
if git remote get-url origin >/dev/null 2>&1; then
  CURRENT=$(git remote get-url origin)
  if [ "$CURRENT" != "$REMOTE" ]; then
    echo "→ updating origin: $CURRENT → $REMOTE"
    git remote set-url origin "$REMOTE"
  fi
else
  echo "→ adding origin: $REMOTE"
  git remote add origin "$REMOTE"
fi

# Push
echo "→ pushing main"
git push -u origin main
echo "→ pushing tag v1.0.0"
git push origin v1.0.0

echo
echo "✓ published. View at:"
echo "  $(echo $REMOTE | sed 's/\.git$//')"
echo
echo "Next steps:"
echo "  1. Open your repo on GitHub and verify everything uploaded."
echo "  2. Add a short description in About → 'Autonomous BSC trading agent — BNB HACK 2026'."
echo "  3. (When ready) record the 3-min demo video (docs/demo-script.md) and add the link to README."
echo "  4. Submit on DoraHacks: https://dorahacks.io/hackathon/bnbhack-twt-cmc/"
