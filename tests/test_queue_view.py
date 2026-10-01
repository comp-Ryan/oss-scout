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

def test_render_table_keeps_rows_on_one_line(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn, "I1", 5.0, "quant_trading")
    conn.execute("update issues set title = ?", ("x" * 300,)); conn.commit()
    t = queue_view.render_table(queue_view.queue_rows(conn, None, None, 10))
    title_col = [c for c in t.columns if c.header == "title"][0]
    assert title_col.no_wrap and title_col.overflow == "ellipsis"
    assert all(c.no_wrap for c in t.columns)

def test_queue_rows_filter_by_lane_and_carry_preview_and_repo_facts(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute("insert into repos(full_name, stars, language, rising) values ('o/r', 1200, 'Python', 1)")
    for iid, lane, body in (("I1", "resume", "line one\n\nline two " + "x" * 400), ("I2", "discover", None)):
        conn.execute("insert into issues(id, repo, number, title, body, url, state, harvested_at, last_seen_at, lane) values (?,'o/r',?, 't',?, 'u','shortlisted','x','x',?)", (iid, int(iid[1:]), body, lane))
        conn.execute("insert into scores(issue_id, total, breakdown, weights_hash, scored_at) values (?,5.0,'{}','h','x')", (iid,))
    conn.commit()
    rows = queue_view.queue_rows(conn, None, None, 10, lane="discover")
    assert [r["number"] for r in rows] == [2] and rows[0]["preview"] == ""
    rows = queue_view.queue_rows(conn, None, None, 10, lane="resume")
    assert rows[0]["preview"].startswith("line one line two") and len(rows[0]["preview"]) <= 220
    assert rows[0]["stars"] == 1200 and rows[0]["language"] == "Python" and rows[0]["rising"] == 1 and rows[0]["lane"] == "resume"
    assert len(queue_view.queue_rows(conn, None, None, 10)) == 2

def test_preview_skips_template_boilerplate_and_comments():
    body = """<!-- raise_langchain_minimums-failure -->
### Submission checklist
- [x] This is a bug, not a usage question.
- [x] I added a clear and descriptive title that summarizes this issue.
### Description
`raise_langchain_minimums.yml` failed, so LangChain-ecosystem dependency lower bounds are no longer raised.

More detail here."""
    p = queue_view.preview(body)
    assert p.startswith("`raise_langchain_minimums.yml` failed") and "checklist" not in p and "<!--" not in p and "[x]" not in p

def test_preview_falls_back_to_plain_text_when_nothing_else():
    assert queue_view.preview("- [x] done\n### Only headings") == ""
    assert queue_view.preview("Plain sentence. " * 30).endswith("…")
