#!/usr/bin/env bash
# À lancer au début de chaque session (conteneur neuf) depuis la racine du dépôt.
set -e
cd "$(dirname "$0")/.."
python3 -c "import faster_whisper" 2>/dev/null || pip install -q faster-whisper
command -v espeak-ng >/dev/null || (apt-get update -qq && apt-get install -y -qq espeak-ng) >/dev/null 2>&1 || true
[ -d assets/official/screenshots/People ] || python3 studio/fetch_assets.py
# voix libre Kokoro (gratuite) : modèle + voix depuis Hugging Face
python3 -c "import kokoro_onnx, soundfile" 2>/dev/null || pip install -q kokoro-onnx soundfile
if [ ! -f .cache/kokoro/voices.npz ]; then
  mkdir -p .cache/kokoro && B=https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main
  curl -sL -o .cache/kokoro/model.onnx "$B/onnx/model.onnx"
  for v in am_michael am_fenrir am_puck am_onyx am_eric am_liam am_adam am_echo bm_george bm_fable bm_lewis; do
    curl -sL -o .cache/kokoro/$v.bin "$B/voices/$v.bin"; done
  python3 -c "import numpy as np,glob,os; np.savez('.cache/kokoro/voices.npz', **{os.path.basename(f)[:-4]: np.fromfile(f,dtype=np.float32).reshape(-1,1,256) for f in glob.glob('.cache/kokoro/*.bin')})"
fi
echo "[OK] studio prêt"
