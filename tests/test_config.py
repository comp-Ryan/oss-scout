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

def test_systemd_unit_puts_local_bin_on_path():
    from pathlib import Path
    unit = (Path(__file__).resolve().parents[1] / "systemd" / "oss-scout-run.service").read_text()
    assert "Environment=PATH=/home/ryan/.local/bin:" in unit
