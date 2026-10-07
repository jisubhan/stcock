import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('API_DATA_DIR', str(tmp_path / 'runtime'))
    monkeypatch.delenv('TOSS_API_MANIFEST', raising=False)
    monkeypatch.delenv('TOSS_CLIENT_ID', raising=False)
    monkeypatch.delenv('TOSS_CLIENT_SECRET', raising=False)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client
