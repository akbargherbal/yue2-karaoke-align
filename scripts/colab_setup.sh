#!/usr/bin/env bash
# Colab provisioning for the alignment benchmark. Idempotent.
#
#   bash scripts/colab_setup.sh
#
# Colab already ships torch/torchaudio with CUDA, so we only add the pieces it
# lacks. Re-runnable after a session restart.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

echo "== deps =="
pip install -q --upgrade demucs transformers soundfile

echo "== torch =="
python -c "import torch; print('torch', torch.__version__, '| cuda', torch.cuda.is_available(),
    '|', (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only'))"

echo "== aligner vocab (downloads the baseline model once) =="
python scripts/a0_align.py --check

echo
echo "ready. Next:"
echo "  1. stage songs: a folder of <name>.mp3 + <name>.txt pairs"
echo "  2. smoke:  python scripts/bench.py --models bench/models.txt --songs bench/songs.json \\"
echo "               --data-dir <DIR> --out results/bench --device cuda --sep-device cuda \\"
echo "               --limit-models 1 --limit-songs 1"
echo "  3. sweep:  the same without --limit-*"
