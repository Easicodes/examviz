# ExamViz

Data Visualization System for an Examination Schooling System. It turns examination records into
dashboards that compare boys and girls, flags students who are slipping (with a focus on boys),
and tracks the support given to them.

Stack: **Python 3 and Flask** (application layer), **SQLite** (data layer, relational),
**HTML, CSS and Chart.js** (presentation layer).

## Run it

```bash
python -m venv venv
venv\Scripts\activate          # Windows.  On Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
python seed.py                 # creates instance/examviz.db with SAMPLE data
python app.py                  # open http://127.0.0.1:5000
```

Charts load Chart.js and the fonts from the internet. Without a connection the pages still work, but the charts stay empty.

Demo accounts (created by `seed.py`; change them before any real use):

| Username | Password | Role |
|---|---|---|
| admin | Admin@123 | Administrator |
| teacher1 | Teacher@123 | Teacher (Mathematics) |
| counselor1 | Counsel@123 | Counselor |
| parent1 | Parent@123 | Parent of one boy |
| student1 | Student@123 | Student |

The sample data is computer-generated (90 students, 6 subjects, 3 exams) and is built so that girls average
higher than boys and some boys decline over the year. Replace it with real records through **Import data**.

## Tests

```bash
pytest
```

18 tests cover sign-in and lockout, CSRF, role access, the dashboard numbers, repeatable alert detection,
the intervention flow, score validation, CSV import and exports.

## How it maps to Chapter 3

| Chapter 3 | Where it lives |
|---|---|
| Layered architecture (Fig. 2) | Presentation: `templates/`, `static/`. Application: `app.py`, `analytics.py`. Data: `schema.sql`, `db.py`. |
| ERD (Fig. 5) | `schema.sql`: student, parent, class, teacher, subject, examination, assessment, alert, intervention, user, plus attendance, log, class_subject, teacher_subject. |
| Use cases (Fig. 3) | Login, dashboards, compare boys and girls, at-risk list, generate solutions and interventions, track progress, reports, user/subject/class/exam management, import. |
| Sequence (Fig. 4) | Login, then `/api/dashboard` (query, process, return), then charts, then `/risk` and interventions. |
| Functional requirements | Authentication (`/login`), data management (`/scores`, `/import`), analysis and visualization (`/dashboard`), gender comparison, reports (`/reports`). |
| Non-functional | Security: hashed passwords, CSRF tokens, role checks, parameterised SQL, audit log, login lockout, security headers. Usability: keyboard focus, labelled forms, responsive layout. |

## Rules the system uses

- Score: CA out of 40 plus exam out of 60 gives a total out of 100.
- Grades: A 70+, B 60+, C 50+, D 45+, E 40+, F below 40. Pass is 40 or more.
- A student is flagged when the latest average is below 45, the average fell 8 points or more since the previous exam,
  or attendance is below 80%. Change these in `analytics.py` (`LOW_AVG`, `DROP`, `MIN_ATT`).
- Suggested support follows the alert: remedial classes, mentoring and counselling, or attendance monitoring with parental engagement.

## Differences from the Chapter 3 diagrams (say these in Chapter 4)

- EXAMINATION has no `class_id`, because exams are school-wide. A class reaches an exam through its students' results.
- Notifications are in-app: a parent sees alerts and support plans for their child after signing in. There is no email or SMS yet.
- PDF export is the browser's Print, then Save as PDF. Tabular exports are CSV, which opens in Excel.
- Data warehouse and backup servers from the infrastructure layer are out of scope. Back up by copying `instance/examviz.db`.

## Limits worth stating in Chapter 5

- SQLite suits one school. For many schools, move to MySQL or PostgreSQL.
- The login lockout is held in memory, so it resets when the server restarts.
- Sample data shows the system working but is not evidence about real students. Chapter 4 results should use the school's own records.

## Deploying

GitHub Pages cannot run Flask. Put the code on GitHub for version control, and host the running app on a Python host such as
PythonAnywhere or Render. In production: set the `EXAMVIZ_SECRET` environment variable, serve over HTTPS, run with gunicorn instead of `python app.py`, and change every demo password.
