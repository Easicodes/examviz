import io

from seed import ACCOUNTS

PW = {n: p for n, p, _ in ACCOUNTS}


def test_login_required(client):
    r = client.get("/dashboard")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_login_wrong_password_and_lockout(client):
    for _ in range(5):
        assert client.login("admin", "nope").status_code == 200
    assert "Too many" in client.login("admin", PW["admin"]).get_data(as_text=True)


def test_csrf_blocks_post_without_token(client):
    assert client.c.post("/login", data={"username": "admin", "password": PW["admin"]}).status_code == 400


def test_every_page_renders_for_every_role(as_):
    pages = {"admin": ["/dashboard", "/api/dashboard", "/students", "/student/1", "/risk", "/risk?gender=all&status=all",
                       "/interventions", "/scores", "/import", "/reports", "/reports/print", "/admin", "/password"],
             "counselor1": ["/dashboard", "/risk", "/interventions", "/reports"],
             "teacher1": ["/dashboard", "/scores", "/import", "/students"],
             "parent1": ["/family"], "student1": ["/me"]}
    for user, urls in pages.items():
        c = as_(user, PW[user])
        for u in urls:
            r = c.get(u, follow_redirects=True)
            assert r.status_code == 200, (user, u, r.status_code)


def test_role_restrictions(as_):
    assert as_("student1", PW["student1"]).get("/dashboard").status_code == 403
    assert as_("parent1", PW["parent1"]).get("/admin").status_code == 403
    assert as_("counselor1", PW["counselor1"]).get("/scores").status_code == 403
    assert as_("teacher1", PW["teacher1"]).get("/admin").status_code == 403


def test_parent_sees_only_own_child(as_):
    c = as_("parent1", PW["parent1"])
    html = c.get("/family").get_data(as_text=True)
    import re
    ids = {int(x) for x in re.findall(r"/student/(\d+)", html)}
    assert len(ids) == 1
    assert c.get(f"/student/{ids.pop()}").status_code == 200
    assert c.get("/student/2").status_code == 403 or c.get("/student/3").status_code == 403


def test_student_sees_only_self(as_):
    c = as_("student1", PW["student1"])
    mine = c.get("/me").headers["Location"]
    assert c.get(mine).status_code == 200
    other = 1 if not mine.endswith("/1") else 2
    assert c.get(f"/student/{other}").status_code == 403


def test_dashboard_numbers(as_):
    c = as_("admin", PW["admin"])
    d = c.get("/api/dashboard").get_json()
    assert len(d["subjects"]) == 6 and d["kpi"]["students"] == 90
    assert d["kpi"]["girls"] > d["kpi"]["boys"]          # sample data builds in a gap
    assert d["risk"]["M"] > d["risk"]["F"]
    sub = d["subjects"][0]["id"]
    f = c.get(f"/api/dashboard?subject={sub}&class=1").get_json()
    assert f["kpi"]["students"] == 30
    assert abs(sum(d["grades"]["M"]) - 100) < 1


def test_detection_is_repeatable(as_):
    c = as_("counselor1", PW["counselor1"])
    before = c.get("/risk?gender=all&status=all").get_data(as_text=True).count("sev-")
    assert before > 0
    c.post("/risk/detect", {"exam": 3})
    after = c.get("/risk?gender=all&status=all").get_data(as_text=True).count("sev-")
    assert after == before   # running it again creates no duplicates


def test_intervention_flow(as_, app):
    from db import q1
    c = as_("counselor1", PW["counselor1"])
    with app.app_context():
        aid = q1("SELECT al.id FROM alert al JOIN assessment a ON a.id=al.assessment_id WHERE a.exam_id=3 AND al.status='open'")["id"]
    r = c.post("/interventions/new", {"alert_id": aid, "type": "Mentoring", "description": "Weekly", "start_date": "2026-09-01"})
    assert r.status_code == 302
    with app.app_context():
        assert q1("SELECT status FROM alert WHERE id=?", (aid,))["status"] == "acknowledged"
        iid = q1("SELECT id FROM intervention")["id"]
    assert c.post(f"/interventions/{iid}/status", {"status": "completed"}).status_code == 302
    with app.app_context():
        row = q1("SELECT status, end_date FROM intervention WHERE id=?", (iid,))
        assert row["status"] == "completed" and row["end_date"]
    assert c.post(f"/interventions/{iid}/status", {"status": "bogus"}).status_code == 400
    assert "Mentoring" in c.get("/interventions").get_data(as_text=True)


