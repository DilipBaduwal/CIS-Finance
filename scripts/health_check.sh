#!/usr/bin/env bash
set -u
status=0
python3 --version || status=1
if [ -f .env ]; then set -a; source .env; set +a; fi
PYTHONPATH=src python -m cis_fin.cli check-db || status=1
PYTHONPATH=src python -m cis_fin.cli check-qdrant || status=1
if command -v curl >/dev/null 2>&1; then curl -fsS "${OLLAMA_BASE_URL:-http://127.0.0.1:11434}/api/tags" >/dev/null || status=1; fi
mkdir -p artifacts && test -w artifacts || status=1
exit "$status"
