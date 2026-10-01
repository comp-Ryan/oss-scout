import io, json
from email.message import Message
from oss_scout.jev_client import JevClient

class FakeResp(io.BytesIO):
    def __init__(self, body): super().__init__(json.dumps(body).encode()); self.headers = Message()
    def __enter__(self): return self
    def __exit__(self, *a): return False

def test_ask_posts_contract_and_caches(tmp_path):
    calls = []
    def opener(req, timeout=0):
        calls.append(json.loads(req.data))
        return FakeResp({"model": "jev-1", "answers": {"q": {"choice": "a", "confidence": 1.0}}})
    c = JevClient("k", cache_path=tmp_path / "c.db", opener=opener)
    qs = {"q": {"type": "choice", "instructions": "i", "criteria": {"a": "A", "b": "B"}}}
    r1 = c.ask("state", qs)
    r2 = c.ask("state", qs)
    assert r1 == r2 and len(calls) == 1
    assert calls[0] == {"model": "jev-latest", "state": "state", "questions": qs}

def test_ask_retries_twice_then_raises(tmp_path):
    import pytest
    from oss_scout.jev_client import JevError
    n = {"calls": 0}
    def opener(req, timeout=0):
        n["calls"] += 1
        raise TimeoutError("slow")
    c = JevClient("k", opener=opener)
    with pytest.raises(JevError):
        c.ask("state", {})
    assert n["calls"] == 3
