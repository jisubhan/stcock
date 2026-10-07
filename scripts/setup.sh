#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if(major<20 || (major===20 && minor<9)){console.error("Node.js 20.9+ is required");process.exit(1)}'
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
npm ci --no-audit --no-fund --cache /workspace/.npm-cache
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check --cache-dir /workspace/.pip-cache -r apps/api/requirements.lock.txt
npm run typecheck
npm run test:api
