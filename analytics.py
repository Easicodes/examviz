"""Queries behind the dashboard, the at-risk detection engine and the student report."""
from db import get_db, q, q1

LOW_AVG, DROP, MIN_ATT = 45, 8, 80  # at-risk thresholds: low average, fall in points, attendance %

SUGGEST = {
    "LOW_SCORE": ("Remedial classes", "Extra lessons in the weakest subjects."),
    "DECLINE": ("Mentoring and counselling", "Meet the student to find out what changed."),
    "LOW_ATTENDANCE": ("Attendance monitoring and parental engagement", "Track attendance weekly and talk to the parent."),
}
LABEL = {"LOW_SCORE": "Low average", "DECLINE": "Scores falling", "LOW_ATTENDANCE": "Low attendance"}

FROM = ("FROM assessment a JOIN student s ON s.id=a.student_id JOIN class c ON c.id=s.class_id "
        "JOIN subject su ON su.id=a.subject_id JOIN examination e ON e.id=a.exam_id")


def grade(t):
    return "A" if t >= 70 else "B" if t >= 60 else "C" if t >= 50 else "D" if t >= 45 else "E" if t >= 40 else "F"


def exams():
    return q("SELECT * FROM examination ORDER BY academic_year, term")


def latest_exam():
    rows = exams()
    return rows[-1] if rows else None


def where(exam=None, cls=None, sub=None):
    w, p = ["1=1"], []
    for col, v in (("a.exam_id", exam), ("s.class_id", cls), ("a.subject_id", sub)):
        if v:
            w.append(col + "=?")
            p.append(v)
    return " AND ".join(w), p


def r1(x):
    return None if x is None else round(x, 1)


def dashboard(exam, cls, sub):
    """Everything the dashboard needs, as plain dicts. Filters: exam, class, subject."""
    w, p = where(exam, cls, sub)
    gen = {r["gender"]: r for r in q(
        f"SELECT s.gender, AVG(a.total_score) avg, COUNT(DISTINCT s.id) n, AVG(a.total_score >= 40) pr "
        f"{FROM} WHERE {w} GROUP BY s.gender", p)}
    av = lambda g: r1(gen[g]["avg"]) if g in gen else None  # noqa: E731

    w2, p2 = where(exam, cls)  # the subject chart ignores the subject filter so every subject stays visible
    subjects = {}
    for r in q(f"SELECT su.id, su.name, s.gender, AVG(a.total_score) v {FROM} WHERE {w2} "
               f"GROUP BY su.id, s.gender ORDER BY su.name", p2):
        subjects.setdefault(r["id"], {"id": r["id"], "name": r["name"], "M": None, "F": None})[r["gender"]] = r1(r["v"])

    w3, p3 = where(None, cls, sub)  # the trend chart shows every exam
    trend = {}
    for r in q(f"SELECT e.id, e.name, s.gender, AVG(a.total_score) v {FROM} WHERE {w3} "
               f"GROUP BY e.id, s.gender ORDER BY e.academic_year, e.term", p3):
        trend.setdefault(r["id"], {"name": r["name"], "M": None, "F": None})[r["gender"]] = r1(r["v"])

    cnt = {(r["grade"], r["gender"]): r["n"] for r in q(
        f"SELECT a.grade, s.gender, COUNT(*) n {FROM} WHERE {w} GROUP BY a.grade, s.gender", p)}
    tot = {g: sum(v for (_, gg), v in cnt.items() if gg == g) for g in "MF"}
    grades = {g: [round(100 * cnt.get((l, g), 0) / tot[g], 1) if tot[g] else 0 for l in "ABCDEF"] for g in "MF"}

    rw, rp = ["al.status='open'"], []
    if exam:
        rw.append("a.exam_id=?")
        rp.append(exam)
    if cls:
        rw.append("s.class_id=?")
        rp.append(cls)
    risk = {"M": 0, "F": 0}
    for r in q("SELECT s.gender, COUNT(DISTINCT s.id) n FROM alert al JOIN assessment a ON a.id=al.assessment_id "
               f"JOIN student s ON s.id=a.student_id WHERE {' AND '.join(rw)} GROUP BY s.gender", rp):
        risk[r["gender"]] = r["n"]

    top = [dict(r, avg=r1(r["avg"])) for r in q(
        f"SELECT s.id, s.first_name||' '||s.last_name name, c.name cls, s.gender, AVG(a.total_score) avg "
        f"{FROM} WHERE {w} GROUP BY s.id ORDER BY avg DESC LIMIT 10", p)]
    w4, p4 = where(exam, None, sub)
    classes = [dict(r, avg=r1(r["avg"])) for r in q(
        f"SELECT c.id, c.name, AVG(a.total_score) avg, COUNT(DISTINCT s.id) n {FROM} WHERE {w4} "
        f"GROUP BY c.id ORDER BY avg DESC", p4)]

    gap = round(av("F") - av("M"), 1) if av("M") is not None and av("F") is not None else None
    diffs = [(s["F"] - s["M"], s["name"]) for s in subjects.values() if s["M"] is not None and s["F"] is not None]
    if gap is None:
        insight = "No results match this selection yet."
    else:
        insight = f"{'Girls' if gap >= 0 else 'Boys'} lead by {abs(gap)} points on average."
        if diffs:
            d, name = max(diffs, key=lambda x: abs(x[0]))
            insight += f" The widest gap is in {name}, where {'girls' if d >= 0 else 'boys'} lead by {abs(d):.1f}."
    return {
        "insight": insight,
        "kpi": {"students": sum(r["n"] for r in gen.values()), "boys": av("M"), "girls": av("F"), "gap": gap,
                "pass_rate": r1(100 * sum(r["pr"] * r["n"] for r in gen.values()) / max(1, sum(r["n"] for r in gen.values()))) if gen else None,
                "risk_boys": risk["M"], "risk_girls": risk["F"]},
        "subjects": list(subjects.values()), "trend": list(trend.values()), "grades": grades,
        "risk": risk, "top": top, "classes": classes,
    }


