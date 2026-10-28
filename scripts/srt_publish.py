#!/usr/bin/env python3
"""Publish an A1 run's SRT/VTT/words.json to GCS, with a verified manifest.

`work/` is ephemeral: a recycled runtime takes the SRTs with it, and the
committed `results/a1/` holds metrics only. The SRT is the deliverable, so a run
is published next to the stem cache:

    gs://akbar-december-2024-backup/SRT/v1/<run>/<name>.srt
    gs://akbar-december-2024-backup/SRT/v1/<run>/<name>.vtt
    gs://akbar-december-2024-backup/SRT/v1/<run>/<name>.words.json
    gs://akbar-december-2024-backup/SRT/v1/<run>/manifest.jsonl

`manifest.jsonl` carries uri + bytes + sha256 per file and **no lyric text**, so
a copy can be committed to `results/a1/<run>/`. It is uploaded **last**: a run
prefix with files but no manifest is an incomplete upload, by construction.

Usage
-----
    python scripts/srt_publish.py --dir work/srt/ready10 --run ready10-2026-10-29 --dry-run
    python scripts/srt_publish.py --dir work/srt/ready10 --run ready10-2026-10-29
    python scripts/srt_publish.py --dir work/srt/ready10 --run ready10-2026-10-29 --verify-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

SUFFIXES = (".srt", ".vtt", ".words.json")
DEFAULT_BASE = "gs://akbar-december-2024-backup/SRT/v1"
MANIFEST = "manifest.jsonl"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(directory: Path) -> list[Path]:
    files = [p for p in sorted(directory.iterdir())
             if p.is_file() and any(p.name.endswith(s) for s in SUFFIXES)]
    return files


def run_provenance(directory: Path) -> dict:
    """model/revision/flags from summary.json, when the run wrote one."""
    path = directory / "summary.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    keep = ("run", "date", "device", "model", "revision")
    return {k: data[k] for k in keep if k in data}


def gcloud(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gcloud", "storage", *args], capture_output=True, text=True)


def remote_listing(prefix: str) -> dict[str, int]:
    """uri -> bytes, from one recursive listing (empty dict if the prefix is new)."""
    proc = gcloud("ls", "-l", f"{prefix}/**")
    if proc.returncode != 0:
        return {}
    out: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        # Slugs contain spaces ("Elissa - 2004 ... .srt"), so locate the URI
        # instead of splitting the line: only the prefix before it is tabular.
        at = line.find("gs://")
        if at < 0:
            continue
        uri, head = line[at:].strip(), line[:at].split()
        try:
            out[uri] = int(head[0])
        except (IndexError, ValueError):
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="A1 output dir, e.g. work/srt/ready10")
    ap.add_argument("--run", help="run label used in the prefix (default: dir name)")
    ap.add_argument("--base", default=DEFAULT_BASE, help="gs:// prefix above <run>/")
    ap.add_argument("--manifest-out", help="also write the manifest here (e.g. results/a1/<run>)")
    ap.add_argument("--dry-run", action="store_true", help="list and hash; upload nothing")
    ap.add_argument("--verify-only", action="store_true", help="compare remote with local, upload nothing")
    args = ap.parse_args(argv)

    directory = Path(args.dir)
    if not directory.is_dir():
        sys.exit(f"{directory} is not a directory")
    run = args.run or directory.name
    prefix = f"{args.base.rstrip('/')}/{run}"
    files = collect(directory)
    if not files:
        sys.exit(f"no {'/'.join(SUFFIXES)} files in {directory}")

    prov = run_provenance(directory)
    rows = [{
        "run": run, "file": p.name, "uri": f"{prefix}/{p.name}",
        "bytes": p.stat().st_size, "sha256": sha256(p), **prov,
    } for p in files]

    srt = [r for r in rows if r["file"].endswith(".srt")]
    print(f"{run}: {len(srt)} SRT · {len(rows)} file(s) · {sum(r['bytes'] for r in rows)/1e6:.1f} MB")
    print(f"target  {prefix}/")
    if args.dry_run or args.verify_only:
        for r in rows:
            print(f"  {r['bytes']:>9}  {r['sha256'][:12]}  {r['file']}")
        if args.dry_run:
            return 0

    remote = remote_listing(prefix)
    if args.verify_only:
        missing = [r for r in rows if r["uri"] not in remote]
        size_bad = [r for r in rows if r["uri"] in remote and remote[r["uri"]] != r["bytes"]]
        print(f"remote: {len(remote)} object(s) under the prefix")
        for r in missing:
            print(f"  MISSING {r['file']}")
        for r in size_bad:
            print(f"  SIZE    {r['file']} local={r['bytes']} remote={remote[r['uri']]}")
        ok = not missing and not size_bad and (f"{prefix}/{MANIFEST}" in remote)
        print("verify:", "ok" if ok else "INCOMPLETE")
        return 0 if ok else 1

    uploaded = 0
    for r in rows:
        proc = gcloud("cp", str(directory / r["file"]), r["uri"])
        if proc.returncode != 0:
            print(f"FAILED {r['file']}: {proc.stderr.strip()[:160]}", file=sys.stderr)
            continue
        uploaded += 1
    if uploaded != len(rows):
        sys.exit(f"uploaded {uploaded}/{len(rows)}; manifest not written (prefix stays incomplete)")

    manifest = directory / MANIFEST
    manifest.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                        encoding="utf-8")
    proc = gcloud("cp", str(manifest), f"{prefix}/{MANIFEST}")
    if proc.returncode != 0:
        sys.exit(f"manifest upload failed: {proc.stderr.strip()[:200]}")

    if args.manifest_out:
        out = Path(args.manifest_out)
        out.mkdir(parents=True, exist_ok=True)
        (out / MANIFEST).write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")

    remote = remote_listing(prefix)
    ok = all(r["uri"] in remote and remote[r["uri"]] == r["bytes"] for r in rows)
    print(f"published {uploaded} file(s) + manifest -> {prefix}/")
    print("verify:", "ok" if ok else "SIZE MISMATCH — re-run with --verify-only")
    print(f"published_at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
