#!/usr/bin/env bash
# Build the .venv used by the alignment pipeline (scripts/a0_align.py,
# scripts/a1_align_run.py, scripts/bench.py, ...).
#
#   scripts/setup.sh              # auto: CUDA wheels if a GPU is present, else CPU
#   scripts/setup.sh --cpu        # force CPU wheels
#   scripts/setup.sh --cuda       # force CUDA torch wheels
#   scripts/setup.sh --doctor     # build/reuse, then diagnostics
#   scripts/setup.sh --warm       # pre-download the aligner + demucs weights
#   scripts/setup.sh --force      # recreate the venv
#   scripts/setup.sh --plots      # also install matplotlib/seaborn/pandas
#
# The code targets torch's stable forced_align API, transformers' CTC models
# and Demucs 4, so requirements.txt carries lower bounds, not pins (tested with
# torch 2.14 / torchaudio 2.11 / transformers 5.19 / demucs 4.1.0 on Python
# 3.12-3.13). The torch flavor is auto-detected from the hardware: with an
# NVIDIA GPU (nvidia-smi) it installs the CUDA wheels so that a1_align_run.py's
# default `--device/--sep-device cuda` is honoured; without one, the CPU wheels
# (a much smaller download). --cpu/--cuda override the detection.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ALIGN_VENV:-$REPO_ROOT/.venv}"
PY="$VENV/bin/python"
FLAVOR="auto"         # auto | cpu | cuda
DOCTOR=0
FORCE=0
WARM=0
PLOTS=0

usage() {
  awk 'NR>1 && /^set -euo/{exit} NR>1{print}' "$0"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --cpu)    FLAVOR="cpu"; shift ;;
    --cuda)   FLAVOR="cuda"; shift ;;
    --doctor) DOCTOR=1; shift ;;
    --force)  FORCE=1; shift ;;
    --warm)   WARM=1; shift ;;
    --plots)  PLOTS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

echo "== prerequisites =="
command -v python3 >/dev/null 2>&1 || { echo "[fail] python3 not found" >&2; exit 1; }
if command -v uv >/dev/null 2>&1; then
  PIP=(uv pip install --python "$PY")
  echo "  uv:     $(uv --version)"
else
  # uv is optional; plain venv+pip works too, just slower.
  PIP=("$PY" -m pip install)
  echo "  pip:    $("$PY" -m pip --version 2>/dev/null || echo 'pip (via ensurepip at venv creation)')"
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "  [warn] ffmpeg not found; fine for the pipeline (soundfile decodes"
  echo "         wav/mp3), but handy for inspecting audio. Debian/Ubuntu:"
  echo "         apt-get install -y ffmpeg"
fi

echo "== venv =="
if [ -z "$VENV" ] || [ "$VENV" = "/" ] || [ "$VENV" = "$HOME" ] || [ "$VENV" = "$REPO_ROOT" ]; then
  echo "[fail] refusing unsafe ALIGN_VENV: $VENV" >&2
  exit 1
