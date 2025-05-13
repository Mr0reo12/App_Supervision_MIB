from fastapi.testclient import TestClient
from backend.app import app

client = TestClient(app)

def test_assets_endpoint_works():
    r = client.get("/assets")
    assert r.status_code == 200
    assert "data" in r.json()
