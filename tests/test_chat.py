import json
import pytest
from oss_scout import db
from oss_scout.agents import chat
from oss_scout.config import Config

FIRST = {"conversation_id": "c-1", "status": "SUCCESS", "response": "Hello, I read the brief.\n", "usage": {"input_tokens": 100, "output_tokens": 8}}
RESUMED = {"conversation_id": "c-1", "status": "SUCCESS", "response": "The fix goes in core.py.\n", "usage": {"input_tokens": 200, "output_tokens": 9}}

class R:
    def __init__(self, body, code=0): self.stdout, self.returncode, self.stderr = json.dumps(body), code, ""

def _seed(conn, state="analyzed", with_brief=True):
    conn.execute("insert into repos(full_name, stars, language, contributing_text) values ('o/r', 5, 'Python', 'Be nice')")
    conn.execute("insert into issues(id, repo, number, title, body, labels, url, state, harvested_at, last_seen_at) values ('I1','o/r',1,'T','B','[]','u',?,'x','x')", (state,))
    if with_brief:
        conn.execute("insert into agent_runs(issue_id, model, status, started_at, brief) values ('I1','m','done','x','{\"summary\":\"S\",\"approach\":\"A\",\"files\":[\"core.py\"]}')")
    conn.commit()

def test_first_turn_starts_conversation_and_stores_both_messages(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    calls = []
    def runner(cmd, **kw): calls.append(cmd); return R(FIRST)
    out = chat.send(conn, Config(data_dir=tmp_path), "I1", "Where should the fix go?", runner=runner)
    assert out["assistant"]["content"].startswith("Hello") and out["user"]["content"] == "Where should the fix go?"
    assert chat.thread(conn, "I1")["conversation_id"] == "c-1"
    assert [m["role"] for m in chat.messages(conn, "I1")] == ["user", "assistant"]
    cmd = calls[0]
    assert "--conversation" not in cmd and cmd[-2] == "--print"
    prompt = cmd[-1]
    assert "T" in prompt and "S" in prompt and "Be nice" in prompt and "Where should the fix go?" in prompt
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"

def test_second_turn_resumes_and_sends_only_the_message(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    responses = [R(FIRST), R(RESUMED)]
    calls = []
    def runner(cmd, **kw): calls.append(cmd); return responses.pop(0)
    chat.send(conn, Config(data_dir=tmp_path), "I1", "first", runner=runner)
    out = chat.send(conn, Config(data_dir=tmp_path), "I1", "second", runner=runner)
    assert calls[1][calls[1].index("--conversation") + 1] == "c-1"
    assert calls[1][-1] == "second"
    assert out["assistant"]["content"].startswith("The fix")
    assert len(chat.messages(conn, "I1")) == 4

def test_missing_clone_runs_without_add_dir(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    calls = []
    chat.send(conn, Config(data_dir=tmp_path), "I1", "hi", runner=lambda cmd, **kw: calls.append((cmd, kw)) or R(FIRST))
    cmd, kw = calls[0]
    assert "--add-dir" not in cmd and kw["cwd"] == str(tmp_path)
    (tmp_path / "clones" / "o__r" / ".git").mkdir(parents=True)
    chat.send(conn, Config(data_dir=tmp_path), "I1", "hi again", runner=lambda cmd, **kw: calls.append((cmd, kw)) or R(RESUMED))
    cmd, kw = calls[1]
    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path / "clones" / "o__r") and kw["cwd"] == str(tmp_path / "clones" / "o__r")

def test_agy_failure_keeps_user_message_and_raises(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    with pytest.raises(chat.ChatError):
        chat.send(conn, Config(data_dir=tmp_path), "I1", "hi", runner=lambda cmd, **kw: R({"status": "ERROR"}, code=1))
    assert [m["role"] for m in chat.messages(conn, "I1")] == ["user"]

def test_set_plan(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    chat.send(conn, Config(data_dir=tmp_path), "I1", "hi", runner=lambda cmd, **kw: R(FIRST))
    chat.set_plan(conn, "I1", "Do X then Y")
    assert chat.thread(conn, "I1")["plan"] == "Do X then Y"
