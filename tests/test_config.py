from pathlib import Path
from oss_scout.config import load_config, Config

def test_defaults_without_file(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert isinstance(cfg, Config)
    assert cfg.min_stars == 200
    assert cfg.shortlist_count == 20
    assert cfg.shortlist_floor == 4.0
    assert "Python" in cfg.languages
    assert cfg.weights["recognition"] == 3.0

def test_yaml_overrides(tmp_path):
    p = tmp_path / "scout.yaml"
    p.write_text("min_stars: 50\nweights:\n  bounty: 2.0\n")
    cfg = load_config(p)
    assert cfg.min_stars == 50
    assert cfg.weights["bounty"] == 2.0
    assert cfg.weights["recognition"] == 3.0  # untouched default survives a partial override
