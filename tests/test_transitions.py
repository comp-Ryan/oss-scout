import pytest
from oss_scout import db, transitions

def _issue(conn, state="harvested"):
    conn.execute("insert into repos(full_name) values ('o/r')")
    conn.execute("insert into issues(id, repo, number, title, url, state, harvested_at, last_seen_at) values ('I1','o/r',1,'t','u',?, 'x','x')", (state,))
    conn.commit()

def test_valid_edge_writes_row_and_updates_state(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _issue(conn)
    transitions.apply(conn, "I1", "classified", "classifier", "ok")
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "classified"
    row = conn.execute("select from_state,to_state,actor,note from transitions").fetchone()
    assert tuple(row) == ("harvested", "classified", "classifier", "ok")

def test_invalid_edge_raises_and_changes_nothing(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _issue(conn)
    with pytest.raises(transitions.InvalidTransition):
        transitions.apply(conn, "I1", "pr_open", "user")
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "harvested"
    assert conn.execute("select count(*) from transitions").fetchone()[0] == 0

def test_user_can_dismiss_from_any_state_but_classifier_only_from_classified(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _issue(conn, "shortlisted")
    with pytest.raises(transitions.InvalidTransition):
        transitions.apply(conn, "I1", "dismissed", "classifier")
    transitions.apply(conn, "I1", "dismissed", "user")
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "dismissed"

def test_terminal_states_cannot_advance(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _issue(conn, "dismissed")
    with pytest.raises(transitions.InvalidTransition):
        transitions.apply(conn, "I1", "classified", "classifier")
