#!/usr/bin/env python3
"""Two-stem cache for the Elissa / Fadl Shaker SRT pipeline.

Contract: `docs/STEM-CACHE.md`. Default split is **two stems**, not four:

    vocals.flac        Demucs vocals
    instrumental.flac  Demucs no_vocals (mixture minus the vocal estimate)

The cache is content-addressed by the SHA-256 of the source object and lives on
GCS. Separation is a one-time GPU job; later alignment runs fetch the stems and
use `a1_align_run.py --skip-separation`.

Commands
--------
    audit      what is cached, what is missing (no downloads, no GPU)
    separate   download -> SHA-256 -> Demucs -> FLAC -> upload -> manifest
    fetch      pull cached stems into work/stems for A1

Examples
--------
    python scripts/gcs_stems.py audit --inventory work/lyrics/inventory.csv

    # benchmark on 3 tracks before the full pass
    python scripts/gcs_stems.py separate --inventory work/lyrics/inventory.csv \
        --device cuda --limit 3

    python scripts/gcs_stems.py separate --inventory work/lyrics/inventory.csv \
        --device cuda

    python scripts/gcs_stems.py fetch --inventory work/lyrics/inventory.csv \
        --local work/stems --limit 12

Use `--local-only --root /tmp/...` to exercise the whole flow without writing to
GCS. The source audio may still be fetched from GCS with `gcloud storage cp`.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np
import soundfile as sf

DEFAULT_ROOT = "gs://akbar-december-2024-backup/STEMS/htdemucs/v1"
DEFAULT_INVENTORY = "work/lyrics/inventory.csv"
DEFAULT_LOCAL = "work/stems"
DEFAULT_LOG_DIR = "work/stems-gcs"
CACHE_VERSION = "v1"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pkg_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{now()} {message}\n")


def is_gcs(uri: str) -> bool:
    return uri.startswith("gs://")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def gcs_list(uri: str) -> set[str]:
    proc = subprocess.run(["gcloud", "storage", "ls", "-r", uri],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def exists(uri: str) -> bool:
    if is_gcs(uri):
        return bool(gcs_list(uri))
    return Path(uri).exists()


def copy(src: str, dst: str) -> None:
    if is_gcs(src):
        run(["gcloud", "storage", "cp", src, dst])
    elif is_gcs(dst):
        run(["gcloud", "storage", "cp", src, dst])
    else:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_inventory(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit(f"inventory is empty: {path}")
    return rows


def manifest_location(root: str, local_manifest: str | None,
                      log_dir: str) -> Path:
    if local_manifest:
        return Path(local_manifest)
    if is_gcs(root):
        return Path(log_dir) / "manifest.jsonl"
    return Path(root) / "manifest.jsonl"


def load_manifest(root: str, local_manifest: str | None,
                  log_dir: str) -> dict[str, dict]:
    path = manifest_location(root, local_manifest, log_dir)
    if is_gcs(root) and exists(f"{root.rstrip('/')}/manifest.jsonl"):
        path.parent.mkdir(parents=True, exist_ok=True)
        copy(f"{root.rstrip('/')}/manifest.jsonl", str(path))
    if not path.exists():
        return {}
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        rows[row["track_id"]] = row
    return rows


def save_manifest(rows: dict[str, dict], root: str,
                  local_manifest: str | None, log_dir: str) -> None:
    path = manifest_location(root, local_manifest, log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(rows[k], ensure_ascii=False, sort_keys=True)
                        for k in sorted(rows)) + "\n"
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    if is_gcs(root):
        copy(str(path), f"{root.rstrip('/')}/manifest.jsonl")
    upload_logs(root, Path(log_dir))


def sync_logs(root: str, log_dir: Path) -> None:
    """Pull remote run.log / errors.log before appending on resume."""
    if not is_gcs(root):
        return
    for name in ("run.log", "errors.log"):
        remote = f"{root.rstrip('/')}/{name}"
        local = log_dir / name
        if exists(remote):
            local.parent.mkdir(parents=True, exist_ok=True)
            copy(remote, str(local))


def upload_logs(root: str, log_dir: Path) -> None:
    if not is_gcs(root):
        return
    for name in ("run.log", "errors.log"):
        local = log_dir / name
        if local.exists():
            try:
                copy(str(local), f"{root.rstrip('/')}/{name}")
            except subprocess.CalledProcessError as exc:
                print(f"[warn] could not upload {name}: {exc}", file=sys.stderr)


def probe(path: Path, source_duration: float | None = None) -> dict:
    info = sf.info(str(path))
    if source_duration and abs(info.duration - source_duration) > 1.0:
        raise ValueError(f"duration mismatch: {info.duration:.2f}s vs "
                         f"{source_duration:.2f}s")
    data, _ = sf.read(str(path), always_2d=True, dtype="float32")
    rms = float(np.sqrt(np.mean(np.square(data))))
    if not np.isfinite(data).all():
        raise ValueError("non-finite samples")
    if rms < 1e-6:
        raise ValueError("stem is silent")
    return {
        "bytes": path.stat().st_size,
        "format": info.format.lower(),
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "subtype": info.subtype,
        "duration_s": round(info.duration, 3),
        "rms": round(rms, 6),
    }


def to_flac(src: Path, dst: Path) -> None:
    data, sr = sf.read(str(src), always_2d=True, dtype="int16")
    sf.write(str(dst), data, sr, format="FLAC", subtype="PCM_16")


def gcs_glob_escape(uri: str) -> str:
    """Make a literal GCS object URI safe for gcloud's wildcard parser.

    gcloud storage treats ``[``, ``*`` and ``?`` in object names as glob
    metacharacters, so a track called ``13.Kermalak [Remix].mp3`` fails to
    download. Bracket expressions match the literal character.
    """
    return uri.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def source_fetch(uri: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if is_gcs(uri):
        run(["gcloud", "storage", "cp", gcs_glob_escape(uri), str(dst)])
    else:
        shutil.copy2(uri, dst)


def demucs(audio: Path, out_dir: Path, device: str, model: str,
           shifts: int, overlap: float, segment: int | None) -> Path:
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals",
           "-n", model, "-d", device, "--shifts", str(shifts),
           "--overlap", str(overlap), "-o", str(out_dir)]
    if segment:
        cmd += ["--segment", str(segment)]
    cmd.append(str(audio))
    run(cmd)
    return out_dir / model / audio.stem


def _device_name(device: str) -> str:
    if device.startswith("cuda"):
        try:
            import torch
            return torch.cuda.get_device_name(0)
        except Exception:
            return "cuda"
    return device


def build_row(track: dict, source_sha: str, source_md5: str,
              source_duration: float | None, vocals: Path, instrumental: Path,
              model: str, device: str, shifts: int, overlap: float,
              segment: int | None) -> dict:
    return {
        "track_id": track["track_id"],
        "artist": track.get("artist", ""),
        "album": track.get("album", ""),
        "file": track.get("file", ""),
        "source_uri": track.get("gcs_uri") or track.get("source_uri", ""),
        "source_sha256": source_sha,
        "source_md5": source_md5,
        "source_duration_s": source_duration,
        "stems": {
            "vocals": {
                "demucs_stem": "vocals",
                **probe(vocals, source_duration),
                "sha256": sha256(vocals),
            },
            "instrumental": {
                "demucs_stem": "no_vocals",
                **probe(instrumental, source_duration),
                "sha256": sha256(instrumental),
            },
        },
        "separator": {
            "name": "demucs",
            "model": model,
            "two_stems": "vocals",
            "device": device,
            "device_name": _device_name(device),
            "shifts": shifts,
            "overlap": overlap,
            "segment": segment,
            "versions": {"demucs": pkg_version("demucs"),
                         "torch": pkg_version("torch")},
            "cache_version": CACHE_VERSION,
        },
        "status": "ok",
        "created_at": now(),
    }


def _object_set(root: str) -> set[str]:
    if is_gcs(root):
        return gcs_list(root)
    return set()


def _has_objects(row: dict, objects: set[str]) -> bool:
    for key in ("vocals", "instrumental"):
        uri = (row.get("stems") or {}).get(key, {}).get("uri")
        if not uri:
            return False
        if is_gcs(uri):
            if uri not in objects:
                return False
        elif not Path(uri).is_file():
            return False
    return True


def cmd_audit(args) -> int:
    inventory = load_inventory(args.inventory)
    manifest = load_manifest(args.root, args.manifest, args.log_dir)
    objects = _object_set(args.root)

    cached, missing, failed, stale = [], [], [], []
    for track in inventory:
        row = manifest.get(track["track_id"])
        if not row:
            missing.append(track)
        elif row.get("status") != "ok":
            failed.append(row)
        elif _has_objects(row, objects):
            cached.append(row)
        else:
            stale.append(row)

    print(f"inventory : {len(inventory)}")
    print(f"cached ok : {len(cached)}")
    print(f"missing   : {len(missing)}")
    print(f"failed    : {len(failed)}")
    print(f"stale     : {len(stale)}  (manifest ok, stem object missing)")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["track_id", "artist", "album", "file", "status",
                        "source_sha256", "vocals_uri", "instrumental_uri"])
            for track in inventory:
                row = manifest.get(track["track_id"], {})
                stems = row.get("stems", {})
                if not row:
                    status = "missing"
                elif row.get("status") != "ok":
                    status = row.get("status", "failed")
                elif _has_objects(row, objects):
                    status = "ok"
                else:
                    status = "stale"
                w.writerow([track["track_id"], track.get("artist", ""),
                            track.get("album", ""), track.get("file", ""),
                            status, row.get("source_sha256", ""),
                            stems.get("vocals", {}).get("uri", ""),
                            stems.get("instrumental", {}).get("uri", "")])
        print(f"wrote {out}")
    return 0


def cmd_separate(args) -> int:
    inventory = load_inventory(args.inventory)
    if args.limit:
        inventory = inventory[:args.limit]
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    sync_logs(args.root, log_dir)
    run_log = log_dir / "run.log"
    err_log = log_dir / "errors.log"

    manifest = load_manifest(args.root, args.manifest, args.log_dir)
    objects = _object_set(args.root)
    by_sha: dict[str, dict] = {
        row["source_sha256"]: row for row in manifest.values()
        if row.get("status") == "ok" and row.get("source_sha256")
        and _has_objects(row, objects)
    }

    started = time.monotonic()
    deadline = started + args.max_minutes * 60 if args.max_minutes else None
    done = skipped = failed = 0
    consecutive_failures = 0

    append_log(run_log, f"start inventory={len(inventory)} root={args.root} "
                        f"device={args.device} max_minutes={args.max_minutes}")
    for i, track in enumerate(inventory, 1):
        track_id = track["track_id"]
        if deadline and time.monotonic() >= deadline:
            append_log(run_log, f"time budget reached after {i-1}/{len(inventory)}")
            print(f"[time] budget of {args.max_minutes} min reached; "
                  f"stopping cleanly after {i-1}/{len(inventory)} tracks")
            break

        old = manifest.get(track_id)
        reusable = bool(old and old.get("status") == "ok"
                        and _has_objects(old, objects) and not args.force)
        if reusable:
            print(f"[{i}/{len(inventory)}] cached: {track_id}")
            append_log(run_log, f"skip cached {track_id}")
            skipped += 1
            continue
        if old and old.get("status") == "ok" and not reusable:
            append_log(err_log, f"stale {track_id}: manifest ok but stem missing")
            print(f"[warn] stale manifest row, redoing: {track_id}")
        if args.dry_run:
            print(f"[{i}/{len(inventory)}] would separate: {track_id}")
            continue

        tmp = Path(tempfile.mkdtemp(prefix="stems-", dir=args.tmp_dir))
        try:
            # Use a wildcard-free local name: gcloud also rejects a *destination*
            # containing glob metacharacters (e.g. "13.Kermalak [Remix].mp3").
            # The cache key is the content hash, so the local name is irrelevant.
            suffix = Path(track["file"]).suffix
            if not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", suffix):
                suffix = ".mp3"
            source = tmp / f"source{suffix}"
            source_fetch(track.get("gcs_uri") or track.get("source_uri", ""),
                         source)
            source_sha = sha256(source)
            source_md5 = md5(source)
            duration = float(track["duration_s"]) if track.get("duration_s") else None

            if source_sha in by_sha and not args.force:
                base = by_sha[source_sha]
                row = {**base, "track_id": track_id,
                       "artist": track.get("artist", ""),
                       "album": track.get("album", ""),
                       "file": track.get("file", ""),
                       "source_uri": track.get("gcs_uri", ""),
                       "created_at": base.get("created_at", now())}
                manifest[track_id] = row
                save_manifest(manifest, args.root, args.manifest, args.log_dir)
                append_log(run_log, f"reuse hash {track_id} -> {source_sha}")
                print(f"[{i}/{len(inventory)}] reuse hash: {track_id}")
                skipped += 1
                continue

            print(f"[{i}/{len(inventory)}] separating: {track_id}", flush=True)
            t0 = time.monotonic()
            out = demucs(source, tmp / "out", args.device, args.model,
                         args.shifts, args.overlap, args.segment)
            vocals_flac = tmp / "vocals.flac"
            inst_flac = tmp / "instrumental.flac"
            to_flac(out / "vocals.wav", vocals_flac)
            to_flac(out / "no_vocals.wav", inst_flac)
            row = build_row(track, source_sha, source_md5, duration,
                            vocals_flac, inst_flac, args.model, args.device,
                            args.shifts, args.overlap, args.segment)

            dest = f"{args.root.rstrip('/')}/{source_sha}"
            for key, local in (("vocals", vocals_flac),
                               ("instrumental", inst_flac)):
                if args.local_only:
                    dst = Path(dest) / f"{key}.flac"
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local, dst)
                    row["stems"][key]["uri"] = str(dst)
                else:
                    uri = f"{dest}/{key}.flac"
                    copy(str(local), uri)
                    row["stems"][key]["uri"] = uri

            manifest[track_id] = row
            save_manifest(manifest, args.root, args.manifest, args.log_dir)
            by_sha[source_sha] = row
            objects.update(row["stems"][k]["uri"] for k in ("vocals", "instrumental"))
            elapsed = time.monotonic() - t0
            append_log(run_log, f"ok {track_id} in {elapsed:.0f}s")
            print(f"[{i}/{len(inventory)}] ok in {elapsed:.0f}s: {track_id}")
            done += 1
            consecutive_failures = 0
        except KeyboardInterrupt:
            append_log(run_log, "interrupted by user")
            print("\n[stop] interrupted; completed tracks are on GCS", file=sys.stderr)
            return 130
        except Exception as exc:  # keep the batch going; record the failure
            manifest[track_id] = {
                "track_id": track_id,
                "artist": track.get("artist", ""),
                "album": track.get("album", ""),
                "file": track.get("file", ""),
                "source_uri": track.get("gcs_uri", ""),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "created_at": now(),
            }
            try:
                save_manifest(manifest, args.root, args.manifest, args.log_dir)
            except Exception as save_exc:
                print(f"[fatal] cannot save manifest: {save_exc}", file=sys.stderr)
                return 2
            append_log(err_log, f"FAILED {track_id}\n{traceback.format_exc()}")
            print(f"[{i}/{len(inventory)}] FAILED: {track_id}: {exc}",
                  file=sys.stderr)
            failed += 1
            consecutive_failures += 1
            if args.max_failures and consecutive_failures >= args.max_failures:
                print(f"[fatal] {consecutive_failures} consecutive failures; "
                      "stopping to avoid burning GPU. Fix the cause and rerun.",
                      file=sys.stderr)
                append_log(err_log, f"stop: {consecutive_failures} consecutive failures")
                return 2
        finally:
            if not args.keep_temp:
                shutil.rmtree(tmp, ignore_errors=True)

    append_log(run_log, f"finish separated={done} skipped={skipped} failed={failed}")
    print(f"separated={done} skipped={skipped} failed={failed}")
    return 1 if failed else 0


def cmd_fetch(args) -> int:
    inventory = load_inventory(args.inventory)
    if args.limit:
        inventory = inventory[:args.limit]
    manifest = load_manifest(args.root, args.manifest, args.log_dir)
    local = Path(args.local)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    err_log = log_dir / "errors.log"
    done = skipped = missing = failed = 0
    for i, track in enumerate(inventory, 1):
        row = manifest.get(track["track_id"])
        if not row or row.get("status") != "ok":
            missing += 1
            continue
        stem_dir = local / "htdemucs" / Path(track["file"]).stem
        stem_dir.mkdir(parents=True, exist_ok=True)
        fetched_any = False
        try:
            for key in ("vocals", "instrumental"):
                dest = stem_dir / f"{key}.flac"
                if dest.exists() and not args.force:
                    continue
                src = row["stems"][key].get("uri")
                if not src:
                    raise ValueError(f"manifest row has no {key} uri")
                copy(src, str(dest))
                fetched_any = True
        except Exception as exc:
            append_log(err_log, f"FETCH FAILED {track['track_id']}\n"
                                f"{traceback.format_exc()}")
            print(f"[{i}/{len(inventory)}] FETCH FAILED: {track['track_id']}: {exc}",
                  file=sys.stderr)
            failed += 1
            continue
        if fetched_any:
            print(f"[{i}/{len(inventory)}] fetched: {track['track_id']}")
            done += 1
        else:
            skipped += 1
    upload_logs(args.root, log_dir)
    print(f"fetched={done} already_local={skipped} missing={missing} failed={failed}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--inventory", default=DEFAULT_INVENTORY)
        p.add_argument("--root", default=DEFAULT_ROOT,
                       help="GCS prefix or a local directory with --local-only")
        p.add_argument("--manifest", help="local mirror of manifest.jsonl")
        p.add_argument("--log-dir", default=DEFAULT_LOG_DIR,
                       help="local directory for manifest.jsonl, run.log, errors.log")
        p.add_argument("--local-only", action="store_true",
                       help="write stems under a local --root instead of GCS")

    p_audit = sub.add_parser("audit", help="report cached / missing tracks")
    common(p_audit)
    p_audit.add_argument("--out", help="write a per-track audit CSV")
    p_audit.set_defaults(func=cmd_audit)

    p_sep = sub.add_parser("separate", help="separate missing tracks")
    common(p_sep)
    p_sep.add_argument("--device", default="cuda", help="Demucs device")
    p_sep.add_argument("--model", default="htdemucs")
    p_sep.add_argument("--shifts", type=int, default=1)
    p_sep.add_argument("--overlap", type=float, default=0.25)
    p_sep.add_argument("--segment", type=int, default=None)
    p_sep.add_argument("--max-minutes", type=float, default=0,
                       help="optional clean-stop budget in minutes "
                            "(0 = no limit; do not treat as a Colab limit)")
    p_sep.add_argument("--max-failures", type=int, default=5,
                       help="stop after N consecutive failures (0 = never stop)")
    p_sep.add_argument("--limit", type=int)
    p_sep.add_argument("--force", action="store_true")
    p_sep.add_argument("--dry-run", action="store_true")
    p_sep.add_argument("--keep-temp", action="store_true")
    p_sep.add_argument("--tmp-dir", default=None)
    p_sep.set_defaults(func=cmd_separate)

    p_fetch = sub.add_parser("fetch", help="download cached stems for A1")
    common(p_fetch)
    p_fetch.add_argument("--local", default=DEFAULT_LOCAL)
    p_fetch.add_argument("--limit", type=int)
    p_fetch.add_argument("--force", action="store_true")
    p_fetch.set_defaults(func=cmd_fetch)

    args = ap.parse_args(argv)
    if args.local_only and is_gcs(args.root):
        ap.error("--local-only needs a local --root (e.g. /tmp/stems-cache)")
    if args.local_only and args.log_dir == DEFAULT_LOG_DIR:
        args.log_dir = str(Path(args.root) / "state")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
