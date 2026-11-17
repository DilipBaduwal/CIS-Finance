#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
OLLAMA_MODEL=qwen3.5:latest PYTHONUNBUFFERED=1 PYTHONPATH=src .venv/bin/python scripts/score_healthcare_system.py --system 5
