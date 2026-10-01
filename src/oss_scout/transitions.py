from __future__ import annotations
import sqlite3
from oss_scout.db import utcnow

STATES = [
    "harvested", "classified", "shortlisted", "analyzed", "discussing", "plan_approved",
    "drafting", "pr_open", "merged", "closed", "dismissed", "stale",
]
TERMINAL = {"merged", "closed", "dismissed", "stale"}

# (from, to) -> set of actor kinds allowed. Actor "agent:<id>" matches "agent".
EDGES: dict[tuple[str, str], set[str]] = {
    ("harvested", "classified"): {"classifier"},
    ("classified", "dismissed"): {"classifier", "user"},
    ("classified", "shortlisted"): {"scorer"},
    ("shortlisted", "classified"): {"scorer"},          # fell out of the shortlist on re-score
    ("shortlisted", "analyzed"): {"agent"},
    ("analyzed", "discussing"): {"user"},
    ("discussing", "plan_approved"): {"user"},
    ("plan_approved", "drafting"): {"user", "agent"},
    ("drafting", "discussing"): {"user", "agent"},      # tests failed
    ("drafting", "pr_open"): {"user"},
    ("pr_open", "merged"): {"harvester", "user"},
    ("pr_open", "closed"): {"harvester", "user"},
}
for _s in STATES:
    if _s not in TERMINAL:
        EDGES.setdefault((_s, "dismissed"), set()).add("user")
        EDGES.setdefault((_s, "stale"), set()).add("harvester")


class InvalidTransition(Exception):
    pass


def _actor_kind(actor: str) -> str:
    return "agent" if actor.startswith("agent:") else actor


def apply(conn: sqlite3.Connection, issue_id: str, to_state: str, actor: str, note: str = "") -> None:
    if to_state not in STATES:
        raise InvalidTransition(f"unknown state {to_state}")
    row = conn.execute("select state from issues where id = ?", (issue_id,)).fetchone()
    if row is None:
        raise InvalidTransition(f"unknown issue {issue_id}")
    from_state = row[0]
    allowed = EDGES.get((from_state, to_state), set())
    if _actor_kind(actor) not in allowed:
        raise InvalidTransition(f"{actor} may not move {issue_id} from {from_state} to {to_state}")
    with conn:
        conn.execute(
            "insert into transitions(issue_id, from_state, to_state, actor, note, at) values (?,?,?,?,?,?)",
            (issue_id, from_state, to_state, actor, note, utcnow()),
        )
        conn.execute("update issues set state = ? where id = ?", (to_state, issue_id))
