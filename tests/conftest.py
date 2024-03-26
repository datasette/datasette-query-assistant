import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-key")
