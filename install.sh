#!/bin/bash
set -e
cd "$(dirname "$0")"
cp -n .env.example .env || true
python3 -m venv venv 2>/dev/null || true
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt
python - <<'PY'
from pathlib import Path
p=Path('.env')
print('Edit .env before starting:')
print(p.resolve())
PY
