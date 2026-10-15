#!/usr/bin/env bash
# A0 — isolate the vocal stem with Demucs (htdemucs, two-stems), CPU.
#
#   usage: scripts/a0_separate.sh <audio-in> [out-dir]
#
# Writes:
#   <out-dir>/htdemucs/<basename>/vocals.wav      <- feed this to a0_align.py
#   <out-dir>/htdemucs/<basename>/no_vocals.wav
#
# CPU-only; a 3-minute track takes several minutes. First run downloads the
# htdemucs weights (~80 MB).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$HERE/.venv}"

IN="${1:?usage: a0_separate.sh <audio-in> [out-dir]}"
OUT="${2:-$HERE/work/separated}"
mkdir -p "$OUT"

"$VENV/bin/python" -m demucs --two-stems=vocals -n htdemucs -d cpu -o "$OUT" "$IN"

STEM="$OUT/htdemucs/$(basename "${IN%.*}")/vocals.wav"
if [ -f "$STEM" ]; then
  echo "vocals: $STEM"
else
  echo "expected vocal stem not found: $STEM" >&2
  exit 1
fi