def detect(exam_id):
    """Create alerts for the exam and return how many new ones were made. Safe to run repeatedly."""
    db = get_db()
    this = q1("SELECT academic_year, term FROM examination WHERE id=?", (exam_id,))
    if not this:
        return 0
    prev = q1("SELECT id FROM examination WHERE academic_year||term < ? ORDER BY academic_year DESC, term DESC LIMIT 1",
              (this["academic_year"] + str(this["term"]),))
    avgs = lambda eid: {r["sid"]: r["v"] for r in q(  # noqa: E731
        "SELECT student_id sid, AVG(total_score) v FROM assessment WHERE exam_id=? GROUP BY student_id", (eid,))}
    cur, pre = avgs(exam_id), avgs(prev["id"]) if prev else {}
    att = {r["sid"]: 100.0 * r["p"] / r["o"] for r in q(
        "SELECT student_id sid, days_present p, days_open o FROM attendance WHERE exam_id=?", (exam_id,))}
    made = 0
    for sid, v in cur.items():
        flags = []
        if v < LOW_AVG:
            flags.append(("LOW_SCORE", f"Average {v:.1f} is below {LOW_AVG}"))
        if sid in pre and pre[sid] - v >= DROP:
            flags.append(("DECLINE", f"Average fell {pre[sid] - v:.1f} points, from {pre[sid]:.1f} to {v:.1f}"))
        if sid in att and att[sid] < MIN_ATT:
            flags.append(("LOW_ATTENDANCE", f"Attendance {att[sid]:.0f}% is below {MIN_ATT}%"))
        if not flags:
            continue
        weakest = db.execute("SELECT id FROM assessment WHERE student_id=? AND exam_id=? ORDER BY total_score LIMIT 1",
                             (sid, exam_id)).fetchone()
        for typ, note in flags:
            dup = db.execute("SELECT 1 FROM alert al JOIN assessment x ON x.id=al.assessment_id "
                             "WHERE x.student_id=? AND x.exam_id=? AND al.alert_type=?", (sid, exam_id, typ)).fetchone()
            if not dup:
                db.execute("INSERT INTO alert(assessment_id, alert_type, notes) VALUES(?,?,?)", (weakest["id"], typ, note))
                made += 1
    db.commit()
    return made


