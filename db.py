"""SQLite helpers. Every query uses ? placeholders, never string formatting of user input."""
import os
import sqlite3

from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path):
    con = sqlite3.connect(path)
    with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
        con.executescript(f.read())
    con.commit()
    con.close()


def q(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def q1(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def ex(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


def audit(user_id, action, detail=""):
    ex("INSERT INTO log(user_id, action, detail) VALUES(?,?,?)", (user_id, action, detail))