fi
if [ "$FORCE" = 1 ] && [ -d "$VENV" ]; then
  case "$VENV" in
    "$REPO_ROOT"/*) ;;
    *)
      echo "[fail] --force only removes a venv under $REPO_ROOT; remove $VENV manually" >&2
      exit 1
      ;;
  esac
  echo "  removing existing $VENV (--force)"
  rm -rf "$VENV"
fi
if [ ! -x "$PY" ]; then
  echo "  creating $VENV"
  if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV" --python 3.12
  else
    python3 -m venv "$VENV"
  fi
else
  echo "  reusing $VENV"
fi
# Plain venvs start without pip inside uv-created ones; make sure it exists.
if ! "$PY" -m pip --version >/dev/null 2>&1 && command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" -q pip
fi

echo "== torch =="
# Auto-detect the flavor from the hardware: nvidia-smi present and listing a
# GPU means CUDA wheels; anything else (CI, CPU-only laptop, WSL without the
# driver) gets the CPU wheels.
if [ "$FLAVOR" = auto ]; then
  if command -v nvidia-smi >/dev/null 2>&1 && [ -n "$(nvidia-smi -L 2>/dev/null)" ]; then
    FLAVOR=cuda
    echo "  GPU detected via nvidia-smi -> CUDA wheels"
  else
    FLAVOR=cpu
    echo "  no NVIDIA GPU detected -> CPU wheels"
  fi
fi
# Flavor check: a `+cpu` local build already satisfies `torch>=2.2`, so uv/pip
# would silently keep CPU wheels when switching to --cuda. Compare the local
# version tag and force a reinstall when the flavor differs.
INSTALLED=$("$PY" -c "import torch; print(torch.__version__)" 2>/dev/null || true)
case "$FLAVOR" in
  cpu)  TORCH_ARGS=(torch torchaudio --index-url https://download.pytorch.org/whl/cpu) ;;
  cuda) TORCH_ARGS=(torch torchaudio) ;;
esac
case "$INSTALLED" in
  *+cpu)
    if [ "$FLAVOR" = cpu ]; then
      echo "  already CPU wheels ($INSTALLED)"
    else
      echo "  switching $INSTALLED -> CUDA wheels"
      "${PIP[@]}" -q --reinstall "${TORCH_ARGS[@]}"
    fi
    ;;
  "")
    echo "  installing torch/torchaudio ($FLAVOR)"
    "${PIP[@]}" -q "${TORCH_ARGS[@]}"
    ;;
  *)
    if [ "$FLAVOR" = cuda ]; then
      echo "  already CUDA wheels ($INSTALLED)"
    else
      echo "  switching $INSTALLED -> CPU wheels"
      "${PIP[@]}" -q --reinstall "${TORCH_ARGS[@]}"
    fi
    ;;
esac

echo "== pipeline dependencies =="
"${PIP[@]}" -q -r "$REPO_ROOT/requirements.txt"
if [ "$PLOTS" = 1 ]; then
  echo "  + plotting extras (matplotlib, seaborn, pandas)"
  "${PIP[@]}" -q -r "$REPO_ROOT/scripts/requirements-plots.txt"
fi

"$PY" - <<'PYCHECK'
import torch, torchaudio, demucs, transformers, soundfile as sf
print(f"  [ok] torch {torch.__version__} | torchaudio {torchaudio.__version__}"
      f" | transformers {transformers.__version__} | demucs {demucs.__version__}")
if torch.cuda.is_available():
    print(f"  [ok] cuda: {torch.cuda.get_device_name(0)}")
else:
    print("  [warn] cuda not available; demucs/a1 will run on CPU (slow)")
print("  [ok] libsndfile mp3 decode: " +
      ("yes" if sf.check_format("MP3") else "NO — install a libsndfile with mp3 support"))
PYCHECK
if [ "$FLAVOR" = cuda ] && ! "$PY" -c "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)"; then
  echo "  [warn] CUDA wheels installed but torch.cuda.is_available() is False" >&2
  echo "         (driver/CUDA mismatch?); a1_align_run.py would fall back to CPU" >&2
fi

if [ "$WARM" = 1 ]; then
  echo "== warming model caches =="
  "$PY" - <<'PYWARM'
from demucs.pretrained import get_model
get_model("htdemucs")
print("  [ok] demucs htdemucs weights")
PYWARM
  # --check loads the production aligner's tokenizer/weights (pinned via
  # bench/models.lock) and verifies the CTC vocab.
  "$PY" scripts/a0_align.py --check
fi

if [ "$DOCTOR" = 1 ]; then
  echo "== doctor =="
  "$PY" scripts/a0_align.py --check
  "$PY" - <<'PYDOCTOR'
import torch
print(f"  torch {torch.__version__} | cuda available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"  device: {torch.cuda.get_device_name(0)} | torch built for CUDA: {torch.version.cuda}")
PYDOCTOR
fi

echo
echo "[done] $VENV ready"
echo "  source $VENV/bin/activate"
echo "  karaoke:  python scripts/a1_align_run.py --data-dir <DIR> --auto-songs"
echo "  bench:    python scripts/bench.py --models bench/models.txt --songs bench/songs.json \\"
echo "              --data-dir <DIR> --out results/bench --sep-device cuda --limit-models 1 --limit-songs 1"
