"""
Minimalistic Flask UI for the SQE1 question bank.

Routes:
    /                       Dashboard — DB stats and filter form
    /quiz                   Random question (GET) / record answer (POST)
    /browse                 Filtered list of questions
    /question/<qid>         Single question detail with answer + explanations
    /score                  Session score & list of wrong QIDs
    /reset                  Clear session score
    /export                 Generate a PDF or Anki deck and download it
    /upload-wrong-qids      Upload a CSV of wrong QIDs (your own practice errors)
    /wrong-analysis         Show areas-to-improve summary based on the uploaded CSV
    /downloads/<filename>   Serve a generated PDF / Anki file

Start with:
    python app.py
Then open  http://localhost:5000  in a browser.
"""
import csv, io, sqlite3, subprocess, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, send_from_directory, session, url_for)

ROOT      = Path(__file__).parent
DB_PATH   = ROOT / "sqe1.db"
DOWNLOADS = ROOT / "downloads"
DOWNLOADS.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "change-me-if-you-care-about-cookie-tampering"
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024   # cap uploads at 256 KB


# ── DB helpers ────────────────────────────────────────────────────────

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def areas_for(flk=None):
    sql = "SELECT code, name FROM topics WHERE level=1"
    params = []
    if flk:
        sql += " AND flk=?"
        params.append(flk)
    return [(r["code"], r["name"]) for r in db().execute(sql, params).fetchall()]


def filtered_qids(flk, area):
    # If an area code is provided, walk down its taxonomy subtree first.
    # A question linked at lvl2/3/4 still counts for the area-level filter.
    cte, cte_params = "", []
    where, where_params = [], []
    if area:
        cte = (
            "WITH RECURSIVE matched(topic_id) AS ("
            "  SELECT topic_id FROM topics WHERE code=? AND level=1 "
            "  UNION "
            "  SELECT t.topic_id FROM topics t JOIN matched m ON t.parent_id=m.topic_id"
            ") "
        )
        cte_params = [area]
        where.append(
            "EXISTS (SELECT 1 FROM question_topics qt "
            " WHERE qt.question_id=q.question_id AND qt.topic_id IN matched)"
        )
    if flk:
        where.append("q.flk=?"); where_params.append(flk)
    sql = cte + "SELECT q.question_id FROM questions q"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [r["question_id"] for r in
            db().execute(sql, cte_params + where_params).fetchall()]


def fetch_question(qid):
    q = db().execute(
        "SELECT question_id, flk, source_file, source_question_num, stem, lead_in "
        "FROM questions WHERE question_id=?", (qid,),
    ).fetchone()
    if not q:
        return None
    opts = db().execute(
        "SELECT label, text, is_correct, explanation FROM options "
        "WHERE question_id=? ORDER BY label", (qid,),
    ).fetchall()
    topic_rows = db().execute(
        "SELECT t.code, t.name, t.level FROM question_topics qt "
        "JOIN topics t ON t.topic_id=qt.topic_id "
        "WHERE qt.question_id=? ORDER BY t.level", (qid,),
    ).fetchall()
    return dict(
        q=q,
        options=opts,
        correct=next((o["label"] for o in opts if o["is_correct"]), None),
        topics=topic_rows,
    )


# ── Routes ────────────────────────────────────────────────────────────

@app.route("/")
def home():
    if not DB_PATH.exists():
        return ("DB not found. Run <code>python ingest.py init</code> first.", 500)
    total = db().execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
    by_flk = {r["flk"]: r["c"] for r in
              db().execute("SELECT flk, COUNT(*) c FROM questions GROUP BY flk").fetchall()}
    classified = db().execute(
        "SELECT COUNT(DISTINCT question_id) c FROM question_topics").fetchone()["c"]
    with_all_expl = db().execute(
        "SELECT COUNT(*) c FROM questions q WHERE NOT EXISTS ("
        "  SELECT 1 FROM options o WHERE o.question_id=q.question_id "
        "    AND o.explanation IS NULL)").fetchone()["c"]
    by_area_rows = db().execute(
        "SELECT t.code, t.name, t.flk, COUNT(qt.question_id) c "
        "FROM topics t LEFT JOIN question_topics qt ON qt.topic_id=t.topic_id "
        "WHERE t.level=1 GROUP BY t.code ORDER BY t.flk, t.code"
    ).fetchall()
    return render_template(
        "home.html",
        total=total, by_flk=by_flk,
        classified=classified, with_all_expl=with_all_expl,
        by_area_rows=by_area_rows,
        score=session.get("score", {"correct": 0, "total": 0, "wrong": []}),
    )


@app.route("/quiz", methods=["GET", "POST"])
def quiz():
    flk  = request.values.get("flk")  or None
    area = request.values.get("area") or None

    if request.method == "POST":
        qid     = int(request.form["qid"])
        chosen  = request.form.get("answer")
        info    = fetch_question(qid)
        if info is None:
            return redirect(url_for("quiz", flk=flk or "", area=area or ""))
        is_correct = (chosen == info["correct"])
        s = session.setdefault("score", {"correct": 0, "total": 0, "wrong": []})
        s["total"] += 1
        if is_correct:
            s["correct"] += 1
        else:
            if qid not in s["wrong"]:
                s["wrong"].append(qid)
        session.modified = True
        return render_template(
            "quiz.html", info=info, chosen=chosen,
            is_correct=is_correct, reveal=True,
            flk=flk, area=area, areas=areas_for(flk),
        )

    qids = filtered_qids(flk, area)
    if not qids:
        return render_template("quiz.html", info=None, flk=flk, area=area,
                               areas=areas_for(flk))
    import random
    qid  = random.choice(qids)
    info = fetch_question(qid)
    return render_template("quiz.html", info=info, reveal=False,
                           flk=flk, area=area, areas=areas_for(flk))


