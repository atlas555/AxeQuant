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

MODULES=(signals core config defense orchestrator evaluation optimizer execution strategies)

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

# Also vendor DataAuto as data_io/ (external dep of backTestSys runner).
DATAAUTO_SOURCE="${DATAAUTO_SOURCE:-$(dirname "$SOURCE")/DataAuto/program}"
if [[ -d "$DATAAUTO_SOURCE" ]]; then
  echo "  [*] rsync DataAuto → data_io/"
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    --exclude 'config.yaml' \
    --exclude 'config_future.yaml' \
    "$DATAAUTO_SOURCE/" "$DEST/data_io/"
fi

echo
echo "  [*] Rewriting imports: backTestSys.* → app.services.backtestsys_plugin.*"
find "$DEST" -name '*.py' -type f -exec sed -i.bak \
  -e 's|from backTestSys\.|from app.services.backtestsys_plugin.|g' \
  -e 's|import backTestSys\.|import app.services.backtestsys_plugin.|g' \
  -e 's|"backTestSys\.|"app.services.backtestsys_plugin.|g' \
  -e "s|'backTestSys\.|'app.services.backtestsys_plugin.|g" \
  -e 's|from DataAuto\.program\.|from app.services.backtestsys_plugin.data_io.|g' \
  -e 's|import DataAuto\.program\.|import app.services.backtestsys_plugin.data_io.|g' {} \;
find "$DEST" -name '*.bak' -type f -delete

echo
echo "  [*] Audit: remaining backTestSys imports (should be empty):"
residual=$(grep -rn "backTestSys" "$DEST" --include='*.py' 2>/dev/null | \
  grep -vE "(^[^:]+:[0-9]+:\s*#|backTestSys\..*\"\"\"|\"\"\".*backTestSys|:class:.*backTestSys|:func:.*backTestSys|:mod:.*backTestSys)" || true)
# Only flag as error if it's an actual import or code reference (not docstring/comment)
code_residual=$(echo "$residual" | grep -E "(^from backTestSys|^import backTestSys|from backTestSys\.|import backTestSys\.)" || true)
if [[ -n "$residual" ]]; then
  echo "$residual" | sed 's/^/      /'
fi
if [[ -n "$code_residual" ]]; then
  echo "ERROR: residual backTestSys imports in code — vendor surface incomplete" >&2
  exit 1
fi
if [[ -z "$residual" ]]; then
  echo "      (clean)"
else
  echo "  [*] Residual mentions are in comments/docstrings — runtime-safe"
fi

echo
SOURCE_SHA="$(cd "$SOURCE/.." && git rev-parse HEAD 2>/dev/null || echo 'unknown')"
echo "$SOURCE_SHA" > "$DEST/VERSION"
echo "  [*] VERSION: $SOURCE_SHA"

echo
echo "Sync complete."
