#!/usr/bin/env python3
"""Flask review app for the title map (L2) — one card, four keys.

Reads ``work/review/items.jsonl``, streams the track's original MP3 from GCS into
the browser (cached on disk, prefetched ahead), and records one decision per
track in SQLite plus an append-only event log. Every answer can mirror to GCS so
a dead Colab session loses nothing.

Routes
------
    GET  /                card: current unresolved item
    GET  /audio/<id>      the MP3, HTTP Range (seeking works)
    POST /answer          c1 | c2 | none | unsure  (+ note)
    GET  /audit           all answers, editable
    POST /audit           one-row correction
    GET  /export.csv      title-map-shaped export
    GET  /health          liveness (no token required)

Usage
-----
    python scripts/review_build.py --out work/review/items.jsonl
    python scripts/review_app.py --items work/review/items.jsonl \
        --db work/review/answers.db --audio-dir work/review/audio \
        --token "$TOKEN" --port 5000

On Colab, run it behind a tunnel (see docs/REVIEW-APP.md §5).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, redirect, render_template_string,
                   request, send_file, session, url_for)

ANSWER_MAP = {
    "c1": ("match", 1),
    "c2": ("match", 2),
    "none": ("no_match", 0),
    "unsure": ("ambiguous", 0),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    track_id      TEXT PRIMARY KEY,
    decision      TEXT NOT NULL,
    chosen        INTEGER NOT NULL DEFAULT 0,
    lyric_title   TEXT,
    lyric_source  TEXT,
    note          TEXT,
    reviewer      TEXT,
    answered_at   TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    track_id  TEXT NOT NULL,
    action    TEXT NOT NULL,
    payload   TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gcs_glob_escape(uri: str) -> str:
    """gcloud treats [ * ? as wildcards; two real tracks contain brackets."""
    return uri.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


class Store:
    """SQLite answers + append-only event log + optional GCS mirror."""

    def __init__(self, db_path: Path, events_path: Path, mirror_uri: str | None,
                 reviewer: str):
        self.db_path = db_path
        self.events_path = events_path
        self.mirror_uri = mirror_uri
        self.reviewer = reviewer
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)
        if mirror_uri:
            self._restore_mirror()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def answers(self) -> dict[str, dict]:
        with self.connect() as c:
            return {r["track_id"]: dict(r) for r in c.execute("SELECT * FROM answers")}

    def save(self, track_id: str, decision: str, chosen: int,
             lyric_title: str | None, lyric_source: str | None, note: str) -> None:
        ts = now()
        with self.connect() as c:
            c.execute("""
                INSERT INTO answers (track_id, decision, chosen, lyric_title,
                                     lyric_source, note, reviewer, answered_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(track_id) DO UPDATE SET
                    decision=excluded.decision, chosen=excluded.chosen,
                    lyric_title=excluded.lyric_title,
                    lyric_source=excluded.lyric_source, note=excluded.note,
                    reviewer=excluded.reviewer, updated_at=excluded.updated_at
            """, (track_id, decision, chosen, lyric_title, lyric_source, note,
                  self.reviewer, ts, ts))
            c.execute("INSERT INTO events (ts, track_id, action, payload) VALUES (?,?,?,?)",
                      (ts, track_id, "answer",
                       json.dumps({"decision": decision, "chosen": chosen})))
        self._append_event(track_id, decision, chosen, note, ts)

    def _append_event(self, track_id: str, decision: str, chosen: int,
                      note: str, ts: str) -> None:
        line = json.dumps({
            "ts": ts, "track_id": track_id, "action": "answer",
            "decision": decision, "chosen": chosen, "note": note,
            "reviewer": self.reviewer,
        }, ensure_ascii=False)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.mirror_uri:
            threading.Thread(target=self._mirror, daemon=True).start()

    def _mirror(self) -> None:
        try:
            subprocess.run(["gcloud", "storage", "cp", str(self.events_path),
                            self.mirror_uri],
                           capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[mirror] failed: {exc}", file=sys.stderr)

    def _restore_mirror(self) -> None:
        tmp = self.events_path.with_suffix(".restore.jsonl")
        try:
            proc = subprocess.run(["gcloud", "storage", "cp", self.mirror_uri, str(tmp)],
                                  capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return
        if proc.returncode != 0 or not tmp.exists():
            return
        print(f"[mirror] restored {tmp}", file=sys.stderr)
        with open(tmp, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Answered here already? Local DB wins; the mirror is a backup.
                if ev["track_id"] in self.answers():
                    continue
                self.save(ev["track_id"], ev["decision"], ev.get("chosen", 0),
                          None, None, ev.get("note", ""))
        tmp.unlink(missing_ok=True)


class AudioCache:
    """Fetch-on-demand MP3 cache with per-file locks and background prefetch."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def path(self, item: dict) -> Path:
        key = hashlib.sha1(item["track_id"].encode()).hexdigest()[:12]
        return self.dir / f"{key}.mp3"

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def ensure(self, item: dict) -> Path | None:
        dst = self.path(item)
        if dst.exists() and dst.stat().st_size > 0:
            return dst
        with self._lock(str(dst)):
            if dst.exists() and dst.stat().st_size > 0:
                return dst
            tmp = dst.with_suffix(".part")
            try:
                proc = subprocess.run(
                    ["gcloud", "storage", "cp", gcs_glob_escape(item["gcs_uri"]),
                     str(tmp)], capture_output=True, text=True, timeout=300)
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"[audio] {item['file']}: {exc}", file=sys.stderr)
                return None
            if proc.returncode != 0 or not tmp.exists():
                print(f"[audio] {item['file']}: {proc.stderr.strip()[:200]}",
                      file=sys.stderr)
                tmp.unlink(missing_ok=True)
                return None
            tmp.replace(dst)
        return dst

    def prefetch(self, items: list[dict]) -> None:
        for item in items:
            if item.get("audio_exists") and not self.path(item).exists():
                threading.Thread(target=self.ensure, args=(item,), daemon=True).start()


PAGE = """<!doctype html>
<html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>مراجعة العناوين</title>
<style>
  :root { --bg:#0f1115; --card:#171a21; --line:#2a2f3a; --txt:#e7e9ee;
          --dim:#9aa3b2; --acc:#4f8cff; --ok:#2ea36b; --warn:#b8862b; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
         font:16px/1.6 system-ui,"Segoe UI",Tahoma,sans-serif; }
  .wrap { max-width:860px; margin:0 auto; padding:18px 16px 60px; }
  .bar { display:flex; justify-content:space-between; align-items:center;
         color:var(--dim); font-size:14px; margin-bottom:10px; }
  h1 { font-size:19px; margin:0 0 2px; }
  .meta { color:var(--dim); font-size:14px; margin-bottom:14px; }
  audio { width:100%; margin:10px 0 18px; }
  .cand { display:block; background:var(--card); border:1px solid var(--line);
          border-radius:12px; padding:12px 14px; margin-bottom:10px; }
  .cand .row { display:flex; justify-content:space-between; gap:12px; }
  .cand .t { font-weight:600; font-size:17px; }
  .cand .s { color:var(--dim); font-size:13px; white-space:nowrap; }
  .cand .x { color:var(--dim); font-size:14px; margin-top:4px; }
  .cand a { color:var(--acc); font-size:12px; text-decoration:none; }
  .btns { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:18px 0 12px; }
  button { font:inherit; padding:14px 10px; border-radius:10px; border:1px solid var(--line);
           background:#1d2330; color:var(--txt); cursor:pointer; }
  button:hover { border-color:var(--acc); }
  button.primary { background:#1b3a63; border-color:#2c5c99; }
  button.ghost { background:transparent; color:var(--dim); }
  input[type=text], textarea, select {
      width:100%; background:#12151b; color:var(--txt); border:1px solid var(--line);
      border-radius:8px; padding:9px 10px; font:inherit; }
  .foot { display:flex; justify-content:space-between; margin-top:14px; font-size:14px; }
  .foot a { color:var(--dim); text-decoration:none; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th,td { text-align:right; padding:8px 6px; border-bottom:1px solid var(--line); }
  th { color:var(--dim); font-weight:500; }
  .pill { padding:2px 8px; border-radius:99px; font-size:12px; background:#232936; }
  .pill.match { background:#17402c; color:#8ee0ae; }
  .pill.no_match { background:#3d1f22; color:#f0a0a6; }
  .pill.ambiguous { background:#3d3418; color:#e6c877; }
  .err { background:#3d1f22; border:1px solid #6b2a2f; padding:10px 12px;
         border-radius:10px; margin-bottom:12px; }
</style></head><body><div class="wrap">
{% block body %}{% endblock %}
</div>
<script>
document.addEventListener('keydown', function (e) {
  var t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return;
  var audio = document.querySelector('audio');
  if (e.key === ' ' && audio) { e.preventDefault();
      audio.paused ? audio.play() : audio.pause(); return; }
  if (e.key === 'ArrowLeft' && window.PREV) { location.href = window.PREV; return; }
  var map = {'1':'c1','2':'c2','3':'none','4':'unsure'};
  if (map[e.key] && window.ANSWER_FORM) submitAnswer(map[e.key]);
});
function submitAnswer(v) {
  var f = document.getElementById('answer-form'); if (!f) return;
  f.answer.value = v; f.submit();
}
</script></body></html>"""

CARD = PAGE.replace("{% block body %}{% endblock %}", """
<div class="bar">
  <span>{{ item.idx }} / {{ total }}</span>
  <span>تمت {{ answered }} · بقي {{ total - answered }}</span>
</div>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<h1>{{ item.file }}</h1>
<div class="meta">{{ item.artist }} · {{ item.album }} · {{ mmss }}</div>

<audio controls preload="auto" src="{{ url_for("serve_audio", item_id=item.idx) }}"></audio>

<div class="meta">هذه الأغنية هي:</div>
{% for c in item.candidates %}
  <label class="cand">
    <div class="row">
      <span class="t">{{ c.n }}) {{ c.title }}</span>
      <span class="s">{{ c.score|round(0)|int }} · {{ c.source }}</span>
    </div>
    <div class="x">{{ c.excerpt }}</div>
    {% if c.url %}<a href="{{ c.url }}" target="_blank" rel="noopener">المصدر</a>{% endif %}
  </label>
{% endfor %}

<form id="answer-form" method="post" action="{{ url_for('answer') }}">
  <input type="hidden" name="track_id" value="{{ item.track_id }}">
  <input type="hidden" name="answer" value="">
  <input type="text" name="note" placeholder="ملاحظة (اختياري)" autocomplete="off">
  <div class="btns">
    <button type="button" class="primary" onclick="submitAnswer('c1')">1 · نعم، الأول</button>
    <button type="button" onclick="submitAnswer('c2')">2 · لا، الثاني</button>
    <button type="button" onclick="submitAnswer('none')">3 · لا هذا ولا ذاك</button>
    <button type="button" class="ghost" onclick="submitAnswer('unsure')">4 · غير متأكد</button>
  </div>
</form>

<div class="foot">
  <span>{% if prev_url %}<a href="{{ prev_url }}">→ السابق</a>{% endif %}</span>
  <span><a href="{{ url_for('audit') }}">كل الأجوبة ({{ answered }})</a></span>
</div>
<script>
  window.ANSWER_FORM = document.getElementById('answer-form');
  {% if prev_url %}window.PREV = {{ prev_url|tojson }};{% endif %}
</script>
""")

AUDIT = PAGE.replace("{% block body %}{% endblock %}", """
<div class="bar">
  <span>كل الأجوبة</span>
  <span><a href="{{ url_for('export') }}" style="color:var(--acc)">تصدير CSV</a></span>
</div>
<h1>{{ answered }} / {{ total }} تمت المراجعة</h1>
<div class="meta">
  {% for k, v in counts.items() %}{{ k }}: {{ v }} · {% endfor %}
  <a href="/?i=1" style="color:var(--acc)">العودة للبطاقات</a>
</div>
<table>
  <tr><th>#</th><th>الملف</th><th>القرار</th><th>العنوان</th><th>ملاحظة</th><th></th></tr>
  {% for row in rows %}
  <tr>
    <td>{{ row.idx }}</td>
    <td>{{ row.item.file }}<div class="meta">{{ row.item.artist }}</div></td>
    <td><span class="pill {{ row.decision }}">{{ row.label }}</span></td>
    <td>{{ row.title or '—' }}</td>
    <td>
      <form method="post" action="{{ url_for('audit') }}">
        <input type="hidden" name="track_id" value="{{ row.item.track_id }}">
        <select name="answer" onchange="this.form.submit()">
          {% for val, lab in options %}
          <option value="{{ val }}" {% if val == row.answer %}selected{% endif %}>{{ lab }}</option>
          {% endfor %}
        </select>
      </form>
    </td>
    <td><a href="/?i={{ row.idx }}" style="color:var(--dim)">فتح</a></td>
  </tr>
  {% endfor %}
</table>
""")


def mmss(seconds: float) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def create_app(items_path: Path, db_path: Path, audio_dir: Path,
               token: str, mirror_uri: str | None, reviewer: str) -> Flask:
    items = [json.loads(line) for line in
             Path(items_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    by_idx = {item["idx"]: item for item in items}
    store = Store(db_path, db_path.with_name("events.jsonl"), mirror_uri, reviewer)
    cache = AudioCache(audio_dir)

    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)

    @app.before_request
    def gate():
        if request.endpoint == "health":
            return None
        if session.get("ok"):
            return None
        if token and request.args.get("k") == token:
            session["ok"] = True
            return None
        return Response("forbidden: open the URL printed at startup "
                        "(it carries ?k=…)", status=403, mimetype="text/plain")

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "items": len(items),
                        "answered": len(store.answers())})

    def next_unanswered(after_idx: int = 0) -> dict | None:
        done = store.answers()
        ordered = sorted(items, key=lambda x: (x["idx"] <= after_idx,
                                               abs(x["idx"] - after_idx)))
        for item in ordered:
            if item["track_id"] not in done:
                return item
        return None

    @app.get("/")
    def card():
        answered = store.answers()
        item = None
        if request.args.get("i"):
            try:
                item = by_idx.get(int(request.args["i"]))
            except ValueError:
                item = None
        if item is None:
            item = next_unanswered(0) or items[0]
        prev_item = by_idx.get(item["idx"] - 1)
        cache.prefetch([by_idx[i] for i in (item["idx"], item["idx"] + 1, item["idx"] + 2)
                        if i in by_idx])
        return render_template_string(
            CARD, item=item, total=len(items), answered=len(answered),
            mmss=mmss(item["duration_s"]), error=request.args.get("error"),
            prev_url=(url_for("card", i=prev_item["idx"]) if prev_item else None))

    @app.post("/answer")
    def answer():
        track_id = request.form.get("track_id", "")
        key = request.form.get("answer", "")
        note = (request.form.get("note") or "").strip()
        item = next((x for x in items if x["track_id"] == track_id), None)
        if item is None:
            abort(404)
        if key not in ANSWER_MAP:
            return redirect(url_for("card", i=item["idx"], error="لم يُحدد جواب"))
        decision, chosen = ANSWER_MAP[key]
        title = source = None
        if chosen:
            cand = next(c for c in item["candidates"] if c["n"] == chosen)
            title, source = cand["title"], cand["source"]
        store.save(track_id, decision, chosen, title, source, note)
        nxt = next_unanswered(item["idx"])
        return redirect(url_for("card", i=nxt["idx"]) if nxt else url_for("audit"))

    @app.get("/audio/<int:item_id>")
    def serve_audio(item_id: int):
        item = by_idx.get(item_id)
        if item is None:
            abort(404)
        path = cache.ensure(item)
        if path is None:
            abort(503)
        return send_file(path, mimetype="audio/mpeg", conditional=True)

    @app.get("/audit")
    def audit():
        answered = store.answers()
        rows = []
        for item in items:
            a = answered.get(item["track_id"])
            if not a:
                rows.append({"item": item, "idx": item["idx"], "decision": "",
                             "label": "بلا جواب", "title": None, "answer": ""})
                continue
            if a["decision"] == "match":
                answer = f"c{a['chosen']}" if a["chosen"] else "c1"
                label = "مطابقة"
            else:
                answer, label = a["decision"], {"no_match": "لا كلمات",
                                                "ambiguous": "مؤجل"}[a["decision"]]
            rows.append({"item": item, "idx": item["idx"], "decision": a["decision"],
                         "label": label, "title": a["lyric_title"] or None,
                         "answer": answer})
        counts = {lab: sum(1 for r in rows if r["label"] == lab)
                  for lab in ("مطابقة", "لا كلمات", "مؤجل", "بلا جواب")}
        options = [("", "—"), ("c1", "الأول"), ("c2", "الثاني"),
                   ("none", "لا هذا ولا ذاك"), ("unsure", "مؤجل")]
        return render_template_string(AUDIT, rows=rows, total=len(items),
                                      answered=len(answered), counts=counts,
                                      options=options)

    @app.post("/audit")
    def audit_edit():
        track_id = request.form.get("track_id", "")
        key = request.form.get("answer", "")
        item = next((x for x in items if x["track_id"] == track_id), None)
        if item is None:
            abort(404)
        if key == "":
            with store.connect() as c:
                c.execute("DELETE FROM answers WHERE track_id=?", (track_id,))
                c.execute("INSERT INTO events (ts, track_id, action, payload) "
                          "VALUES (?,?,?,?)", (now(), track_id, "clear", None))
        elif key in ANSWER_MAP:
            decision, chosen = ANSWER_MAP[key]
            title = source = None
            if chosen:
                cand = next(c for c in item["candidates"] if c["n"] == chosen)
                title, source = cand["title"], cand["source"]
            store.save(track_id, decision, chosen, title, source,
                       (store.answers().get(track_id) or {}).get("note") or "")
        return redirect(url_for("audit"))

    @app.get("/export.csv")
    def export():
        answered = store.answers()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["track_id", "decision", "lyric_title", "lyric_source",
                    "reviewer", "reviewed_at", "notes"])
        for item in items:
            a = answered.get(item["track_id"])
            if not a:
                continue
            w.writerow([item["track_id"], a["decision"], a["lyric_title"] or "",
                        a["lyric_source"] or "", a["reviewer"], a["updated_at"],
                        a["note"] or ""])
        return Response(buf.getvalue(), mimetype="text/csv", headers={
            "Content-Disposition": "attachment; filename=title_map_review.csv"})

    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--items", default="work/review/items.jsonl")
    ap.add_argument("--db", default="work/review/answers.db")
    ap.add_argument("--audio-dir", default="work/review/audio")
    ap.add_argument("--token", default=None, help="shared-secret query param")
    ap.add_argument("--mirror-uri", default=None,
                    help="gs://…/answers.jsonl mirror, written after each answer")
    ap.add_argument("--reviewer", default="akbar")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args(argv)

    if not Path(args.items).exists():
        sys.exit(f"{args.items} missing; run scripts/review_build.py first")
    token = args.token or secrets.token_urlsafe(9)
    app = create_app(Path(args.items), Path(args.db), Path(args.audio_dir),
                     token, args.mirror_uri, args.reviewer)
    print(f"open: http://{args.host}:{args.port}/?k={token}", flush=True)
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
