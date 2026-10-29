#!/usr/bin/env python3
"""Build one catalog over every published A1 prefix: the corpus, as a list.

Each run publishes `<prefix>/<track>.srt|.vtt|.words.json` with a manifest that
carries `track_id` (see `srt_publish.py`). This script reads those manifests,
joins them to the committed index, and writes:

    results/corpus/corpus.jsonl   one row per audio track: track_id, files, metrics
    results/corpus/CORPUS.md      the human view + the coverage check

No lyric text: names, uris, counts and timings only. The point is to be able to
answer "does every track we can align have an SRT?" from the repo alone.

Usage:
    python scripts/corpus_index.py                    # build from GCS
    python scripts/corpus_index.py --offline          # only committed manifests
    python scripts/corpus_index.py --check            # exit 1 if a matched track is missing
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

from srt_publish import index_slug

DEFAULT_BASE = "gs://akbar-december-2024-backup/SRT/v1"
# Prefixes are runs, not versions: one track must appear once across all of them.
RUNS = (
    "ready10-2026-10-28",
    "cleanup3-2026-10-28",
    "exp2-2026-10-28",
    "triage-2026-10-28",
    "corpus4-2026-10-29",
)


def load_manifest(path: str, offline: bool) -> list[dict]:
    """Rows of one run's manifest: local file if committed, else from GCS."""
    local = Path("results/a1") / Path(path).name / "manifest.jsonl"
    if local.is_file():
        return [json.loads(l) for l in local.read_text(encoding="utf-8").splitlines() if l.strip()]
    if offline:
        return []
    proc = subprocess.run(["gcloud", "storage", "cat", f"{path}/manifest.jsonl"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  cannot read {path}/manifest.jsonl: {proc.stderr.strip()[:120]}", file=sys.stderr)
        return []
    return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--runs", nargs="+", default=list(RUNS),
                    help="run labels to include (default: the four published runs)")
    ap.add_argument("--index", default="index/lyrics/title_map.csv")
    ap.add_argument("--coverage-csv", default="index/lyrics/coverage.csv")
    ap.add_argument("--out", default="results/corpus")
    ap.add_argument("--offline", action="store_true", help="committed manifests only")
    ap.add_argument("--check", action="store_true", help="fail if a matched in-scope track is missing")
    ap.add_argument("--max-duration", type=float, default=360.0,
                    help="scope filter; the stem cache only holds tracks within it")
    args = ap.parse_args(argv)

    index = {r["track_id"]: r for r in csv.DictReader(open(args.index, encoding="utf-8"))}
    cov = {r["track_id"]: r for r in csv.DictReader(open(args.coverage_csv, encoding="utf-8"))}

    # Manifests written before track_id existed carry only the filename, and
    # `name` vs `file` differ between generations; accept both.
    slug2tid = {index_slug(r["album"], r["file"]): t for t, r in index.items()}

    # track_id -> {srt, vtt, words, run}
    tracks: dict[str, dict] = {}
    dupes, unmapped = [], []
    for run in args.runs:
        rows = load_manifest(f"{args.base.rstrip('/')}/{run}", args.offline)
        for row in rows:
            fname = row.get("name") or row.get("file") or ""
            stem = (fname[:-len(".words.json")] if fname.endswith(".words.json")
                    else fname.rsplit(".", 1)[0])
            tid = row.get("track_id") or slug2tid.get(stem)
            if not tid:
                unmapped.append((run, fname))
                continue
            slot = tracks.setdefault(tid, {"run": run})
            if slot["run"] != run and any(k in slot for k in ("srt", "words")):
                dupes.append((tid, slot["run"], run))
            for kind, suffix in (("srt", ".srt"), ("vtt", ".vtt"), ("words", ".words.json")):
                if fname.endswith(suffix):
                    slot[kind] = row["uri"]

    in_scope = [t for t, r in index.items()
                if r["decision"] == "match" and float(r["duration_s"] or 0) <= args.max_duration]
    have = [t for t in in_scope if tracks.get(t, {}).get("srt")]
    missing = sorted(set(in_scope) - set(have))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "corpus.jsonl", "w", encoding="utf-8") as fh:
        for tid in sorted(have):
            row = index[tid]
            entry = {
                "track_id": tid, "run": tracks[tid]["run"],
                "srt": tracks[tid].get("srt"), "vtt": tracks[tid].get("vtt"),
                "words_json": tracks[tid].get("words"),
                "artist": row["artist"], "album": row["album"], "file": row["file"],
                "duration_s": row["duration_s"], "lyric_title": row["lyric_title"],
                "lyric_source": row["lyric_source"],
                "wpm": cov.get(tid, {}).get("wpm", ""),
                "readiness": cov.get(tid, {}).get("readiness", ""),
            }
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    files = {"srt": sum(1 for t in have if tracks[t].get("srt")),
             "vtt": sum(1 for t in have if tracks[t].get("vtt")),
             "words": sum(1 for t in have if tracks[t].get("words"))}
    by_run: dict[str, int] = {}
    for t in have:
        by_run[tracks[t]["run"]] = by_run.get(tracks[t]["run"], 0) + 1

    lines = [
        "# A1 corpus — one SRT per aligned track", "",
        f"Built by `scripts/corpus_index.py` from the published manifests under "
        f"`{args.base}`. Metadata only: no lyric text.", "",
        f"- matched tracks in scope (duration <= {args.max_duration:.0f}s): **{len(in_scope)}**",
        f"- with an SRT: **{len(have)}**",
        f"- missing: **{len(missing)}**",
        "",
        "| run | tracks |", "|---|---:|",
        *[f"| `{run}` | {n} |" for run, n in sorted(by_run.items())],
        "",
        f"Companion files: {files['srt']} SRT · {files['vtt']} VTT · {files['words']} words.json.",
        "",
    ]
    if missing:
        lines += ["## Missing", "", "| track | duration | readiness |", "|---|---:|---|"]
        lines += [f"| `{t}` | {index[t]['duration_s']} | {cov.get(t, {}).get('readiness', '')} |"
                  for t in missing]
        lines.append("")
    else:
        lines += ["Every matched in-scope track has its three files.", ""]
    if dupes:
        lines += ["## Alignment produced twice", ""]
        lines += [f"- `{tid}`: {a} and {b}" for tid, a, b in dupes]
        lines.append("")
    (out / "CORPUS.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"in scope {len(in_scope)} | with SRT {len(have)} | missing {len(missing)}")
    for run, n in sorted(by_run.items()):
        print(f"  {run:24} {n:>3} track(s)")
    if dupes:
        print(f"  WARN {len(dupes)} track(s) aligned twice")
    if unmapped:
        print(f"  WARN {len(unmapped)} object(s) not in the index, e.g. {unmapped[0]}")
    print(f"wrote {out}/corpus.jsonl and {out}/CORPUS.md")
    if missing and args.check:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
