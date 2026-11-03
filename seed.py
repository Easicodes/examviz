"""Fill the database with SAMPLE data (computer-generated, not real students).

Run:  python seed.py            (replaces instance/examviz.db)
"""
import os
import random
import sqlite3
import sys

from werkzeug.security import generate_password_hash

import analytics as an
from db import init_db

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "instance", "examviz.db")
YEAR = "2025/2026"
SUBJECTS = [("Mathematics", "MTH"), ("English Language", "ENG"), ("Physics", "PHY"),
            ("Biology", "BIO"), ("Civic Education", "CIV"), ("Computer Science", "CSC")]
EXAMS = [("First Term", 1, "2025-12-10"), ("Second Term", 2, "2026-03-25"), ("Third Term", 3, "2026-07-10")]
BOYS = "Musa Ibrahim Yusuf Emeka Chidi Tunde Samuel Daniel Abdul Joseph Peter Sunday Ahmed Isaac Paul".split()
GIRLS = "Aisha Fatima Blessing Grace Zainab Hauwa Ngozi Esther Mary Amina Ruth Hannah Halima Joy Sarah".split()
LAST = "Adamu Okafor Bello Danjuma Eze Usman Audu Ojo Sani Yakubu Obi Garba".split()
TEACHERS = [("Hauwa", "Lawal"), ("Chinedu", "Nwosu"), ("Bala", "Mohammed"), ("Funmi", "Adeyemi"), ("Joy", "Ekpo"), ("Sani", "Abubakar")]
ACCOUNTS = [("admin", "Admin@123", "admin"), ("counselor1", "Counsel@123", "counselor"), ("teacher1", "Teacher@123", "teacher"),
            ("parent1", "Parent@123", "parent"), ("student1", "Student@123", "student")]


def seed(path=DEFAULT_DB, per_class=30):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    init_db(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    r = random.Random(2026)
    noise = lambda: (r.random() + r.random() + r.random() + r.random() - 2) * 1.73  # roughly N(0,1)  # noqa: E731
    clamp = lambda x, hi=100: max(0, min(hi, round(x)))  # noqa: E731

    sub_ids = [con.execute("INSERT INTO subject(name, code) VALUES(?,?)", s).lastrowid for s in SUBJECTS]
    tch_ids = []
    for i, (f, l) in enumerate(TEACHERS):
        tid = con.execute("INSERT INTO teacher(first_name, last_name, email, role) VALUES(?,?,?,?)",
                          (f, l, f"{f.lower()}.{l.lower()}@school.example", "Subject teacher")).lastrowid
        con.execute("INSERT INTO teacher_subject VALUES(?,?)", (tid, sub_ids[i]))
        tch_ids.append(tid)
    exam_ids = [con.execute("INSERT INTO examination(name, term, academic_year, exam_date) VALUES(?,?,?,?)",
                            (n, t, YEAR, d)).lastrowid for n, t, d in EXAMS]
    first_decliner = None
    n = 0
    for ci, cname in enumerate(["SS1A", "SS1B", "SS2A"]):
        cid = con.execute("INSERT INTO class(name, grade_level, academic_year, class_teacher_id) VALUES(?,?,?,?)",
                          (cname, cname[:3], YEAR, tch_ids[ci])).lastrowid
        con.executemany("INSERT INTO class_subject VALUES(?,?)", [(cid, s) for s in sub_ids])
        for j in range(per_class):
            i = ci * per_class + j
            male = r.random() < .5
            first = (BOYS if male else GIRLS)[i % 15]
            last = LAST[(i // 15) % 12]
            pid = con.execute("INSERT INTO parent(first_name, last_name, phone) VALUES(?,?,?)",
                              (r.choice(BOYS + GIRLS), last, f"0803{i:07d}")).lastrowid
            sid = con.execute("INSERT INTO student(admission_no, first_name, last_name, gender, class_id, parent_id, admission_date) "
                              "VALUES(?,?,?,?,?,?,?)", (f"ESK/25/{i + 1:03d}", first, last, "M" if male else "F", cid, pid, "2025-09-08")).lastrowid
            base = (57 if male else 62) + noise() * 9
            att = clamp((85 if male else 91) + noise() * 5)
            decline = male and r.random() < .2
            trend = -7 if decline else noise() * 1.5
            if decline and first_decliner is None:
                first_decliner = sid, pid
            bias = [2, -3, 2, -1, -2, 2] if male else [1, 4, 0, 2, 3, 1]
            for t, eid in enumerate(exam_ids):
                for k, subid in enumerate(sub_ids):
                    total = clamp(base + bias[k] + trend * t + noise() * 6 + (att - 85) * .15)
                    ca = clamp(total * .4 + noise() * 2, 40)
                    es = clamp(total - ca, 60)
                    con.execute("INSERT INTO assessment(student_id, subject_id, exam_id, ca_score, exam_score, total_score, grade) "
                                "VALUES(?,?,?,?,?,?,?)", (sid, subid, eid, ca, es, ca + es, an.grade(ca + es)))
                present = clamp(60 * (att + noise() * 3 - (3 * t if decline else 0)) / 100, 60)
                con.execute("INSERT INTO attendance(student_id, exam_id, days_present, days_open) VALUES(?,?,?,60)", (sid, eid, present))
            n += 1
    # demo accounts
    first_student = con.execute("SELECT id FROM student ORDER BY id LIMIT 1").fetchone()[0]
    links = {"teacher": {"teacher_id": tch_ids[0]}, "parent": {"parent_id": first_decliner[1]},
             "student": {"student_id": first_decliner[0] or first_student}}
    for name, pw, role in ACCOUNTS:
        l = links.get(role, {})
        con.execute("INSERT INTO user(username, password_hash, role, teacher_id, parent_id, student_id) VALUES(?,?,?,?,?,?)",
                    (name, generate_password_hash(pw), role, l.get("teacher_id"), l.get("parent_id"), l.get("student_id")))
    con.commit()
    con.close()
    # run the at-risk detection on every exam so alert history exists
    from app import create_app
    app = create_app({"DATABASE": path})
    with app.app_context():
        total = sum(an.detect(e) for e in exam_ids)
    print(f"Seeded {n} students, {len(exam_ids)} exams, {total} alerts -> {path}")
    return path


if __name__ == "__main__":
    seed(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB)
    print("\nDemo accounts (change these before real use):")
    for name, pw, role in ACCOUNTS:
        print(f"  {name:<11} {pw:<13} {role}")
