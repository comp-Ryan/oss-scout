from __future__ import annotations
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_WEIGHTS = {
    "recognition": 3.0, "mergeability": 3.0, "language_fit": 2.0, "category": 2.0,
    "difficulty_fit": 1.5, "freshness": 1.0, "bounty": 0.5, "ai_policy": 1.0,
}
DEFAULT_CATEGORY_VALUES = {
    "quant_trading": 1.0, "prediction_markets_finance": 1.0, "ai_agents_llm": 1.0,
    "data_scraping": 0.7, "dev_tools_cli": 0.7, "web_frontend": 0.8, "other": 0.3,
}
DEFAULT_LANGUAGE_FIT = {"Python": 1.0, "TypeScript": 0.8, "JavaScript": 0.5}
DEFAULT_CATEGORY_KEYWORDS = {
    "quant_trading": ["backtest", "backtesting", "trading bot", "algorithmic trading"],
    "prediction_markets_finance": ["polymarket", "kalshi", "prediction market"],
    "ai_agents_llm": ["mcp server", "llm agent", "langchain", "tool calling"],
}


@dataclass
class Config:
    min_stars: int = 200
    repo_idle_days: int = 60
    issue_updated_within_days: int = 30
    pages_per_query: int = 3
    per_page: int = 100
    repo_pages_per_query: int = 1
    github_min_interval: float = 0.25  # seconds between GitHub requests; stays under the secondary rate limit
    github_search_min_interval: float = 2.1  # search bucket is 30/min and trips a secondary limit when burst
    repo_qualifiers: list[str] = field(default_factory=lambda: ["good-first-issues", "help-wanted-issues"])
    languages: list[str] = field(default_factory=lambda: ["Python", "TypeScript"])
    good_labels: list[str] = field(default_factory=lambda: ["good first issue", "help wanted"])
    bounty_labels: list[str] = field(default_factory=lambda: ["bounty", "💎 bounty", "algora", "💰 bounty"])
    category_keywords: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_KEYWORDS))
    known_orgs: list[str] = field(default_factory=lambda: [
        "pandas-dev", "numpy", "scipy", "python", "pallets", "psf", "encode", "tiangolo", "pydantic",
        "huggingface", "langchain-ai", "microsoft", "google", "facebook", "vercel", "apache", "polars-rs",
    ])
    ai_policy_banned_patterns: list[str] = field(default_factory=lambda: [
        r"no\s+(ai|llm)[-\s]?(generated|written|assisted)",
        r"(ai|llm)[-\s]?(generated|assisted)\s+(prs?|pull requests?|contributions?|code)\s+(are|is|will be)\s+(not accepted|rejected|closed|banned|prohibited)",
        r"do not (submit|open) (ai|llm)[-\s]?(generated|assisted)",
        r"(do not|don'?t|won'?t|will not|cannot|can'?t)\s+(accept|merge|review)\s+(any\s+)?(ai|llm)[-\s]?(generated|assisted|written)",
    ])
    ai_policy_discouraged_patterns: list[str] = field(default_factory=lambda: [
        r"(ai|llm)[-\s]?(generated|assisted)",
        r"copilot|chatgpt|claude|gemini",
    ])
    repo_signal_refresh_days: int = 7
    contributing_refresh_days: int = 30
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    category_values: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_VALUES))
    language_fit: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LANGUAGE_FIT))
    shortlist_count: int = 20
    shortlist_floor: float = 4.0
    shortlist_per_repo_cap: int = 3  # keeps one big repo from filling the whole shortlist
    dismiss_confidence: float = 0.9
    jev_model: str = "jev-latest"
    dropped_questions: list[str] = field(default_factory=list)
    gemini_model: str = "gemini-3.8-flash-medium"
    analysis_model: str = "gemini-3.8-flash-medium"
    analysis_daily_cap: int = 30
    analysis_timeout_s: float = 600.0
    analysis_max_attempts: int = 2
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "scout.db"

    @property
    def jev_cache_path(self) -> Path:
        return self.data_dir / "jev_cache.db"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "scout.log"

    @property
    def clones_dir(self) -> Path:
        return self.data_dir / "clones"


def load_config(path: Path | None = None) -> Config:
    load_dotenv(PROJECT_ROOT / ".env")
    path = path or PROJECT_ROOT / "scout.yaml"
    raw = {}
    if Path(path).exists():
        raw = yaml.safe_load(Path(path).read_text()) or {}
    cfg = Config()
    for f in fields(Config):
        if f.name not in raw:
            continue
        current = getattr(cfg, f.name)
        value = raw[f.name]
        if isinstance(current, dict) and isinstance(value, dict):
            merged = dict(current)
            merged.update(value)
            setattr(cfg, f.name, merged)
        elif f.name == "data_dir":
            setattr(cfg, f.name, Path(value))
        else:
            setattr(cfg, f.name, value)
    return cfg


def jev_api_key() -> str:
    key = os.environ.get("jev_model_api_key")
    if not key:
        raise RuntimeError("jev_model_api_key not set; copy .env.example to .env")
    return key
