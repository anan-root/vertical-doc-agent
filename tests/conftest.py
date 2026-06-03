import pytest


@pytest.fixture(autouse=True)
def _disable_auth_by_default(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
