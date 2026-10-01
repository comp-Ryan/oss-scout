from __future__ import annotations
import hashlib
import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Callable

JEV_API_URL = "https://api.typesafe.ai/v1/systemone"


class JevError(Exception):
    pass


class JevClient:
    """Typed-decision client. Same wire contract as jev-trader's RealJevClient:
    POST {model, state, questions} -> {model, answers: {name: {choice, confidence, probabilities}}, usage}."""

    def __init__(self, api_key: str, model: str = "jev-latest", cache_path: Path | None = None,
                 opener: Callable = urllib.request.urlopen, timeout: float = 20.0):
        self.api_key = api_key
        self.model = model
        self.opener = opener
        self.timeout = timeout
        self._cache = None
        if cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            self._cache = sqlite3.connect(cache_path, timeout=30.0)
            self._cache.execute("create table if not exists cache (key text primary key, response text)")
            self._cache.commit()

    def ask(self, state: str, questions: dict) -> dict:
        payload = {"model": self.model, "state": state, "questions": questions}
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if self._cache:
            row = self._cache.execute("select response from cache where key = ?", (key,)).fetchone()
            if row:
                return json.loads(row[0])
        req = urllib.request.Request(JEV_API_URL, data=json.dumps(payload).encode(), headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        last = None
        for _attempt in range(3):
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
                break
            except Exception as e:  # timeouts and 5xx: retry twice
                last = e
        else:
            raise JevError(f"jev request failed: {last}")
        if self._cache:
            self._cache.execute("insert or replace into cache(key, response) values (?, ?)", (key, json.dumps(data)))
            self._cache.commit()
        return data
