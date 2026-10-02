import sqlite3
from oss_scout import db

def test_connect_creates_tables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"repos", "issues", "classifications", "scores", "transitions", "meta"} <= names
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "7"

def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    db.migrate(conn)

def test_schema_v2_tables_and_version(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"agent_runs", "agent_events"} <= names
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "7"

def test_v1_database_upgrades_in_place(tmp_path):
    import sqlite3
    raw = sqlite3.connect(tmp_path / "old.db")
    raw.executescript(db.SCHEMA_V1)
    raw.execute("insert into meta(key, value) values ('schema_version', '1')"); raw.commit(); raw.close()
    conn = db.connect(tmp_path / "old.db")
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "7"
    conn.execute("select id, run_id, seq, at, event from agent_events")

def test_schema_v3_tables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"threads", "messages"} <= names

def test_schema_v4_drafts(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    cols = {r[1] for r in conn.execute("pragma table_info(drafts)")}
    assert {"issue_id", "work_dir", "worktree", "branch", "base_branch", "brief_path", "test_cmd", "pr_url", "created_at", "pushed_at", "submitted_at", "last_error"} <= cols

def test_schema_v5_lane_and_repo_source(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert "lane" in {r[1] for r in conn.execute("pragma table_info(issues)")}
    cols = {r[1] for r in conn.execute("pragma table_info(repos)")}
    assert {"source", "rising", "created_at"} <= cols
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "7"

def test_schema_v6_star_history_and_velocity(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert "repo_star_history" in {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert "stars_per_day" in {r[1] for r in conn.execute("pragma table_info(repos)")}
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "7"

def test_schema_v7_author_columns(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert {"author", "author_type"} <= {r[1] for r in conn.execute("pragma table_info(issues)")}
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "7"
