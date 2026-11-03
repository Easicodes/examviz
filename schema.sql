-- ExamViz database (SQLite). Follows the ERD in Figure 5 of the project, plus
-- attendance, user and log tables from the architecture diagram (Figure 2).
-- One deliberate change: EXAMINATION has no class_id, because exams are school-wide.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS parent(
  id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
  phone TEXT, email TEXT, address TEXT);

CREATE TABLE IF NOT EXISTS teacher(
  id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
  email TEXT UNIQUE, phone TEXT, role TEXT, qualification TEXT);

CREATE TABLE IF NOT EXISTS class(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, grade_level TEXT, academic_year TEXT NOT NULL,
  class_teacher_id INTEGER REFERENCES teacher(id), UNIQUE(name, academic_year));

CREATE TABLE IF NOT EXISTS subject(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL UNIQUE, description TEXT);

CREATE TABLE IF NOT EXISTS student(
  id INTEGER PRIMARY KEY, admission_no TEXT NOT NULL UNIQUE,
  first_name TEXT NOT NULL, last_name TEXT NOT NULL,
  gender TEXT NOT NULL CHECK(gender IN ('M','F')), date_of_birth TEXT,
  class_id INTEGER NOT NULL REFERENCES class(id),
  parent_id INTEGER REFERENCES parent(id), admission_date TEXT);

CREATE TABLE IF NOT EXISTS teacher_subject(
  teacher_id INTEGER NOT NULL REFERENCES teacher(id), subject_id INTEGER NOT NULL REFERENCES subject(id),
  PRIMARY KEY(teacher_id, subject_id));

CREATE TABLE IF NOT EXISTS class_subject(
  class_id INTEGER NOT NULL REFERENCES class(id), subject_id INTEGER NOT NULL REFERENCES subject(id),
  PRIMARY KEY(class_id, subject_id));

CREATE TABLE IF NOT EXISTS examination(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, term INTEGER NOT NULL CHECK(term BETWEEN 1 AND 3),
  academic_year TEXT NOT NULL, exam_date TEXT, UNIQUE(name, academic_year));

CREATE TABLE IF NOT EXISTS user(
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','teacher','counselor','parent','student')),
  email TEXT, teacher_id INTEGER REFERENCES teacher(id), parent_id INTEGER REFERENCES parent(id),
  student_id INTEGER REFERENCES student(id), active INTEGER NOT NULL DEFAULT 1, last_login TEXT);

-- CA is out of 40 and the exam out of 60, so the total is out of 100.
CREATE TABLE IF NOT EXISTS assessment(
  id INTEGER PRIMARY KEY,
  student_id INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  subject_id INTEGER NOT NULL REFERENCES subject(id),
  exam_id INTEGER NOT NULL REFERENCES examination(id),
  ca_score REAL NOT NULL CHECK(ca_score BETWEEN 0 AND 40),
  exam_score REAL NOT NULL CHECK(exam_score BETWEEN 0 AND 60),
  total_score REAL NOT NULL, grade TEXT NOT NULL,
  entered_by INTEGER REFERENCES user(id), entry_date TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(student_id, subject_id, exam_id));

CREATE TABLE IF NOT EXISTS attendance(
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  exam_id INTEGER NOT NULL REFERENCES examination(id),
  days_present INTEGER NOT NULL, days_open INTEGER NOT NULL,
  CHECK(days_open > 0 AND days_present BETWEEN 0 AND days_open), UNIQUE(student_id, exam_id));

CREATE TABLE IF NOT EXISTS alert(
  id INTEGER PRIMARY KEY, assessment_id INTEGER NOT NULL REFERENCES assessment(id) ON DELETE CASCADE,
  alert_type TEXT NOT NULL CHECK(alert_type IN ('LOW_SCORE','DECLINE','LOW_ATTENDANCE')),
  alert_date TEXT DEFAULT CURRENT_TIMESTAMP,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','resolved')), notes TEXT);

CREATE TABLE IF NOT EXISTS intervention(
  id INTEGER PRIMARY KEY, assessment_id INTEGER NOT NULL REFERENCES assessment(id) ON DELETE CASCADE,
  intervention_type TEXT NOT NULL, description TEXT, recommended_by INTEGER REFERENCES user(id),
  start_date TEXT NOT NULL, end_date TEXT,
  status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','active','completed','cancelled')));

CREATE TABLE IF NOT EXISTS log(
  id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES user(id), action TEXT NOT NULL,
  detail TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE INDEX IF NOT EXISTS ix_assessment_exam ON assessment(exam_id, student_id);
CREATE INDEX IF NOT EXISTS ix_student_class ON student(class_id);
CREATE INDEX IF NOT EXISTS ix_alert_assessment ON alert(assessment_id);
