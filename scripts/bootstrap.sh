#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/dev.py setup
echo "Next: python3 scripts/dev.py up && python3 scripts/dev.py migrate && python3 scripts/dev.py seed"
