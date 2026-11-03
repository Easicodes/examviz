"""ExamViz: a data visualization system for an examination schooling system (Flask + SQLite)."""
import csv
import hmac
import io
import os
import secrets
import sqlite3
import time
from datetime import date
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import analytics as an
from db import audit, close_db, ex, get_db, init_db, q, q1

STAFF = ("admin", "teacher", "counselor")
NAV = [("dashboard", "Dashboard", STAFF), ("risk", "At-risk students", STAFF), ("students", "Students", STAFF),
       ("interventions", "Interventions", STAFF), ("scores", "Enter scores", ("admin", "teacher")),
       ("import_data", "Import data", ("admin", "teacher")), ("reports", "Reports", STAFF),
       ("admin_page", "Administration", ("admin",)), ("family", "My children", ("parent",)),
       ("mine", "My results", ("student",))]
FAILS = {}  # (ip, username) -> [failed attempts, locked until]
ALERT_STATUS = ("open", "acknowledged", "resolved")
IV_STATUS = ("planned", "active", "completed", "cancelled")


def safe_path(p, default):
    return p if p and p.startswith("/") and not p.startswith("//") and "\\" not in p else default


def cell(v):  # stop spreadsheet formula injection in CSV exports
    return "'" + v if isinstance(v, str) and v[:1] in "=+-@" else v