@app.route("/browse")
def browse():
    flk  = request.args.get("flk")  or None
    area = request.args.get("area") or None
    q    = (request.args.get("q") or "").strip()

    qids = filtered_qids(flk, area)
    rows = []
    if qids:
        sql = ("SELECT question_id, flk, substr(lead_in,1,120) AS lead_in "
               "FROM questions WHERE question_id IN ({}) ".format(
                   ",".join("?" * len(qids))))
        params = list(qids)
        if q:
            sql += " AND (lead_in LIKE ? OR stem LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY question_id LIMIT 200"
        rows = db().execute(sql, params).fetchall()

    return render_template("browse.html",
                           rows=rows, total_match=len(qids),
                           flk=flk, area=area, q=q,
                           areas=areas_for(flk))


@app.route("/question/<int:qid>")
def question_detail(qid):
    info = fetch_question(qid)
    if info is None:
        return ("No such question.", 404)
    return render_template("question.html", info=info)


@app.route("/score")
def score():
    s = session.get("score", {"correct": 0, "total": 0, "wrong": []})
    return render_template("score.html", score=s)


@app.route("/reset")
def reset():
    session.pop("score", None)
    return redirect(url_for("home"))


# ─────────────────────────────────────────────────────────────────────
# Export: generate a PDF or Anki deck of randomised questions

def _run_export(kind, flk, area, only_wrong, count):
    """Invoke make_pdf.py or make_anki.py as a subprocess, return generated filename."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "pdf" if kind == "pdf" else "apkg"
    tag    = "wrong" if only_wrong else (flk or area or "all")
    fname  = f"sqe1_{tag}_{ts}.{suffix}"
    out    = DOWNLOADS / fname

    cmd = [sys.executable,
           "make_pdf.py" if kind == "pdf" else "make_anki.py",
           "--output", str(out)]
    if flk:   cmd += ["--flk", flk]
    if area:  cmd += ["--area", area]
    if count: cmd += ["--count", str(count)]

    if only_wrong:
        qids = session.get("wrong_qids") or []
        if not qids:
            return None, "No wrong-QIDs CSV uploaded yet."
        tmp_csv = DOWNLOADS / f"_tmp_wrong_{ts}.csv"
        tmp_csv.write_text("qid\n" + "\n".join(str(q) for q in qids),
                           encoding="utf-8")
        cmd += ["--qids-file", str(tmp_csv)]

    try:
        subprocess.run(cmd, check=True, cwd=ROOT)
    except subprocess.CalledProcessError as e:
        return None, f"Export failed: {e}"
    return fname, None


@app.route("/export", methods=["GET", "POST"])
def export():
    if request.method == "POST":
        kind       = request.form.get("kind", "pdf")
        flk        = request.form.get("flk")  or None
        area       = request.form.get("area") or None
        only_wrong = request.form.get("only_wrong") == "1"
        count_raw  = request.form.get("count", "").strip()
        count      = int(count_raw) if count_raw.isdigit() else None
        filename, err = _run_export(kind, flk, area, only_wrong, count)
        if err:
            flash(err, "danger")
            return redirect(url_for("export"))
        return render_template("export_done.html", filename=filename, kind=kind)
    return render_template(
        "export.html",
        areas=areas_for(None),
        n_wrong=len(session.get("wrong_qids") or []),
    )


@app.route("/downloads/<path:filename>")
def downloads(filename):
    # Prevent directory traversal
    safe = Path(filename).name
    if not (DOWNLOADS / safe).exists():
        abort(404)
    return send_from_directory(DOWNLOADS, safe, as_attachment=True)


# ─────────────────────────────────────────────────────────────────────
# Wrong-QIDs upload + analysis

@app.route("/upload-wrong-qids", methods=["GET", "POST"])
def upload_wrong_qids():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("No file uploaded.", "danger")
            return redirect(url_for("upload_wrong_qids"))
        try:
            text   = f.read().decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
        except Exception as e:
            flash(f"Could not parse CSV: {e}", "danger")
            return redirect(url_for("upload_wrong_qids"))
        if "qid" not in (reader.fieldnames or []):
            flash(f"CSV must have a 'qid' column. Found: {reader.fieldnames}",
                  "danger")
            return redirect(url_for("upload_wrong_qids"))
        qids = []
        for row in reader:
            v = (row.get("qid") or "").strip()
            if v.isdigit():
                qids.append(int(v))
        if not qids:
            flash("No usable QIDs found in the CSV.", "danger")
            return redirect(url_for("upload_wrong_qids"))
        session["wrong_qids"] = qids
        flash(f"Loaded {len(qids)} wrong QIDs.", "success")
        return redirect(url_for("wrong_analysis"))
    return render_template(
        "upload_wrong_qids.html",
        n_existing=len(session.get("wrong_qids") or []),
    )


@app.route("/wrong-analysis")
def wrong_analysis():
    qids = session.get("wrong_qids") or []
    if not qids:
        flash("Upload a wrong-QIDs CSV first.", "warning")
        return redirect(url_for("upload_wrong_qids"))
    # Re-use the standalone analyser
    import analyse_errors
    # Patch the analyser's DB_PATH so it picks up our app's DB
    analyse_errors.DB_PATH = DB_PATH
    result = analyse_errors.analyse(qids)
    summary_text = analyse_errors.format_text(result, top=10)
    return render_template(
        "wrong_analysis.html",
        result=result,
        summary_text=summary_text,
        n=len(qids),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
