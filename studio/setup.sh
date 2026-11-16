#!/usr/bin/env bash
# À lancer au début de chaque session (conteneur neuf) depuis la racine du dépôt.
set -e
cd "$(dirname "$0")/.."
python3 -c "import faster_whisper" 2>/dev/null || pip install -q faster-whisper
command -v espeak-ng >/dev/null || (apt-get update -qq && apt-get install -y -qq espeak-ng) >/dev/null 2>&1 || true
[ -d assets/official/screenshots/People ] || python3 studio/fetch_assets.py
echo "[OK] studio prêt"
