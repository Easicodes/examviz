import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402
import seed  # noqa: E402


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return seed.seed(str(tmp_path_factory.mktemp("db") / "test.db"))


@pytest.fixture()
def app(db_path, tmp_path):
    import shutil
    copy = tmp_path / "copy.db"
    shutil.copy(db_path, copy)  # every test gets a fresh copy of the seeded data
    app_module.FAILS.clear()
    return app_module.create_app({"DATABASE": str(copy), "TESTING": True, "SECRET_KEY": "test"})


class Client:
    def __init__(self, c):
        self.c = c

    def token(self, path="/login"):
        html = self.c.get(path).get_data(as_text=True)
        m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
        return m.group(1)

    def login(self, user, pw):
        return self.c.post("/login", data={"username": user, "password": pw, "csrf": self.token()})

    def post(self, path, data=None, **kw):
        with self.c.session_transaction() as s:
            tok = s.setdefault("csrf", "tok")
        return self.c.post(path, data={**(data or {}), "csrf": tok}, **kw)

    def get(self, *a, **k):
        return self.c.get(*a, **k)


@pytest.fixture()
def client(app):
    return Client(app.test_client())


@pytest.fixture()
def as_(app):
    def make(user, pw):
        c = Client(app.test_client())
        assert c.login(user, pw).status_code == 302
        return c
    return make
