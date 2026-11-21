#!/usr/bin/env python3
"""Build a score + karaoke viewer for every take in a run, plus an index.

This is the batch wrapper ported from `akbargherbal/score-lab` (MIT), adapted
to this repo's two input shapes:

  * **A1 output** — a dir of `<song>.words.json` (plus the audio, and an
    optional `<song>.musicxml` for score pages);
  * **SheetSage2 transcripts** — a tree of `<take>/score.abc` + `*.lab` files
    (the score-lab contract), with optional `.musicxml` pages.

Both can be mixed in one run. Audio comes from the take's own directory, from
the paths recorded in the words JSON, or from `--audio-dir` (a local dir or a
`gs://` prefix fetched with `gcloud storage cp`). Transcript archives
(`.tar.gz`, `.tgz`, `.tar`, `.zip`) are unpacked under `<out>/.transcripts/`,
so the run directory is never written to.

Per take it writes `<out>/<take>/{viewer.html,audio.*,pages/}` and one
`<out>/index.html` gallery. `--no-pages` never touches MuseScore or MusicXML:
the viewer is the waveform, chords, sections, piano roll and karaoke lane.

Usage
-----
    # A1 results dir, audio already next to the words JSON
    python scripts/view_run.py --run results/a1/yue2-real --out /tmp/view

    # YuE2 run dir: <name>_<seed>.wav + prompts/, with A1 words alongside
    python scripts/view_run.py --run /path/to/out/<run> \
        --audio-dir gs://<bucket>/.../<run> --out /path/to/out/<run>/score

    # score-lab transcript tree, no MuseScore
    python scripts/view_run.py --run ~/score-view/<run> \
        --transcripts transcripts --audio-dir gs://<bucket>/.../<run> \
        --out ~/score-view/<run> --no-pages
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from score_viewer import (  # noqa: E402
    AUDIO_SUFFIXES, copy_pages, render_pages, sorted_pages)

VIEWER = HERE / "score_viewer.py"
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")


@dataclass
class Take:
    name: str
    root: Path
    words: Path | None = None
    lab: Path | None = None
    musicxml: Path | None = None
    pages: Path | None = None
    audio: Path | None = None
    error: str | None = None


def is_archive(path: Path) -> bool:
    return path.is_file() and path.name.lower().endswith(ARCHIVE_SUFFIXES)


def archive_stem(archive: Path) -> str:
    for suffix in ARCHIVE_SUFFIXES:
        if archive.name.lower().endswith(suffix):
            return archive.name[:-len(suffix)]
    return archive.stem


def is_take_dir(directory: Path) -> bool:
    return ((directory / "score.abc").is_file()
            or (directory / "words.json").is_file()
            or any(directory.glob("*.words.json")))


def take_dirs(root: Path, depth: int = 3):
    """Yield dirs holding `score.abc` or a `*.words.json`, at most `depth` deep."""
    if is_take_dir(root):
        yield root
        return
    if depth <= 0 or not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            yield from take_dirs(child, depth - 1)


def archives_under(root: Path, depth: int = 3) -> list[Path]:
    if is_archive(root):
        return [root]
    if not root.is_dir() or depth <= 0:
        return []
    found: list[Path] = []
    for child in sorted(root.iterdir()):
        if is_archive(child):
            found.append(child)
        elif child.is_dir() and not child.name.startswith("."):
            found.extend(archives_under(child, depth - 1))
    return found


def member_names(archive: Path) -> list[str]:
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            return zf.namelist()
    with tarfile.open(archive) as tar:
        return tar.getnames()


def unpack(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
        return
    with tarfile.open(archive) as tar:
        try:
            tar.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12 has no extraction filters
            tar.extractall(dest)


def archive_take_names(names: list[str], archive: Path) -> set[str]:
    """Take names an archive claims to contain (top-level files included)."""
    found: set[str] = set()
    for name in names:
        path = PurePosixPath(name)
        if path.name in ("score.abc", "words.json"):
            found.add(path.parent.name or archive_stem(archive))
        elif path.name.endswith(".words.json"):
            found.add(path.parent.name
                      or path.name[:-len(".words.json")])
    return found


def words_name(path: Path, directory: Path) -> str:
    if path.name == "words.json":
        return directory.name
    if path.name.endswith(".words.json"):
        return path.name[:-len(".words.json")]
    return path.stem


def musicxml_for(directory: Path, name: str) -> Path | None:
    for candidate in (directory / f"{name}.musicxml",
                      directory.parent / f"{name}.musicxml",
                      directory / "score.musicxml"):
        if candidate.is_file():
            return candidate
    return None


def takes_from_dir(directory: Path) -> list[Take]:
    lab = directory if (directory / "score.abc").is_file() else None
    pages = directory / "pages" if sorted_pages(directory / "pages") else None

    words_files: list[Path] = []
    if (directory / "words.json").is_file():
        words_files.append(directory / "words.json")
    words_files.extend(sorted(directory.glob("*.words.json")))

    if not words_files:
        name = directory.name
        return [Take(name=name, root=directory, lab=lab,
                     musicxml=musicxml_for(directory, name), pages=pages)]

    takes = []
    seen: set[str] = set()
    for words in words_files:
        name = words_name(words, directory)
        if name in seen:
            continue
        seen.add(name)
        takes.append(Take(name=name, root=directory, words=words, lab=lab,
                          musicxml=musicxml_for(directory, name), pages=pages))
    return takes


def discover_takes(roots: list[Path], cache: Path) -> list[Take]:
    """Every take reachable from `roots`, unpacking archives as needed.

    Expanded takes win over archived copies of the same name. Archives are
    unpacked under `cache`, never next to themselves, so the run is not written.
    """
    takes: dict[str, Take] = {}

    def add(take: Take) -> None:
        takes.setdefault(take.name, take)

    for root in roots:
        for directory in take_dirs(root):
            for take in takes_from_dir(directory):
                add(take)

    for root in roots:
        for archive in archives_under(root):
            try:
                names = member_names(archive)
            except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
                print(f"skip {archive}: {exc}", file=sys.stderr)
                continue
            inside = archive_take_names(names, archive)
            if not inside:
                print(f"skip {archive.name}: no take inside", file=sys.stderr)
                continue
            missing = inside - takes.keys()
            if not missing:
                continue
            dest = cache / archive_stem(archive)
            have = {t.name for t in take_dirs(dest)} if dest.is_dir() else set()
            if missing - have:
                unpack(archive, dest)
                print(f"unpacked {archive.name}", flush=True)
            for directory in take_dirs(dest):
                for take in takes_from_dir(directory):
                    add(take)

    return sorted(takes.values(), key=lambda t: t.name)


# --- audio -----------------------------------------------------------------

def resolve_audio(take: Take, audio_dir: str | None, out_track: Path,
                  words_data: dict | None) -> Path | None:
    out_track.mkdir(parents=True, exist_ok=True)
    for ext in AUDIO_SUFFIXES:
        cached = out_track / f"audio{ext}"
        if cached.is_file() and cached.stat().st_size > 0:
            return cached

    def copy_from(src: Path) -> Path | None:
        if not src.is_absolute() and not src.is_file():
            # words JSON often records a path relative to where A1 ran, not to
            # this checkout. Try the take dir and the words file's dir too.
            for base in (take.root, take.words.parent if take.words else None,
                         Path.cwd()):
                if base is not None and (base / src).is_file():
                    src = base / src
                    break
        if not src.is_file():
            return None
        dest = out_track / f"audio{src.suffix.lower()}"
        shutil.copy2(src, dest)
        return dest

    if audio_dir:
        if str(audio_dir).startswith("gs://"):
            base = str(audio_dir).rstrip("/")
            # One listing is cheaper than six failed `cp` probes. Fall back to
            # per-extension probes if listing is not permitted.
            try:
                listing = subprocess.run(
                    ["gcloud", "storage", "ls", f"{base}/{take.name}.*"],
                    capture_output=True, text=True)
            except OSError as exc:
                print(f"warn: cannot run gcloud: {exc}", file=sys.stderr)
                return None
            uris = [line.strip() for line in listing.stdout.splitlines()
                    if line.strip()]
            if not uris:
                uris = [f"{base}/{take.name}{ext}" for ext in AUDIO_SUFFIXES]
            for uri in uris:
                ext = Path(uri).suffix.lower()
                if ext not in AUDIO_SUFFIXES:
                    continue
                dest = out_track / f"audio{ext}"
                proc = subprocess.run(
                    ["gcloud", "storage", "cp", uri, str(dest)],
                    capture_output=True, text=True)
                if (proc.returncode == 0 and dest.is_file()
                        and dest.stat().st_size > 0):
                    return dest
                if dest.exists():
                    dest.unlink()
        else:
            for ext in AUDIO_SUFFIXES:
                found = copy_from(Path(audio_dir) / f"{take.name}{ext}")
                if found:
                    return found

    for ext in AUDIO_SUFFIXES:
        found = copy_from(take.root / f"{take.name}{ext}")
        if found:
            return found

    # Some archives/take dirs store the audio as `audio.<ext>` rather than
    # `<take>.<ext>`.
    for ext in AUDIO_SUFFIXES:
        found = copy_from(take.root / f"audio{ext}")
        if found:
            return found

    for key in ("audio", "stem"):
        value = (words_data or {}).get(key)
        if value:
            found = copy_from(Path(value))
            if found:
                return found
    return None


# --- index -----------------------------------------------------------------

INDEX = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Score + karaoke viewers — {run}</title>
<style>
 body{{margin:0;background:#14171c;color:#e8eaef;font:14px/1.5 system-ui,"Segoe UI",sans-serif}}
 header{{padding:16px 20px;border-bottom:1px solid #262c35;background:#1c2129}}
 h1{{margin:0;font-size:17px}}
 .sub{{color:#8b93a1;font-size:12.5px;margin-top:4px}}
 table{{border-collapse:collapse;width:100%}}
 th,td{{padding:8px 10px;border-bottom:1px solid #222831;text-align:left;white-space:nowrap}}
 th{{color:#8b93a1;font-weight:500;font-size:12px;position:sticky;top:0;background:#14171c}}
 tr:hover{{background:#1a1f27}}
 a{{color:#4ea1ff;text-decoration:none}}
 a:hover{{text-decoration:underline}}
 img{{width:72px;border-radius:3px;background:#fff}}
 .num{{font-variant-numeric:tabular-nums;color:#b9c2cf}}
 .err{{color:#ff7b72}}
</style></head><body>
<header><h1>Score + karaoke viewers — {n} take(s)</h1>
<div class="sub">{run}</div></header>
<table><thead><tr>{cover_th}<th>take</th><th>duration</th><th>words</th>
<th>lines</th><th>coverage</th><th>bars</th><th>chords</th>{pages_th}
<th>viewer</th></tr></thead><tbody>
{rows}
</tbody></table></body></html>
"""


