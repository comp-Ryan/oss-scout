from __future__ import annotations
import json
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from oss_scout import classify, db, queue_view, score, transitions
from oss_scout.agents import analysis, chat
from oss_scout.config import PROJECT_ROOT, Config, load_config, update_yaml
from oss_scout.transitions import InvalidTransition

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class Note(BaseModel):
    note: str = ""


class Content(BaseModel):
    content: str


class Plan(BaseModel):
    plan: str


class Settings(BaseModel):
    weights: dict[str, float] | None = None
    category_values: dict[str, float] | None = None


def create_app(cfg: Config, *, chat_runner=subprocess.run, gh_factory=None, config_path: Path | None = None) -> FastAPI:
    """Read-mostly JSON API over the same modules the CLI uses. Nothing here talks to GitHub except
    the related-PR lookup that seeds a chat, and that is a read."""
    app = FastAPI(title="OSS Scout")

    def conn():
        return db.connect(cfg.db_path)

    def issue_or_404(c, issue_id):
        row = c.execute("select * from issues where id = ?", (issue_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no issue {issue_id}")
        return dict(row)

    @app.get("/api/status")
    def status():
        c = conn()
        counts = {r["state"]: r["n"] for r in c.execute("select state, count(*) n from issues group by state")}
        last = c.execute("select at from transitions order by id desc limit 1").fetchone()
        repos = c.execute("select count(*), sum(excluded_reason is not null) from repos").fetchone()
        return {"issues": counts, "last_transition": last[0] if last else None, "repos": repos[0], "repos_excluded": repos[1] or 0}

    @app.get("/api/queue")
    def queue(category: str | None = None, state: str | None = None, limit: int = 50):
        return queue_view.queue_rows(conn(), category, state, limit)

    @app.get("/api/issues/{issue_id}")
    def issue_detail(issue_id: str):
        c = conn()
        issue = issue_or_404(c, issue_id)
        repo = dict(c.execute("select * from repos where full_name = ?", (issue["repo"],)).fetchone() or {})
        repo.pop("contributing_text", None)
        sc = score.latest_score(c, issue_id)
        runs = [dict(r) for r in c.execute(
            "select id, status, model, attempt, started_at, finished_at, duration_s, input_tokens, output_tokens, error "
            "from agent_runs where issue_id = ? order by id desc", (issue_id,))]
        trans = [dict(r) for r in c.execute("select from_state, to_state, actor, note, at from transitions where issue_id = ? order by id", (issue_id,))]
        return {"issue": issue, "repo": repo, "classification": classify.latest(c, issue_id),
                "score": {"total": sc[0], "breakdown": sc[1]} if sc else None,
                "brief": analysis.latest_brief(c, issue_id), "runs": runs, "transitions": trans,
                "thread": chat.thread(c, issue_id), "messages": chat.messages(c, issue_id)}

    def _move(issue_id, to_state, note=""):
        c = conn()
        issue_or_404(c, issue_id)
        try:
            transitions.apply(c, issue_id, to_state, "user", note)
        except InvalidTransition as e:
            raise HTTPException(409, str(e))
        return {"id": issue_id, "state": to_state}

    @app.post("/api/issues/{issue_id}/dismiss")
    def dismiss(issue_id: str, body: Note):
        return _move(issue_id, "dismissed", body.note)

    @app.post("/api/issues/{issue_id}/discuss")
    def discuss(issue_id: str):
        return _move(issue_id, "discussing")

    @app.post("/api/issues/{issue_id}/approve")
    def approve(issue_id: str, body: Plan):
        out = _move(issue_id, "plan_approved", "plan approved")
        chat.set_plan(conn(), issue_id, body.plan)
        return out

    @app.get("/api/issues/{issue_id}/messages")
    def get_messages(issue_id: str):
        c = conn()
        issue_or_404(c, issue_id)
        return chat.messages(c, issue_id)

    @app.post("/api/issues/{issue_id}/messages")
    def post_message(issue_id: str, body: Content):
        c = conn()
        issue_or_404(c, issue_id)
        related = None
        if gh_factory is not None:
            try:
                related = gh_factory().related_prs(issue_id)
            except Exception:
                related = None
        try:
            return chat.send(c, cfg, issue_id, body.content, runner=chat_runner, related=related)
        except chat.ChatError as e:
            raise HTTPException(502, str(e))

    @app.get("/api/runs")
    def runs(limit: int = 50):
        c = conn()
        return [dict(r) for r in c.execute(
            "select a.id, a.issue_id, a.status, a.model, a.started_at, a.finished_at, a.duration_s, a.input_tokens, a.output_tokens, a.error, "
            "i.repo, i.number, i.title from agent_runs a join issues i on i.id = a.issue_id order by a.id desc limit ?", (limit,))]

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: int, after: int = 0):
        c = conn()
        run = c.execute("select status from agent_runs where id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(404, f"no run {run_id}")
        evs = []
        for r in c.execute("select seq, at, event from agent_events where run_id = ? and seq > ? order by seq", (run_id, after)):
            try:
                payload = json.loads(r["event"])
            except json.JSONDecodeError:
                payload = {"raw": r["event"]}
            evs.append({"seq": r["seq"], "at": r["at"], "event": payload})
        return {"status": run["status"], "events": evs}

    def current():
        return load_config(config_path) if config_path else cfg

    @app.get("/api/settings")
    def get_settings():
        cur = current()
        return {"weights": cur.weights, "category_values": cur.category_values}

    @app.put("/api/settings")
    def put_settings(body: Settings):
        cur = current()
        for section, allowed in (("weights", cur.weights), ("category_values", cur.category_values)):
            values = getattr(body, section)
            if values:
                unknown = set(values) - set(allowed)
                if unknown:
                    raise HTTPException(422, f"unknown {section}: {sorted(unknown)}")
        path = config_path or (PROJECT_ROOT / "scout.yaml")
        if body.weights:
            update_yaml(path, "weights", body.weights)
        if body.category_values:
            update_yaml(path, "category_values", body.category_values)
        new = load_config(path)
        new.data_dir = cfg.data_dir
        score.run_scoring(conn(), new)
        return {"weights": new.weights, "category_values": new.category_values}

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            return FileResponse(WEB_DIST / "index.html")
    else:
        @app.get("/{path:path}")
        def hint(path: str):
            return HTMLResponse("<p>OSS Scout API is running. Build the frontend: <code>cd web && npm ci && npm run build</code> (writes web/dist).</p>")

    return app
