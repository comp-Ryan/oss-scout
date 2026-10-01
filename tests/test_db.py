import sqlite3
from oss_scout import db

def test_connect_creates_tables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"repos", "issues", "classifications", "scores", "transitions", "meta"} <= names
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "1"

def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    db.migrate(conn)
