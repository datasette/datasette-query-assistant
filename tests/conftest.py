import os
import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-key")


@pytest.fixture(scope="module")
def vcr_config():
    return {"filter_headers": ["authorization"]}
