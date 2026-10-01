import pytest
from oss_scout import seeds


@pytest.fixture(autouse=True)
def no_network_seeds(request, monkeypatch):
    """Tests never fetch seed lists from the internet; the seeds tests inject their own fetcher, the rest get none."""
    if request.module.__name__.endswith("test_seeds"):
        return
    monkeypatch.setattr(seeds, "fetch_all", lambda cfg, **kw: [])