def create_app(test_config=None):
    app = Flask(__name__)
    inst = os.path.join(app.root_path, "instance")
    os.makedirs(inst, exist_ok=True)
    keyfile = os.path.join(inst, "secret.key")
    if not os.path.exists(keyfile):
        with open(keyfile, "w") as f:
            f.write(secrets.token_hex(32))
    with open(keyfile) as f:
        key = f.read().strip()
    app.config.update(SECRET_KEY=os.environ.get("EXAMVIZ_SECRET") or key, DATABASE=os.path.join(inst, "examviz.db"),
                      MAX_CONTENT_LENGTH=2 * 1024 * 1024, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True)
    if test_config:
        app.config.update(test_config)
    if not os.path.exists(app.config["DATABASE"]):
        init_db(app.config["DATABASE"])
    app.teardown_appcontext(close_db)

    # ---------- request plumbing: current user, CSRF, headers, errors ----------
    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    @app.before_request
    def load():
        uid = session.get("uid")
        g.user = q1("SELECT * FROM user WHERE id=? AND active=1", (uid,)) if uid else None
        if request.method == "POST":
            sent, have = request.form.get("csrf") or request.headers.get("X-CSRF", ""), session.get("csrf", "")
            if not have or not hmac.compare_digest(sent, have):
                abort(400, "The form expired. Go back, reload the page and try again.")

    @app.after_request
    def headers(r):
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["X-Frame-Options"] = "DENY"
        r.headers["Referrer-Policy"] = "same-origin"
        r.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "img-src 'self' data:; frame-ancestors 'none'")
        return r

    @app.context_processor
    def ctx():
        return dict(user=g.get("user"), csrf=csrf_token, NAV=NAV, today=date.today().isoformat())

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def error(e):
        msg = {403: "You do not have access to this page.", 404: "That page does not exist.",
               413: "The file is too large. The limit is 2 MB."}.get(e.code, e.description)
        return render_template("error.html", code=e.code, msg=msg), e.code

    def need(*roles):
        def deco(f):
            @wraps(f)
            def wrapper(*a, **k):
                if not g.user:
                    return redirect(url_for("login", next=request.full_path.rstrip("?")))
                if roles and g.user["role"] not in roles:
                    abort(403)
                return f(*a, **k)
            return wrapper
        return deco

    def exam_arg():
        latest = an.latest_exam()
        return request.args.get("exam", type=int) or (latest["id"] if latest else None)

    def can_view(sid):
        u = g.user
        if u["role"] in STAFF:
            return True
        if u["role"] == "student":
            return u["student_id"] == sid
        if u["role"] == "parent":
            return bool(q1("SELECT 1 FROM student WHERE id=? AND parent_id=?", (sid, u["parent_id"])))
        return False

    def allowed_subjects():
        if g.user["role"] == "admin":
            return q("SELECT * FROM subject ORDER BY name")
        return q("SELECT su.* FROM subject su JOIN teacher_subject ts ON ts.subject_id=su.id "
                 "WHERE ts.teacher_id=? ORDER BY su.name", (g.user["teacher_id"],))

    # ---------- sign in ----------
    @app.route("/")
    def index():
        if not g.user:
            return redirect(url_for("login"))
        r = g.user["role"]
        return redirect(url_for("dashboard" if r in STAFF else "family" if r == "parent" else "mine"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            name = request.form.get("username", "").strip()
            key = (request.remote_addr, name.lower())
            f = FAILS.get(key, [0, 0])
            if f[1] > time.time():
                flash("Too many failed attempts. Wait five minutes and try again.", "bad")
            else:
                u = q1("SELECT * FROM user WHERE username=? AND active=1", (name,))
                if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
                    FAILS.pop(key, None)
                    session.clear()
                    session["uid"], session["csrf"] = u["id"], secrets.token_hex(16)
                    ex("UPDATE user SET last_login=CURRENT_TIMESTAMP WHERE id=?", (u["id"],))
                    audit(u["id"], "login")
                    return redirect(safe_path(request.args.get("next"), url_for("index")))
                f[0] += 1
                f[1] = time.time() + 300 if f[0] >= 5 else 0
                FAILS[key] = f
                audit(None, "login_failed", name[:50])
                flash("Wrong username or password.", "bad")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        if g.user:
            audit(g.user["id"], "logout")
        session.clear()
        return redirect(url_for("login"))

    @app.route("/password", methods=["GET", "POST"])
    @need()
    def password():
        if request.method == "POST":
            old, new, again = (request.form.get(k, "") for k in ("old", "new", "again"))
            if not check_password_hash(g.user["password_hash"], old):
                flash("Your current password is wrong.", "bad")
            elif len(new) < 8 or new != again:
                flash("The new password needs at least 8 characters and must match the confirmation.", "bad")
            else:
                ex("UPDATE user SET password_hash=? WHERE id=?", (generate_password_hash(new), g.user["id"]))
                audit(g.user["id"], "password_changed")
                flash("Password changed.", "ok")
                return redirect(url_for("index"))
        return render_template("password.html")

    # ---------- dashboard ----------
    @app.route("/dashboard")
    @need(*STAFF)
    def dashboard():
        return render_template("dashboard.html", exams=an.exams(), latest=an.latest_exam(),
                               classes=q("SELECT * FROM class ORDER BY name"), subjects=q("SELECT * FROM subject ORDER BY name"))

    @app.route("/api/dashboard")
    @need(*STAFF)
    def api_dashboard():
        return jsonify(an.dashboard(exam_arg(), request.args.get("class", type=int), request.args.get("subject", type=int)))

    # ---------- students ----------
    @app.route("/students")
    @need(*STAFF)
    def students():
        text, cls = request.args.get("q", "").strip(), request.args.get("class", type=int)
        sql = ("SELECT s.id, s.admission_no, s.first_name||' '||s.last_name name, s.gender, c.name cls "
               "FROM student s JOIN class c ON c.id=s.class_id WHERE 1=1")
        p = []
        if text:
            sql += " AND (s.first_name||' '||s.last_name LIKE ? OR s.admission_no LIKE ?)"
            p += [f"%{text}%"] * 2
        if cls:
            sql += " AND s.class_id=?"
            p.append(cls)
        return render_template("students.html", rows=q(sql + " ORDER BY s.last_name, s.first_name LIMIT 300", p),
                               classes=q("SELECT * FROM class ORDER BY name"), text=text, cls=cls)

    @app.route("/student/<int:sid>")
    @need()
    def student(sid):
        if not can_view(sid):
            abort(403)
        rep = an.student_report(sid)
        if not rep:
            abort(404)
        return render_template("student.html", **rep, SUGGEST=an.SUGGEST, LABEL=an.LABEL)

    @app.route("/family")
    @need("parent")
    def family():
        latest = an.latest_exam()
        kids = q("SELECT s.id, s.first_name||' '||s.last_name name, c.name cls, "
                 "(SELECT AVG(total_score) FROM assessment WHERE student_id=s.id AND exam_id=?) latest, "
                 "(SELECT COUNT(*) FROM alert al JOIN assessment a ON a.id=al.assessment_id "
                 " WHERE a.student_id=s.id AND al.status!='resolved') open_alerts "
                 "FROM student s JOIN class c ON c.id=s.class_id WHERE s.parent_id=?",
                 (latest["id"] if latest else None, g.user["parent_id"]))
        return render_template("family.html", kids=kids, latest=latest)

    @app.route("/me")
    @need("student")
    def mine():
        if not g.user["student_id"]:
            abort(404)
        return redirect(url_for("student", sid=g.user["student_id"]))

    # ---------- at-risk alerts and interventions ----------
    @app.route("/risk")
    @need(*STAFF)
    def risk():
        exam, gender, status = exam_arg(), request.args.get("gender", "M"), request.args.get("status", "open")
        cls = request.args.get("class", type=int)
        sql = ("SELECT al.id, al.alert_type, al.notes, al.status, s.id sid, s.admission_no, "
               "s.first_name||' '||s.last_name name, s.gender, c.name cls, "
               "(SELECT COUNT(*) FROM intervention i JOIN assessment x ON x.id=i.assessment_id "
               " WHERE x.student_id=s.id AND i.status IN ('planned','active')) active_iv "
               "FROM alert al JOIN assessment a ON a.id=al.assessment_id JOIN student s ON s.id=a.student_id "
               "JOIN class c ON c.id=s.class_id WHERE a.exam_id=?")
        p = [exam]
        for cond, val, ok in (("s.gender=?", gender, ("M", "F")), ("al.status=?", status, ALERT_STATUS)):
            if val in ok:
                sql += " AND " + cond
                p.append(val)
        if cls:
            sql += " AND s.class_id=?"
            p.append(cls)
        return render_template("risk.html", rows=q(sql + " ORDER BY s.last_name, al.alert_type", p), exam=exam,
                               exams=an.exams(), classes=q("SELECT * FROM class ORDER BY name"), gender=gender,
                               status=status, cls=cls, SUGGEST=an.SUGGEST, LABEL=an.LABEL, thresholds=(an.LOW_AVG, an.DROP, an.MIN_ATT))

    @app.post("/risk/detect")
    @need(*STAFF)
    def risk_detect():
        exam = request.form.get("exam", type=int)
        n = an.detect(exam) if exam else 0
        audit(g.user["id"], "detect", f"exam {exam}: {n} new alerts")
        flash(f"Detection finished: {n} new alert{'s' if n != 1 else ''}.", "ok")
        return redirect(url_for("risk", exam=exam))

    @app.post("/alert/<int:aid>")
    @need(*STAFF)
    def alert_status(aid):
        status = request.form.get("status")
        if status not in ALERT_STATUS or not q1("SELECT 1 FROM alert WHERE id=?", (aid,)):
            abort(400)
        ex("UPDATE alert SET status=? WHERE id=?", (status, aid))
        audit(g.user["id"], "alert_status", f"alert {aid} -> {status}")
        return redirect(safe_path(request.form.get("back"), url_for("risk")))

    @app.route("/interventions")
    @need(*STAFF)
    def interventions():
        status = request.args.get("status", "")
        sql = ("SELECT i.*, s.id sid, s.first_name||' '||s.last_name name, c.name cls, e.name exam, u.username who "
               "FROM intervention i JOIN assessment a ON a.id=i.assessment_id JOIN student s ON s.id=a.student_id "
               "JOIN class c ON c.id=s.class_id JOIN examination e ON e.id=a.exam_id "
               "LEFT JOIN user u ON u.id=i.recommended_by")
        p = []
        if status in IV_STATUS:
            sql += " WHERE i.status=?"
            p.append(status)
        return render_template("interventions.html", rows=q(sql + " ORDER BY i.start_date DESC, i.id DESC", p),
                               status=status, IV_STATUS=IV_STATUS)

    @app.post("/interventions/new")
    @need(*STAFF)
    def intervention_new():
        alert = q1("SELECT * FROM alert WHERE id=?", (request.form.get("alert_id", type=int),))
        kind, desc, start = (request.form.get(k, "").strip() for k in ("type", "description", "start_date"))
        try:
            date.fromisoformat(start)
        except ValueError:
            start = date.today().isoformat()
        if not alert or not kind:
            abort(400)
        ex("INSERT INTO intervention(assessment_id, intervention_type, description, recommended_by, start_date) VALUES(?,?,?,?,?)",
           (alert["assessment_id"], kind[:120], desc[:500], g.user["id"], start))
        if alert["status"] == "open":
            ex("UPDATE alert SET status='acknowledged' WHERE id=?", (alert["id"],))
        audit(g.user["id"], "intervention_new", f"alert {alert['id']}: {kind[:60]}")
        flash("Intervention planned. The alert is now acknowledged.", "ok")
        return redirect(safe_path(request.form.get("back"), url_for("interventions")))

    @app.post("/interventions/<int:iid>/status")
    @need(*STAFF)
    def intervention_status(iid):
        status = request.form.get("status")
        if status not in IV_STATUS or not q1("SELECT 1 FROM intervention WHERE id=?", (iid,)):
            abort(400)
        end = date.today().isoformat() if status in ("completed", "cancelled") else None
        ex("UPDATE intervention SET status=?, end_date=? WHERE id=?", (status, end, iid))
        audit(g.user["id"], "intervention_status", f"{iid} -> {status}")
        return redirect(safe_path(request.form.get("back"), url_for("interventions")))

    # ---------- score entry and import ----------
    def save_score(sid, sub, exam, ca, es):
        try:
            ca, es = float(ca), float(es)
        except ValueError:
            return "CA and exam scores must be numbers"
        if not 0 <= ca <= 40:
            return "CA score must be between 0 and 40"
        if not 0 <= es <= 60:
            return "Exam score must be between 0 and 60"
        total = ca + es
        get_db().execute(
            "INSERT INTO assessment(student_id, subject_id, exam_id, ca_score, exam_score, total_score, grade, entered_by) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(student_id, subject_id, exam_id) DO UPDATE SET "
            "ca_score=excluded.ca_score, exam_score=excluded.exam_score, total_score=excluded.total_score, "
            "grade=excluded.grade, entered_by=excluded.entered_by, entry_date=CURRENT_TIMESTAMP",
            (sid, sub, exam, ca, es, total, an.grade(total), g.user["id"]))
        return None

    @app.route("/scores", methods=["GET", "POST"])
    @need("admin", "teacher")
    def scores():
        exam, cls, sub = (request.values.get(k, type=int) for k in ("exam", "class", "subject"))
        subjects, rows = allowed_subjects(), []
        if exam and cls and sub:
            if sub not in [s["id"] for s in subjects]:
                abort(403)
            rows = q("SELECT s.id, s.admission_no, s.first_name||' '||s.last_name name, a.ca_score, a.exam_score "
                     "FROM student s LEFT JOIN assessment a ON a.student_id=s.id AND a.subject_id=? AND a.exam_id=? "
                     "WHERE s.class_id=? ORDER BY s.last_name, s.first_name", (sub, exam, cls))
            if request.method == "POST":
                saved, errs = 0, []
                for r in rows:
                    ca, es = request.form.get(f"ca_{r['id']}", "").strip(), request.form.get(f"ex_{r['id']}", "").strip()
                    if not ca and not es:
                        continue
                    err = "Enter both CA and exam scores" if not (ca and es) else save_score(r["id"], sub, exam, ca, es)
                    if err:
                        errs.append(f"{r['name']}: {err}")
                    else:
                        saved += 1
                get_db().commit()
                audit(g.user["id"], "scores_saved", f"exam {exam} class {cls} subject {sub}: {saved} rows")
                flash(f"Saved {saved} score{'s' if saved != 1 else ''}." + (f" {len(errs)} not saved." if errs else ""), "ok" if not errs else "bad")
                for e in errs[:5]:
                    flash(e, "bad")
                return redirect(url_for("scores", exam=exam, **{"class": cls}, subject=sub))
        return render_template("scores.html", exams=an.exams(), classes=q("SELECT * FROM class ORDER BY name"),
                               subjects=subjects, rows=rows, exam=exam, cls=cls, sub=sub)

    def read_csv(file):
        rd = csv.DictReader(io.StringIO(file.read().decode("utf-8-sig")))
        rd.fieldnames = [(f or "").strip().lower() for f in rd.fieldnames or []]
        return rd.fieldnames, [{k: (v or "").strip() for k, v in r.items() if k} for r in rd]

    IMPORTS = {
        "students": ("admin", ["admission_no", "first_name", "last_name", "gender", "class_name"],
                     "Optional: academic_year, date_of_birth, parent_name, parent_phone"),
        "scores": ("teacher", ["admission_no", "subject_code", "exam_name", "ca_score", "exam_score"],
                   "CA is out of 40 and exam out of 60"),
        "attendance": ("teacher", ["admission_no", "exam_name", "days_present", "days_open"], "Days present out of days open"),
    }

    def do_import(kind, rows):
        errs, ok = [], 0
        stu = {r["admission_no"]: r["id"] for r in q("SELECT id, admission_no FROM student")}
        subj = {r["code"].upper(): r["id"] for r in allowed_subjects()}
        exams = {r["name"].lower(): r["id"] for r in q("SELECT id, name FROM examination ORDER BY academic_year")}
        year = (an.latest_exam() or {"academic_year": "2025/2026"})["academic_year"]
        db = get_db()
        for i, r in enumerate(rows, start=2):
            try:
                if kind == "students":
                    gnd = r["gender"][:1].upper()
                    if gnd not in ("M", "F") or not r["admission_no"] or not r["first_name"]:
                        raise ValueError("gender must be M or F, and admission_no and first_name are required")
                    yr = r.get("academic_year") or year
                    cl = db.execute("SELECT id FROM class WHERE name=? AND academic_year=?", (r["class_name"], yr)).fetchone()
                    if cl:
                        cid = cl["id"]
                    else:
                        cid = db.execute("INSERT INTO class(name, grade_level, academic_year) VALUES(?,?,?)",
                                         (r["class_name"], r["class_name"][:3], yr)).lastrowid
                        db.execute("INSERT INTO class_subject SELECT ?, id FROM subject", (cid,))
                    pid = None
                    if r.get("parent_name"):
                        first, _, last = r["parent_name"].rpartition(" ")
                        pid = db.execute("INSERT INTO parent(first_name, last_name, phone) VALUES(?,?,?)",
                                         (first or last, last, r.get("parent_phone"))).lastrowid
                    if r["admission_no"] in stu:
                        db.execute("UPDATE student SET first_name=?, last_name=?, gender=?, class_id=?, "
                                   "parent_id=COALESCE(?, parent_id) WHERE admission_no=?",
                                   (r["first_name"], r["last_name"], gnd, cid, pid, r["admission_no"]))
                    else:
                        stu[r["admission_no"]] = db.execute(
                            "INSERT INTO student(admission_no, first_name, last_name, gender, date_of_birth, class_id, parent_id, admission_date) "
                            "VALUES(?,?,?,?,?,?,?,?)", (r["admission_no"], r["first_name"], r["last_name"], gnd,
                                                        r.get("date_of_birth") or None, cid, pid, date.today().isoformat())).lastrowid
                else:
                    sid, eid = stu.get(r["admission_no"]), exams.get(r["exam_name"].lower())
                    if not sid:
                        raise ValueError(f"unknown admission_no {r['admission_no']}")
                    if not eid:
                        raise ValueError(f"unknown exam {r['exam_name']}")
                    if kind == "scores":
                        sub = subj.get(r["subject_code"].upper())
                        if not sub:
                            raise ValueError(f"unknown subject {r['subject_code']} (or not one you teach)")
                        err = save_score(sid, sub, eid, r["ca_score"], r["exam_score"])
                        if err:
                            raise ValueError(err)
                    else:
                        dp, do = int(r["days_present"]), int(r["days_open"])
                        if not 0 <= dp <= do or do <= 0:
                            raise ValueError("days_present must be between 0 and days_open")
                        db.execute("INSERT INTO attendance(student_id, exam_id, days_present, days_open) VALUES(?,?,?,?) "
                                   "ON CONFLICT(student_id, exam_id) DO UPDATE SET days_present=excluded.days_present, "
                                   "days_open=excluded.days_open", (sid, eid, dp, do))
                ok += 1
            except (KeyError, ValueError, sqlite3.Error) as e:
                errs.append(f"Row {i}: {e}")
        db.commit()
        return ok, errs

    @app.route("/import", methods=["GET", "POST"])
    @need("admin", "teacher")
    def import_data():
        kinds = {k: v for k, v in IMPORTS.items() if g.user["role"] == "admin" or v[0] == "teacher"}
        result = None
        if request.method == "POST":
            kind, file = request.form.get("kind"), request.files.get("file")
            if kind not in kinds or not file or not file.filename:
                flash("Choose what you are importing and attach a CSV file.", "bad")
            else:
                try:
                    cols, rows = read_csv(file)
                except (UnicodeDecodeError, csv.Error):
                    cols, rows = [], []
                missing = [c for c in kinds[kind][1] if c not in cols]
                if missing:
                    flash("Missing column(s): " + ", ".join(missing), "bad")
                elif not rows:
                    flash("The file has no data rows.", "bad")
                else:
                    ok, errs = do_import(kind, rows)
                    audit(g.user["id"], "import", f"{kind}: {ok} ok, {len(errs)} rejected")
                    result = {"ok": ok, "errs": errs[:20], "more": max(0, len(errs) - 20)}
        return render_template("import.html", kinds=kinds, result=result)

    # ---------- reports ----------
    def csv_response(name, header, rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows([[cell(v) for v in row] for row in rows])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}-{date.today().isoformat()}.csv"})

    @app.route("/reports")
    @need(*STAFF)
    def reports():
        return render_template("reports.html", exams=an.exams(), exam=exam_arg())

    @app.route("/reports/<kind>.csv")
    @need(*STAFF)
    def report_csv(kind):
        exam = exam_arg()
        if kind == "at-risk":
            rows = q("SELECT s.admission_no, s.first_name||' '||s.last_name, c.name, s.gender, al.alert_type, al.notes, al.status "
                     "FROM alert al JOIN assessment a ON a.id=al.assessment_id JOIN student s ON s.id=a.student_id "
                     "JOIN class c ON c.id=s.class_id WHERE a.exam_id=? ORDER BY c.name, s.last_name", (exam,))
            return csv_response("at-risk", ["admission_no", "name", "class", "gender", "alert", "details", "status"], rows)
        if kind == "results":
            rows = q("SELECT s.admission_no, s.first_name||' '||s.last_name, c.name, s.gender, su.name, a.ca_score, a.exam_score, a.total_score, a.grade "
                     "FROM assessment a JOIN student s ON s.id=a.student_id JOIN class c ON c.id=s.class_id "
                     "JOIN subject su ON su.id=a.subject_id WHERE a.exam_id=? ORDER BY c.name, s.last_name, su.name", (exam,))
            return csv_response("results", ["admission_no", "name", "class", "gender", "subject", "ca", "exam", "total", "grade"], rows)
        if kind == "gender-summary":
            d = an.dashboard(exam, None, None)
            rows = [(s["name"], s["M"], s["F"], None if s["M"] is None or s["F"] is None else round(s["F"] - s["M"], 1))
                    for s in d["subjects"]]
            return csv_response("gender-summary", ["subject", "boys_average", "girls_average", "gap_girls_minus_boys"], rows)
        abort(404)

    @app.route("/reports/print")
    @need(*STAFF)
    def report_print():
        exam = exam_arg()
        e = q1("SELECT * FROM examination WHERE id=?", (exam,))
        flagged = q("SELECT s.first_name||' '||s.last_name name, c.name cls, al.alert_type, al.notes FROM alert al "
                    "JOIN assessment a ON a.id=al.assessment_id JOIN student s ON s.id=a.student_id JOIN class c ON c.id=s.class_id "
                    "WHERE a.exam_id=? AND s.gender='M' AND al.status!='resolved' ORDER BY c.name, s.last_name", (exam,))
        return render_template("report_print.html", d=an.dashboard(exam, None, None), e=e, flagged=flagged, LABEL=an.LABEL)

    # ---------- administration ----------
    @app.route("/admin")
    @need("admin")
    def admin_page():
        return render_template(
            "admin.html", users=q("SELECT * FROM user ORDER BY role, username"),
            teachers=q("SELECT t.*, (SELECT group_concat(su.code, ', ') FROM teacher_subject ts JOIN subject su ON su.id=ts.subject_id "
                       "WHERE ts.teacher_id=t.id) subjects FROM teacher t ORDER BY last_name"),
            subjects=q("SELECT * FROM subject ORDER BY name"), classes=q("SELECT * FROM class ORDER BY name"),
            exams=an.exams(), log=q("SELECT l.*, u.username FROM log l LEFT JOIN user u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 15"))

    @app.post("/admin/add/<kind>")
    @need("admin")
    def admin_add(kind):
        f = {k: v.strip() for k, v in request.form.items()}
        db = get_db()
        try:
            if kind == "subject":
                sid = db.execute("INSERT INTO subject(name, code) VALUES(?,?)", (f["name"], f["code"].upper())).lastrowid
                db.execute("INSERT INTO class_subject SELECT id, ? FROM class", (sid,))
            elif kind == "class":
                cid = db.execute("INSERT INTO class(name, grade_level, academic_year) VALUES(?,?,?)",
                                 (f["name"], f.get("grade_level"), f["academic_year"])).lastrowid
                db.execute("INSERT INTO class_subject SELECT ?, id FROM subject", (cid,))
            elif kind == "exam":
                if f["term"] not in ("1", "2", "3"):
                    raise ValueError("Term must be 1, 2 or 3")
                db.execute("INSERT INTO examination(name, term, academic_year, exam_date) VALUES(?,?,?,?)",
                           (f["name"], int(f["term"]), f["academic_year"], f.get("exam_date") or None))
            elif kind == "teacher":
                tid = db.execute("INSERT INTO teacher(first_name, last_name, email) VALUES(?,?,?)",
                                 (f["first_name"], f["last_name"], f["email"])).lastrowid
                for code in filter(None, (c.strip().upper() for c in f.get("subjects", "").split(","))):
                    s = db.execute("SELECT id FROM subject WHERE code=?", (code,)).fetchone()
                    if not s:
                        raise ValueError(f"Unknown subject code {code}")
                    db.execute("INSERT INTO teacher_subject VALUES(?,?)", (tid, s["id"]))
            elif kind == "user":
                role, link = f["role"], f.get("link", "")
                if len(f["password"]) < 8 or len(f["username"]) < 3:
                    raise ValueError("Username needs 3+ characters and password 8+")
                ids = {"teacher_id": None, "parent_id": None, "student_id": None}
                if role == "teacher":
                    t = db.execute("SELECT id FROM teacher WHERE email=?", (link,)).fetchone()
                    if not t:
                        raise ValueError("No teacher with that email. Add the teacher first.")
                    ids["teacher_id"] = t["id"]
                elif role in ("parent", "student"):
                    s = db.execute("SELECT id, parent_id FROM student WHERE admission_no=?", (link,)).fetchone()
                    if not s or (role == "parent" and not s["parent_id"]):
                        raise ValueError("No student with that admission number (or no parent on record).")
                    ids["parent_id" if role == "parent" else "student_id"] = s["parent_id"] if role == "parent" else s["id"]
                elif role not in ("admin", "counselor"):
                    raise ValueError("Unknown role")
                db.execute("INSERT INTO user(username, password_hash, role, teacher_id, parent_id, student_id) VALUES(?,?,?,?,?,?)",
                           (f["username"], generate_password_hash(f["password"]), role, ids["teacher_id"], ids["parent_id"], ids["student_id"]))
            else:
                abort(404)
            db.commit()
            audit(g.user["id"], "admin_add", kind)
            flash(f"Added {kind}.", "ok")
        except sqlite3.IntegrityError:
            db.rollback()
            flash("That already exists or a required value is missing.", "bad")
        except (KeyError, ValueError) as e:
            db.rollback()
            flash(str(e) if isinstance(e, ValueError) else "Fill in every required field.", "bad")
        return redirect(url_for("admin_page"))

    @app.post("/admin/user/<int:uid>/toggle")
    @need("admin")
    def admin_toggle(uid):
        if uid == g.user["id"]:
            flash("You cannot deactivate your own account.", "bad")
        else:
            ex("UPDATE user SET active=1-active WHERE id=?", (uid,))
            audit(g.user["id"], "user_toggle", str(uid))
        return redirect(url_for("admin_page"))

    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("EXAMVIZ_DEBUG") == "1")
