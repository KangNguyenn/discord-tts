#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  python -m venv --system-site-packages .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
exec python -m discord_tts.main "$@"
