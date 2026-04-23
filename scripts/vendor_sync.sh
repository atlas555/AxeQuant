#!/usr/bin/env bash
# Sync backTestSys modules into the AxeQuant plugin package.
# One-way: backTestSys is the source of truth.
#
# Phase 1: signals only.
# Later phases extend MODULES=( ... ).
#
# Usage: bash scripts/vendor_sync.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="${BACKTESTSYS_SOURCE:-/Users/allen/coding/github/AxeAlgo1M/backTestSys}"
DEST="$REPO_ROOT/backend_api_python/app/services/backtestsys_plugin"

if [[ ! -d "$SOURCE" ]]; then
  echo "ERROR: BACKTESTSYS_SOURCE not found: $SOURCE" >&2
  exit 1
fi

MODULES=(signals)

echo "Vendoring from: $SOURCE"
echo "Into:           $DEST"
echo "Modules:        ${MODULES[*]}"
echo

for mod in "${MODULES[@]}"; do
  echo "  [*] rsync $mod/"
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    "$SOURCE/$mod/" "$DEST/$mod/"
done

echo
echo "  [*] Rewriting imports: backTestSys.* → app.services.backtestsys_plugin.*"
find "$DEST" -name '*.py' -type f -exec sed -i.bak \
  -e 's|from backTestSys\.|from app.services.backtestsys_plugin.|g' \
  -e 's|import backTestSys\.|import app.services.backtestsys_plugin.|g' \
  -e 's|"backTestSys\.|"app.services.backtestsys_plugin.|g' \
  -e "s|'backTestSys\.|'app.services.backtestsys_plugin.|g" {} \;
find "$DEST" -name '*.bak' -type f -delete

echo
echo "  [*] Audit: remaining backTestSys imports (should be empty):"
if grep -rn "backTestSys" "$DEST" --include='*.py' 2>/dev/null; then
  echo "ERROR: residual backTestSys imports — vendor surface incomplete" >&2
  exit 1
fi
echo "      (clean)"

echo
SOURCE_SHA="$(cd "$SOURCE/.." && git rev-parse HEAD 2>/dev/null || echo 'unknown')"
echo "$SOURCE_SHA" > "$DEST/VERSION"
echo "  [*] VERSION: $SOURCE_SHA"

echo
echo "Sync complete."
