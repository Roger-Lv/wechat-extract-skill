#!/bin/bash
# Create the skill's private venv. Idempotent — safe to re-run.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

echo "skill dir: $SKILL_DIR"

if [ ! -x "$VENV/bin/python" ]; then
  echo "creating venv at $VENV"
  python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet cryptography zstandard

"$VENV/bin/python" - <<'PY'
import cryptography, zstandard
print(f"cryptography {cryptography.__version__}")
print(f"zstandard    {zstandard.__version__}")
print("ready")
PY
