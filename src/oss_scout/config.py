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
    "quant_trading": 1.0, "prediction_markets_finance": 1.0, "ai_agents_llm": 1.0, "ml_data_science": 0.9,
    "data_scraping": 0.7, "backend_api": 0.8, "web_frontend": 0.8, "dev_tools_cli": 0.7, "devops_cloud": 0.8,
    "security": 0.8, "iot_embedded": 0.9, "logic_formal_methods": 0.9, "philosophy_religion": 0.9,
    "math_competitive": 0.8, "science_simulation": 0.7, "knowledge_reading": 0.8, "other": 0.3,
}
DEFAULT_LANGUAGE_FIT = {"Python": 1.0, "TypeScript": 0.8, "JavaScript": 0.5}
DEFAULT_DISCOVER_TOPICS = {
    "quant_trading": ["backtesting", "trading-bot", "algorithmic-trading", "quant", "quantitative-finance"],
    "prediction_markets_finance": ["polymarket", "prediction-markets", "kalshi", "sports-betting"],
    "ai_agents_llm": ["mcp-server", "llm-agents", "ai-agents", "agentic-ai", "model-context-protocol"],
    "ml_data_science": ["pytorch", "deep-learning", "machine-learning"],
    "data_scraping": ["web-scraping", "dataframe", "etl"],
    "backend_api": ["fastapi", "django", "graphql", "postgresql"],
    "web_frontend": ["react-components", "design-system"],
    "dev_tools_cli": ["developer-tools", "cli-tool"],
    "devops_cloud": ["kubernetes", "terraform", "docker", "observability"],
    "security": ["security-tools", "cryptography", "authentication"],
    "iot_embedded": ["esp32", "arduino", "home-assistant", "micropython", "raspberry-pi", "robotics", "ros2", "3d-printing"],
    "logic_formal_methods": ["lean4", "coq", "theorem-proving", "smt", "formal-verification", "logic"],
    "philosophy_religion": ["bible", "theology", "philosophy", "digital-humanities", "scripture"],
    "math_competitive": ["mathematics", "competitive-programming", "computer-algebra"],
    "science_simulation": ["physics-simulation", "astronomy", "cosmology", "computational-chemistry"],
    "knowledge_reading": ["obsidian-plugin", "note-taking", "knowledge-management", "spaced-repetition", "ebook"],
}
DEFAULT_DISCOVER_WEIGHTS = {
    "recognition": 0.0, "mergeability": 3.0, "language_fit": 2.0, "category": 3.0,
    "difficulty_fit": 1.5, "freshness": 2.0, "bounty": 0.5, "ai_policy": 1.0, "rising": 2.0,
}
DEFAULT_CATEGORY_KEYWORDS = {
    "quant_trading": ["backtest", "backtesting", "trading bot", "algorithmic trading"],
    "prediction_markets_finance": ["polymarket", "kalshi", "prediction market"],
    "ai_agents_llm": ["mcp server", "llm agent", "langchain", "tool calling"],
    "iot_embedded": ["esp32", "home assistant integration", "micropython"],
    "logic_formal_methods": ["theorem prover", "proof assistant", "smt solver"],
    "philosophy_religion": ["bible app", "scripture", "theology"],
    "knowledge_reading": ["obsidian plugin", "spaced repetition"],
}


@dataclass
class Config:
    min_stars: int = 200
    repo_idle_days: int = 60
    issue_updated_within_days: int = 30
    pages_per_query: int = 3
    per_page: int = 100
    repo_pages_per_query: int = 1
    # Discover lane: smaller and rising repos found by topic, kept apart from the resume-first ranking.
    discover_min_stars: int = 30
    discover_topics: dict[str, list[str]] = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_DISCOVER_TOPICS.items()})
    discover_pages_per_query: int = 1
    discover_issues_per_repo: int = 30
    rising_days: int = 90
    rising_min_stars: int = 50
    rising_stars_per_day: float = 20.0  # measured velocity that marks a repo rising, once we have two days of history
    discover_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_DISCOVER_WEIGHTS))
    shortlist_per_owner_cap: int = 5
    # Seed lists: sites and repos that point at other repos. Fetched once a day, cached under data/seeds.
    seed_sources: list[str] = field(default_factory=lambda: ["gittrend", "up_for_grabs", "good_first_issue", "awesome_for_beginners"])
    seed_refresh_hours: float = 24.0
    gittrend_pages: list[str] = field(default_factory=lambda: ["rising", "hidden-gems", "trending/ai-agent", "trending/ai-coding-assistant"])
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
    shortlist_count: int = 60
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
    chat_model: str = "claude-sonnet-4-6"
    chat_timeout_s: float = 300.0
    github_user: str = ""  # empty: ask `gh api user`
    draft_model: str = "claude-sonnet-4-6"
    submit_test_timeout_s: float = 900.0
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

    @property
    def seeds_dir(self) -> Path:
        return self.data_dir / "seeds"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"


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


def update_yaml(path: Path, section: str, values: dict) -> None:
    """Merge values into one top-level section of scout.yaml, leaving every other key as it was."""
    text = Path(path).read_text() if Path(path).exists() else ""
    raw = (yaml.safe_load(text) if text.strip() else {}) or {}
    merged = dict(raw.get(section) or {})
    merged.update(values)
    raw[section] = merged
    Path(path).write_text(yaml.safe_dump(raw, sort_keys=False))