def test_teacher_can_only_score_own_subject(as_):
    c = as_("teacher1", PW["teacher1"])       # teaches Mathematics (subject 1)
    assert c.get("/scores?exam=3&class=1&subject=1").status_code == 200
    assert c.get("/scores?exam=3&class=1&subject=2").status_code == 403


def test_score_entry_validates_and_saves(as_, app):
    from db import q1
    c = as_("teacher1", PW["teacher1"])
    base = "/scores?exam=3&class=1&subject=1"
    c.post(base, {"exam": 3, "class": 1, "subject": 1, "ca_1": "30", "ex_1": "50"})
    with app.app_context():
        r = q1("SELECT total_score, grade FROM assessment WHERE student_id=1 AND subject_id=1 AND exam_id=3")
        assert (r["total_score"], r["grade"]) == (80, "A")
    c.post(base, {"exam": 3, "class": 1, "subject": 1, "ca_1": "99", "ex_1": "50"})   # CA above 40 is rejected
    with app.app_context():
        assert q1("SELECT total_score FROM assessment WHERE student_id=1 AND subject_id=1 AND exam_id=3")["total_score"] == 80


def test_csv_import_reports_bad_rows(as_, app):
    from db import q1
    c = as_("admin", PW["admin"])
    csv = ("admission_no,subject_code,exam_name,ca_score,exam_score\n"
           "ESK/25/001,MTH,Third Term,20,40\n"
           "NOPE,MTH,Third Term,20,40\n"
           "ESK/25/002,MTH,Third Term,70,40\n")
    r = c.post("/import", {"kind": "scores", "file": (io.BytesIO(csv.encode()), "s.csv")}, content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert "<strong>1</strong> row imported" in html and "unknown admission_no" in html and "between 0 and 40" in html
    with app.app_context():
        assert q1("SELECT total_score FROM assessment WHERE student_id=1 AND subject_id=1 AND exam_id=3")["total_score"] == 60


def test_student_and_attendance_import(as_, app):
    from db import q1
    c = as_("admin", PW["admin"])
    st = "admission_no,first_name,last_name,gender,class_name,parent_name\nNEW/001,Tola,Bright,F,SS3A,Ada Bright\n"
    assert "<strong>1</strong>" in c.post("/import", {"kind": "students", "file": (io.BytesIO(st.encode()), "a.csv")},
                                          content_type="multipart/form-data").get_data(as_text=True)
    at = "admission_no,exam_name,days_present,days_open\nNEW/001,Third Term,40,60\n"
    assert "<strong>1</strong>" in c.post("/import", {"kind": "attendance", "file": (io.BytesIO(at.encode()), "b.csv")},
                                          content_type="multipart/form-data").get_data(as_text=True)
    with app.app_context():
        assert q1("SELECT days_present FROM attendance a JOIN student s ON s.id=a.student_id WHERE s.admission_no='NEW/001'")["days_present"] == 40


def test_csv_reports(as_):
    c = as_("admin", PW["admin"])
    for kind in ("at-risk", "results", "gender-summary"):
        r = c.get(f"/reports/{kind}.csv")
        assert r.status_code == 200 and r.mimetype == "text/csv" and "attachment" in r.headers["Content-Disposition"]
    assert c.get("/reports/nothing.csv").status_code == 404


def test_admin_creates_user_who_can_sign_in(as_, client):
    c = as_("admin", PW["admin"])
    c.post("/admin/add/user", {"username": "counsel2", "password": "longenough1", "role": "counselor", "link": ""})
    assert client.login("counsel2", "longenough1").status_code == 302
    c.post("/admin/add/user", {"username": "x", "password": "short", "role": "counselor"})
    assert "counsel2" in c.get("/admin").get_data(as_text=True)


def test_change_password(as_, client):
    c = as_("counselor1", PW["counselor1"])
    c.post("/password", {"old": PW["counselor1"], "new": "brandnew123", "again": "brandnew123"})
    assert client.login("counselor1", PW["counselor1"]).status_code == 200
    assert client.login("counselor1", "brandnew123").status_code == 302


def test_security_headers_and_open_redirect(client):
    r = client.get("/login")
    assert "Content-Security-Policy" in r.headers and r.headers["X-Frame-Options"] == "DENY"
    r = client.c.post("/login?next=//evil.example", data={"username": "admin", "password": PW["admin"], "csrf": client.token()})
    assert r.status_code == 302 and "evil" not in r.headers["Location"]
