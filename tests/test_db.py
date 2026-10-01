import sqlite3
from oss_scout import db

def test_connect_creates_tables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"repos", "issues", "classifications", "scores", "transitions", "meta"} <= names
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "2"

def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    db.migrate(conn)

def test_schema_v2_tables_and_version(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"agent_runs", "agent_events"} <= names
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "2"

def test_v1_database_upgrades_in_place(tmp_path):
    import sqlite3
    raw = sqlite3.connect(tmp_path / "old.db")
    raw.executescript(db.SCHEMA_V1)
    raw.execute("insert into meta(key, value) values ('schema_version', '1')"); raw.commit(); raw.close()
    conn = db.connect(tmp_path / "old.db")
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "2"
    conn.execute("select id, run_id, seq, at, event from agent_events")