def student_report(sid):
    s = q1("SELECT s.*, c.name cls, c.id cid, p.first_name||' '||p.last_name parent, p.phone parent_phone "
           "FROM student s JOIN class c ON c.id=s.class_id LEFT JOIN parent p ON p.id=s.parent_id WHERE s.id=?", (sid,))
    if not s:
        return None
    trend = [dict(r, mine=r1(r["mine"]), cls=r1(r["cls"])) for r in q(
        "SELECT e.id, e.name, AVG(CASE WHEN a.student_id=? THEN a.total_score END) mine, AVG(a.total_score) cls "
        "FROM assessment a JOIN student s ON s.id=a.student_id JOIN examination e ON e.id=a.exam_id "
        "WHERE s.class_id=? GROUP BY e.id ORDER BY e.academic_year, e.term", (sid, s["cid"]))]
    last = q1("SELECT a.exam_id, e.name FROM assessment a JOIN examination e ON e.id=a.exam_id WHERE a.student_id=? "
              "ORDER BY e.academic_year DESC, e.term DESC LIMIT 1", (sid,))
    scores = []
    if last:
        scores = [dict(r, cls=r1(r["cls"])) for r in q(
            "SELECT su.name, MAX(CASE WHEN a.student_id=? THEN a.ca_score END) ca, "
            "MAX(CASE WHEN a.student_id=? THEN a.exam_score END) exam, "
            "MAX(CASE WHEN a.student_id=? THEN a.total_score END) total, "
            "MAX(CASE WHEN a.student_id=? THEN a.grade END) grade, AVG(a.total_score) cls "
            "FROM assessment a JOIN student s ON s.id=a.student_id JOIN subject su ON su.id=a.subject_id "
            "WHERE s.class_id=? AND a.exam_id=? GROUP BY su.id ORDER BY su.name",
            (sid, sid, sid, sid, s["cid"], last["exam_id"]))]
    attendance = q("SELECT e.name, t.days_present, t.days_open FROM attendance t JOIN examination e ON e.id=t.exam_id "
                   "WHERE t.student_id=? ORDER BY e.academic_year, e.term", (sid,))
    alerts = q("SELECT al.*, e.name exam, su.name subject FROM alert al JOIN assessment a ON a.id=al.assessment_id "
               "JOIN examination e ON e.id=a.exam_id JOIN subject su ON su.id=a.subject_id "
               "WHERE a.student_id=? ORDER BY al.alert_date DESC, al.id DESC", (sid,))
    now = next((t["mine"] for t in reversed(trend) if t["mine"] is not None), None)
    ivs = []
    for r in q("SELECT i.*, a.exam_id, e.name exam FROM intervention i JOIN assessment a ON a.id=i.assessment_id "
               "JOIN examination e ON e.id=a.exam_id WHERE a.student_id=? ORDER BY i.start_date DESC, i.id DESC", (sid,)):
        before = r1(q1("SELECT AVG(total_score) v FROM assessment WHERE student_id=? AND exam_id=?",
                       (sid, r["exam_id"]))["v"])
        ivs.append(dict(r, before=before, now=now, change=r1(now - before) if now is not None and before is not None else None))
    return {"s": s, "trend": trend, "scores": scores, "last_exam": last["name"] if last else None,
            "attendance": attendance, "alerts": alerts, "interventions": ivs}