def count_lines(path: Path | None) -> int:
    if not path or not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8",
                                            errors="replace").splitlines()
               if line.strip())


def duration_of(audio: Path | None) -> float:
    if not audio:
        return 0.0
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", str(audio)],
            capture_output=True, text=True)
        return float(proc.stdout.strip())
    except (OSError, ValueError):
        return 0.0


def mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, required=True,
                    help="run dir, A1 results dir, or transcript root")
    ap.add_argument("--out", type=Path, default=None,
                    help="where <take>/viewer.html + index.html go "
                         "(default: <run>/score)")
    ap.add_argument("--audio-dir", default=None,
                    help="local dir or gs:// prefix holding <take>.mp3/.wav")
    ap.add_argument("--musicxml-dir", type=Path, default=None,
                    help="extra dir holding <take>.musicxml score files")
    ap.add_argument("--transcripts", default=None, metavar="PATH",
                    help="takes to render, relative to --run (default: "
                         "transcripts/ if it exists, else the run dir)")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--force", action="store_true", help="rebuild viewers")
    ap.add_argument("--no-pages", action="store_true",
                    help="karaoke/timeline only: no MuseScore, no MusicXML, "
                         "no page images")
    args = ap.parse_args(argv)

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("error: ffmpeg and ffprobe are required for the waveform and "
              "duration", file=sys.stderr)
        return 2
    run = args.run.expanduser().resolve()
    if not run.is_dir():
        print(f"--run is not a directory: {run}", file=sys.stderr)
        return 2
    out_root = (args.out or run / "score").expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    cache = out_root / ".transcripts"

    roots: list[Path] = []
    if args.transcripts:
        roots.append((run / args.transcripts).resolve())
    else:
        if (run / "transcripts").is_dir():
            roots.append(run / "transcripts")
        roots.append(run)
    takes = discover_takes(roots, cache)
    if not takes and args.transcripts:
        # A wrong/missing --transcripts should not hide a run that has the
        # takes elsewhere (score-lab falls back the same way).
        extra = [run / "abc_transcript", run]
        takes = discover_takes(extra, cache)
        roots += extra
    if not takes:
        print("no takes found: looked for <take>/score.abc or *.words.json in\n  "
              + "\n  ".join(str(r) for r in roots), file=sys.stderr)
        print("run scripts/a1_align_run.py first to produce <song>.words.json",
              file=sys.stderr)
        return 2
    if args.limit:
        takes = takes[:args.limit]

    rows = []
    ok_names: set[str] = set()
    built = 0
    for i, take in enumerate(takes, 1):
        if args.musicxml_dir and not take.musicxml:
            candidate = args.musicxml_dir.expanduser() / f"{take.name}.musicxml"
            if candidate.is_file():
                take.musicxml = candidate
        track_out = out_root / take.name
        viewer = track_out / "viewer.html"
        words_data = None
        if take.words and take.words.is_file():
            try:
                words_data = json.loads(take.words.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                take.error = f"bad words JSON: {exc}"
                print(f"warn: {take.name}: {take.error}", file=sys.stderr)

        audio = resolve_audio(take, args.audio_dir, track_out, words_data)
        if not audio:
            take.error = "audio not found"
            print(f"[{i}/{len(takes)}] skip {take.name}: audio not found",
                  file=sys.stderr)
            continue

        pages: list[Path] = []
        if not args.no_pages:
            if take.pages:
                pages = copy_pages(take.pages, track_out / "pages")
            elif take.musicxml:
                pages = render_pages(take.musicxml, track_out / "pages")
            else:
                pages = sorted_pages(track_out / "pages")

        if args.force or not viewer.is_file():
            track_out.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, str(VIEWER), "--audio", str(audio),
                   "--out", str(viewer), "--title", take.name]
            if take.words:
                cmd += ["--words", str(take.words)]
            if take.lab:
                cmd += ["--lab", str(take.lab)]
            if pages:
                cmd += ["--pages", str(track_out / "pages")]
            if args.no_pages:
                cmd += ["--no-pages"]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                take.error = proc.stderr.strip().splitlines()[-1] if proc.stderr else "viewer failed"
                print(f"[{i}/{len(takes)}] FAILED {take.name}: {take.error}",
                      file=sys.stderr)
                continue
            built += 1
        else:
            pages = sorted_pages(track_out / "pages")

        duration = duration_of(audio)
        n_words = len((words_data or {}).get("words") or [])
        n_lines = len((words_data or {}).get("lines") or [])
        coverage = ((words_data or {}).get("qa") or {}).get("coverage")
        cov_text = "—" if coverage is None else f"{coverage:.2f}"
        bars = count_lines(take.lab / "downbeat.lab") if take.lab else 0
        chords = count_lines(take.lab / "chord.lab") if take.lab else 0

        cover = ""
        if not args.no_pages:
            cover = (f'<td><a href="{html.escape(take.name)}/viewer.html">'
                     f'<img src="{html.escape(take.name)}/pages/{pages[0].name}" '
                     f'alt=""></a></td>' if pages else "<td></td>")
        rows.append(
            "<tr>"
            + cover
            + f'<td><a href="{html.escape(take.name)}/viewer.html">'
              f'{html.escape(take.name)}</a></td>'
            + f'<td class="num">{mmss(duration)}</td>'
            + f'<td class="num">{n_words}</td>'
            + f'<td class="num">{n_lines}</td>'
            + f'<td class="num">{cov_text}</td>'
            + f'<td class="num">{bars}</td>'
            + f'<td class="num">{chords}</td>'
            + (f'<td class="num">{len(pages)}</td>' if not args.no_pages else "")
            + f'<td><a href="{html.escape(take.name)}/viewer.html">open</a></td>'
            "</tr>")
        ok_names.add(take.name)
        print(f"[{i}/{len(takes)}] {take.name}  {duration:.0f}s  "
              f"{n_words} words  {len(pages)} pages", flush=True)

    error_span = 7 if args.no_pages else 9
    for take in takes:
        if take.error and take.name not in ok_names:
            rows.append(
                f'<tr><td class="err">{html.escape(take.name)}</td>'
                f'<td colspan="{error_span}" class="err">'
                f'{html.escape(take.error)}</td></tr>')

    index = out_root / "index.html"
    index.write_text(
        INDEX.format(run=html.escape(run.name), n=len(rows),
                     rows="\n".join(rows),
                     cover_th="" if args.no_pages else "<th>cover</th>",
                     pages_th="" if args.no_pages else "<th>pages</th>"),
        encoding="utf-8")
    print(f"\nbuilt {built} viewer(s); index: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
