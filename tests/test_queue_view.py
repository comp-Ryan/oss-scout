from oss_scout import db, queue_view

def _seed(conn, iid, total, category, state="shortlisted"):
    conn.execute("insert or ignore into repos(full_name, stars) values ('o/r', 1)")
    conn.execute("insert into issues(id, repo, number, title, url, state, harvested_at, last_seen_at) values (?,'o/r',?, 't','u',?, 'x','x')", (iid, int(iid[1:]), state))
    conn.execute("insert into classifications(issue_id, question, choice, confidence, probabilities, model, asked_at) values (?,'category',?,0.9,'{}','s','x')", (iid, category))
    conn.execute("insert into scores(issue_id, total, breakdown, weights_hash, scored_at) values (?,?,'{\"a\":1}','h','x')", (iid, total))
    conn.commit()

def test_queue_rows_sorted_filtered_and_ranked(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn, "I1", 5.0, "quant_trading"); _seed(conn, "I2", 9.0, "other"); _seed(conn, "I3", 7.0, "quant_trading", state="dismissed")
    rows = queue_view.queue_rows(conn, None, None, 10)
    assert [r["score"] for r in rows] == [9.0, 5.0] and rows[0]["rank"] == 1
    assert rows[0]["breakdown"] == {"a": 1}
    assert [r["number"] for r in queue_view.queue_rows(conn, "quant_trading", None, 10)] == [1]
    assert [r["number"] for r in queue_view.queue_rows(conn, None, "dismissed", 10)] == [3]

def test_render_table_has_a_row_per_item(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn, "I1", 5.0, "quant_trading")
    t = queue_view.render_table(queue_view.queue_rows(conn, None, None, 10))
    assert t.row_count == 1
